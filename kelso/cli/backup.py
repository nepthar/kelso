import argparse

from tabulate import tabulate

from kelso.lib import backup as backups_lib
from kelso.lib.apps import AppID
from kelso.lib.backup import MANUAL, PRE_RESTORE, Backup
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle import RestorePlan, restore, restore_plan

FIRST_BACKUP = """\

************************************************************************
  This is kelso's first backup. It is encrypted, and the only way to
  read it -- here after a disk dies, or on another machine -- is your
  twelve-word recovery phrase. Without it, these backups are noise.

  If it is not in your password manager yet, put it there now:

      kelso system recovery-phrase
************************************************************************"""


def register(subparsers) -> None:
  parser = subparsers.add_parser(
    "backup",
    help="Back up apps and kelso, list backups, and restore an app from one",
  )
  parser.set_defaults(func=run_status)
  sub = parser.add_subparsers(dest="backup_command")

  run = sub.add_parser("run", help="Back up now: the apps named, or everything")
  run.add_argument("apps", nargs="*", metavar="APP", help="Apps to back up (all)")
  run.set_defaults(func=run_run, activity="backup")

  listing = sub.add_parser("list", help="List backups, of one app or all")
  listing.add_argument("app", nargs="?", metavar="APP")
  listing.set_defaults(func=run_list)

  rest = sub.add_parser("restore", help="Put an app back as a backup holds it")
  rest.add_argument("app", metavar="APP")
  rest.add_argument("backup", metavar="BACKUP", help="The backup's id, from `list`")
  rest.add_argument("-y", "--yes", action="store_true", help="Do not ask first")
  rest.add_argument(
    "--no-backup",
    action="store_true",
    help="Do not back up what is there now before overwriting it",
  )
  rest.set_defaults(func=run_restore, activity="restore")


def run_status(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  config = ctx.config
  keep = config.backup.keep
  root = config.backups_root
  try:
    where = str(backups_lib.destination(ctx))
    if root.is_symlink():
      where += f" (through {root})"
  except ValueError as e:
    where = f"unavailable: {e}"
  last = ctx.kelso_db.last_backup_run()
  if last is None:
    last_line = "never"
  else:
    outcome = f"{len(last['failed'])} failed" if last["failed"] else "ok"
    last_line = f"{last['time']}, {last['reason']}, {outcome}"
  print(
    tabulate(
      [
        ("Kelso id", ctx.kelso_db.kelso_id()),
        ("Backups to", where),
        ("Schedule", config.backup.schedule),
        (
          "Keep",
          f"{keep.daily} daily, {keep.weekly} weekly, {keep.monthly} monthly, "
          f"{keep.manual} manual",
        ),
        ("Last run", last_line),
      ],
      tablefmt="plain",
    )
  )
  if last and last["failed"]:
    for failure in last["failed"]:
      print(f"  failed: {failure}")


def run_run(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  apps = [ctx.resolve_app(name) for name in args.apps] or None
  result = backups_lib.run_backups(ctx, reason=MANUAL, apps=apps)
  for app in result.backed_up:
    print(f"Backed up {app}")
  if apps is None and not any("state" in f for f in result.failed):
    print("Backed up kelso's own state")
  print(f"Backup {result.run}")
  if result.created:
    print(FIRST_BACKUP)
  if result.failed:
    raise ValueError(
      f"{len(result.failed)} could not be backed up:\n"
      + "\n".join(f"  {failure}" for failure in result.failed)
    )


def _rows(found: list[Backup]) -> list[tuple[str, ...]]:
  return [
    (b.id, str(b.app), b.version or "-", b.reason, b.time) for b in reversed(found)
  ]


def run_list(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  restic = backups_lib.repository(ctx)
  if not restic.exists():
    print("No backups yet. Take one with `kelso backup run`.")
    return
  app = AppID(args.app) if args.app else None
  found = backups_lib.backups(restic, app)
  if not found:
    print(f"No backups of {app}." if app else "No backups yet.")
    return
  print(
    tabulate(
      _rows(found),
      headers=["BACKUP", "APP", "VERSION", "REASON", "TAKEN"],
      tablefmt="simple",
      disable_numparse=True,
    )
  )


def run_restore(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  # Not resolved against the catalog: an app removed since can come back.
  app = AppID(args.app)
  restic = backups_lib.repository(ctx)
  plan = restore_plan(app, args.backup, ctx, restic)
  backup_first = not args.no_backup
  if not args.yes and not _confirmed(plan, backup_first):
    print("Nothing restored.")
    return
  with ctx.locked(f"restore {app}", app):
    restore(plan, ctx, restic, backup_first=backup_first)
  print(f"Restored {app} from backup {plan.backup.id}")


def _confirmed(plan: RestorePlan, backup_first: bool) -> bool:
  app = plan.app_id
  print(f"Restoring {app} from backup {plan.backup.id} ({plan.backup.time}) replaces:")
  print(
    f"  its data volumes{', bulk volumes' if 'bulk' in plan.backup.snapshots else ''}"
  )
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
