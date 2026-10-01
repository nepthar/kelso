"""`kelso up` and `kelso down`: the whole box, in start_order groups."""

import json
from pathlib import Path

import pytest

from kelso.lib.apps import read_last_app_action
from kelso.lib.config import load_config_file
from kelso.lib.docker import KelsoRunUnitStatus
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle import updown


@pytest.fixture(autouse=True)
def no_settle(monkeypatch):
  monkeypatch.setattr(updown, "SETTLE", 0)


def _compose(kelso_env, verb: str) -> list[str]:
  """App ids in the order `compose <verb>` ran for them."""
  calls = [json.loads(line) for line in kelso_env.docker_log.read_text().splitlines()]
  return [Path(c["cwd"]).name for c in calls if c["args"][:2] == ["compose", verb]]


def _install(kelso_env, *apps):
  for app in apps:
    assert kelso_env.run("install", app).returncode == 0


def test_up_starts_each_start_order_group_before_the_next(kelso_env):
  _install(kelso_env, "ports-demo", "routes-demo")
  set_ = kelso_env.run("config", "routes-demo", "--set", "start_order=4")
  assert set_.returncode == 0, set_.stderr

  up = kelso_env.run("up")

  assert up.returncode == 0, up.stderr
  assert _compose(kelso_env, "up") == ["routes-demo", "ports-demo"]
  assert up.stdout.index("Starting run group 4 - routing & connections") < (
    up.stdout.index("Starting run group 6 - applications")
  )


def test_down_stops_in_reverse_and_up_brings_it_back(kelso_env):
  _install(kelso_env, "ports-demo", "routes-demo")
  kelso_env.run("config", "routes-demo", "--set", "start_order=4")
  assert kelso_env.run("up").returncode == 0

  down = kelso_env.run("down")

  assert down.returncode == 0, down.stderr
  assert _compose(kelso_env, "down") == ["ports-demo", "routes-demo"]
  assert kelso_env.run("up").returncode == 0
  assert _compose(kelso_env, "up") == ["routes-demo", "ports-demo"] * 2


def test_up_leaves_an_app_stopped_with_kelso_stop_alone(kelso_env):
  assert kelso_env.run("start", "ports-demo").returncode == 0
  assert kelso_env.run("stop", "ports-demo").returncode == 0

  up = kelso_env.run("up")

  assert up.returncode == 0, up.stderr
  assert "ports-demo: stopped with `kelso stop`, left stopped" in up.stdout
  assert _compose(kelso_env, "up") == ["ports-demo"]


def test_a_group_not_ready_in_time_is_reported_and_the_next_still_starts(kelso_env):
  _install(kelso_env, "ports-demo", "routes-demo")
  kelso_env.run("config", "routes-demo", "--set", "start_order=4")
  assert kelso_env.run("start", "routes-demo").returncode == 0
  containers = json.loads(kelso_env.docker_state.read_text())
  containers[0]["status"] = "Up 1 second (health: starting)"
  kelso_env.docker_state.write_text(json.dumps(containers))

  up = kelso_env.run("up", "--timeout", "0")

  assert up.returncode == 1
  assert "routes-demo: not ready after 0s" in up.stderr
  assert _compose(kelso_env, "up")[-1] == "ports-demo"


def test_up_and_down_leave_group_0_to_kelsod(kelso_env):
  _install(kelso_env, "ports-demo", "routes-demo")
  kelso_env.run("config", "routes-demo", "--set", "start_order=0")
  assert kelso_env.run("start", "routes-demo").returncode == 0

  up = kelso_env.run("up")
  down = kelso_env.run("down")

  assert up.returncode == 0, up.stderr
  assert down.returncode == 0, down.stderr
  assert "routes-demo" not in up.stdout + down.stdout
  assert _compose(kelso_env, "down") == ["ports-demo"]


def test_kelsods_groups_come_up_and_go_down_with_it(kelso_env):
  _install(kelso_env, "ports-demo", "routes-demo")
  kelso_env.run("config", "routes-demo", "--set", "start_order=0")
  ctx = KelsoCtx(load_config_file(kelso_env.config))

  assert updown.up(ctx, updown.KELSOD_GROUPS) == []
  assert updown.down(ctx, updown.KELSOD_GROUPS) == []

  assert _compose(kelso_env, "up") == ["routes-demo"]
  assert _compose(kelso_env, "down") == ["routes-demo"]


def test_an_odd_group_is_named_by_its_number(kelso_env):
  _install(kelso_env, "ports-demo")
  kelso_env.run("config", "ports-demo", "--set", "start_order=3")

  assert "Starting run group 3\n" in kelso_env.run("up").stdout


def test_down_records_its_own_action_so_up_does_not_skip(kelso_env):
  assert kelso_env.run("start", "ports-demo").returncode == 0
  assert kelso_env.run("down").returncode == 0

  ctx = KelsoCtx(load_config_file(kelso_env.config))
  assert read_last_app_action(ctx.resolve_app("ports-demo"), ctx) == updown.DOWN_ACTION


@pytest.mark.parametrize(
  ("state", "status", "settled", "ready"),
  [
    ("running", "Up 3 seconds", False, False),
    ("running", "Up 12 seconds", True, True),
    ("running", "Up 2 seconds (healthy)", False, True),
    ("running", "Up 30 seconds (unhealthy)", True, False),
    ("exited", "Exited (0) 1 second ago", False, True),
    ("exited", "Exited (1) 1 second ago", True, False),
    ("restarting", "Restarting (1) 1 second ago", True, False),
  ],
)
def test_what_counts_as_ready(state, status, settled, ready):
  unit = KelsoRunUnitStatus("a", "main", "id", "a-main-1", state, status)
  assert updown._unit_ready(unit, settled) is ready
