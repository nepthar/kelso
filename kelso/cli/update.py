import argparse

from kelso.cli.load import confirm_compose_warnings
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle import update
from kelso.lib.lifecycle.update import update_source
from kelso.lib.lifecycle.updown import DEFAULT_WAIT


def register(subparsers) -> None:
  parser = subparsers.add_parser(
    "update",
    help="Update an app from where it was loaded from, backing it up first",
  )
  parser.add_argument("app", metavar="APP", help="App ID to update")
  parser.add_argument(
    "--no-backup",
    action="store_true",
    help="Update without backing up first, when the backup disk is not there",
  )
  parser.add_argument(
    "--start",
    action="store_true",
    help="Start the app after updating, even if it was stopped",
  )
  parser.add_argument(
    "--timeout",
    type=float,
    default=DEFAULT_WAIT,
    metavar="SECONDS",
    help=f"How long to wait for the app to be healthy (default: {DEFAULT_WAIT})",
  )
  parser.add_argument(
    "--allow-compose",
    action="store_true",
    help=(
      "Go ahead although the manifest sets compose keys kelso does not model; "
      "the operator should review them first"
    ),
  )
  parser.set_defaults(func=run, activity="update")


def run(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  app = ctx.resolve_app_id(args.app, prefer_loaded=True)
  if not args.allow_compose and not confirm_compose_warnings(
    app, update_source(app, ctx)
  ):
    print("Nothing updated.")
    return
  print(
    update(
      app,
      ctx,
      backup=not args.no_backup,
      start_after=args.start,
      wait=args.timeout,
    ).summary(app)
  )
