import argparse

from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle.updown import DEFAULT_WAIT, to_group


def register(subparsers) -> None:
  group_parser = subparsers.add_parser(
    "start_group",
    help="Run every loaded app in start_group 1 to N; stop those above N",
  )
  group_parser.add_argument("group", type=int, choices=range(10), metavar="N")
  _timeout(group_parser)
  group_parser.set_defaults(func=run_group, activity="start_group")

  up_parser = subparsers.add_parser("up", help="Same as `kelso start_group 9`")
  _timeout(up_parser)
  up_parser.set_defaults(func=run_group, group=9, activity="up")

  down_parser = subparsers.add_parser("down", help="Same as `kelso start_group 0`")
  down_parser.set_defaults(
    func=run_group, group=0, timeout=DEFAULT_WAIT, activity="down"
  )


def _timeout(parser: argparse.ArgumentParser) -> None:
  parser.add_argument(
    "--timeout",
    type=float,
    default=DEFAULT_WAIT,
    metavar="SECONDS",
    help=f"How long to wait for a group to be ready (default: {DEFAULT_WAIT})",
  )


def run_group(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  if to_group(ctx, args.group, wait=args.timeout):
    raise SystemExit(1)
