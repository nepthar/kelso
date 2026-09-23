"""The Repos page: the repos apps come from, and the bundles in each of them."""

from api import api
from fastapi import APIRouter
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
