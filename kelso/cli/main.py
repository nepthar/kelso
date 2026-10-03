import argparse
import gc
import logging
import sys

from kelso import VERSION
from kelso.cli import (
  cleanup,
  cmd,
  config,
  cron,
  dev,
  init,
  inspect,
  load,
  logs,
  ps,
  remove,
  repo,
  routes,
  shell,
  snapshot,
  start,
  status,
  stop,
  system,
  update,
  updown,
)
from kelso.lib.config import load_config
from kelso.lib.kelso import KelsoCtx
from kelso.lib.util import refuse_root

logger = logging.getLogger("kelso.cli")

COMMANDS = [
  updown,
  status,
  cleanup,
  ps,
  start,
  stop,
  config,
  cmd,
  load,
  update,
  remove,
  inspect,
  logs,
  shell,
  dev,
  snapshot,
  cron,
  repo,
  routes,
  system,
  init,
]

HELP = """\
Apps
  ps          List loaded apps and their state
  start       Start an app, loading it first if needed
  stop        Stop a running app
  config      View or set an app's config, routes, and volume binds
  cmd         List or run an app's commands
  load        Load or re-load an app, restarting it if running
  update      Snapshot an app and re-load it from its source's new version
  unload      Stop an app and remove its loaded copy, keeping data and config
  rm          Unload a stopped app and delete its temp and logs
              (--temp, --data, --purge: more or less)
  inspect     Show an app's state, ports, routes, volumes, and config
  logs        Show an app's logs
  shell       Open a shell in one of an app's containers

  dev         Run an app bundle in this terminal

This box
  status      Show the host, apps, routes, and storage at a glance
  up          Start all apps in start_order groups
  down        Stop all apps in start_order groups
  cleanup     List what kelso no longer needs; --apply deletes it
              (old snapshots, unused images, orphaned routes; --temp)

Snapshots     kelso snapshot take | list | restore
Cron          kelso cron | cron tick
Repos         kelso repo list | add | update | remove
Routes        kelso route list | add | remove | check | add-provider
System        kelso system doctor | activity | volumes | secret | host-volume
                           service | gen-masterkey | decrypt
Setup         kelso init

Run `kelso COMMAND --help` for details on any command.
"""


class _Narration(logging.Formatter):
  """kelso's own log records as plain lines; warnings and errors say so."""

  def format(self, record: logging.LogRecord) -> str:
    message = record.getMessage()
    if record.levelno >= logging.ERROR:
      return f"Error: {message}"
    if record.levelno >= logging.WARNING:
      return f"Warning: {message}"
    return message


def _configure_logging() -> None:
  """Narration to stderr, leaving stdout to what a command outputs.

  Called per run against the current `sys.stderr`, so a caller that redirects
  the stream still sees it.
  """
  # Everything that is not kelso's own: warnings only, and labelled.
  logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s %(name)s: %(message)s",
    force=True,
  )
  handler = logging.StreamHandler(sys.stderr)
  handler.setFormatter(_Narration())
  kelso = logging.getLogger("kelso")
  for old in [h for h in kelso.handlers if isinstance(h.formatter, _Narration)]:
    kelso.removeHandler(old)
  kelso.addHandler(handler)
  kelso.setLevel(logging.INFO)
  kelso.propagate = False


def build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(
    prog="kelso",
    usage="kelso COMMAND ...",
    description="Kelso Server runs apps on hardware you own.",
    epilog=HELP,
    formatter_class=argparse.RawDescriptionHelpFormatter,
  )
  parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
  parser.set_defaults(func=lambda args, ctx: parser.print_help())
  # SUPPRESS keeps argparse's flat command list out of --help; HELP replaces it.
  subparsers = parser.add_subparsers(
    dest="command", prog="kelso", help=argparse.SUPPRESS
  )

  for command in COMMANDS:
    command.register(subparsers)

  return parser


def _dispatch(args: argparse.Namespace) -> None:
  try:
    # Before anything else, and before `init` in particular: the first command
    # is the one that would create the kelso root with the wrong owner.
    refuse_root("kelso")
    if args.command is None or args.command == "init":
      args.func(args, None)
    else:
      cfg = load_config()
      if not cfg:
        raise ValueError("Kelso is not initialized; run `kelso init` first")
      args.func(args, KelsoCtx(cfg))
  except KeyboardInterrupt:
    raise SystemExit(130) from None
  except (RuntimeError, ValueError) as error:
    logger.error("%s", error)
    raise SystemExit(1) from error


def run(argv: list[str] | None = None) -> int:
  """Execute one kelso command and return its exit code."""
  _configure_logging()
  parser = build_parser()
  try:
    _dispatch(parser.parse_args(argv))
  except SystemExit as exit_:
    if exit_.code is None:
      return 0
    return exit_.code if isinstance(exit_.code, int) else 1
  return 0


def main() -> None:
  # Not at import time: `run` is called in-process by the tests, which do
  # need a collector.
  gc.disable()
  raise SystemExit(run())
