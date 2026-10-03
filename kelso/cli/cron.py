import argparse
import sys
from datetime import UTC, datetime

from tabulate import tabulate

from kelso.lib.activity import BY_CLI
from kelso.lib.kelso import KelsoCtx, lock_holder_hint
from kelso.lib.lifecycle.cron import cron_lock_path, cron_runs, tick

# Each unit takes over once a time is more than two of it.
_UNITS = (
  ("second", 1),
  ("minute", 60),
  ("hour", 60 * 60),
  ("day", 24 * 60 * 60),
  ("month", 30 * 24 * 60 * 60),
  ("year", 365 * 24 * 60 * 60),
)


def register(subparsers) -> None:
  parser = subparsers.add_parser(
    "cron", help="List upcoming cron jobs; tick runs due ones"
  )
  parser.set_defaults(func=run_list)
  sub = parser.add_subparsers(dest="cron_command")
  tick_parser = sub.add_parser("tick", help="Run every cron job that is due now")
  tick_parser.set_defaults(func=run_tick)


def run_list(_args: argparse.Namespace, ctx: KelsoCtx) -> None:
  with ctx.kelso_lock("cron"):
    runs = cron_runs(ctx)
  if not runs:
    print("No cron jobs.")
    return
  now = datetime.now(UTC)
  rows = []
  for run in runs:
    when = in_words((run.next_at - now).total_seconds())
    rows.append(
      (run.app_id, run.name, when if run.runnable else f"{when} (app stopped)")
    )
  print(tabulate(rows, tablefmt="plain"))


def run_tick(_args: argparse.Namespace, ctx: KelsoCtx) -> None:
  ran = tick(ctx, by="cron tick", started_by=BY_CLI, echo=sys.stderr)
  if ran is None:
    raise ValueError(
      "A cron tick is already running; nothing was started.\n"
      + lock_holder_hint(cron_lock_path(ctx)).rstrip()
    )
  if not ran:
    print("No cron jobs ran.")
  else:
    for run in ran:
      print(f"Ran {run.app_id} {run.name}")


def in_words(seconds: float) -> str:
  """`in 3 hours`, or `due` for a time that has come."""
  if seconds <= 0:
    return "due"
  name, size = _UNITS[0]
  for unit, length in _UNITS[1:]:
    if seconds > 2 * length:
      name, size = unit, length
  count = round(seconds / size)
  return f"in {count} {name}{'' if count == 1 else 's'}"
