import argparse

from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle import stop


def register(subparsers) -> None:
  parser = subparsers.add_parser("stop", help="Stop a running app")
  parser.add_argument("app_id", help="App ID to stop")
  parser.set_defaults(func=run, activity="stop")


def run(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  app = ctx.resolve_app_id(args.app_id, prefer_loaded=True)
  stop(app, ctx)
