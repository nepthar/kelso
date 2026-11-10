"""An app's icon, found on its route and served from here.

The browser asks this server and never the app, so a route the browser cannot
reach, or an http icon on an https page, still shows. What was found is kept
for a while; an app with nothing to show costs one lookup a minute.
"""

import ssl
import time
from functools import cache
from http.client import HTTPException
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, build_opener

from api import API, ApiError, api
from fastapi import APIRouter
from fastapi.responses import Response
from web import STATIC

router = APIRouter()

FOUND_FOR = 600
MISSING_FOR = 60
LIMIT = 256 * 1024
TIMEOUT = 1.5
FILES = ("/icon.svg", "/favicon.ico")
LOOPBACK = ("localhost", "127.0.0.1", "::1")

# Icon fetches alone skip certificate checks: an app's own port is often
# self-signed, and an icon is untrusted data whoever sends it.
_tls = ssl.create_default_context()
_tls.check_hostname = False
_tls.verify_mode = ssl.CERT_NONE

# What an app sends is served from this origin, so it may not run anything.
HEADERS = {
  "Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'",
  "Cache-Control": "private, max-age=60",
}

_kept = {}


class _SameOrigin(HTTPRedirectHandler):
  """Follows a redirect only to where it already is: an app may move its icon."""

  def redirect_request(self, req, fp, code, msg, headers, newurl):
    here, there = urlsplit(req.full_url), urlsplit(newurl)
    if (here.scheme, here.netloc) != (there.scheme, there.netloc):
      return None
    return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = build_opener(HTTPSHandler(context=_tls), _SameOrigin)


@cache
def _drawn(name):
  return (STATIC / "icons" / f"{name}.svg").read_bytes()


def _fetch(url):
  """(content type, bytes) of the image at `url`, or None."""
  try:
    with _opener.open(url, timeout=TIMEOUT) as response:
      kind = response.headers.get_content_type()
      body = response.read(LIMIT + 1)
  except (OSError, ValueError, HTTPException):
    return None
  if not kind.startswith("image/") or not body or len(body) > LIMIT:
    return None
  return kind, body


def _origin(url, loopback=""):
  """scheme://host:port of an http(s) `url`, with `loopback` for localhost."""
  try:
    parts = urlsplit(url or "")
    port = f":{parts.port}" if parts.port else ""
  except ValueError:
    return None
  if parts.scheme not in ("http", "https") or not parts.hostname:
    return None
  host = loopback if loopback and parts.hostname in LOOPBACK else parts.hostname
  return f"{parts.scheme}://{host}{port}"


def _origins(route):
  """Where to look, in order: the route's published address, then its host's.

  kelso names the host as people click it, usually `localhost`, which from here
  is this container. Where kelsod is, is where the host is.
  """
  kelsod = API.split("://", 1)[-1].rsplit(":", 1)[0] if API else ""
  found = []
  for origin in (
    _origin(route.get("published_url")),
    _origin(route.get("host_url"), kelsod),
  ):
    if origin and origin not in found:
      found.append(origin)
  return found


def _search(route):
  for origin in _origins(route):
    for path in FILES:
      if found := _fetch(origin + path):
        return found
  return None


@router.get("/icons/{app_id}")
@router.get("/icons/{app_id}/{route}")
def icon(app_id: str, route: str = ""):
  """The app's icon from its `main` route, or the named route's, or the drawn one."""
  drawn = "route" if route else "app"
  hit = _kept.get((app_id, route))
  if hit and hit[0] > time.monotonic():
    found = hit[1]
  else:
    try:
      routes = api(f"/apps/{quote(app_id, safe='')}").get("routes", [])
    except ApiError:
      routes = None
    chosen = next((r for r in routes or [] if r["name"] == (route or "main")), None)
    if chosen is None:
      found = None
    else:
      found = _search(chosen)
      ttl = FOUND_FOR if found else MISSING_FOR
      _kept[(app_id, route)] = (time.monotonic() + ttl, found)
  kind, body = found or ("image/svg+xml", _drawn(drawn))
  return Response(body, media_type=kind, headers=HEADERS)
