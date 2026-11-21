from api import api
from fastapi import APIRouter
from web import PageDep

router = APIRouter()


@router.get("/backups")
def backups(page: PageDep):
  view = api("/backups")
  return page.render("pages/backups.html", "Backups", view=view)
