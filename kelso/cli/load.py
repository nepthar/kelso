import argparse
import logging
from pathlib import Path

from kelso.lib.apps import AppID
from kelso.lib.bundle import load_bundle
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle import load_target, reload_app
from kelso.lib.receipt import capability_receipt, route_receipt_lines
from kelso.lib.run_layout import AppRunData
from kelso.lib.spec import ComposeWarning

logger = logging.getLogger("kelso.cli")


def register(subparsers) -> None:
  parser = subparsers.add_parser(
    "load",
    help="Load or re-load an app from its bundle, restarting it if running",
  )
  parser.add_argument(
    "app",
    metavar="APP",
    help="App ID (e.g. io.example.myapp or myapp) or path to an app",
  )
  parser.add_argument(
    "--force",
    action="store_true",
    help="Load even though this id was last loaded from somewhere else",
  )
  parser.add_argument(
    "-y",
    "--yes",
    action="store_true",
    help="Skip the confirmation for compose keys kelso does not model",
  )
  parser.set_defaults(func=run, activity="load")


def run(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  target = load_target(ctx, args.app, force=args.force)
  app = target.app_id
  bundle = target.bundle or ctx.bundle_path(app)
  if not args.yes and not confirm_compose_warnings(app, bundle):
    print("Nothing loaded.")
    return
  with ctx.locked(f"load {app}", app):
    result = reload_app(app, bundle, ctx, bound=target.bound_to)
  load = result.load
  for name in load.dropped_volumes:
    logger.warning(
      f"volume {name} is no longer declared in the manifest; "
      f"its link is gone but its data was left in place"
    )
  print(f"Loaded {app} at {ctx.run_path(app)}")
  if result.was_running:
    print(f"Restarted {app}")
    print(capability_receipt(load.spec, load.run_data, ctx, compact=True))
    # Routes were re-published just now; a stopped app has none to show.
    for line in route_receipt_lines(load.spec, load.run_data, ctx):
      print(line)
  else:
    print(start_hint(app, load.run_data))


def start_hint(app: AppID, run_data: AppRunData) -> str:
  """How to start a freshly loaded app, naming the config it still needs."""
  unset = [
    name for name, value in run_data.config_values.items() if value.value is None
  ]
  if not unset:
    return f"Start it with: kelso start {app}"
  flags = " ".join(f"--set {name}=<{name}>" for name in unset)
  return f"Set {', '.join(unset)}, then start it with: kelso start {app} {flags}"


def _compose_warnings(bundle: Path) -> tuple[ComposeWarning, ...]:
  """This bundle's off-allowlist compose keys, or none if it does not parse."""
  try:
    return load_bundle(bundle).app_spec().compose_warnings
  except (ValueError, RuntimeError, OSError):
    return ()


def confirm_compose_warnings(app: str, bundle: Path) -> bool:
  """Ask only when the manifest passes something through unmodelled."""
  warnings = _compose_warnings(bundle)
  if not warnings:
    return True

  for warning in warnings:
    print(f"Warning: {warning.message()}:")
    for line in warning.option_lines():
      print(f"  {line}")

  try:
    answer = input(f"Load {app} anyway? [y/N] ")
  except EOFError:
    return False
  return answer.strip().lower() in ("y", "yes")
