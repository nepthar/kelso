import argparse

from kelso.cli.load import confirm_compose_warnings
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle import update
from kelso.lib.lifecycle.update import update_source


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
    "-y",
    "--yes",
    action="store_true",
    help="Skip the confirmation for compose keys kelso does not model",
  )
  parser.set_defaults(func=run, activity="update")


def run(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  app = ctx.resolve_app(args.app)
  if not args.yes and not confirm_compose_warnings(app, update_source(app, ctx)):
    print("Nothing updated.")
    return
  print(update(app, ctx, backup=not args.no_backup).summary(app))
