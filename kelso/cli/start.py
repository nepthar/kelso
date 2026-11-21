import argparse
import logging

from kelso.cli.kv import parse_kv
from kelso.cli.load import confirm_compose_warnings
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle import load_target, start
from kelso.lib.receipt import capability_receipt, location_receipt

logger = logging.getLogger("kelso.cli")


def register(subparsers) -> None:
  parser = subparsers.add_parser(
    "start",
    help="Start an app, loading it first if needed (accepts app id or .klso path)",
  )
  parser.add_argument(
    "app",
    metavar="APP",
    help="App ID (e.g. io.example.myapp or myapp) or path to a .klso directory or .klso.md file",
  )
  parser.add_argument(
    "--set",
    action="append",
    default=[],
    dest="sets",
    metavar="KEY=VALUE",
    help="Set a config value before starting (repeatable)",
  )
  parser.add_argument(
    "--bind",
    action="append",
    default=[],
    dest="binds",
    metavar="VOLUME=HOST_VOLUME",
    help="Bind an app volume to a host_volume tag before starting (repeatable)",
  )
  parser.add_argument(
    "--force",
    action="store_true",
    help="Start even though this id was last loaded from somewhere else",
  )
  parser.add_argument(
    "--allow-compose",
    action="store_true",
    help=(
      "Go ahead although the manifest sets compose keys kelso does not model; "
      "the operator should review them first"
    ),
  )
  parser.set_defaults(func=run, activity="start")


def run(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  target = load_target(ctx, args.app, force=args.force)
  app = target.app_id
  sets = [parse_kv(item, "--set") for item in args.sets]
  binds = [parse_kv(item, "--bind") for item in args.binds]
  loading = bool(sets or binds) or not ctx.is_loaded(app)
  if loading:
    bundle = target.require_bundle()
  else:
    # Catalog may be gone; start will use the run copy as-is.
    bundle = ctx.config.app_run_path(app)
  if loading and not args.allow_compose and not confirm_compose_warnings(app, bundle):
    print("Nothing started.")
    return

  with ctx.locked(f"start {app}", app):
    result = start(target.app_id, bundle, ctx, sets=sets, binds=binds)

    compact = capability_receipt(result.spec, result.run_data, ctx, compact=True)
    if compact.strip():
      print(compact)
    print(location_receipt(result.spec, result.run_data, ctx))
