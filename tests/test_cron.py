"""Cron: schedules, the manifest's `[cron]`, listing, and ticks."""

import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from filelock import FileLock

import kelso.lib.lifecycle.run
from kelso.cli.cron import in_words
from kelso.daemon.api import create_app
from kelso.jobs import JobRunner
from kelso.lib import activity
from kelso.lib.config import load_config
from kelso.lib.cronexpr import CronSchedule
from kelso.lib.docker import DockerTimeout
from kelso.lib.kelso import KelsoCtx, write_lock_holder
from kelso.lib.lifecycle.cron import cron_lock_path, cron_runs

APP = "cron-demo"

MANIFEST = """\
[app]
version      = "0.1.0"
display_name = "Cron demo"

[run.main]
image = "alpine:latest"
cmd   = ["/bin/sh", "-c", "sleep 100"]

[commands]
hello = {{ cmd = "echo hi" }}

[cron]
{cron}
"""

EVERY_MINUTE = 'every-minute = { schedule = "* * * * *", command = "hello" }'


def ctx() -> KelsoCtx:
  return KelsoCtx(load_config())


def a_cron_app(kelso_env, cron: str = EVERY_MINUTE) -> None:
  bundle = kelso_env.local_repo / f"{APP}.klso"
  bundle.mkdir()
  (bundle / "manifest.toml").write_text(MANIFEST.format(cron=cron))


def last_ran_long_ago() -> None:
  ctx().activity_log.write(f"apps/{APP}/cron/every-minute", "2000-01-01T00:00:00Z")


def listed(kelso_env) -> list[str]:
  shown = kelso_env.run("cron")
  assert shown.returncode == 0, shown.stderr
  return [line for line in shown.stdout.splitlines() if line.startswith(APP)]


@pytest.mark.parametrize(
  "schedule, after, expected",
  [
    ("0 3 * * *", datetime(2026, 10, 2, 14, 7), datetime(2026, 10, 3, 3, 0)),
    ("*/15 * * * *", datetime(2026, 10, 2, 14, 7), datetime(2026, 10, 2, 14, 15)),
    ("30 9 * * 1-5", datetime(2026, 10, 2, 14, 7), datetime(2026, 10, 5, 9, 30)),
    # Both day fields restricted: either one matching is enough.
    ("0 12 13 * 5", datetime(2026, 10, 2, 14, 7), datetime(2026, 10, 9, 12, 0)),
    ("0 0 29 2 *", datetime(2026, 10, 2, 14, 7), datetime(2028, 2, 29, 0, 0)),
    ("0 0 * * 7", datetime(2026, 10, 2, 14, 7), datetime(2026, 10, 4, 0, 0)),
    ("0 3 * * *", datetime(2026, 10, 3, 3, 0), datetime(2026, 10, 4, 3, 0)),
  ],
)
def test_next_after(schedule, after, expected):
  assert CronSchedule.parse(schedule).next_after(after) == expected


@pytest.mark.parametrize(
  "schedule, message",
  [
    ("* * *", "needs 5 fields"),
    ("60 * * * *", "outside 0-59"),
    ("x * * * *", "not a number"),
    ("*/0 * * * *", "at least 1"),
  ],
)
def test_bad_schedules_are_refused(schedule, message):
  with pytest.raises(ValueError, match=message):
    CronSchedule.parse(schedule)


@pytest.mark.parametrize(
  "seconds, words",
  [
    (0, "due"),
    (1, "in 1 second"),
    (120, "in 120 seconds"),
    (121, "in 2 minutes"),
    (2 * 3600, "in 120 minutes"),
    (3 * 3600, "in 3 hours"),
    (3 * 86400, "in 3 days"),
    (61 * 86400, "in 2 months"),
    (3 * 365 * 86400, "in 3 years"),
  ],
)
def test_in_words_switches_units_past_two_of_the_next(seconds, words):
  assert in_words(seconds) == words


@pytest.mark.parametrize(
  "cron, message",
  [
    ('nope = { schedule = "* * * * *", command = "missing" }', "not declared"),
    ('nope = { schedule = "0 0 30 2 *", command = "hello" }', "never matches"),
    ('nope = { schedule = "* * * * *", command = "hello", args = "\'" }', "args"),
    (
      'nope = { schedule = "* * * * *", command = "hello", timeout = 1801 }',
      "background process",
    ),
  ],
)
def test_a_cron_entry_is_checked_against_the_manifest(kelso_env, cron, message):
  a_cron_app(kelso_env, cron)
  loaded = kelso_env.run("load", APP)
  assert loaded.returncode == 1
  assert message in loaded.stderr


def test_a_stopped_apps_job_is_listed_as_skipped_and_not_run(kelso_env):
  a_cron_app(kelso_env)
  assert kelso_env.run("load", APP).returncode == 0
  last_ran_long_ago()

  [row] = listed(kelso_env)
  assert row.split() == [APP, "every-minute", "due", "(app", "stopped)"]

  ticked = kelso_env.run("cron", "tick")
  assert ticked.stdout == "No cron jobs ran.\n"
  assert activity.list_runs(ctx(), verb="cron") == []


