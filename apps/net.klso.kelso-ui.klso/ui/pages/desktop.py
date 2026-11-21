from urllib.parse import quote, urlencode

from api import ApiError, api, where
from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from web import (
  NO_STORE,
  PageDep,
  brand,
  known_hostname,
  local_path,
  percent,
  templates,
)

router = APIRouter()


def _state(app):
  if not (app.get("containers") or {}).get("running"):
    return "off"
  return "ok" if app.get("status") in ("healthy", "ok", "finished") else "bad"


def _links(app, detail):
  name = app.get("display_name") or app["app_id"]
  found = []
  for route in detail.get("routes", []):
    url = route.get("published_url") or route.get("host_url") or ""
    if url.startswith(("https://", "http://")):
      label = name if route["name"] == "main" else f"{name} {route['name']}"
      found.append(
        {
          "app_id": app["app_id"],
          "name": route["name"],
          "label": label,
          "url": url,
          "state": _state(app),
        }
      )
  return found


def _apps():
  found = []
  for app in api("/apps").get("apps", []):
    if app.get("state") != "loaded":
      continue
    try:
      detail = api(f"/apps/{quote(app['app_id'], safe='')}")
    except ApiError:
      detail = {}
    found.append(
      {
        **app,
        "desk_state": _state(app),
        "links": _links(app, detail),
        "commands": detail.get("commands") or [],
        "shells": [
          u["name"] for u in detail.get("units", []) if u.get("state") == "running"
        ],
      }
    )
  return found


def _uptime(seconds):
  days, rest = divmod(int(seconds), 86400)
  hours, rest = divmod(rest, 3600)
  if days:
    return f"{days}d {hours}h"
  return f"{hours}h {rest // 60}m" if hours else f"{rest // 60}m"


def _status(apps):
  host = api("/host")
  return {
    "host": brand(known_hostname() or api("/version").get("hostname", "")),
    "running": sum(1 for a in apps if a["desk_state"] == "ok"),
    "failing": [
      a.get("display_name") or a["app_id"] for a in apps if a["desk_state"] == "bad"
    ],
    "uptime": _uptime(host["uptime_seconds"]),
    "load": " ".join(f"{n:.2f}" for n in host["load"]),
    "memory": percent(host["memory_used_bytes"], host["memory_bytes"]),
    "disks": [
      {
        "name": disk["mountpoint"],
        "title": f"{disk['mountpoint']} on {disk['device']}",
        "percent": percent(disk["used_bytes"], disk["total_bytes"]),
      }
      for disk in host["disks"]
    ],
  }


def _icons():
  apps = _apps()
  return {
    "apps": apps,
    "routes": [r for a in apps for r in a["links"]],
    "status": _status(apps),
  }


@router.get("/")
def desktop(page: PageDep):
  page.title = "Desktop"
  return page.render("desktop.html", "Desktop", where=where(), **_icons())


@router.get("/done")
def done(page: PageDep, then: str = "/host", ok: str = ""):
  target = local_path(then) or "/host"
  if ok:
    target += ("&" if "?" in target else "?") + urlencode({"ok": ok})
  return page.render("done.html", "Done", target=target, message=ok)


@router.get("/desktop/icons")
def icons():
  html = templates.get_template("_desk_icons.html").render(**_icons())
  return HTMLResponse(html, headers=NO_STORE)
