from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from kelso.lib.apps import AppID, read_app_actions
from kelso.lib.bundle import load_bundle
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


# How a loaded app is doing, as one word.
HEALTHY = "healthy"  # everything up, and every healthcheck passes
OK = "ok"  # everything up, with no healthcheck to say more
DEGRADED = "degraded"  # some unit is down, failed, or failing its healthcheck
STOPPED = "stopped"
FINISHED = "finished"  # nothing running, and every unit exited 0


def _active(unit: KelsoRunUnitStatus) -> bool:
  """Up and not failing its healthcheck, or a one-shot that finished cleanly."""
  if unit.state.lower() == "running":
    return unit.health != "unhealthy"
  return unit.finished


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
  # Config, binds or route assignments written since the app was last loaded.
  config_pending: bool = False
  loaded_version: str | None = None
  # The version in the bundle it was loaded from; None if that is gone or broken.
  source_version: str | None = None

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
  def update_version(self) -> str | None:
    """The source's version, when a loaded app's source has a different one."""
    if not self.loaded or self.source_version in (None, self.loaded_version):
      return None
    return self.source_version

  @property
  def loaded(self) -> bool:
    return self.state == LOADED

  @property
  def known(self) -> bool:
    """Whether this id is more than a catalog entry -- something kelso put
    on disk, in docker, or in its own db."""
    return self.state != AVAILABLE or self.db_present

  @property
  def orphaned_routes(self) -> bool:
    """Routes kelso holds for an app with no bundle, loaded copy, or containers."""
    return (
      self.db_present
      and self.bundle_path is None
      and not self.run_dir_exists
      and not self.containers
    )

  def status(self, units: Iterable[str] = ()) -> str:
    """`healthy`, `ok`, `degraded`, `finished` or `stopped`. `units` are the run
    units the manifest declares; without them, only the containers that exist
    count."""
    containers = {c.run_unit: c for c in self.containers}
    if not self.running_count:
      done = containers and all(
        unit in containers and containers[unit].finished
        for unit in {*units, *containers}
      )
      return FINISHED if done else STOPPED
    if any(
      unit not in containers or not _active(containers[unit])
      for unit in {*units, *containers}
    ):
      return DEGRADED
    checked = [c.health for c in containers.values() if c.state.lower() == "running"]
    checked = [health for health in checked if health]
    return HEALTHY if checked and all(h == "healthy" for h in checked) else OK


def _loaded_from(app_id: AppID, ctx: KelsoCtx) -> Path | None:
  """Where an app outside every repo was loaded from, if that is still there."""
  origin = ctx.loaded_origin(app_id)
  return origin if origin is not None and origin.exists() else None


def _versions(app_id: AppID, ctx: KelsoCtx) -> tuple[str | None, str | None]:
  """The loaded version, and the version in the bundle it was loaded from."""
  store = ctx.app_store(app_id)
  origin = store.get_meta("origin")
  source = None
  if origin and Path(origin).exists():
    try:
      source = load_bundle(Path(origin)).app_spec().version
    except ValueError:
      pass
  return store.get_meta("loaded_version"), source


# What a reload applies: the app store keys an operator changes.
_CONFIG_KEYS = ("config/", "binds/", "routes/")


def config_changed_since_load(path: Path) -> bool:
  """Whether the app store at `path` has config written after its last load.

  By order in the log rather than by timestamp, which is only to the second.
  """
  changed = False
  for key, _ in LogTab(path).history():
    if key == "meta/loaded_at":
      changed = False
    elif key.startswith(_CONFIG_KEYS):
      changed = True
  return changed


def collect_observations(
  ctx: KelsoCtx, only: AppID | None = None
) -> dict[str, AppObservation]:
  """Every app kelso knows of, or just `only`, from one pass over each source."""
  bundles = ctx.resolved_bundles()
  run_ids = (
    {path.name for path in ctx.config.run_root.iterdir() if path.is_dir()}
    if ctx.config.run_root.is_dir()
    else set()
  )
  docker = load_kelso_run_unit_status()
  db_ids = set(ctx.kelso_db.app_ids())
  config_ids = ctx.config.app_config_ids()
  app_ids = set(bundles) | run_ids | set(docker) | db_ids | config_ids
  if only is not None:
    app_ids &= {str(only)}

  actions = read_app_actions(ctx)

  observations: dict[str, AppObservation] = {}
  for raw_id in app_ids:
    app_id = AppID(raw_id)
    paths = ctx.loaded_paths(app_id)
    action = actions.get(raw_id)
    run_dir_exists = paths.run_path.is_dir()
    loaded_version, source_version = (
      _versions(app_id, ctx)
      if run_dir_exists and raw_id in config_ids
      else (None, None)
    )
    observations[app_id] = AppObservation(
      app_id=app_id,
      bundle_path=bundles.get(raw_id) or _loaded_from(app_id, ctx),
      run_dir_exists=run_dir_exists,
      compose_exists=paths.compose_path.is_file(),
      config_exists=raw_id in config_ids,
      # Spelled out rather than borrowing `lifecycle.managed_volume_dirs`,
      # which would import back through KelsoCtx into this module.
      volumes_exist=any(
        (root / raw_id).is_dir() for root in ctx.config.volume_roots.values()
      ),
      containers=docker.get(raw_id, ()),
      db_present=raw_id in db_ids,
      last_action=action[1] if action else None,
      config_pending=(
        run_dir_exists
        and raw_id in config_ids
        and config_changed_since_load(ctx.config.app_config_path(app_id))
      ),
      loaded_version=loaded_version,
      source_version=source_version,
    )
  return observations


def observe(app_id: AppID, ctx: KelsoCtx) -> AppObservation:
  """One app's observation. Raises ValueError for an id kelso holds nothing for."""
  observation = collect_observations(ctx, only=app_id).get(str(app_id))
  if observation is None:
    raise ValueError(f'No app state found for "{app_id}"')
  return observation
