"""How a handler answers: templates, the page frame, redirects and form fields.

Markup lives in `templates/`, never in Python. A GET handler takes a `Page`
and returns `page.render(template, title, **context)`; a POST handler does its
work and returns `see(...)` so the browser lands back on a GET.
"""

from functools import cache
from hashlib import blake2s
from pathlib import Path
from typing import Annotated, NamedTuple
from urllib.parse import urlencode

from api import ApiError, api
from fastapi import Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from icons import mdi
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from themes import THEMES

HERE = Path(__file__).parent
STATIC = HERE / "static"

NO_STORE = {"Cache-Control": "no-store"}

# The kelsod API this UI is written against. kelsod bumps its own number
# when a response shape changes, so a mismatch means one of the two was
# installed without the other and fields this UI reads may be missing.
NEEDS_API = 20


class NavItem(NamedTuple):
  href: str
  label: str
  icon: str


NAV = (
  NavItem("/", "Dashboard", "home-outline"),
  NavItem("/catalog", "Repos", "book-multiple-outline"),
  NavItem("/volumes", "Volumes", "database-outline"),
  NavItem("/snapshots", "Snapshots", "camera-outline"),
  NavItem("/routes", "Routes", "network-outline"),
  NavItem("/activity", "Activity", "file-document-multiple-outline"),
)


def nav_active(path):
  """The nav entry a path belongs to.

  App detail pages have no nav entry of their own -- the list they belong to
  lives on the dashboard -- so they light Dashboard instead of nothing.
  """
  if path.startswith("/apps"):
    return NAV[0]
  for item in NAV:
    if path == item.href or (item.href != "/" and path.startswith(item.href + "/")):
      return item
  return None


@cache
def asset(name):
  """URL of a file under static/, carrying a digest of its contents.

  Pages are `no-store` but assets are not, so a changed file needs a changed
  URL or a browser will hold on to the old one.
  """
  try:
    version = blake2s((STATIC / name).read_bytes(), digest_size=6).hexdigest()
  except OSError:
    version = "dev"
  return f"/static/{name}?v={version}"


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
  asset=asset, mdi=mdi, nav=NAV, themes=THEMES, NEEDS_API=NEEDS_API
)
templates.filters["size"] = fmt_size


def render(request, template, title, *, status_code=200, headers=None, **context):
  """A whole page. `ok` / `err` in the query become the notice banner."""
  path = request.url.path
  frame = {
    "path": path,
    "active": nav_active(path),
    "title": title,
    "version": "",
    "daemon_api": None,
    "ok": request.query_params.get("ok"),
    "err": request.query_params.get("err"),
  }
  html = templates.get_template(template).render(frame | context)
  return HTMLResponse(html, status_code=status_code, headers=headers or NO_STORE)


class Page:
  """What a GET handler renders into.

  `render` asks kelsod for its version, so the frame can name it and warn when
  the two disagree. `title` is what the error page is called if kelsod fails
  first -- set it before the handler's first `api()` call when the nav label
  is not the right name.
  """

  def __init__(self, request: Request):
    self.request = request
    active = nav_active(request.url.path)
    self.title = active.label if active else "Kelso"
    request.state.page = self

  def render(self, template, title, **context):
    info = api("/version")
    context.setdefault("version", info.get("kelso", ""))
    context.setdefault("daemon_api", info.get("api"))
    return render(self.request, template, title, **context)


# What a GET handler takes: `def handler(page: PageDep, ...)`.
PageDep = Annotated[Page, Depends()]


def wants_html(request):
  """A browser navigating or submitting a form, rather than a page's fetch.

  The method cannot tell these apart -- the sign-in form is a POST too -- and
  `fetch` asks for `*/*` where a browser names text/html.
  """
  return "text/html" in request.headers.get("accept", "")


def api_error(request, exc: ApiError):
  """kelsod unreachable or refusing: the error inside the frame, or JSON."""
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


def see(location, **params):
  """Post/redirect/get: the browser lands on a GET, so a refresh re-reads
  rather than re-submitting. `params` with a None value are left off."""
  query = urlencode({k: v for k, v in params.items() if v is not None})
  return RedirectResponse(f"{location}?{query}" if query else location, 303)


def field(form, name):
  value = form.get(name)
  return "" if value is None else str(value).strip()


def submitted_values(form):
  """The `set.*` fields of a posted config form, minus blanks.

  Blank means "leave it alone": a secret's input is always empty, since the UI
  never had its value, and kelsod skips anything unchanged.
  """
  return {
    key.removeprefix("set."): field(form, key)
    for key in form
    if key.startswith("set.") and field(form, key)
  }
