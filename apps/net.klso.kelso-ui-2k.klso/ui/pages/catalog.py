"""The Repos page: the repos apps come from, and the bundles in each of them."""

from urllib.parse import quote

from api import ApiError, api
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from web import PageDep

router = APIRouter()


@router.get("/catalog")
def catalog(page: PageDep, app: str = ""):
  """Every repo and its bundles. `app` opens that bundle's card on arrival."""
  body = api("/catalog")
  repos = api("/repos").get("repos", [])
  catalogs = body.get("catalogs", [])
  contested = body.get("contested", {})
  # Each bundle's card is found by id from its row, and needs to know which
  # other repos carry the same app id.
  for entry in catalogs:
    for bundle in entry.get("apps") or []:
      app_id = bundle.get("app_id") or ""
      bundle["card_id"] = f"card-{bundle.get('repo') or 'main'}--{app_id}"
      bundle["contested"] = contested.get(app_id) or []
  return page.render(
    "pages/catalog.html",
    "Repos",
    catalogs=catalogs,
    repos={repo.get("name"): repo for repo in repos},
    contested=contested,
    open_app=app.strip(),
  )


@router.post("/catalog/edit")
async def edit(request: Request):
  """Save an edited manifest from a card. JSON in, JSON out, so a refused edit
  leaves the text in the editor."""
  try:
    body = await request.json()
  except Exception:
    return JSONResponse({"error": "Expected a JSON object"}, status_code=400)
  fields = ("target", "text", "base", "message")
  if not isinstance(body, dict) or not all(
    isinstance(body.get(name, ""), str) for name in fields
  ):
    return JSONResponse({"error": "Expected target, text and base"}, status_code=400)
  try:
    return api(
      f"/manifests/{quote(body.get('target', ''), safe='@')}",
      "POST",
      {
        "text": body.get("text", ""),
        "base": body.get("base", ""),
        "message": body.get("message", ""),
      },
    )
  except ApiError as e:
    return JSONResponse({"error": str(e)}, status_code=400)
