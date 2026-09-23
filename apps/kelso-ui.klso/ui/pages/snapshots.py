"""The Snapshots page: every archive, with restore and delete on each row."""

from api import api
from fastapi import APIRouter
from web import PageDep

router = APIRouter()


@router.get("/snapshots")
def snapshots(page: PageDep):
  return page.render(
    "pages/snapshots.html", "Snapshots", snapshots=api("/snapshots")["snapshots"]
  )
