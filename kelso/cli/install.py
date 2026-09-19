import argparse
from pathlib import Path

from kelso.lib.apps import AppID
from kelso.lib.bundle import load_bundle
from kelso.lib.connections import grants
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle import stage, staging_target
from kelso.lib.spec import AppSpec
from kelso.lib.util import Conn


def register(subparsers) -> None:
  parser = subparsers.add_parser(
    "install",
    help="Install an application so it can be started (accepts app id or .klso path)",
  )
  parser.add_argument(
    "app",
    metavar="APP",
    help="App ID (e.g. io.example.myapp or myapp) or path to an app",
  )
  parser.add_argument(
    "--force",
    action="store_true",
    help="Install even though this id was last installed from somewhere else",
  )
  parser.add_argument(
    "-y",
    "--yes",
    action="store_true",
    help="Skip the confirmation for unmodelled compose keys and connections",
  )
  parser.set_defaults(func=run)


def run(args: argparse.Namespace, ctx: KelsoCtx, conn: Conn) -> None:
  target = staging_target(ctx, args.app, force=args.force)
  app = target.app_id
  bundle = target.bundle or ctx.bundle_path(app)
  if not args.yes and not confirm_install(app, bundle, ctx, conn):
    conn.out("Nothing installed.")
    return
  with ctx.locked(f"stage {app}", app):
    result = stage(app, bundle, ctx, bound=target.bound_to)
    for name in result.dropped_volumes:
      conn.err(
        f"volume {name} is no longer declared in the manifest; "
        f"its link is gone but its data was left in place"
      )
    conn.out(f"Installed {app} at {ctx.run_path(app)}")
    conn.out(f"Start it with: kelso start {app}")


def _bundle_spec(app: AppID, bundle: Path) -> AppSpec | None:
  """The bundle's spec, or None if it does not parse.

  A manifest that cannot be read has nothing to ask about yet -- `stage` is
  about to fail on it with a better message than a prompt could give.
  """
  try:
    return load_bundle(bundle).app_spec()
  except (ValueError, RuntimeError, OSError):
    return None


def _new_grants(app: AppID, spec: AppSpec, ctx: KelsoCtx) -> list[str]:
  """What `spec`'s connections grant that the installed copy, if any, did not."""
  staged = ctx.staged_spec(app)
  granted = {grants(c) for c in staged.connections.values()} if staged else set()
  return [
    f"{grants(c)} (connection {name})"
    for name, c in spec.connections.items()
    if grants(c) not in granted
  ]


def confirm_install(app: AppID, bundle: Path, ctx: KelsoCtx, conn: Conn) -> bool:
  """Ask before installing unmodelled compose keys or a new connection grant."""
  spec = _bundle_spec(app, bundle)
  if spec is None:
    return True
  warnings = spec.compose_warnings
  new_grants = _new_grants(app, spec, ctx)
  if not warnings and not new_grants:
    return True

  for warning in warnings:
    conn.out(f"Warning: {warning.message()}:")
    for line in warning.option_lines():
      conn.out(f"  {line}")
  for grant in new_grants:
    conn.out(f"Warning: {app} asks for {grant}.")

  try:
    answer = conn.read(f"Install {app} anyway? [y/N] ")
  except EOFError:
    return False
  return answer.strip().lower() in ("y", "yes")
