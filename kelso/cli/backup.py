import argparse

from tabulate import tabulate

from kelso.lib import backup as backups_lib
from kelso.lib.apps import AppID
from kelso.lib.backup import MANUAL, Backup
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle import find_backup
from kelso.lib.util import fmt_size

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
    help="Back up apps and kelso now, list backups, and show where they go",
  )
  parser.set_defaults(func=run_status)
  sub = parser.add_subparsers(dest="backup_command")

  run = sub.add_parser("run", help="Back up now: the apps named, or everything")
  run.add_argument("apps", nargs="*", metavar="APP", help="Apps to back up (all)")
  run.set_defaults(func=run_run, activity="backup")

  listing = sub.add_parser("list", help="List backups, of one app or all")
  listing.add_argument("app", nargs="?", metavar="APP")
  listing.set_defaults(func=run_list)

  delete = sub.add_parser("delete", help="Delete one of an app's backups")
  delete.add_argument("app", metavar="APP")
  delete.add_argument("backup", metavar="BACKUP")
  delete.set_defaults(func=run_delete, activity="delete-backup")


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
    (b.id, str(b.app), b.version or "-", b.reason, b.time, fmt_size(b.size))
    for b in reversed(found)
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
      headers=["BACKUP", "APP", "VERSION", "REASON", "TAKEN", "SIZE"],
      tablefmt="simple",
      disable_numparse=True,
    )
  )


def run_delete(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  restic = backups_lib.repository(ctx)
  found = find_backup(AppID(args.app), args.backup, restic)
  backups_lib.delete(restic, found)
  print(f"Deleted backup {found.id} of {found.app}")
