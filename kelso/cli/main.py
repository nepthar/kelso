import argparse
import gc
import logging
import sys
from pathlib import Path

from kelso import VERSION
from kelso.cli import (
  activity,
  agent_help,
  backup,
  cleanup,
  cmd,
  config,
  cron,
  deploy,
  dev,
  doctor,
  init,
  load,
  logs,
  net,
  ps,
  remove,
  repo,
  restart,
  restore,
  root,
  routes,
  secret,
  shell,
  start,
  status,
  stop,
  system,
  update,
  updown,
)
from kelso.cli.confirm import Refused
from kelso.lib.activity import BY_CLI, Activity
from kelso.lib.bundle import app_id_from_path, is_pathlike
from kelso.lib.config import load_config
from kelso.lib.kelso import KelsoCtx
from kelso.lib.util import refuse_root

logger = logging.getLogger("kelso.cli")

COMMANDS = [
  updown,
  status,
  doctor,
  activity,
  cleanup,
  ps,
  start,
  stop,
  restart,
  config,
  cmd,
  load,
  update,
  restore,
  remove,
  logs,
  shell,
  dev,
  deploy,
  net,
  root,
  backup,
  cron,
  repo,
  routes,
  secret,
  system,
  init,
  agent_help,
]

HELP = """\
Develop
  dev PATH          Run an app bundle in this terminal, as a host would run it
  deploy PATH HOST  Copy an app bundle to a host, and start or update it there

Apps
  ps                List loaded apps and their state
  status [APP]      The host at a glance; with APP, what it resolves to, in full
  start APP         Start an app, loading it first if needed
  stop APP          Stop a running app
  restart APP       Stop an app and start it again, applying config changes
  update APP        Back up an app and re-load it from its repo's new version
  rm APP            Unload a stopped app and delete its temp and logs
                    (--reset: every volume too; --purge: its config as well)
  config APP        View or set an app's config, routes, volume binds, network
  logs APP          Show an app's logs
  shell APP         Open a shell in one of an app's containers
  cmd APP           List or run an app's commands
  repo              Where apps come from: list | add | update | remove

Host
  up [N]            Start apps by start_group, 1 to N (default 9); stops nothing
  down [N]          Stop apps by start_group above N (default 0); starts nothing
  doctor            What is wrong on this host, with the fix for each
  activity          What kelso ran, and what each run printed
  backup            Back up now, or list backups: run | list | delete
  restore           Put an app back from a backup, or a whole host

A new machine starts with `kelso init`.

`kelso help advanced` lists the other commands; `kelso COMMAND --help` details one.
Agents: run `kelso agent-help` first. It is written for you.
"""

HELP_ADVANCED = """\
Advanced
  init              Set kelso up on this machine; --with-phrase to restore one
  load APP|PATH     Load or re-load an app, restarting it if running
  unload APP        Stop an app and remove its loaded copy; data and config stay
  net APP           What an app behind kproxy reaches; allow or deny domains
  route             Route providers and manual routes:
                    list | add | remove | check | add-provider
  secret            Host secrets and the recovery phrase:
                    set | unset | reveal | rekey | recovery-phrase
  system            Set up and tear down this host:
                    service | host-volume | volumes | purge
  cleanup [APP]     What kelso no longer needs; --apply deletes it
                    (--temp: stopped apps' temp and logs too)
  cron              List upcoming cron jobs; cron tick runs the due ones
  root              Print the kelso root path

`kelso help` lists the everyday commands; `kelso COMMAND --help` details one.
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

  help_parser = subparsers.add_parser("help", help="Show the commands")
  help_parser.add_argument("topic", nargs="?", choices=["advanced"])
  help_parser.set_defaults(
    func=lambda args, ctx: (
      print(HELP_ADVANCED, end="") if args.topic else parser.print_help()
    )
  )

  return parser


def _activity(args: argparse.Namespace) -> str | None:
  """The verb a command that changes something records itself under.

  A command sets `activity` with `set_defaults`: a verb, or a function of its
  arguments for one that only sometimes writes. Unset, nothing is recorded.
  """
  activity = getattr(args, "activity", None)
  return activity(args) if callable(activity) else activity


def _app_named(args: argparse.Namespace, ctx: KelsoCtx) -> str | None:
  """The app a command acts on, as the activity log files it."""
  raw = getattr(args, "app", None) or getattr(args, "app_id", None)
  if not raw:
    return None
  if is_pathlike(raw):
    try:
      return app_id_from_path(Path(raw).expanduser())
    except ValueError:
      return None
  name = raw.partition("@")[0]
  try:
    return ctx.resolve_app_id(name)
  except (ValueError, RuntimeError):
    return name


def _dispatch(args: argparse.Namespace) -> None:
  try:
    # Before anything else, and before `init` in particular: the first command
    # is the one that would create the kelso root with the wrong owner.
    refuse_root("kelso")
    if args.command in (None, "help", "init", "agent-help"):
      args.func(args, None)
    else:
      cfg = load_config()
      if not cfg:
        raise ValueError("Kelso is not initialized; run `kelso init` first")
      ctx = KelsoCtx(cfg)
      verb = _activity(args)
      if verb is None:
        args.func(args, ctx)
      else:
        # The CLI's own handler already shows kelso's log lines. On a terminal,
        # docker writes straight to it, keeping its live progress display;
        # anywhere else its output is captured and echoed line by line.
        with Activity(
          ctx,
          verb,
          app=_app_named(args, ctx),
          echo=sys.stderr,
          echo_logs=False,
          capture_docker=not sys.stderr.isatty(),
          started_by=BY_CLI,
        ):
          args.func(args, ctx)
  except KeyboardInterrupt:
    raise SystemExit(130) from None
  except Refused as error:
    logger.error("%s", error)
    raise SystemExit(2) from error
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
