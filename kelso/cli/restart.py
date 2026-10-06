import argparse

from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle import restart
from kelso.lib.receipt import published_urls


def register(subparsers) -> None:
  parser = subparsers.add_parser(
    "restart",
    help="Stop an app and start it again, applying any config changes",
  )
  parser.add_argument("app_id", help="App ID to restart")
  parser.set_defaults(func=run, activity="restart")


def run(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  app = ctx.resolve_app(args.app_id)
  with ctx.locked(f"restart {app}", app):
    result = restart(app, ctx)
  print(f"Restarted {app}")
  for url in published_urls(result.spec, result.run_data, ctx):
    print(f"  {url}")
