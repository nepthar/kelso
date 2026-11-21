import argparse

from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle import updown


def register(subparsers) -> None:
  up = subparsers.add_parser(
    "up", help="Start apps by start_group, 1 to N (default 9); stops nothing"
  )
  _group(up, 9)
  up.add_argument(
    "--timeout",
    type=float,
    default=updown.DEFAULT_WAIT,
    metavar="SECONDS",
    help=f"How long to wait for a group to be ready (default: {updown.DEFAULT_WAIT})",
  )
  up.set_defaults(func=run_up, activity="up")

  down = subparsers.add_parser(
    "down", help="Stop apps by start_group, 9 down to N+1 (default 0); starts nothing"
  )
  _group(down, 0)
  down.set_defaults(func=run_down, activity="down")


def _group(parser: argparse.ArgumentParser, default: int) -> None:
  parser.add_argument(
    "group",
    type=int,
    choices=range(10),
    nargs="?",
    default=default,
    metavar="N",
    help=f"start_group, 0 to 9 (default: {default})",
  )


def run_up(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  if updown.up(ctx, args.group, wait=args.timeout):
    raise SystemExit(1)


def run_down(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  if updown.down(ctx, args.group):
    raise SystemExit(1)
