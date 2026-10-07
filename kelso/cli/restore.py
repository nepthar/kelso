import argparse
from pathlib import Path

from tabulate import tabulate

from kelso.lib import backup as backups_lib
from kelso.lib.apps import AppID
from kelso.lib.backup import PRE_RESTORE
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle import RestorePlan, restore, restore_plan
from kelso.lib.lifecycle.recover import held_apps, recorded_layout, recover


def register(subparsers) -> None:
  parser = subparsers.add_parser(
    "restore",
    help="Restore one app from a backup, or all of kelso from a backups directory",
    description="`kelso restore <app> <backup>` puts one app back as a backup "
    "holds it. `kelso restore <path>` restores kelso's state and every app from "
    "the backups at <path>, onto a kelso that holds no app yet: the second step "
    "after `kelso init --with-phrase`.",
  )
  parser.add_argument(
    "target", metavar="APP|PATH", help="An app id, or a backups directory"
  )
  parser.add_argument(
    "backup", nargs="?", metavar="BACKUP", help="With an app: the backup's id"
  )
  parser.add_argument("-y", "--yes", action="store_true", help="Do not ask first")
  parser.add_argument(
    "--no-backup",
    action="store_true",
    help="With an app: do not back up what is there now before overwriting it",
  )
  parser.set_defaults(func=run, activity="restore")


def run(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  if args.backup is None:
    _restore_everything(Path(args.target).expanduser(), ctx, yes=args.yes)
  else:
    _restore_app(AppID(args.target), args.backup, args, ctx)


def _restore_everything(path: Path, ctx: KelsoCtx, *, yes: bool) -> None:
  if not path.is_dir():
    raise ValueError(
      f"{path} is not a directory. Restore one app with "
      f"`kelso restore <app> <backup>`, or all of kelso with "
      f"`kelso restore <backups directory>`."
    )
  # Before anything is restored, which is when re-linking a volume root is
  # still free. A kelso that holds apps is refused by `recover` itself.
  layout = None if held_apps(ctx) else recorded_layout(ctx, path)
  if layout is not None:
    _show_layout(layout, ctx)
    if layout.differs(ctx) and not yes and not _go_ahead():
      print("Nothing restored. Re-link what should move, then restore again.")
      return
  result = recover(ctx, path)
  print(f"Restored kelso {result.kelso_id}'s configuration and state")
  for app in result.restored:
    print(f"Restored {app}")
  if result.linked:
    print(f"backups/ now points at {path.resolve()}; new backups go there too.")
  else:
    print(
      f"backups/ was left as it is, so new backups do not go to {path}. Link it "
      f"there if they should."
    )
  print(
    "\nApps are stopped. Check config.toml's host volumes and kelso_address for "
    "this machine (`kelso system doctor` reports paths that are not there), "
    "then start them with `kelso up`."
  )
  if result.failed:
    raise ValueError(
      f"{len(result.failed)} app(s) could not be restored:\n"
      + "\n".join(f"  {failure}" for failure in result.failed)
    )


def _show_layout(layout, ctx: KelsoCtx) -> None:
  print(f"When the backup was taken ({layout.taken}), at {layout.kelso_root}:")
  rows = [
    (
      change.name,
      change.then or "(a directory in the root)",
      "same here"
      if change.then == change.now
      else (change.now or "(a directory here)"),
    )
    for change in layout.compared(ctx)
  ]
  print(tabulate(rows, headers=["PATH", "POINTED AT", "HERE"], tablefmt="simple"))
  print("")


def _go_ahead() -> bool:
  try:
    answer = input("A volume root here points somewhere else. Restore anyway? [y/N] ")
  except EOFError:
    return False
  return answer.strip().lower() in ("y", "yes")


def _restore_app(
  app: AppID, backup_id: str, args: argparse.Namespace, ctx: KelsoCtx
) -> None:
  # Not resolved against the catalog: an app removed since can come back.
  restic = backups_lib.repository(ctx)
  plan = restore_plan(app, backup_id, ctx, restic)
  backup_first = not args.no_backup
  if not args.yes and not _confirmed(plan, backup_first):
    print("Nothing restored.")
    return
  with ctx.locked(f"restore {app}", app):
    restore(plan, ctx, restic, backup_first=backup_first)
  print(f"Restored {app} from backup {plan.backup.id}")


def _confirmed(plan: RestorePlan, backup_first: bool) -> bool:
  app = plan.app_id
  bulk = ", bulk volumes" if "bulk" in plan.backup.snapshots else ""
  print(f"Restoring {app} from backup {plan.backup.id} ({plan.backup.time}) replaces:")
  print(f"  its data volumes{bulk}")
  print(f"  {plan.config_path} (config, secrets)")
  print(f"  {plan.run_path} (loaded bundle, compose)")
  print("  its route and host-port allocations")

  exists = plan.run_path.exists() or plan.config_path.exists()
  if exists and not backup_first:
    print(f"What {app} holds right now is destroyed, not backed up (--no-backup).")
  if backup_first and exists and plan.is_latest_pre_restore:
    print(f"This is the newest {PRE_RESTORE} backup, so no new one is taken first.")
  elif backup_first and exists:
    print(f"What {app} holds now is backed up first.")
  try:
    answer = input(f"Restore {app} to {plan.backup.id}? [y/N] ")
  except EOFError:
    return False
  return answer.strip().lower() in ("y", "yes")
