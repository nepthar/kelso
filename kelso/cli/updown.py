import argparse

from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle.updown import DEFAULT_WAIT, down, up


def register(subparsers) -> None:
  up_parser = subparsers.add_parser(
    "up",
    help="Start every loaded app in start_order groups 1 to 9",
  )
  up_parser.add_argument(
    "--timeout",
    type=float,
    default=DEFAULT_WAIT,
    metavar="SECONDS",
    help=f"How long to wait for a group to be ready (default: {DEFAULT_WAIT})",
  )
  up_parser.set_defaults(func=run_up, activity="up")

  down_parser = subparsers.add_parser(
    "down",
    help="Stop every running app in start_order groups 9 down to 1",
  )
  down_parser.set_defaults(func=run_down, activity="down")


def run_up(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  if up(ctx, wait=args.timeout):
    raise SystemExit(1)


def run_down(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  if down(ctx):
    raise SystemExit(1)
