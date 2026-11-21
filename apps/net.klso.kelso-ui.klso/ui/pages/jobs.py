from urllib.parse import quote

from api import STARTED_BY, ApiError, api
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from web import PageDep, render

router = APIRouter()


@router.get("/job")
def window(page: PageDep):
  return page.render("pages/job.html", "Job", log="")


@router.get("/job/{log}")
def view(page: PageDep, log: str, title: str = ""):
  try:
    api(f"/activity/{quote(log)}")
  except ApiError:
    return render(page.request, "not_found.html", "Not found", status_code=404)
  name = log.removesuffix(".log").split(".", 1)[-1]
  return page.render("pages/job.html", title or name, log=log)


@router.post("/jobs")
async def submit(request: Request):
  try:
    body = await request.json()
  except Exception:
    return JSONResponse({"error": "Expected a JSON object"}, status_code=400)
  verb = body.get("verb") if isinstance(body, dict) else None
  args = body.get("args") if isinstance(body, dict) else None
  if not verb or not isinstance(args, dict):
    return JSONResponse({"error": "Expected verb and args"}, status_code=400)
  try:
    job = api("/jobs", "POST", {"verb": verb, "args": args, "started_by": STARTED_BY})
  except ApiError as e:
    return JSONResponse({"error": str(e)}, status_code=400)
  return JSONResponse(job, status_code=202)


@router.get("/jobs/{job_id}")
def status(job_id: str):
  try:
    return api(f"/jobs/{quote(job_id)}")
  except ApiError as e:
    return JSONResponse({"error": str(e)}, status_code=404)
