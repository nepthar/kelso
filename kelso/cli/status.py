"""`kelso status`: the dashboard as text -- host, apps, routes, storage."""

import argparse
import os
import socket
import time
from datetime import timedelta

import psutil
from tabulate import tabulate

from kelso import VERSION
from kelso.lib import views
from kelso.lib.doctor import diagnose
from kelso.lib.kelso import KelsoCtx
from kelso.lib.metric import cpu_used_ratio
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
      _volumes(ctx),
      _backups(ctx),
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
    spec = ctx.loaded_spec(observation.app_id) if observation.loaded else None
    if spec is None:
      continue
    for url in published_urls(spec, load_run_data(spec, ctx), ctx):
      lines.append(f"{observation.app_id}: {url}")
  return "Routes\n" + ("\n".join(lines) or "None published.")


def _volumes(ctx: KelsoCtx) -> str:
  rows = []
  for root in views.volume_roots_view(ctx):
    if root["used"] is None:
      rows.append((root["kind"], "-", "-", "-", "-", "path is missing"))
      continue
    total = root["used"] + root["available"]
    mine = root["bytes"]
    other = max(root["used"] - mine, 0)
    rows.append(
      (
        root["kind"],
        root["device"],
        fmt_size(total),
        f"{fmt_size(mine)} ({mine / total:.1%})",
        f"{other / total:.1%}",
        f"{root['available'] / total:.1%}",
      )
    )
  headers = ["KIND", "DEVICE", "SIZE", "VOLUMES", "OTHER", "FREE"]
  return f"Volumes\n{tabulate(rows, headers=headers)}"


def _backups(ctx: KelsoCtx) -> str:
  last = ctx.kelso_db.last_backup_run()
  if last is None:
    return "Backups    none yet; `kelso backup run` takes one"
  outcome = f"{len(last['failed'])} failed" if last["failed"] else "ok"
  return f"Backups    last {last['time']} ({last['reason']}), {outcome}"


def _doctor(ctx: KelsoCtx) -> str:
  prognosis = diagnose(ctx)
  found = f"{len(prognosis.problems)} problems, {len(prognosis.warnings)} warnings"
  if prognosis.problems or prognosis.warnings:
    found += "; see `kelso script doctor`"
  return f"Doctor     {found}"
