"""The Settings window: what kelso-ui-2k itself looks like."""

import settings
from fastapi import APIRouter, Request
from web import INSTANCE_ID, PageDep, field, see

router = APIRouter()


@router.get("/settings")
def settings_page(page: PageDep):
  chosen = settings.load().get("background") or {}
  generated = settings.id_colors(INSTANCE_ID)
  top, bottom = settings.background(INSTANCE_ID)
  return page.render(
    "pages/settings.html",
    "Settings",
    custom=chosen.get("mode") == "custom",
    generated=generated,
    top=top,
    bottom=bottom,
  )


@router.post("/settings")
async def post_settings(request: Request):
  form = await request.form()
  mode = "custom" if field(form, "mode") == "custom" else "id"
  top, bottom = field(form, "top").lower(), field(form, "bottom").lower()
  if mode == "custom" and not (settings.HEX.match(top) and settings.HEX.match(bottom)):
    return see("/settings", err="Pick both colours, as #rrggbb.")
  try:
    settings.save({"background": {"mode": mode, "top": top, "bottom": bottom}})
  except OSError as e:
    return see("/settings", err=str(e))
  return see("/settings", ok="Saved")
