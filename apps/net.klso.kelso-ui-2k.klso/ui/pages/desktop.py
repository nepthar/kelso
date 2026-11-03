"""The desktop: the one page that is not a window. Everything else opens in one.

Which icons there are is decided here; how windows behave is js/desktop.js.
"""

from urllib.parse import quote, urlencode

from api import ApiError, api, where
from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from web import NO_STORE, PageDep, local_path, templates

router = APIRouter()


def _state(app):
  """How the icon reads: healthy, troubled or switched off."""
  if not (app.get("containers") or {}).get("running"):
    return "off"
  return "ok" if app.get("status") in ("healthy", "ok", "finished") else "bad"


def _routes(app):
  """An app's reachable routes, each by its primary URL.

  A route with no http(s) URL has nothing a window could open.
  """
  try:
    detail = api(f"/apps/{quote(app['app_id'], safe='')}")
  except ApiError:
    return []
  name = app.get("display_name") or app["app_id"]
  found = []
  for route in detail.get("routes", []):
    url = route.get("published_url") or route.get("host_url") or ""
    if url.startswith(("https://", "http://")):
      label = name if route["name"] == "main" else f"{name} {route['name']}"
      found.append({"label": label, "url": url, "state": _state(app)})
  return found


def _icons():
  apps = [
    {**a, "desk_state": _state(a)}
    for a in api("/apps").get("apps", [])
    if a.get("state") == "loaded"
  ]
  return {"apps": apps, "routes": [r for a in apps for r in _routes(a)]}


@router.get("/")
def desktop(page: PageDep):
  page.title = "Desktop"
  return page.render("desktop.html", "Desktop", where=where(), **_icons())


@router.get("/done")
def done(page: PageDep, then: str = "/host", ok: str = ""):
  """Where a dialog lands once it has done its work; desktop.js closes it."""
  target = local_path(then) or "/host"
  if ok:
    target += ("&" if "?" in target else "?") + urlencode({"ok": ok})
  return page.render("done.html", "Done", target=target, message=ok)


@router.get("/desktop/icons")
def icons():
  """The icons alone: what the desktop re-reads to follow the apps."""
  html = templates.get_template("_desk_icons.html").render(**_icons())
  return HTMLResponse(html, headers=NO_STORE)
