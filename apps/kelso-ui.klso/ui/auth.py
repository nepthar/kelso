import hashlib
import hmac
import os
import sys
import time

# Kelso is a single-user application, there is only one admin password

COOKIE = "kelso_session"
SESSION_SECONDS = 7 * 24 * 3600

PASSWORD = os.environ.get("ADMIN_PASSWORD", "").strip()

# For running the UI on a dev machine. The manifest fixes the container's env,
# so a deployed kelso-ui cannot be switched into this through `kelso config`.
NO_AUTH = os.environ.get("KELSO_UI_NO_AUTH") == "1"

if NO_AUTH:
  print(
    "kelso-ui: KELSO_UI_NO_AUTH=1 -- every request is signed in. Dev only.",
    file=sys.stderr,
  )
elif not PASSWORD:
  raise RuntimeError(
    "ADMIN_PASSWORD is empty. Set it with: "
    "kelso config kelso-ui --set admin_pass=<password>"
  )

_KEY = hmac.new(b"kelso-ui/session/v1", PASSWORD.encode(), hashlib.sha256).digest()


def _sign(payload):
  return hmac.new(_KEY, payload.encode(), hashlib.sha256).hexdigest()


def check_password(candidate):
  """True if this is the admin password. Guessing is the rate limiter's problem."""
  return hmac.compare_digest(
    hashlib.sha256(candidate.encode()).digest(),
    hashlib.sha256(PASSWORD.encode()).digest(),
  )


def issue():
  """A fresh cookie value and how many seconds it is good for."""
  expiry = int(time.time()) + SESSION_SECONDS
  return f"{expiry}.{_sign(str(expiry))}", SESSION_SECONDS


def valid(cookie):
  if NO_AUTH:
    return True
  if not cookie:
    return False
  payload, _, signature = cookie.partition(".")
  if not payload.isdigit() or not hmac.compare_digest(signature, _sign(payload)):
    return False
  return int(payload) > int(time.time())


def is_open(path):
  """Paths served without a session: the login form and its stylesheet."""
  return path == "/login" or path.startswith("/static/")
