import argparse

from tabulate import tabulate

from kelso.lib.kelso import KelsoCtx
from kelso.lib.observations import UNINSTALLED, AppObservation
from kelso.lib.run_layout import load_run_data
from kelso.lib.spec import AppSpec
from kelso.lib.views import config_status

EMPTY = "-"


def register(subparsers) -> None:
  parser = subparsers.add_parser("ps", help="List installed Kelso apps and their state")
  parser.set_defaults(func=run)


def run(args: argparse.Namespace, ctx: KelsoCtx, conn) -> None:
  with ctx.kelso_lock("ps"):
    rows = []
    for observation in ctx.observations():
      if not observation.known:
        continue
      spec = (
        ctx.bundle_spec(observation.app_id)
        if observation.state == UNINSTALLED
        else ctx.staged_spec(observation.app_id)
      )
      rows.append(
        (
          observation.app_id,
          _status(observation),
          _config(observation, spec, ctx),
          _volumes(observation, spec),
          observation.last_action or EMPTY,
        )
      )
    conn.out(
      tabulate(
        rows,
        headers=["APP_ID", "STATUS", "CONFIG", "VOLUMES", "LAST_ACTION"],
        tablefmt="simple",
      )
    )


def _status(observation: AppObservation) -> str:
  if observation.containers:
    return observation.status
  return UNINSTALLED if observation.state == UNINSTALLED else EMPTY


def _config(observation: AppObservation, spec: AppSpec | None, ctx: KelsoCtx) -> str:
  """Whether this app's settings are complete -- kept config included."""
  if not observation.config_exists:
    return EMPTY
  if observation.state == UNINSTALLED:
    if spec is None:
      return "kept"
    return config_status(spec, ctx.app_store(observation.app_id))
  if spec is None:
    return EMPTY
  return "missing" if load_run_data(spec, ctx).start_blockers else "ready"


def _volumes(observation: AppObservation, spec: AppSpec | None) -> str:
  if spec is None:
    return EMPTY
  count = str(len(spec.volumes))
  if observation.state != UNINSTALLED:
    return count
  return f"{count} kept" if observation.volumes_exist else EMPTY
