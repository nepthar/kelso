"""The Backups page: where they go, how the last run went, and every app
backup, with restore on each row."""

from api import api
from fastapi import APIRouter
from web import PageDep

router = APIRouter()


@router.get("/backups")
def backups(page: PageDep):
  view = api("/backups")
  return page.render("pages/backups.html", "Backups", view=view)
