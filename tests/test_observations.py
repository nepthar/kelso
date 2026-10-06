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
  config_changed_since_load,
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


def _log(tmp_path, *keys: str):
  path = tmp_path / "app.logtab"
  table = LogTab(path)
  for key in keys:
    table.write(key, '"x"')
  return path


def test_config_written_after_the_load_is_pending(tmp_path):
  path = _log(tmp_path, "config/a", "meta/loaded_at", "config/subdomain")
  assert config_changed_since_load(path)


def test_config_written_during_the_load_is_applied(tmp_path):
  """Load writes generated secrets and route defaults before `loaded_at`."""
  path = _log(tmp_path, "config/a", "routes/main", "meta/loaded_at", "meta/x")
  assert not config_changed_since_load(path)


@pytest.mark.parametrize("key", ["binds/media", "routes/main"])
def test_binds_and_route_assignments_are_config_too(tmp_path, key):
  assert config_changed_since_load(_log(tmp_path, "meta/loaded_at", key))


def test_a_reload_applies_what_was_pending(tmp_path):
  path = _log(tmp_path, "meta/loaded_at", "config/a", "meta/loaded_at")
  assert not config_changed_since_load(path)


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
