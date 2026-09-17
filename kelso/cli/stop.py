import argparse

from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle import stop


def register(subparsers) -> None:
  parser = subparsers.add_parser("stop", help="Stop a running app")
  parser.add_argument("app_id", help="App ID to stop")
  parser.set_defaults(func=run)


def run(args: argparse.Namespace, ctx: KelsoCtx, conn) -> None:
  state = ctx.run_state(args.app_id)
  with ctx.locked(f"stop {state.app_id}", state.app_id):
    stop(state.app_id, ctx)
