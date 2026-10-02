"""The Snapshots page: every archive, with restore and delete on each row."""

from api import api
from fastapi import APIRouter
from web import PageDep

router = APIRouter()


@router.get("/snapshots")
def snapshots(page: PageDep):
  snapshots = api("/snapshots")["snapshots"]
  return page.render(
    "pages/snapshots.html",
    "Snapshots",
    snapshots=snapshots,
    total_bytes=sum(snap.get("bytes") or 0 for snap in snapshots),
  )
