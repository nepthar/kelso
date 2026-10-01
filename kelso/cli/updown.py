import argparse
import logging
from collections.abc import Iterator
from contextlib import contextmanager

from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle.updown import DEFAULT_WAIT, down, logger, up
from kelso.lib.util import Conn


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
  up_parser.set_defaults(func=run_up)

  down_parser = subparsers.add_parser(
    "down",
    help="Stop every running app in start_order groups 9 down to 1",
  )
  down_parser.set_defaults(func=run_down)


def run_up(args: argparse.Namespace, ctx: KelsoCtx, conn: Conn) -> None:
  with _progress_to(conn):
    problems = up(ctx, wait=args.timeout)
  if problems:
    raise SystemExit(1)


def run_down(args: argparse.Namespace, ctx: KelsoCtx, conn: Conn) -> None:
  with _progress_to(conn):
    problems = down(ctx)
  if problems:
    raise SystemExit(1)


class _ConnHandler(logging.Handler):
  def __init__(self, conn: Conn) -> None:
    super().__init__()
    self.conn = conn

  def emit(self, record: logging.LogRecord) -> None:
    message = record.getMessage()
    if record.levelno >= logging.WARNING:
      self.conn.err(message)
    else:
      self.conn.out(message)


@contextmanager
def _progress_to(conn: Conn) -> Iterator[None]:
  """Print what up and down log, plainly, instead of as log records."""
  handler = _ConnHandler(conn)
  level, propagate = logger.level, logger.propagate
  logger.setLevel(logging.INFO)
  logger.propagate = False
  logger.addHandler(handler)
  try:
    yield
  finally:
    logger.removeHandler(handler)
    logger.setLevel(level)
    logger.propagate = propagate
