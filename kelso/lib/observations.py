from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from kelso.lib.apps import (
  AppID,
  read_app_actions,
  read_app_starts,
  read_last_app_action,
)
from kelso.lib.docker import KelsoRunUnitStatus, load_kelso_run_unit_status
from kelso.lib.logtab import LogTab

if TYPE_CHECKING:
  from kelso.lib.kelso import KelsoCtx

logger = logging.getLogger("kelso")


@dataclass(frozen=True)
class RunState:
  """The run-state of a single app, for lifecycle/snapshot operations."""

  app_id: AppID
  run_path: Path
  run_dir_exists: bool
  compose_exists: bool
  containers: tuple[KelsoRunUnitStatus, ...]

  @property
  def running_count(self) -> int:
    return sum(container.state.lower() == "running" for container in self.containers)


# Where an app stands, as one word.
LOADED = "loaded"  # there is a run dir kelso can start from
UNLOADED = "unloaded"  # no run dir, but kelso still holds state for it
AVAILABLE = "available"  # a catalog entry and nothing else


def app_state(
  *,
  run_dir_exists: bool,
  config_exists: bool,
  volumes_exist: bool,
  has_containers: bool = False,
) -> str:
  """`loaded`, `unloaded`, or `available`."""
  if run_dir_exists or has_containers:
    return LOADED
  # A lone kelsodb row is an orphan for `doctor`, not a kept app.
  if config_exists or volumes_exist:
    return UNLOADED
  return AVAILABLE


@dataclass(frozen=True)
class AppObservation:
  """A union of every possible place an app can leave a trace - for diagnostics and status"""

  app_id: AppID
  bundle_path: Path | None
  run_dir_exists: bool
  compose_exists: bool
  config_exists: bool
  volumes_exist: bool
  containers: tuple[KelsoRunUnitStatus, ...]
  db_present: bool
  last_action: str | None
  config_changed_at: str | None = None
  started_at: str | None = None

  @property
  def running_count(self) -> int:
    return sum(container.state.lower() == "running" for container in self.containers)

  @property
  def state(self) -> str:
    """Where this app stands. See `app_state`."""
    return app_state(
      run_dir_exists=self.run_dir_exists,
      config_exists=self.config_exists,
      volumes_exist=self.volumes_exist,
      has_containers=bool(self.containers),
    )

  @property
  def config_pending(self) -> bool:
    """Configuration written since the running containers were started.

    `start` reads config values and route assignments fresh and hands them to
    `compose up`, so a change made after that is on disk but not in the app
    that is running. Both timestamps are second-resolution, so a change made
    in the same second as the start reads as applied.
    """
    if not (self.running_count and self.config_changed_at and self.started_at):
      return False
    return self.config_changed_at > self.started_at

  @property
  def loaded(self) -> bool:
    return self.state == LOADED

  @property
  def known(self) -> bool:
    """Whether this id is more than a catalog entry -- something kelso put
    on disk, in docker, or in its own db."""
    return self.state != AVAILABLE or self.db_present

  @property
  def status(self) -> str:
    """Container state as one word: what an operator scanning a list wants."""
    if self.running_count:
      return "running"
    if self.containers:
      return "exited"
    return "stopped"


def _loaded_from(app_id: AppID, ctx: KelsoCtx) -> Path | None:
  """Where an app outside every repo was loaded from, if that is still there."""
  origin = ctx.loaded_origin(app_id)
  return origin if origin is not None and origin.exists() else None


def collect_observations(ctx: KelsoCtx) -> dict[str, AppObservation]:
  """Every app kelso knows of, from one pass over each source."""
  bundles = ctx.resolved_bundles()
  run_ids = (
    {path.name for path in ctx.config.run_root.iterdir() if path.is_dir()}
    if ctx.config.run_root.is_dir()
    else set()
  )
  docker = load_kelso_run_unit_status()
  db_ids = set(ctx.kelso_db.app_ids())
  config_ids = ctx.config.app_config_ids()
  actions = read_app_actions(ctx)
  starts = read_app_starts(ctx)

  return {
    raw_id: _observation(
      AppID(raw_id),
      ctx,
      bundle=bundles.get(raw_id),
      containers=docker.get(raw_id, ()),
      db_present=raw_id in db_ids,
      config_exists=raw_id in config_ids,
      action=actions.get(raw_id, (None, None))[1],
      started_at=starts.get(raw_id),
    )
    for raw_id in set(bundles) | run_ids | set(docker) | db_ids | config_ids
  }


def observe(app_id: AppID, ctx: KelsoCtx) -> AppObservation:
  """One app's observation, without looking at any other app.

  Raises ValueError for an id kelso holds nothing for.
  """
  raw_id = str(app_id)
  entries = ctx.app_catalog().get(raw_id, ())
  starts = [
    entry.ts
    for _, entry in ctx.activity_log.history(prefix=f"apps/{raw_id}/status")
    if entry.value == "started"
  ]
  observation = _observation(
    app_id,
    ctx,
    bundle=entries[0].path if len(entries) == 1 else None,
    containers=load_kelso_run_unit_status().get(raw_id, ()),
    db_present=bool(ctx.kelso_db.list_routes(raw_id)),
    config_exists=ctx.config.app_config_path(app_id).is_file(),
    action=read_last_app_action(app_id, ctx),
    started_at=starts[-1] if starts else None,
  )
  if observation.bundle_path is None and not (
    observation.run_dir_exists
    or observation.containers
    or observation.db_present
    or observation.config_exists
  ):
    raise ValueError(f'No app state found for "{app_id}"')
  return observation


def _observation(
  app_id: AppID,
  ctx: KelsoCtx,
  *,
  bundle: Path | None,
  containers: tuple[KelsoRunUnitStatus, ...],
  db_present: bool,
  config_exists: bool,
  action: str | None,
  started_at: str | None,
) -> AppObservation:
  paths = ctx.loaded_paths(app_id)
  return AppObservation(
    app_id=app_id,
    bundle_path=bundle or _loaded_from(app_id, ctx),
    run_dir_exists=paths.run_path.is_dir(),
    compose_exists=paths.compose_path.is_file(),
    config_exists=config_exists,
    # Spelled out rather than borrowing `lifecycle.managed_volume_dirs`,
    # which would import back through KelsoCtx into this module.
    volumes_exist=any(
      (root / app_id).is_dir() for root in ctx.config.volume_roots.values()
    ),
    containers=containers,
    db_present=db_present,
    last_action=action,
    config_changed_at=(
      LogTab(ctx.config.app_config_path(app_id)).last_ts() if config_exists else None
    ),
    started_at=started_at,
  )
