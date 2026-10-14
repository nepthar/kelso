"""The Activity page: what kelso ran unattended, and what each run printed.

Container logs are docker's and stream through `kelso logs`; this page shows
the other stream, filed under `$kelso/var/logs`.
"""

from urllib.parse import quote

from api import ApiError, api
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from web import PageDep, see

router = APIRouter()


@router.get("/activity")
def activity(page: PageDep):
  runs = api("/activity?limit=100")["activity"]
  return page.render("pages/activity.html", "Activity", runs=runs)


@router.get("/activity/{filename}")
def log(filename: str):
  """One run's output, for the job modal. JSON."""
  try:
    return api(f"/activity/{quote(filename)}")
  except ApiError as e:
    return JSONResponse({"error": str(e)}, status_code=404)


@router.get("/logs")
def old_logs():
  """The page this used to live at; old links land on it rather than 404."""
  return see("/activity")
