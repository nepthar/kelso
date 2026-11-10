"""The Host window, after Task Manager: every app, and how busy the host is."""

from api import api, where
from fastapi import APIRouter
from web import PageDep, fmt_size

router = APIRouter()

# A history graph's box, in SVG units: time across, 0-100% up. CSS sizes it
# and draws the grid behind it.
GRAPH_W, GRAPH_H = 600, 100


@router.get("/host")
def dashboard(page: PageDep):
  host = api("/host")
  body = api("/metrics?prefix=host_&hours=1")
  metrics = body.get("metrics") or {}

  def resource(name, gauge, total, notes="", title=""):
    points = metrics.get(gauge) or []
    return {
      "name": name,
      "title": title,
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
        f"Disk {disk['mountpoint']}",
        disk["gauge"],
        fmt_size(disk["total_bytes"]),
        ", ".join(disk["holds"]),
        title=disk["device"],
      )
      for disk in host.get("disks", [])
    ),
  ]
  return page.render(
    "pages/dashboard.html",
    "Host",
    where=where(),
    apps=api("/apps").get("apps", []),
    resources=resources,
    cpu=resources[0]["current"],
    ram=resources[1]["current"],
    graph_box=f"0 0 {GRAPH_W} {GRAPH_H}",
  )


def _line(points, since, until):
  """SVG polyline points for ratios over [since, until]: time across, 0-100% up."""
  span = max(until - since, 1)
  return " ".join(
    f"{(p['t'] - since) / span * GRAPH_W:.1f},{(1 - min(max(p['v'], 0), 1)) * GRAPH_H:.1f}"
    for p in points
  )
