import argparse
from pathlib import Path

from kelso.lib.apps import read_last_app_action
from kelso.lib.bundle import is_pathlike, load_bundle
from kelso.lib.kelso import KelsoCtx
from kelso.lib.observations import RunState
from kelso.lib.receipt import capability_receipt
from kelso.lib.run_layout import load_run_data


def register(subparsers) -> None:
  parser = subparsers.add_parser(
    "inspect",
    help="Show state, images, ports, routes, volumes, config, and sharp edges",
  )
  parser.add_argument(
    "app",
    metavar="APP",
    help="App ID or path to a kelso app",
  )
  parser.set_defaults(func=run)


def run(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  if is_pathlike(args.app):
    source = Path(args.app).expanduser().resolve()
    with ctx.kelso_lock(f"inspect {source}"):
      spec = load_bundle(source).app_spec()
      print(capability_receipt(spec, None, ctx, compact=False))
    return

  app = ctx.resolve_app(args.app)
  with ctx.locked(f"inspect {app}", app):
    loaded = ctx.loaded_spec(app)
    spec = loaded or ctx.bundle_spec(app)
    if spec is None:
      raise ValueError(
        f"No manifest for {app}: it is neither loaded nor in a catalog. "
        f"Pass a path to a .klso to inspect one directly."
      )

    notes: tuple[str, ...] = ()
    if loaded is None:
      notes = (
        f"{app} is not loaded; this is the manifest it would be loaded "
        f"from. Load it with `kelso load {app}`",
      )
    elif ctx.manifest_stale(app):
      notes = (
        f"manifest has changed, `kelso load {app}` may be required to reflect changes",
      )

    print(
      capability_receipt(
        spec,
        load_run_data(spec, ctx) if loaded is not None else None,
        ctx,
        compact=False,
        notes=notes,
        state_line=_state_line(ctx.run_state(app)),
        last_action=read_last_app_action(app, ctx) or "-",
        show_logs=True,
      )
    )


def _state_line(state: RunState) -> str:
  total = len(state.containers)
  if state.running_count:
    return f"running, {state.running_count}/{total or state.running_count} containers"
  if total:
    return f"exited, 0/{total} containers"
  return "-"
