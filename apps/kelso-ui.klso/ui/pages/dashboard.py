"""The dashboard: host CPU and memory, and every installed app."""

from api import api, where
from fastapi import APIRouter
from web import PageDep

router = APIRouter()


@router.get("/")
def dashboard(page: PageDep):
  body = api("/metrics?prefix=host_&hours=1")
  metrics = body.get("metrics") or {}
  return page.render(
    "pages/dashboard.html",
    "Dashboard",
    where=where(),
    apps=api("/apps").get("apps", []),
    metrics={
      "since": body["since"],
      "until": body["until"],
      "cpu": metrics.get("host_cpu_used_ratio") or [],
      "mem": metrics.get("host_mem_used_ratio") or [],
    },
  )
