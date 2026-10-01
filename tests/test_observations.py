"""Where an app stands, and whether what is running is current."""

import pytest

from kelso.lib.apps import AppID
from kelso.lib.config import load_config_file
from kelso.lib.docker import KelsoRunUnitStatus
from kelso.lib.kelso import KelsoCtx
from kelso.lib.observations import AppObservation, observe


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


def test_observing_one_app_agrees_with_observing_them_all(kelso_env):
  assert kelso_env.run("start", "ports-demo").returncode == 0
  assert kelso_env.run("load", "routes-demo").returncode == 0
  assert (
    kelso_env.run(
      "start", "io.p2net.basic-features", "--set", "admin_user=a"
    ).returncode
    == 0
  )
  assert kelso_env.run("unload", "io.p2net.basic-features", "-y").returncode == 0
  ctx = KelsoCtx(load_config_file(kelso_env.config))

  every = {o.app_id: o for o in ctx.observations()}
  for app_id in ("ports-demo", "routes-demo", "io.p2net.basic-features"):
    assert observe(AppID(app_id), ctx) == every[app_id], app_id


def test_observing_an_app_kelso_holds_nothing_for_is_refused(kelso_env):
  ctx = KelsoCtx(load_config_file(kelso_env.config))
  with pytest.raises(ValueError, match="No app state found"):
    observe(AppID("io.example.nothing"), ctx)
