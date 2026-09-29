import argparse

from kelso.lib.docker import docker_run_command
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle.console import ConsoleRecord, console_command


def register(subparsers) -> None:
  parser = subparsers.add_parser(
    "shell", help="Open a shell in one of an app's running containers"
  )
  parser.add_argument("app_id", help="App ID")
  parser.add_argument(
    "unit", nargs="?", default="main", help="Run unit to open it in (default: main)"
  )
  # No lock: a session can last hours and must not shut out stop or reload.
  parser.set_defaults(func=run)


def run(args: argparse.Namespace, ctx: KelsoCtx, conn) -> None:
  cmd = console_command(args.app_id, args.unit, ctx)
  record = ConsoleRecord(ctx, cmd, "cli")
  code = None
  try:
    code = docker_run_command(
      cmd.docker_args, cwd=cmd.cwd, json_output=False, check=False, env=cmd.env
    ).returncode
  finally:
    ending = "disconnected" if code is None else f"exited {code}"
    record.close(ending, ok=code == 0)
  if code:
    raise SystemExit(code)
