"""Alerts: unattended runs that failed, until the operator acknowledges them."""

import pytest

import kelso.lib.logtab
from kelso.lib import activity, alerts
from kelso.lib.activity import BY_CLI, BY_CRON, BY_KELSOD, Activity
from kelso.lib.config import load_config
from kelso.lib.kelso import KelsoCtx


@pytest.fixture
def ctx(kelso_env):
  cfg = load_config()
  assert cfg is not None
  return KelsoCtx(cfg)


@pytest.fixture
def clock(monkeypatch):
  """Set the activity log's clock: `clock("09:00:00")`."""
  return lambda hms: monkeypatch.setattr(
    kelso.lib.logtab, "now_ts", lambda: f"2026-10-18T{hms}Z"
  )


def _fail(ctx, verb, started_by, error="it broke"):
  with pytest.raises(RuntimeError), Activity(ctx, verb, started_by=started_by):
    raise RuntimeError(error)


def test_only_unattended_failures_alert_until_acknowledged(kelso_env, ctx, clock):
  clock("09:00:00")
  _fail(ctx, "up", BY_KELSOD, "app x did not start")
  _fail(ctx, "start", BY_CLI)
  with Activity(ctx, "backup", started_by=BY_KELSOD):
    pass

  listed = kelso_env.run("system", "alerts")
  assert listed.returncode == 0, listed.stderr
  assert "up failed: app x did not start" in listed.stdout
  assert [a["verb"] for a in alerts.alerts(ctx)] == ["up"]
  assert "Alerts     1 new" in kelso_env.run("status").stdout
  assert "There are unacknowledged alerts" in kelso_env.run("system", "doctor").stdout

  clock("09:05:00")
  acked = kelso_env.run("system", "alerts", "ack")
  assert acked.stdout == "Acknowledged 1 alert(s).\n"
  assert kelso_env.run("system", "alerts").stdout == "No new alerts.\n"

  clock("09:10:00")
  _fail(ctx, "cron", BY_CRON)
  (alert,) = alerts.alerts(ctx)
  assert (alert["verb"], alert["ts"]) == ("cron", "2026-10-18T09:10:00Z")


def test_a_long_error_is_cut_in_the_index_but_whole_in_the_run_file(ctx):
  _fail(ctx, "up", BY_KELSOD, "x" * 2000)
  (run,) = activity.list_runs(ctx)
  assert run["error"] == "x" * activity.ERROR_CHARS
  assert "x" * 2000 in activity.read_run_log(ctx, run["log"])


def test_an_alert_raised_by_hand_shows_its_text(kelso_env):
  text = "This is a new alert. This is the text of the alert"
  assert kelso_env.run("system", "alerts", "--new", text).returncode == 0
  listed = kelso_env.run("system", "alerts").stdout
  assert listed.splitlines()[0].endswith(f"  {text}")
