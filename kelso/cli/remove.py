"""`kelso unload` and `kelso rm`, which differ only in how much they take: an
app's loaded copy under `var/run/`, its volumes by kind, and its config and
routes.
"""

import argparse

from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle import (
  DATA,
  PURGE,
  RM,
  TEMP,
  UNLOAD,
  RemovalMode,
  RemovalPlan,
  removal_plan,
  rm,
)


def register(subparsers) -> None:
  unload = subparsers.add_parser(
    "unload",
    help="Stop an app and remove its loaded copy, keeping its data and config",
  )
  unload.add_argument("app_id", help="App ID to unload")
  _add_yes(unload)
  unload.set_defaults(func=run, mode=UNLOAD, activity="unload")

  remove = subparsers.add_parser(
    "rm",
    help="Unload a stopped app and delete its temp and logs volumes",
  )
  remove.add_argument("app_id", help="App ID to remove")
  tier = remove.add_mutually_exclusive_group()
  tier.add_argument(
    "--temp",
    dest="mode",
    action="store_const",
    const=TEMP,
    help="Only empty its temp and logs volumes; it stays loaded",
  )
  tier.add_argument(
    "--data",
    dest="mode",
    action="store_const",
    const=DATA,
    help="Empty all its volumes, keeping its config; it stays loaded",
  )
  tier.add_argument(
    "--purge",
    dest="mode",
    action="store_const",
    const=PURGE,
    help="Delete everything: volumes, config, secrets, and routes",
  )
  _add_yes(remove)
  remove.set_defaults(func=run, mode=RM, activity="rm")


def _add_yes(parser: argparse.ArgumentParser) -> None:
  parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation")


def run(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  state = ctx.run_state(args.app_id)
  plan = removal_plan(state.app_id, ctx, mode=args.mode)

  if not args.yes and not _confirmed(plan):
    print("Nothing removed.")
    return

  with ctx.locked(f"{plan.mode} {plan.app_id}", plan.app_id):
    rm(plan, ctx)
  print(_DONE[plan.mode].format(app=plan.app_id))


_DONE: dict[RemovalMode, str] = {
  UNLOAD: (
    "Unloaded {app}. Configuration and volume data were kept.\n"
    "Load it again with `kelso load {app}`."
  ),
  RM: "Removed {app}. Its data and configuration were kept.",
  TEMP: "Emptied {app}'s temp and logs volumes.",
  DATA: "Emptied all of {app}'s volumes. Its configuration and address are kept.",
  PURGE: "Purged {app}.",
}

# How each removal asks.
_ASKED: dict[RemovalMode, str] = {
  UNLOAD: "Unload",
  RM: "Remove",
  TEMP: "Empty temp and logs of",
  DATA: "Empty all volumes of",
  PURGE: "Purge",
}


def _confirmed(plan: RemovalPlan) -> bool:
  """Say what the operator is deciding, and nothing else."""
  if plan.mode == UNLOAD:
    print(
      f"Configuration and volume data will be kept. Use `kelso rm {plan.app_id}` "
      f"to delete its temp and logs too."
    )
  else:
    _describe_removal(plan)
  try:
    answer = input(f"{_ASKED[plan.mode]} {plan.app_id}? [y/N] ")
  except EOFError:
    return False
  return answer.strip().lower() in ("y", "yes")


def _describe_removal(plan: RemovalPlan) -> None:
  """Describe an rm, naming the volumes it deletes or empties."""
  verb = "empties" if plan.empties else "deletes"
  print(f"This {verb} {plan.app_id}'s volumes:")
  for line in _volume_lines(plan):
    print(f"  {line}")
  if plan.purges:
    print("along with its configuration, secrets, and route allocations.")
  else:
    print("Its configuration and address are kept.")
  for path in plan.host_paths:
    print(f"The host volume at {path} is left alone.")
  print("If you want this data back, take a snapshot first.")


def _volume_lines(plan: RemovalPlan) -> list[str]:
  """One line per volume, which is how the manifest names them."""
  lines: list[str] = []
  for path in plan.volume_paths:
    volumes = sorted(p for p in path.iterdir() if p.is_dir()) if path.is_dir() else []
    lines += [str(volume) for volume in volumes] or [str(path)]
  return lines or ["nothing -- this app has no such volumes on disk"]
