import os
from functools import cache
from hashlib import blake2s
from pathlib import Path
from typing import Annotated, NamedTuple
from urllib.parse import urlencode

import auth
import settings
from api import ApiError, api
from fastapi import Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from icons import favicon, mdi
from jinja2 import Environment, FileSystemLoader, StrictUndefined

HERE = Path(__file__).parent
STATIC = HERE / "static"

NO_STORE = {"Cache-Control": "no-store"}

# The kelsod API version this UI is written against; a mismatch means one of
# the two was loaded without the other.
NEEDS_API = 2

# Random per load (kelso-ui's `instance_id`), so two kelso-ui tabs can be
# told apart even when neither can reach its kelsod.
INSTANCE_ID = os.environ.get("KELSO_UI_ID", "").strip()


# kelsod's hostname, as it last gave it. Pages that do not otherwise need
# kelsod (sign-in) read this rather than asking again.
_hostname = ""


def known_hostname():
  return _hostname


def remember_hostname(name):
  global _hostname
  if name:
    _hostname = name


def brand(hostname):
  short = hostname.rsplit(".", 1)[0] if "." in hostname else hostname
  return short or f"Kelso {INSTANCE_ID}".strip()


class NavItem(NamedTuple):
  href: str
  label: str
  icon: str


# The system icons on the desktop and in the start menu. `icon` names a file in
# static/icons/.
NAV = (
  NavItem("/host", "Host", "host"),
  NavItem("/catalog", "App Repos", "repos"),
  NavItem("/volumes", "Volumes", "volumes"),
  NavItem("/backups", "Backups", "backups"),
  NavItem("/routes", "Routes", "routes"),
  NavItem("/cron", "Cron", "cron"),
  NavItem("/activity", "Activity", "activity"),
  NavItem("/logs", "Logs", "logs"),
  NavItem("/host/shell", "Shell", "shell"),
  NavItem("/settings", "Settings", "settings"),
)


def nav_active(path):
  matches = [i for i in NAV if path == i.href or path.startswith(i.href + "/")]
  return max(matches, key=lambda i: len(i.href), default=None)


@cache
def asset(name):
  try:
    version = blake2s((STATIC / name).read_bytes(), digest_size=6).hexdigest()
  except OSError:
    version = "dev"
  return f"/static/{name}?v={version}"


def icon_url(name):
  return asset(f"icons/{name}.svg")


def percent(used, total):
  return round(100 * used / total) if total else 0


def fmt_size(n):
  if n is None:
    return "—"
  size = float(n)
  for unit in ("B", "KB", "MB", "GB", "TB"):
    if size < 1024:
      return f"{size:.1f} {unit}"
    size /= 1024
  return f"{size:.1f} PB"


# Every template is HTML, so every template escapes. StrictUndefined turns a
# misspelt variable or a missing required field into an error rather than a
# blank; optional API fields are read with `.get()`, as in Python.
templates = Environment(
  loader=FileSystemLoader(HERE / "templates"),
  autoescape=True,
  undefined=StrictUndefined,
  trim_blocks=True,
  lstrip_blocks=True,
  extensions=["jinja2.ext.do"],
)
templates.globals.update(
  asset=asset,
  mdi=mdi,
  icon_url=icon_url,
  desktop_colors=lambda: settings.background(INSTANCE_ID),
  theme_url=lambda name=None: asset(f"themes/{name or settings.theme()}.css"),
  nav=NAV,
  NEEDS_API=NEEDS_API,
  INSTANCE_ID=INSTANCE_ID,
  NO_PASSWORD=auth.TESTING_NO_PASSWORD,
  # A tab icon has no theme, and this reads on light and dark.
  FAVICON=favicon("dune", "#f08a4b"),
)
templates.filters["size"] = fmt_size
templates.filters["brand"] = brand


def render(request, template, title, *, status_code=200, headers=None, **context):
  frame = {
    "title": title,
    "version": "",
    "daemon_api": None,
    "ok": request.query_params.get("ok"),
    "err": request.query_params.get("err"),
  }
  html = templates.get_template(template).render(frame | context)
  return HTMLResponse(html, status_code=status_code, headers=headers or NO_STORE)


class Page:
  def __init__(self, request: Request):
    self.request = request
    active = nav_active(request.url.path)
    self.title = active.label if active else "Kelso"
    request.state.page = self

  def render(self, template, title, **context):
    info = api("/version")
    context.setdefault("version", info.get("kelso", ""))
    context.setdefault("daemon_api", info.get("api"))
    remember_hostname(info.get("hostname", ""))
    context.setdefault("hostname", info.get("hostname", ""))
    return render(self.request, template, title, **context)


# What a GET handler takes: `def handler(page: PageDep, ...)`.
PageDep = Annotated[Page, Depends()]


def wants_html(request):
  return "text/html" in request.headers.get("accept", "")


def api_error(request, exc: ApiError):
  if not wants_html(request):
    return JSONResponse({"error": str(exc)}, status_code=502, headers=NO_STORE)
  page = getattr(request.state, "page", None)
  active = nav_active(request.url.path)
  title = page.title if page else (active.label if active else "Kelso")
  return render(
    request,
    "error.html",
    title,
    status_code=502,
    message=str(exc),
  )


def local_path(value):
  # Browsers drop tabs and newlines and read \ as /: /<tab>/x and /\x are off-site.
  if not value.startswith("/") or "\\" in value or value[1:2] == "/":
    return None
  if any(ord(c) < 32 or ord(c) == 127 for c in value):
    return None
  return value


def see(location, **params):
  query = urlencode({k: v for k, v in params.items() if v is not None})
  return RedirectResponse(f"{location}?{query}" if query else location, 303)


def field(form, name):
  value = form.get(name)
  return "" if value is None else str(value).strip()


def submitted_values(form):
  return {
    key.removeprefix("set."): field(form, key)
    for key in form
    if key.startswith("set.") and field(form, key)
  }
