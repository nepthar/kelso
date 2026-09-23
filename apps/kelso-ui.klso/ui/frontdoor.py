"""What every request passes before it reaches a page, in order: the rate
limits, the origin check, the session. And the headers every response leaves
with."""

import time
from urllib.parse import quote, urlsplit

import auth
from fastapi.responses import JSONResponse
from web import render, see, wants_html

RATE_LIMITED = "Rate limiter hit - something is hitting this page too often."

GENERAL_HINT = (
  "Every request to this app shares one budget, so a browser left hammering it "
  "holds everyone up. Wait a moment, then reload."
)

RELOAD = "kelso reload kelso-ui"

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Everything a page needs comes from this server -- it may be on a network with
# no internet at all. The one exception is the fonts, which are optional (see
# js/boot.js). No inline script anywhere: a script that got into a page some
# other way cannot run.
CSP = "; ".join(
  (
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' https://fonts.googleapis.com",
    "font-src https://fonts.gstatic.com",
    "img-src 'self' data:",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  )
)

SECURITY_HEADERS = {
  "Content-Security-Policy": CSP,
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "same-origin",
  "X-Frame-Options": "DENY",
}


def auth_hint(seconds):
  return (
    f"Ten sign-in attempts an hour, shared by everyone. Another opens in about "
    f"{max(1, round(seconds / 60))} minutes, or clear the count by restarting:"
  )


class Bucket:
  """A fixed window: `limit` through it, then nothing until it turns over.

  The window is anchored to the first request after an idle gap, not to the
  wall clock, and a refusal does not move it -- so a flood costs the rest of
  the current window and nothing beyond it.
  """

  def __init__(self, limit, window):
    self.limit = limit
    self.window = window
    self.count = 0
    self.reset_at = 0.0

  def check(self):
    now = time.monotonic()
    if now - self.reset_at >= self.window:
      self.count = 0
      self.reset_at = now
    self.count += 1
    return self.count <= self.limit

  def retry_after(self):
    """Seconds left in the open window. Only meaningful just after a refusal."""
    return max(1, int(self.reset_at + self.window - time.monotonic()) + 1)


# One budget for the whole process, not one per client: behind a reverse proxy
# every request arrives from the proxy's address. `/static` is outside both --
# a refused stylesheet renders a page that looks broken rather than limited.
general = Bucket(10, 1.0)
signin = Bucket(10, 3600.0)


def _authority(value):
  """host[:port], lowercased, without the port a scheme implies."""
  value = value.strip().lower()
  for default in (":443", ":80"):
    value = value.removesuffix(default)
  return value


def same_origin(headers):
  """Whether a request came from a page this server served.

  SameSite=Lax on the session cookie is not enough here. Kelso publishes every
  app on a subdomain beside this one, and sibling subdomains are the same
  *site*, so any installed app's pages could POST a kelso verb with the cookie
  attached. The Origin header names the exact origin and no page can set it.
  A proxy may rename the host; X-Forwarded-Host is not something a page can
  send cross-origin either, so it is trusted as a second name.
  """
  source = headers.get("origin") or headers.get("referer")
  if not source:
    return False
  claimed = _authority(urlsplit(source).netloc)
  if not claimed:
    return False
  names = [headers.get("host") or ""]
  names += (headers.get("x-forwarded-host") or "").split(",")
  return claimed in {_authority(n) for n in names if n.strip()}


def _refuse(request, status, title, message, hint, command="", headers=None):
  headers = {"Cache-Control": "no-store", **(headers or {})}
  if not wants_html(request):
    return JSONResponse({"error": message}, status, headers=headers)
  return render(
    request,
    "notice.html",
    title,
    status_code=status,
    headers=headers,
    message=message,
    hint=hint,
    command=command,
  )


def _limited(request, bucket, hint, command=""):
  retry = {"Retry-After": str(bucket.retry_after())}
  return _refuse(request, 429, "Rate limited", RATE_LIMITED, hint, command, retry)


async def _admit(request, call_next):
  path = request.url.path
  if path.startswith("/static/"):
    return await call_next(request)

  # Everything answers to the general bucket; a sign-in attempt answers to both,
  # so a flood spends itself on the per-second budget before it can eat far into
  # the hour's worth of guesses.
  if not general.check():
    return _limited(request, general, GENERAL_HINT)
  if path == "/login" and request.method == "POST" and not signin.check():
    return _limited(request, signin, auth_hint(signin.retry_after()), RELOAD)

  if request.method not in SAFE_METHODS and not same_origin(request.headers):
    return _refuse(
      request,
      403,
      "Refused",
      "Refused a request from another site.",
      "Something outside this kelso page tried to act on it. If that was you, "
      "open kelso directly and try again from there.",
    )

  if auth.is_open(path) or auth.valid(request.cookies.get(auth.COOKIE)):
    return await call_next(request)
  if not wants_html(request):
    return JSONResponse({"error": "Session expired. Reload and sign in."}, 401)
  # A form submission that lost its session cannot be replayed by landing on
  # it, and `next` would point at a path with no GET. Send those to the door.
  if request.method != "GET":
    return see("/login")
  here = path + (f"?{request.url.query}" if request.url.query else "")
  return see(f"/login?next={quote(here)}")


async def front_door(request, call_next):
  """Rate limit, origin, session. Everything reaches a page through here."""
  response = await _admit(request, call_next)
  response.headers.update(SECURITY_HEADERS)
  return response
