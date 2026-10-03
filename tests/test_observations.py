"""Where an app stands, and whether what is running is current."""

import pytest

from kelso.lib.apps import AppID
from kelso.lib.config import load_config_file
from kelso.lib.docker import KelsoRunUnitStatus
from kelso.lib.kelso import KelsoCtx
from kelso.lib.observations import (
  DEGRADED,
  HEALTHY,
  OK,
  STOPPED,
  AppObservation,
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


def test_config_written_after_the_start_is_pending():
  assert _observed(
    started_at="2026-08-27T10:00:00Z", config_changed_at="2026-08-27T10:05:00Z"
  ).config_pending


def test_config_written_before_the_start_is_applied():
  assert not _observed(
    started_at="2026-08-27T10:05:00Z", config_changed_at="2026-08-27T10:00:00Z"
  ).config_pending


def test_nothing_is_pending_on_an_app_that_is_not_running():
  """The next start reads config fresh, so there is nothing to warn about."""
  assert not _observed(
    containers=(),
    started_at="2026-08-27T10:00:00Z",
    config_changed_at="2026-08-27T10:05:00Z",
  ).config_pending


def test_unknown_timestamps_are_not_pending():
  """A start the activity log has compacted away is unknown, not stale."""
  assert not _observed(
    started_at=None, config_changed_at="2026-08-27T10:05:00Z"
  ).config_pending
  assert not _observed(
    started_at="2026-08-27T10:00:00Z", config_changed_at=None
  ).config_pending


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
    ((_unit("main", "exited", "Exited (0) 1 minute ago"),), ("main",), STOPPED),
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
