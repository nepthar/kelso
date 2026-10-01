import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from kelso.lib.apps import AppID, record_app_action
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle._common import container_recovery_message, logger
from kelso.lib.lifecycle.run import stop

# Deleting config while keeping data regenerates an app's secrets against a
# database initialised with the old ones, and it comes back as an app that no
# longer starts. So config only ever goes with the data, in PURGE.
RemovalMode = Literal["unload", "rm", "temp", "data", "purge"]

# Stop it and remove the loaded copy under var/run. Nothing else.
UNLOAD: RemovalMode = "unload"
# Of a stopped app: the loaded copy, and its temp and logs volumes.
RM: RemovalMode = "rm"
# Of a stopped app: empty its temp and logs volumes. It stays loaded.
TEMP: RemovalMode = "temp"
# Of a stopped app: empty every volume, keeping its config, so it starts as if
# just loaded. It stays loaded.
DATA: RemovalMode = "data"
# Of a stopped app: everything kelso holds for it -- the loaded copy, every
# volume, its config and secrets, routes and host ports, and snapshots.
PURGE: RemovalMode = "purge"

_TEMP_KINDS = ("temp", "logs")
_ALL_KINDS = ("data", "bulk", "temp", "logs")

_ACTIONS: dict[str, str] = {
  UNLOAD: "unloaded",
  RM: "removed",
  TEMP: "cleared-temp",
  DATA: "cleared-data",
  PURGE: "purged",
}


@dataclass(frozen=True)
class RemovalPlan:
  """What a removal will delete, and what it deliberately will not."""

  app_id: AppID
  mode: RemovalMode
  # The loaded copy, when this mode unloads.
  run_path: Path | None
  config_path: Path | None
  # Deleted outright, or emptied in place for a mode that leaves the app loaded:
  # its bind mounts must still resolve.
  volume_paths: tuple[Path, ...]
  snapshot_path: Path | None
  host_paths: tuple[Path, ...]
  # Containers to take down first: a running app for unload, leftover stopped
  # ones for anything that removes the loaded copy.
  stop_first: bool

  @property
  def purges(self) -> bool:
    return self.mode == PURGE

  @property
  def empties(self) -> bool:
    """Volumes are emptied rather than deleted, because the app stays loaded."""
    return self.mode in (TEMP, DATA)


def removal_plan(app_id: AppID, ctx: KelsoCtx, *, mode: RemovalMode) -> RemovalPlan:
  """Work out what a removal would destroy, without destroying it.

  Raises ValueError for anything but unload on a running app.
  """
  state = ctx.run_state(app_id)
  if state.containers and not state.compose_exists:
    raise ValueError(container_recovery_message(app_id, ctx))
  if mode != UNLOAD and state.running_count:
    raise ValueError(f"App {app_id} is running; run `kelso stop {app_id}` first")

  host_paths: tuple[Path, ...] = ()
  config_path = ctx.config.app_config_path(app_id)
  if config_path.is_file():
    binds = ctx.app_store(app_id).list_binds()
    host_paths = tuple(
      ctx.config.host_volumes[tag].path
      for tag in binds.values()
      if tag in ctx.config.host_volumes
    )

  kinds = {UNLOAD: (), RM: _TEMP_KINDS, TEMP: _TEMP_KINDS}.get(mode, _ALL_KINDS)
  roots = ctx.config.volume_roots
  volumes = tuple(roots[k] / app_id for k in kinds if (roots[k] / app_id).is_dir())
  snapshots = ctx.config.snapshot_root / app_id
  unloads = mode in (UNLOAD, RM, PURGE)

  return RemovalPlan(
    app_id=app_id,
    mode=mode,
    run_path=state.run_path if unloads else None,
    config_path=config_path if mode == PURGE and config_path.is_file() else None,
    volume_paths=volumes,
    snapshot_path=snapshots if mode == PURGE and snapshots.is_dir() else None,
    host_paths=host_paths,
    stop_first=unloads and state.compose_exists and bool(state.containers),
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
    if plan.empties:
      _empty_volumes(path)
      logger.info("emptied volumes in %s", path)
    elif path.is_dir():
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


def _empty_volumes(app_dir: Path) -> None:
  """Delete what is inside each volume under `app_dir`, keeping the volume dirs."""
  for volume in app_dir.iterdir():
    if not volume.is_dir() or volume.is_symlink():
      continue
    for entry in volume.iterdir():
      if entry.is_dir() and not entry.is_symlink():
        shutil.rmtree(entry)
      else:
        entry.unlink()
