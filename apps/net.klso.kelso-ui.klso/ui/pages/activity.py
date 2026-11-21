from urllib.parse import quote

from api import ApiError, api
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from web import PageDep

router = APIRouter()


@router.get("/activity")
def activity(page: PageDep):
  runs = api("/activity?limit=100")["activity"]
  return page.render("pages/activity.html", "Activity", runs=runs)


@router.get("/activity/{filename}")
def log(filename: str):
  try:
    return api(f"/activity/{quote(filename)}")
  except ApiError as e:
    return JSONResponse({"error": str(e)}, status_code=404)
