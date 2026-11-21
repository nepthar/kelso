import argparse
import sys

from tabulate import tabulate

from kelso.cli import service, volumes
from kelso.cli.confirm import Refused
from kelso.cli.kv import parse_kv
from kelso.lib.config_edit import add_host_volume, remove_host_volume, set_host_volume
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle.recover import held_apps, purge


def register(subparsers) -> None:
  parser = subparsers.add_parser(
    "system", help="Set up and tear down this host: service, host volumes, purge"
  )
  sub = parser.add_subparsers(dest="system_command", required=True)

  purge_cmd = sub.add_parser(
    "purge",
    help="Delete every app, its data and config, so kelso can be restored",
    description="Stops and purges every app: its loaded copy, config, secrets, "
    "volumes and routes, as `kelso rm --purge` does for one. Leaves config.toml, "
    "the recovery phrase, repos, backups/ and the volume roots -- links "
    "included -- as they are, so `kelso restore <backups directory>` can follow.",
  )
  purge_cmd.add_argument("-y", "--yes", action="store_true", help="Do not ask first")
  purge_cmd.set_defaults(func=run_purge, activity="purge")

  hv = sub.add_parser(
    "host-volume",
    help="List, add, change or remove [host_volume] entries in config.toml",
  )
  hv.add_argument("--add", metavar="TAG=PATH", help="Declare a new host volume")
  hv.add_argument(
    "--set", dest="set_", metavar="TAG=PATH", help="Replace an existing one"
  )
  hv.add_argument("--rm", metavar="TAG", help="Remove one")
  hv.add_argument(
    "--readonly",
    action="store_true",
    help="With --add/--set: apps may only mount it read-only",
  )
  hv.add_argument(
    "--require-mount",
    action="store_true",
    help="With --add/--set: refuse to start unless the path is a mount point",
  )
  hv.set_defaults(
    func=run_host_volume,
    activity=lambda a: "host-volume" if a.add or a.set_ or a.rm else None,
  )

  for command in (service, volumes):
    command.register(sub)


def run_host_volume(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  chosen = [flag for flag in (args.add, args.set_, args.rm) if flag]
  if len(chosen) > 1:
    raise ValueError("Use one of --add, --set or --rm at a time")

  with ctx.kelso_lock("host-volume"):
    if args.add:
      tag, path = parse_kv(args.add, "--add")
      add_host_volume(
        ctx,
        tag,
        path,
        readonly=args.readonly,
        require_mount=args.require_mount,
      )
      print(f"Added host volume {tag} -> {path}")
      return

    if args.set_:
      tag, path = parse_kv(args.set_, "--set")
      set_host_volume(
        ctx,
        tag,
        path,
        readonly=args.readonly,
        require_mount=args.require_mount,
      )
      print(f"Set host volume {tag} -> {path}")
      return

    if args.rm:
      remove_host_volume(ctx, args.rm)
      print(f"Removed host volume {args.rm}")
      return

    rows = [
      (
        tag,
        str(volume.path),
        "yes" if volume.readonly else "",
        "yes" if volume.require_mount else "",
      )
      for tag, volume in sorted(ctx.config.host_volumes.items())
    ]
    print(
      tabulate(rows, headers=["tag", "path", "readonly", "require_mount"])
      if rows
      else "No host volumes declared."
    )


def run_purge(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  apps = held_apps(ctx)
  if not apps:
    print("No apps to purge; kelso is ready to restore into.")
    return
  if not args.yes:
    if not sys.stdin.isatty():
      raise Refused(
        f"Purge deletes {len(apps)} app(s). There is no terminal to ask on; "
        "pass --yes to go ahead."
      )
    last = ctx.kelso_db.last_backup_run()
    print(f"This permanently deletes {len(apps)} app(s), with their data and config:")
    for app in apps:
      print(f"  {app}")
    print(
      f"The last backup ran {last['time']}." if last else "Kelso has never backed up."
    )
    try:
      answer = input("Type purge to delete them: ")
    except EOFError:
      answer = ""
    if answer.strip() != "purge":
      print("Nothing purged.")
      return
  for app in purge(ctx):
    print(f"Purged {app}")
  print("Kelso holds no apps now; `kelso restore <backups directory>` can follow.")
