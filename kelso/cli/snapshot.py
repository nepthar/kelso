import argparse

from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle import (
  RestorePlan,
  resolve_snapshot_app,
  restore,
  restore_plan,
  snapshot,
  snapshot_names,
  start,
  stop,
)


def register(subparsers) -> None:
  parser = subparsers.add_parser("snapshot", help="Take, list, and restore snapshots")
  sub = parser.add_subparsers(dest="snapshot_command", required=True)

  take = sub.add_parser(
    "take",
    help="Capture a restore point (config, loaded bundle, and data volumes)",
  )
  take.add_argument("app", metavar="APP", help="App ID to snapshot")
  take.add_argument(
    "--label",
    default="",
    metavar="LABEL",
    help="Optional label appended to the snapshot name",
  )
  take.set_defaults(func=run_take)

  listing = sub.add_parser("list", help="List an app's snapshots, newest first")
  listing.add_argument("app", metavar="APP", help="App ID")
  listing.set_defaults(func=run_list)

  rest = sub.add_parser(
    "restore",
    help="Replace an app's run state and data volumes with a snapshot's",
  )
  rest.add_argument("app", metavar="APP", help="App ID to restore")
  rest.add_argument(
    "snapshot",
    metavar="SNAPSHOT",
    help="Snapshot name, from `kelso snapshot list`",
  )
  rest.add_argument("-y", "--yes", action="store_true", help="Skip confirmation")
  rest.add_argument(
    "--no-snapshot",
    action="store_true",
    help="Do not take a pre-restore snapshot of the current run dir",
  )
  rest.set_defaults(func=run_restore)


def run_take(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  app = ctx.resolve_app(args.app)
  print(f"Snapshotting {app}...")
  by = f"snapshot {app}"
  running = 0
  with ctx.app_lock(app, by):
    with ctx.kelso_lock(by):
      try:
        running = ctx.run_state(app).running_count
      except ValueError:
        running = 0
      if running:
        stop(app, ctx)
    try:
      path = snapshot(app, ctx, label=args.label)
    finally:
      if running:
        with ctx.kelso_lock(by):
          start(app, ctx.config.app_run_path(app), ctx)
  print(f"Snapshot of {app} written to {path}")


def run_list(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  app = resolve_snapshot_app(ctx, args.app)
  names = snapshot_names(app, ctx)
  if not names:
    print(f"No snapshots of {app}. Take one with `kelso snapshot take {app}`")
    return
  for name in reversed(names):
    print(name)


def run_restore(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  app = resolve_snapshot_app(ctx, args.app)
  plan = restore_plan(app, args.snapshot, ctx)
  snapshot_first = not args.no_snapshot
  if not args.yes and not _confirmed(plan, snapshot_first):
    print("Nothing restored.")
    return
  with ctx.locked(f"restore {app}", app):
    restore(plan, ctx, snapshot_first=snapshot_first)
  print(f"Restored {plan.app_id} from {plan.snapshot_path}")


def _confirmed(plan: RestorePlan, snapshot_first: bool) -> bool:
  print(f"Restoring {plan.app_id} from {plan.snapshot_path} overwrites:")
  print(f"  {plan.run_path} (loaded bundle, compose)")
  print(f"  {plan.config_path} (config, secrets)")
  for _, dest in plan.data_volumes:
    print(f"  {dest}")
  print("  its route and host-port allocations")

  if plan.run_path.exists() and not snapshot_first:
    print(
      f"Whatever {plan.app_id} holds right now is destroyed, not set aside "
      f"(--no-snapshot)."
    )
  if snapshot_first and plan.run_path.exists() and not plan.is_latest_pre_restore:
    prompt = (
      f"Snapshot {plan.app_id} first, then restore to {plan.snapshot_path.name}? [y/N] "
    )
  else:
    if snapshot_first and plan.run_path.exists():
      print(
        "This is the newest pre-restore snapshot; no new pre-restore "
        "snapshot will be taken."
      )
    prompt = f"Restore {plan.app_id} to {plan.snapshot_path.name}? [y/N] "
  try:
    answer = input(prompt)
  except EOFError:
    return False
  return answer.strip().lower() in ("y", "yes")
