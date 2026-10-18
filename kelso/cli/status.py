"""`kelso status`: the dashboard as text -- host, apps, routes, locations, storage."""

import argparse
import os
import socket
import time
from datetime import timedelta
from pathlib import Path

import psutil
from tabulate import tabulate

from kelso import VERSION
from kelso.lib import alerts, views
from kelso.lib.doctor import diagnose
from kelso.lib.kelso import KelsoCtx
from kelso.lib.metric import KELSO_DIRS, cpu_used_ratio
from kelso.lib.receipt import published_urls
from kelso.lib.run_layout import load_run_data
from kelso.lib.util import fmt_size


def register(subparsers) -> None:
  parser = subparsers.add_parser(
    "status", help="Show the host, apps, routes, and storage at a glance"
  )
  parser.set_defaults(func=run)


def run(_args: argparse.Namespace, ctx: KelsoCtx) -> None:
  with ctx.kelso_lock("status"):
    sections = [
      _host(),
      _apps(ctx),
      _routes(ctx),
      _locations(ctx),
      _volumes(ctx),
      _backups(ctx),
      _alerts(ctx),
      _doctor(ctx),
    ]
  print("\n\n".join(sections))


def _host() -> str:
  uptime = timedelta(seconds=int(time.time() - psutil.boot_time()))
  mem = psutil.virtual_memory()
  swap = psutil.swap_memory()
  load = " ".join(f"{n:.2f}" for n in os.getloadavg())
  return "\n".join(
    [
      f"{socket.gethostname()} · kelso {VERSION} · up {uptime}",
      f"CPU     {cpu_used_ratio():.0%} of {psutil.cpu_count()} cores, load {load}",
      f"Memory  {mem.percent:.0f}% of {fmt_size(mem.total)}",
      f"Swap    {swap.percent:.0f}% of {fmt_size(swap.total)}",
    ]
  )


def _apps(ctx: KelsoCtx) -> str:
  rows = []
  for app in views.apps_view(ctx):
    running, total = app["containers"]["running"], app["containers"]["total"]
    config = app["configured"] or "-"
    if app["changes_pending"] and running:
      config += ", restart to apply"
    rows.append(
      (
        app["app_id"],
        f"{app['status']} {running}/{total}" if total else "-",
        config,
        app["last_action"] or "-",
      )
    )
  if not rows:
    return "Apps\nNone loaded."
  table = tabulate(rows, headers=["APP", "STATUS", "CONFIG", "LAST ACTION"])
  return f"Apps\n{table}"


def _routes(ctx: KelsoCtx) -> str:
  lines = []
  for observation in ctx.observations():
    try:
      spec = ctx.loaded_spec(observation.app_id) if observation.loaded else None
    except ValueError:
      continue
    if spec is None:
      continue
    for url in published_urls(spec, load_run_data(spec, ctx), ctx):
      lines.append(f"{observation.app_id}: {url}")
  return "Routes\n" + ("\n".join(lines) or "None published.")


def _locations(ctx: KelsoCtx) -> str:
  """Where each volume root, kelso directory and host volume really is."""
  root = ctx.config.kelso_root
  places = [
    *(
      (f"volumes/{kind}", path)
      for kind, path in sorted(ctx.config.volume_roots.items())
    ),
    *((entry.name, entry.root(ctx)) for entry in KELSO_DIRS),
  ]
  rows = [("$kelso", str(root))]
  rows += [(name, _where(path, root)) for name, path in places]
  # Host volumes are the operator's own folders: kelso never creates them.
  for tag, volume in sorted(ctx.config.host_volumes.items()):
    missing = "" if volume.path.is_dir() else " (missing)"
    rows.append((f"{tag} (host)", f"{_short(volume.path, root)}{missing}"))
  return f"Locations\n{tabulate(rows, headers=['FOLDER', 'PATH'])}"


def _where(path: Path, root: Path) -> str:
  """A link's target, or where the folder is, under `$kelso` when it can be."""
  if path.is_symlink():
    target = path.readlink()
    if not target.is_absolute():
      target = path.parent / target
    shown = f"→ {_short(target, root)}"
    return shown if path.exists() else f"{shown} (missing)"
  if not path.exists():
    return f"{_short(path, root)} (not created yet)"
  return _short(path, root)


def _short(path: Path, root: Path) -> str:
  """`path`, written from `$kelso` when it is under the kelso root."""
  if path.is_relative_to(root):
    return str(Path("$kelso") / path.relative_to(root))
  return str(path)


def _volumes(ctx: KelsoCtx) -> str:
  rows = []
  roots = views.volume_roots_view(ctx)
  for root in roots:
    if root["used"] is None:
      rows.append((root["kind"], "-", "-", "path is missing"))
      continue
    total = root["used"] + root["available"]
    free = root["available"]
    rows.append(
      (
        root["kind"],
        root["device"],
        "-" if root["bytes"] is None else fmt_size(root["bytes"]),
        f"{fmt_size(free)} ({free / total:.0%})",
      )
    )
  headers = ["KIND", "DEVICE", "SIZE", "FREE"]
  table = f"Volumes\n{tabulate(rows, headers=headers)}"
  if any(root["used"] is not None and root["bytes"] is None for root in roots):
    table += "\nNo sizes: kelsod is not recording them; see `kelso system doctor`."
  return table


def _backups(ctx: KelsoCtx) -> str:
  last = ctx.kelso_db.last_backup_run()
  if last is None:
    return "Backups    none yet; `kelso backup run` takes one"
  outcome = f"{len(last['failed'])} failed" if last["failed"] else "ok"
  return f"Backups    last {last['time']} ({last['reason']}), {outcome}"


def _alerts(ctx: KelsoCtx) -> str:
  count = len(alerts.alerts(ctx))
  if not count:
    return "Alerts     none new"
  return f"Alerts     {count} new; see `kelso system alerts`"


def _doctor(ctx: KelsoCtx) -> str:
  prognosis = diagnose(ctx)
  found = f"{len(prognosis.problems)} problems, {len(prognosis.warnings)} warnings"
  if prognosis.problems or prognosis.warnings:
    found += "; see `kelso system doctor`"
  return f"Doctor     {found}"
