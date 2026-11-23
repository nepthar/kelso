from __future__ import annotations

import json
import logging
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from kelso.lib.apps import AppID, read_app_actions
from kelso.lib.bundle import load_bundle
from kelso.lib.docker import KelsoRunUnitStatus, load_kelso_run_unit_status
from kelso.lib.logtab import LogTab
from kelso.lib.store import REKEY_FIELD

if TYPE_CHECKING:
  from kelso.lib.kelso import KelsoCtx

logger = logging.getLogger("kelso")


@dataclass(frozen=True)
class RunState:
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


def _active(unit: KelsoRunUnitStatus, one_shot: bool) -> bool:
  if unit.state.lower() == "running":
    return unit.health != "unhealthy"
  return one_shot and unit.finished


# Where an app stands, as one word.
LOADED = "loaded"  # its run dir holds a frozen bundle kelso can start from
UNLOADED = "unloaded"  # not loaded, but kelso still holds state for it
AVAILABLE = "available"  # a catalog entry and nothing else


def app_state(*, loaded: bool, config_exists: bool, volumes_exist: bool) -> str:
  if loaded:
    return LOADED
  # A lone kelsodb row is an orphan for `doctor`, not a kept app.
  if config_exists or volumes_exist:
    return UNLOADED
  return AVAILABLE


@dataclass(frozen=True)
class AppObservation:
  app_id: AppID
  bundle_path: Path | None
  loaded: bool
  run_dir_exists: bool
  compose_exists: bool
  config_exists: bool
  volumes_exist: bool
  containers: tuple[KelsoRunUnitStatus, ...]
  db_present: bool
  last_action: str | None
  # Config, binds or route assignments written since the app last started (or
  # was loaded). The next start applies them, so only a running app has
  # anything to do: restart.
  changes_pending: bool = False
  # The subdomain or a route assignment is among them. `start` re-derives the
  # routes when so; nothing a user needs to act on.
  routes_pending: bool = False
  loaded_version: str | None = None
  # The version in the bundle it was loaded from; None if that is gone or broken.
  source_version: str | None = None

  @property
  def running_count(self) -> int:
    return sum(container.state.lower() == "running" for container in self.containers)

  @property
  def state(self) -> str:
    return app_state(
      loaded=self.loaded,
      config_exists=self.config_exists,
      volumes_exist=self.volumes_exist,
    )

  @property
  def update_version(self) -> str | None:
    if not self.loaded or self.source_version in (None, self.loaded_version):
      return None
    return self.source_version

  @property
  def known(self) -> bool:
    return self.state != AVAILABLE or self.db_present or bool(self.containers)

  @property
  def orphaned_routes(self) -> bool:
    return (
      self.db_present
      and self.bundle_path is None
      and not self.run_dir_exists
      and not self.containers
    )

  def status(
    self, units: Iterable[str] = (), one_shots: Collection[str] | None = None
  ) -> str:
    containers = {c.run_unit: c for c in self.containers}
    if not self.running_count:
      done = containers and all(
        unit in containers and containers[unit].finished
        for unit in {*units, *containers}
      )
      return FINISHED if done else STOPPED
    if any(
      unit not in containers
      or not _active(containers[unit], one_shots is None or unit in one_shots)
      for unit in {*units, *containers}
    ):
      return DEGRADED
    checked = [c.health for c in containers.values() if c.state.lower() == "running"]
    checked = [health for health in checked if health]
    return HEALTHY if checked and all(h == "healthy" for h in checked) else OK


def _bundle_path(app_id: AppID, ctx: KelsoCtx) -> Path | None:
  try:
    entry = ctx.inventory.bundle(app_id)
  except ValueError:
    return None
  return entry.path if entry else None


def _source_version(origin: Path | None) -> str | None:
  if origin is None or not origin.exists():
    return None
  try:
    return load_bundle(origin).app_spec().version
  except ValueError:
    return None


# A load or a start applies every change recorded before it.
_APPLIED_AT = ("meta/loaded_at", "meta/started_at")
# The records a start applies, by what it does with them: it reads config and
# binds, and re-derives routes (and the network, which carries them behind
# kproxy) from the subdomain, route assignments and network mode.
_CONFIG_KEYS = ("config/", "binds/")
_ROUTE_KEYS = ("config/subdomain", "routes/", "network/")


def _rekeyed(raw: str) -> bool:
  try:
    value = json.loads(raw)
  except ValueError:
    return False
  return isinstance(value, dict) and value.get(REKEY_FIELD) is True


@dataclass(frozen=True)
class PendingChanges:
  # Config and binds: the next start reads them.
  config: bool = False
  # The subdomain and route assignments: the next start re-derives the routes.
  routes: bool = False

  @property
  def any(self) -> bool:
    return self.config or self.routes


def changes_since_start(path: Path) -> PendingChanges:
  # By order in the log, not timestamp: timestamps are only to the second.
  config = routes = False
  for key, entry in LogTab(path).history():
    if key in _APPLIED_AT:
      config = routes = False
    elif _rekeyed(entry.value):
      continue
    elif key.startswith(_ROUTE_KEYS):
      routes = True
    elif key.startswith(_CONFIG_KEYS):
      config = True
  return PendingChanges(config=config, routes=routes)


def collect_observations(
  ctx: KelsoCtx, only: AppID | None = None
) -> dict[str, AppObservation]:
  inventory = ctx.inventory
  docker = load_kelso_run_unit_status()
  db_ids = set(ctx.kelso_db.app_ids())
  app_ids = (
    {e.app_id for e in inventory.catalog}
    | inventory.loaded
    | set(inventory.origins)
    | set(docker)
    | db_ids
  )
  if only is not None:
    app_ids &= {str(only)}

  actions = read_app_actions(ctx)

  observations: dict[str, AppObservation] = {}
  for raw_id in app_ids:
    app_id = AppID(raw_id)
    paths = ctx.loaded_paths(app_id)
    action = actions.get(raw_id)
    loaded = app_id in inventory.loaded
    kept = app_id in inventory.origins
    store = ctx.app_store(app_id) if loaded and kept else None
    pending = (
      changes_since_start(ctx.config.app_config_path(app_id))
      if store is not None
      else PendingChanges()
    )
    observations[app_id] = AppObservation(
      app_id=app_id,
      bundle_path=_bundle_path(app_id, ctx),
      loaded=loaded,
      run_dir_exists=paths.run_path.is_dir(),
      compose_exists=paths.compose_path.is_file(),
      config_exists=kept,
      volumes_exist=ctx.has_volumes(app_id),
      containers=docker.get(raw_id, ()),
      db_present=raw_id in db_ids,
      last_action=action[1] if action else None,
      changes_pending=pending.any,
      routes_pending=pending.routes,
      loaded_version=store.get_meta("loaded_version") if store is not None else None,
      source_version=(
        _source_version(inventory.origins[app_id]) if store is not None else None
      ),
    )
  return observations


def observe(app_id: AppID, ctx: KelsoCtx) -> AppObservation:
  observation = collect_observations(ctx, only=app_id).get(str(app_id))
  if observation is None:
    raise ValueError(f'No app state found for "{app_id}"')
  return observation
