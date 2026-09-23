"""One installed app: its page, its config form, and its container logs."""

from urllib.parse import quote

from api import ApiError, api
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from web import NO_STORE, PageDep, field, see, submitted_values

router = APIRouter()


@router.get("/apps")
def apps_list():
  """The list moved onto the dashboard; old links land there rather than 404."""
  return see("/")


@router.get("/apps/{app_id}")
def detail(page: PageDep, app_id: str):
  page.title = app_id
  app = api(f"/apps/{quote(app_id)}")
  config = api(f"/apps/{quote(app_id)}/config-request")
  title = app.get("display_name") or app_id
  return page.render("pages/app.html", title, app=app, config=config)


@router.post("/apps/{app_id}")
async def post_config(app_id: str, request: Request):
  """The config form; lifecycle verbs go through the job modal."""
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
  """The last lines of an app's container logs, refreshed on a timer."""
  page.title = app_id
  app = api(f"/apps/{quote(app_id)}")
  tail = api(f"/apps/{quote(app_id)}/logs")
  title = f"{app.get('display_name') or app_id} logs"
  return page.render("pages/app_logs.html", title, app_id=app_id, logs=tail)


@router.get("/apps/{app_id}/logs.json")
def logs_json(app_id: str):
  """What the logs page polls. Straight through; kelsod does the tailing."""
  try:
    return JSONResponse(api(f"/apps/{quote(app_id)}/logs"), headers=NO_STORE)
  except ApiError as e:
    return JSONResponse({"error": str(e)}, status_code=502, headers=NO_STORE)
