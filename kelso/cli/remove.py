"""The removal verbs -- unload, rm, and rm --purge -- which differ only in how
much they take: an app's loaded copy under `var/run/`, its data under the volume
roots, and its config, snapshots and routes.
"""

import argparse

from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle import (
  PURGE,
  RM,
  UNLOAD,
  RemovalMode,
  RemovalPlan,
  removal_plan,
  rm,
)
from kelso.lib.util import Conn


def register(subparsers) -> None:
  unload = subparsers.add_parser(
    "unload",
    help="Stop an app and remove its loaded copy, keeping its data and config",
  )
  unload.add_argument("app_id", help="App ID to unload")
  _add_yes(unload)
  unload.set_defaults(func=_run(UNLOAD))

  remove = subparsers.add_parser(
    "rm",
    help="Unload an app and delete its data, keeping its config unless --purge",
  )
  remove.add_argument("app_id", help="App ID to remove")
  remove.add_argument(
    "--purge",
    action="store_true",
    help="Also delete its config, secrets, snapshots, and route allocations",
  )
  _add_yes(remove)
  remove.set_defaults(func=_run(RM))


def _add_yes(parser: argparse.ArgumentParser) -> None:
  parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation")


def _run(mode: RemovalMode):
  def run(args: argparse.Namespace, ctx: KelsoCtx, conn: Conn) -> None:
    resolved = PURGE if getattr(args, "purge", False) else mode
    state = ctx.run_state(args.app_id)
    plan = removal_plan(state.app_id, ctx, mode=resolved)

    if not args.yes and not _confirmed(plan, conn):
      conn.out("Nothing removed.")
      return

    with ctx.locked(f"{plan.mode} {plan.app_id}", plan.app_id):
      rm(plan, ctx)
    conn.out(_done(plan))

  return run


def _done(plan: RemovalPlan) -> str:
  app = plan.app_id
  if plan.mode == UNLOAD:
    return (
      f"Unloaded {app}. Configuration and volume data were kept.\n"
      f"Load it again with `kelso load {app}`."
    )
  if plan.mode == RM:
    return (
      f"Removed {app}. Its configuration and address are unchanged; "
      f"`kelso load {app}` starts it fresh with them."
    )
  return f"Purged {app}"


def _confirmed(plan: RemovalPlan, conn: Conn) -> bool:
  """Say what the operator is deciding, and nothing else."""
  if plan.mode == UNLOAD:
    conn.out(
      f"Configuration and volume data will be kept. Use `kelso rm {plan.app_id}` "
      f"to delete the data too."
    )
  else:
    _describe_removal(plan, conn)
  try:
    answer = conn.read(f"{_ASKED[plan.mode]} {plan.app_id}? [y/N] ")
  except EOFError:
    return False
  return answer.strip().lower() in ("y", "yes")


# How each removal asks.
_ASKED = {UNLOAD: "Unload", RM: "Remove", PURGE: "Purge"}


def _describe_removal(plan: RemovalPlan, conn: Conn) -> None:
  """Describe rm or rm --purge, naming the data it destroys."""
  conn.out(f"This deletes {plan.app_id}'s data volumes:")
  for line in _volume_lines(plan):
    conn.out(f"  {line}")
  if plan.purges:
    conn.out("along with its configuration, secrets, and route allocations.")
    if plan.snapshot_path is not None:
      conn.out(f"Its snapshots under {plan.snapshot_path} are deleted too.")
  else:
    conn.out(
      f"Its configuration and address are kept. Use `kelso rm --purge "
      f"{plan.app_id}` to delete those too."
    )
  for path in plan.host_paths:
    conn.out(f"The host volume at {path} is left alone.")
  if not plan.purges:
    conn.out("If you want this data back, take a snapshot first.")


def _volume_lines(plan: RemovalPlan) -> list[str]:
  """One line per volume, which is how the manifest names them."""
  lines: list[str] = []
  for path in plan.volume_paths:
    volumes = sorted(p for p in path.iterdir() if p.is_dir()) if path.is_dir() else []
    lines += [str(volume) for volume in volumes] or [str(path)]
  return lines or ["nothing -- this app has no data on disk"]
