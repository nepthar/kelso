"""The app: the front door, then one router per feature, then static files.

Adding a feature means a module under pages/ with its own router, templates
under templates/pages/, and any script under static/js/ -- nothing here but
the line that includes it.
"""

from api import ApiError
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from frontdoor import front_door
from pages import (
  activity,
  apps,
  catalog,
  dashboard,
  jobs,
  routes,
  session,
  snapshots,
  volumes,
)
from web import STATIC, api_error, render

# No OpenAPI: this app has no API consumers, and /docs would be one more
# surface behind the same one password.
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.middleware("http")(front_door)
app.add_exception_handler(ApiError, api_error)

for feature in (
  session,
  dashboard,
  apps,
  catalog,
  volumes,
  snapshots,
  routes,
  activity,
  jobs,
):
  app.include_router(feature.router)

app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/{path:path}")
def not_found(request: Request, path: str):
  return render(request, "not_found.html", "Not found", status_code=404)
