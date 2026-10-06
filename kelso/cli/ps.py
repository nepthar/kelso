import argparse

from tabulate import tabulate

from kelso.lib.kelso import KelsoCtx
from kelso.lib.observations import FINISHED, OK, STOPPED, UNLOADED, AppObservation
from kelso.lib.run_layout import load_run_data
from kelso.lib.spec import AppSpec
from kelso.lib.views import config_status

EMPTY = "-"


def register(subparsers) -> None:
  parser = subparsers.add_parser("ps", help="List loaded Kelso apps and their state")
  parser.add_argument(
    "-a",
    "--all",
    action="store_true",
    help="Also list apps that are not loaded but whose config or data kelso kept",
  )
  parser.set_defaults(func=run)


def run(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  with ctx.kelso_lock("ps"):
    rows = []
    for observation in ctx.observations():
      if not (observation.loaded or (args.all and observation.known)):
        continue
      spec = (
        ctx.bundle_spec(observation.app_id)
        if observation.state == UNLOADED
        else ctx.loaded_spec(observation.app_id)
      )
      rows.append(
        (
          observation.app_id,
          _status(observation, spec),
          _config(observation, spec, ctx),
          _volumes(observation, spec),
          observation.last_action or EMPTY,
          _version(observation, spec),
        )
      )
    print(
      tabulate(
        rows,
        headers=["APP_ID", "STATUS", "CONFIG", "VOLUMES", "LAST_ACTION", "VERSION"],
        tablefmt="simple",
        disable_numparse=True,
      )
    )


def _status(observation: AppObservation, spec: AppSpec | None) -> str:
  """`running`, `running (healthy)`, `running (degraded)`, `finished`,
  `stopped`, or the state of an app that is not loaded."""
  if not observation.loaded:
    return observation.state
  status = observation.status(spec.run_units if spec else ())
  if status in (STOPPED, FINISHED):
    return status
  return "running" if status == OK else f"running ({status})"


def _config(observation: AppObservation, spec: AppSpec | None, ctx: KelsoCtx) -> str:
  """Whether this app's settings are complete -- kept config included."""
  if not observation.config_exists:
    return EMPTY
  if observation.state == UNLOADED:
    if spec is None:
      return "kept"
    return config_status(spec, ctx.app_store(observation.app_id))
  if spec is None:
    return EMPTY
  if load_run_data(spec, ctx).start_blockers:
    return "missing"
  # Complete, but changed since the app was loaded: a reload applies it.
  return "pending" if observation.config_pending else "ready"


def _version(observation: AppObservation, spec: AppSpec | None) -> str:
  version = spec.version if spec else EMPTY
  if observation.update_version:
    return f"{version} ({observation.update_version} available)"
  return version


def _volumes(observation: AppObservation, spec: AppSpec | None) -> str:
  if spec is None:
    return EMPTY
  count = str(len(spec.volumes))
  if observation.state != UNLOADED:
    return count
  return f"{count} kept" if observation.volumes_exist else EMPTY
