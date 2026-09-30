import argparse

from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle.updown import DEFAULT_WAIT, down, up
from kelso.lib.util import Conn


def register(subparsers) -> None:
  up_parser = subparsers.add_parser(
    "up",
    help="Start every installed app, one start_order group at a time",
  )
  up_parser.add_argument(
    "--timeout",
    type=float,
    default=DEFAULT_WAIT,
    metavar="SECONDS",
    help=f"How long to wait for a group to be ready (default: {DEFAULT_WAIT})",
  )
  up_parser.set_defaults(func=run_up)

  down_parser = subparsers.add_parser(
    "down",
    help="Stop every running app, in reverse start_order",
  )
  down_parser.set_defaults(func=run_down)


def run_up(args: argparse.Namespace, ctx: KelsoCtx, conn: Conn) -> None:
  _finish(up(ctx, conn.out, wait=args.timeout), conn)


def run_down(args: argparse.Namespace, ctx: KelsoCtx, conn: Conn) -> None:
  _finish(down(ctx, conn.out), conn)


def _finish(problems: list[str], conn: Conn) -> None:
  if problems:
    for problem in problems:
      conn.err(problem)
    raise SystemExit(1)