def test_a_tick_runs_a_due_job_and_records_it(kelso_env):
  a_cron_app(kelso_env)
  assert kelso_env.run("start", APP).returncode == 0
  last_ran_long_ago()

  ticked = kelso_env.run("cron", "tick")
  assert ticked.returncode == 0, ticked.stderr
  assert ticked.stdout == f"Ran {APP} every-minute\n"

  calls = [json.loads(line)["args"] for line in kelso_env.docker_log.open()]
  assert ["compose", "exec", "main", "/bin/sh", "-c", 'echo hi "$@"', "hello"] in calls
  [run] = activity.list_runs(ctx(), verb="cron")
  assert run["app_id"] == APP
  assert run["args"] == {"job": "every-minute", "command": "hello"}
  assert run["status"] == "ok"
  assert run["started_by"] == "cli"

  # Ran just now, so the next run is under a minute away rather than due.
  [row] = listed(kelso_env)
  assert "second" in row


def test_a_job_never_run_counts_from_when_its_app_was_loaded(kelso_env):
  a_cron_app(kelso_env, 'nightly = { schedule = "0 3 * * *", command = "hello" }')
  assert kelso_env.run("start", APP).returncode == 0

  assert kelso_env.run("cron", "tick").stdout == "No cron jobs ran.\n"
  [row] = listed(kelso_env)
  assert row.split()[:2] == [APP, "nightly"]
  assert row.split()[2] == "in"


def test_a_tick_refuses_and_names_whoever_holds_the_cron_lock(kelso_env):
  a_cron_app(kelso_env)
  assert kelso_env.run("start", APP).returncode == 0
  last_ran_long_ago()

  path = cron_lock_path(ctx())
  with FileLock(path):
    write_lock_holder(path, "cron tick (kelsod)")
    ticked = kelso_env.run("cron", "tick")
  assert ticked.returncode == 1
  assert "A cron tick is already running" in ticked.stderr
  assert "Held by `kelso cron tick (kelsod)`" in ticked.stderr
  assert activity.list_runs(ctx(), verb="cron") == []


def test_cron_over_the_api(kelso_env):
  a_cron_app(kelso_env)
  assert kelso_env.run("start", APP).returncode == 0
  client = TestClient(create_app(ctx, JobRunner(ctx)))

  [job] = client.get("/cron").json()["cron"]
  assert job["app_id"] == APP
  assert job["job"] == "every-minute"
  assert job["command"] == "hello"
  assert job["status"] == "scheduled"
  assert job["next_at"].endswith("+00:00")

  last_ran_long_ago()
  assert kelso_env.run("cron", "tick").returncode == 0
  [run] = client.get("/activity?verb=cron").json()["activity"]
  assert run["args"]["job"] == "every-minute"


# --- command timeouts --------------------------------------------------------


def a_timed_app(kelso_env, command: str, cron: str = EVERY_MINUTE) -> None:
  """The cron app, with `hello` declared as `command` instead."""
  a_cron_app(kelso_env, cron)
  manifest = kelso_env.local_repo / f"{APP}.klso" / "manifest.toml"
  manifest.write_text(
    manifest.read_text().replace('hello = { cmd = "echo hi" }', command)
  )


def test_a_command_timeout_is_bounded(kelso_env):
  a_timed_app(kelso_env, 'hello = { cmd = "echo hi", timeout = 1801 }')
  loaded = kelso_env.run("load", APP)
  assert loaded.returncode == 1
  assert "background process" in loaded.stderr


@pytest.mark.parametrize(
  "cron, expected",
  [
    (EVERY_MINUTE, 42),
    ('every-minute = { schedule = "* * * * *", command = "hello", timeout = 7 }', 7),
  ],
)
def test_a_cron_job_takes_its_commands_timeout_unless_it_sets_one(
  kelso_env, cron, expected
):
  a_timed_app(kelso_env, 'hello = { cmd = "echo hi", timeout = 42 }', cron)
  assert kelso_env.run("load", APP).returncode == 0
  [run] = cron_runs(ctx())
  assert run.timeout == expected


def test_kelso_cmd_waits_only_as_long_as_the_command_allows(kelso_env, monkeypatch):
  a_timed_app(kelso_env, 'hello = { cmd = "echo hi", timeout = 42 }')
  assert kelso_env.run("start", APP).returncode == 0
  waited = []

  def docker(args, **kwargs):
    waited.append(kwargs.get("timeout"))
    raise DockerTimeout("docker compose exec was still running after 42 seconds")

  monkeypatch.setattr(kelso.lib.lifecycle.run, "docker_run_command", docker)
  ran = kelso_env.run("cmd", APP, "hello")

  assert waited == [42]
  assert ran.returncode == 1
  assert "raise its `timeout` in [commands.hello]" in ran.stderr
