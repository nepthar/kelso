from urllib.parse import quote

from api import ApiError, api
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from web import NO_STORE, PageDep, field, see, submitted_values

router = APIRouter()

PROXIED = ("restrict", "ask", "watch")


@router.get("/apps")
def apps_list():
  return see("/host")


@router.get("/logs")
def logs_index(page: PageDep):
  apps = [a for a in api("/apps").get("apps", []) if a.get("state") == "loaded"]
  return page.render("pages/logs.html", "Logs", apps=apps)


@router.get("/apps/{app_id}")
def detail(page: PageDep, app_id: str):
  page.title = app_id
  app = api(f"/apps/{quote(app_id)}")
  config = api(f"/apps/{quote(app_id)}/config-request")
  mode = next(
    (f.get("value") for f in config.get("fields", []) if f["name"] == "network.mode"),
    None,
  )
  net, net_error = None, None
  if mode in PROXIED:
    try:
      net = api(f"/apps/{quote(app_id)}/net")
    except ApiError as e:
      net_error = str(e)
  title = app.get("display_name") or app_id
  return page.render(
    "pages/app.html",
    title,
    app=app,
    config=config,
    mode=mode,
    net=net,
    net_error=net_error,
  )


@router.get("/apps/{app_id}/manifest")
def manifest(page: PageDep, app_id: str):
  page.title = app_id
  app = api(f"/apps/{quote(app_id)}")
  title = f"{app.get('display_name') or app_id} manifest"
  path = f"$kelso/var/run/{app_id}/app_bundle/manifest.toml"
  return page.render(
    "pages/app_manifest.html", title, text=app.get("manifest_text") or "", path=path
  )


@router.post("/apps/{app_id}")
async def post_config(app_id: str, request: Request):
  form = await request.form()
  here = f"/apps/{quote(app_id)}"
  if field(form, "action") != "config":
    return see(here)
  try:
    api(f"{here}/config-response", "POST", {"values": submitted_values(form)})
  except ApiError as e:
    return see(here, err=str(e))
  return see(here, ok="Saved")


@router.get("/apps/{app_id}/logs")
def logs(page: PageDep, app_id: str):
  page.title = app_id
  app = api(f"/apps/{quote(app_id)}")
  tail = api(f"/apps/{quote(app_id)}/logs")
  title = f"{app.get('display_name') or app_id} logs"
  return page.render("pages/app_logs.html", title, app_id=app_id, logs=tail)


@router.get("/apps/{app_id}/logs.json")
def logs_json(app_id: str):
  try:
    return JSONResponse(api(f"/apps/{quote(app_id)}/logs"), headers=NO_STORE)
  except ApiError as e:
    return JSONResponse({"error": str(e)}, status_code=502, headers=NO_STORE)


@router.get("/apps/{app_id}/net.json")
def net_json(app_id: str):
  try:
    return JSONResponse(api(f"/apps/{quote(app_id)}/net"), headers=NO_STORE)
  except ApiError as e:
    return JSONResponse({"error": str(e)}, status_code=502, headers=NO_STORE)


@router.post("/apps/{app_id}/net.json")
async def net_decide(app_id: str, request: Request):
  try:
    body = await request.json()
  except ValueError:
    body = None
  decisions = body.get("decisions") if isinstance(body, dict) else None
  if not isinstance(decisions, dict):
    return JSONResponse({"error": "Expected decisions"}, status_code=400)
  try:
    view = api(f"/apps/{quote(app_id)}/net", "POST", {"decisions": decisions})
  except ApiError as e:
    return JSONResponse({"error": str(e)}, status_code=400, headers=NO_STORE)
  return JSONResponse(view, headers=NO_STORE)
