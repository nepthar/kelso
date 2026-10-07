"""Where an app stands, and whether what is running is current."""

import pytest

from kelso.lib.apps import AppID
from kelso.lib.config import load_config_file
from kelso.lib.docker import KelsoRunUnitStatus
from kelso.lib.kelso import KelsoCtx
from kelso.lib.logtab import LogTab
from kelso.lib.observations import (
  DEGRADED,
  FINISHED,
  HEALTHY,
  OK,
  STOPPED,
  AppObservation,
  PendingChanges,
  changes_since_start,
  observe,
)


def _observed(**kwargs) -> AppObservation:
  base = dict(
    app_id=AppID("demo.app"),
    bundle_path=None,
    run_dir_exists=True,
    compose_exists=True,
    config_exists=True,
    volumes_exist=True,
    containers=(
      KelsoRunUnitStatus(
        app_id="demo.app",
        run_unit="main",
        container_id="abc",
        name="demo",
        state="running",
      ),
    ),
    db_present=True,
    last_action="started",
  )
  return AppObservation(**{**base, **kwargs})


def _log(tmp_path, *records: str):
  """A store with these records, in order: `config` and `routes` are change
  markers, anything else a key written with a placeholder value."""
  path = tmp_path / "app.logtab"
  table = LogTab(path)
  for record in records:
    if record in ("config", "routes"):
      table.write("meta/changed", f'"{record}"')
    else:
      table.write(record, '"x"')
  return path


def test_a_change_after_the_load_is_pending(tmp_path):
  path = _log(tmp_path, "config/a", "meta/loaded_at", "config/b", "config")
  pending = changes_since_start(path)
  assert pending.config and pending.any
  assert not pending.routes


def test_a_change_during_the_load_is_applied(tmp_path):
  """Load writes generated secrets and route defaults before `loaded_at`."""
  path = _log(tmp_path, "config", "routes", "meta/loaded_at", "meta/x")
  assert changes_since_start(path) == PendingChanges()


def test_a_record_with_no_change_marker_leaves_nothing_pending(tmp_path):
  """What a rekey writes: the same values, encrypted under another key."""
  path = _log(tmp_path, "meta/started_at", "config/a", "routes/main")
  assert changes_since_start(path) == PendingChanges()


def test_a_routes_change_is_its_own_kind(tmp_path):
  pending = changes_since_start(_log(tmp_path, "meta/loaded_at", "routes"))
  assert pending.routes and pending.any
  assert not pending.config


@pytest.mark.parametrize("applied", ["meta/loaded_at", "meta/started_at"])
def test_a_load_or_a_start_applies_what_was_pending(tmp_path, applied):
  path = _log(tmp_path, "meta/loaded_at", "config", "routes", applied)
  assert changes_since_start(path) == PendingChanges()


def test_observing_an_app_kelso_holds_nothing_for_is_refused(kelso_env):
  ctx = KelsoCtx(load_config_file(kelso_env.config))
  with pytest.raises(ValueError, match="No app state found"):
    observe(AppID("io.example.nothing"), ctx)


def _unit(name: str, state: str, status: str = "") -> KelsoRunUnitStatus:
  return KelsoRunUnitStatus("demo", name, f"id-{name}", f"demo-{name}-1", state, status)


def _with_units(*units: KelsoRunUnitStatus) -> AppObservation:
  return AppObservation(
    app_id=AppID("demo"),
    bundle_path=None,
    run_dir_exists=True,
    compose_exists=True,
    config_exists=True,
    volumes_exist=False,
    containers=units,
    db_present=False,
    last_action=None,
  )


@pytest.mark.parametrize(
  "units, declared, status",
  [
    ((), ("main",), STOPPED),
    ((_unit("main", "exited", "Exited (0) 1 minute ago"),), ("main",), FINISHED),
    ((_unit("main", "exited", "Exited (1) 1 minute ago"),), ("main",), STOPPED),
    # Finished only when every declared unit is.
    ((_unit("main", "exited", "Exited (0) 1 minute ago"),), ("main", "db"), STOPPED),
    ((_unit("main", "running", "Up 2 minutes"),), ("main",), OK),
    ((_unit("main", "running", "Up 2 minutes (healthy)"),), ("main",), HEALTHY),
    ((_unit("main", "running", "Up 9 seconds (health: starting)"),), ("main",), OK),
    ((_unit("main", "running", "Up 2 minutes (unhealthy)"),), ("main",), DEGRADED),
    # A declared unit with no container at all.
    ((_unit("main", "running", "Up 2 minutes"),), ("main", "db"), DEGRADED),
    (
      (_unit("main", "running", "Up 1 minute"), _unit("db", "exited", "Exited (1) 1s")),
      ("main", "db"),
      DEGRADED,
    ),
    # A one-shot that finished cleanly has done its job.
    (
      (
        _unit("main", "running", "Up 1 minute (healthy)"),
        _unit("init", "exited", "Exited (0) 1 minute ago"),
      ),
      ("main", "init"),
      HEALTHY,
    ),
    # A unit with no healthcheck does not keep the others from reading healthy.
    (
      (_unit("main", "running", "Up 1 minute (healthy)"), _unit("db", "running", "Up")),
      ("main", "db"),
      HEALTHY,
    ),
  ],
)
def test_status_says_how_a_loaded_app_is_doing(units, declared, status):
  assert _with_units(*units).status(declared) == status
