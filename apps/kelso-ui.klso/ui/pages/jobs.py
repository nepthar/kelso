"""The job modal's backend: start a kelso verb, then poll it. JSON only."""

from urllib.parse import quote

from api import ApiError, api
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.post("/jobs")
async def submit(request: Request):
  """Forward a job from the job modal. JSON in, JSON out."""
  try:
    body = await request.json()
  except Exception:
    return JSONResponse({"error": "Expected a JSON object"}, status_code=400)
  verb = body.get("verb") if isinstance(body, dict) else None
  args = body.get("args") if isinstance(body, dict) else None
  if not verb or not isinstance(args, dict):
    return JSONResponse({"error": "Expected verb and args"}, status_code=400)
  try:
    job = api("/jobs", "POST", {"verb": verb, "args": args})
  except ApiError as e:
    return JSONResponse({"error": str(e)}, status_code=400)
  return JSONResponse(job, status_code=202)


@router.get("/jobs/{job_id}")
def status(job_id: str):
  try:
    return api(f"/jobs/{quote(job_id)}")
  except ApiError as e:
    return JSONResponse({"error": str(e)}, status_code=404)
