"""The Volumes page: host-volume declarations and kelso-managed storage."""

from urllib.parse import quote

from api import ApiError, api
from fastapi import APIRouter, Request
from web import PageDep, field, see

router = APIRouter()


@router.get("/volumes")
def volumes(page: PageDep):
  managed = api("/volumes")
  # Biggest first: this table is where you go to find what is eating the disk.
  # A volume not measured yet has no size and sorts to the bottom.
  by_size = sorted(
    managed.get("volumes") or [],
    key=lambda v: -1 if v.get("bytes") is None else v["bytes"],
    reverse=True,
  )
  return page.render(
    "pages/volumes.html",
    "Volumes",
    host_volumes=api("/host-volumes")["host_volumes"],
    volumes=by_size,
    kelso_dirs=managed.get("kelso_dirs") or [],
  )


@router.post("/volumes")
async def post_volumes(request: Request):
  """Declare a host volume, or drop one."""
  form = await request.form()
  action, tag = field(form, "action"), field(form, "tag")
  try:
    if action == "create":
      api(
        "/host-volumes",
        "POST",
        {
          "tag": tag,
          "path": field(form, "path"),
          "readonly": bool(form.get("readonly")),
          "require_mount": bool(form.get("require_mount")),
        },
      )
      return see("/volumes", ok=f"Added host volume {tag}")
    if action == "delete":
      api(f"/host-volumes/{quote(tag)}", "DELETE")
      return see("/volumes", ok=f"Removed host volume {tag}")
  except ApiError as e:
    return see("/volumes", err=str(e))
  return see("/volumes")
