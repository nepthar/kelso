"""The dashboard: what the host has and how busy it is, and every loaded app."""

from api import api, where
from fastapi import APIRouter
from web import PageDep, fmt_size

router = APIRouter()

# The usage line's box, in SVG units; CSS sizes it.
SPARK_W, SPARK_H = 200, 40


@router.get("/")
def dashboard(page: PageDep):
  host = api("/host")
  body = api("/metrics?prefix=host_&hours=1")
  metrics = body.get("metrics") or {}

  def resource(name, gauge, total, notes=""):
    points = metrics.get(gauge) or []
    return {
      "name": name,
      "line": _line(points, body["since"], body["until"]),
      "current": round(points[-1]["v"] * 100) if points else None,
      "total": total,
      "notes": notes,
    }

  resources = [
    resource("Host CPU", "host_cpu_used_ratio", f"{host['cpus']} CPUs"),
    resource("Host memory", "host_mem_used_ratio", fmt_size(host["memory_bytes"])),
    *(
      resource(
        disk["device"],
        disk["gauge"],
        fmt_size(disk["total_bytes"]),
        ", ".join(disk["holds"]),
      )
      for disk in host.get("disks", [])
    ),
  ]
  return page.render(
    "pages/dashboard.html",
    "Dashboard",
    where=where(),
    apps=api("/apps").get("apps", []),
    resources=resources,
    spark_w=SPARK_W,
    spark_box=f"0 0 {SPARK_W} {SPARK_H}",
    # 25%, 50% and 75%; the border stands for 0% and 100%.
    spark_grid=[SPARK_H * q / 4 for q in (1, 2, 3)],
  )


def _line(points, since, until):
  """SVG polyline points for ratios over [since, until]: time across, 0-100% up."""
  span = max(until - since, 1)
  return " ".join(
    f"{(p['t'] - since) / span * SPARK_W:.1f},{(1 - min(max(p['v'], 0), 1)) * SPARK_H:.1f}"
    for p in points
  )
