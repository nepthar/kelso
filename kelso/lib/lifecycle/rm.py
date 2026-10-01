import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from kelso.lib.apps import AppID, record_app_action
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle._common import (
  container_recovery_message,
  logger,
  managed_volume_dirs,
)
from kelso.lib.lifecycle.run import stop

# Deleting config while keeping data regenerates an app's secrets against a
# database initialised with the old ones, and it comes back as an app that no
# longer starts. So config only ever goes with the data, in PURGE.
RemovalMode = Literal["unload", "rm", "purge"]

# The loaded copy under var/run. Data and config stay.
UNLOAD: RemovalMode = "unload"
# That and the data volumes. Config stays, so loading it again starts afresh
# with the same settings and address.
RM: RemovalMode = "rm"
# Everything kelso holds for the app: also its config and secrets, routes and
# host ports, and snapshots.
PURGE: RemovalMode = "purge"

_ACTIONS: dict[str, str] = {UNLOAD: "unloaded", RM: "removed", PURGE: "purged"}


@dataclass(frozen=True)
class RemovalPlan:
  """What a removal will delete, and what it deliberately will not."""

  app_id: AppID
  mode: RemovalMode
  run_path: Path | None
  config_path: Path | None
  volume_paths: tuple[Path, ...]
  snapshot_path: Path | None
  host_paths: tuple[Path, ...]
  stop_first: bool

  @property
  def purges(self) -> bool:
    return self.mode == PURGE


def removal_plan(app_id: AppID, ctx: KelsoCtx, *, mode: RemovalMode) -> RemovalPlan:
  """Work out what a removal would destroy, without destroying it."""
  state = ctx.run_state(app_id)
  if state.containers and not state.compose_exists:
    raise ValueError(container_recovery_message(app_id, ctx))

  host_paths: tuple[Path, ...] = ()
  config_path = ctx.config.app_config_path(app_id)
  if config_path.is_file():
    binds = ctx.app_store(app_id).list_binds()
    host_paths = tuple(
      ctx.config.host_volumes[tag].path
      for tag in binds.values()
      if tag in ctx.config.host_volumes
    )

  volumes: tuple[Path, ...] = ()
  if mode in (RM, PURGE):
    volumes = tuple(d for d in managed_volume_dirs(app_id, ctx) if d.is_dir())
  snapshots = ctx.config.snapshot_root / app_id

  return RemovalPlan(
    app_id=app_id,
    mode=mode,
    run_path=state.run_path,
    config_path=config_path if mode == PURGE and config_path.is_file() else None,
    volume_paths=volumes,
    snapshot_path=snapshots if mode == PURGE and snapshots.is_dir() else None,
    host_paths=host_paths,
    stop_first=state.compose_exists,
  )


def rm(plan: RemovalPlan, ctx: KelsoCtx) -> None:
  """Carry out `plan`."""
  app_id = plan.app_id
  if plan.stop_first:
    logger.info("Stopping %s", app_id)
    stop(app_id, ctx)

  if plan.run_path is not None and plan.run_path.exists():
    shutil.rmtree(plan.run_path)
    logger.info("removed run directory %s", plan.run_path)

  if plan.config_path is not None and plan.config_path.is_file():
    plan.config_path.unlink()
    logger.info("removed config %s", plan.config_path)

  for path in plan.volume_paths:
    if path.is_dir():
      shutil.rmtree(path)
      logger.info("removed volume %s", path)

  if plan.snapshot_path is not None and plan.snapshot_path.is_dir():
    shutil.rmtree(plan.snapshot_path)
    logger.info("removed snapshots %s", plan.snapshot_path)

  if plan.purges:
    ctx.kelso_db.purge_app(app_id)

  # The activity log outlives the app on purpose, so close it out rather than
  # leaving the trail ending at whatever happened before the removal.
  action = _ACTIONS[plan.mode]
  record_app_action(action, app_id, ctx)
  logger.info("%s %s", action, app_id)
