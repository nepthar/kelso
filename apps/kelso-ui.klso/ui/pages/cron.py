"""The Cron page: upcoming runs above a "now" line, finished runs below it."""

from api import api
from fastapi import APIRouter
from web import PageDep

router = APIRouter()


@router.get("/cron")
def cron(page: PageDep):
  # Furthest first, so time runs down the page to the "now" line.
  upcoming = list(reversed(api("/cron")["cron"]))
  runs = api("/activity?verb=cron&limit=100")["activity"]
  return page.render("pages/cron.html", "Cron", upcoming=upcoming, runs=runs)
