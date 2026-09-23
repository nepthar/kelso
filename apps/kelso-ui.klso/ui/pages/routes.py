"""The Routes page: configured route providers, and the form that sets one up."""

from urllib.parse import quote

from api import ApiError, api
from fastapi import APIRouter, Request
from web import PageDep, field, see, submitted_values

router = APIRouter()


@router.get("/routes")
def routes(page: PageDep):
  body = api("/route-providers")
  return page.render(
    "pages/routes.html",
    "Routes",
    providers=body["route_providers"],
    kinds=body["kinds"],
  )


@router.get("/routes/new")
def new(tag: str = "", kind: str = ""):
  """The add row is a GET form; its tag belongs in the path, not the query."""
  return see(f"/routes/{quote(tag.strip())}", kind=kind)


@router.get("/routes/{tag}")
def provider(page: PageDep, tag: str, kind: str = ""):
  """The config form for `tag`; `kind` only for a provider not configured yet."""
  page.title = tag
  query = f"?kind={quote(kind)}" if kind else ""
  config = api(f"/route-providers/{quote(tag)}/config-request{query}")
  return page.render(
    "pages/route_provider.html", tag, config=config, tag=tag, kind=kind
  )


@router.post("/routes/{tag}")
async def post_provider(tag: str, request: Request):
  form = await request.form()
  kind = field(form, "kind")
  query = f"?kind={quote(kind)}" if kind else ""
  try:
    api(
      f"/route-providers/{quote(tag)}/config-response{query}",
      "POST",
      {"values": submitted_values(form)},
    )
  except ApiError as e:
    return see(f"/routes/{quote(tag)}", kind=kind or None, err=str(e))
  return see("/routes", ok=f"Saved {tag}")
