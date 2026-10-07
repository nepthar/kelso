"""Backups: what one holds, putting an app back from one, and what is kept.

Restic runs in a container; the fake docker hands those runs to a fake restic
that keeps each backup as plain files (tests/fakerestic.py), so what these
tests pin down is what kelso asks of restic and what survives the round trip.
Real restic is exercised by the `docker`-marked tests in test_restic.py.
"""

import json
import os
from datetime import datetime, timedelta

import pytest
import yaml

from kelso.lib import backup as backup_lib
from kelso.lib.apps import read_last_app_action
from kelso.lib.backup import Backup, BackupKeep, backup_due, expired
from kelso.lib.config import load_config_file
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle.rootfs import ROOTFS_IMAGE, run_as_root
from kelso.lib.restic import RESTIC_IMAGE

BASIC = "io.p2net.basic-features"


def _ctx(kelso_env) -> KelsoCtx:
  return KelsoCtx(load_config_file(kelso_env.config))


def _backups(kelso_env, app: str | None = None) -> list[Backup]:
  ctx = _ctx(kelso_env)
  return backup_lib.backups(backup_lib.repository(ctx), app)


def _back_up(kelso_env, app: str) -> str:
  """Back `app` up by hand and return the backup's id."""
  taken = kelso_env.run("backup", "run", app)
  assert taken.returncode == 0, taken.stderr
  return taken.stdout.split("Backup ")[1].split()[0]


def _runs(kelso_env, image: str) -> list[list[str]]:
  """Every `docker run` of `image` the fake recorded, oldest first."""
  return [
    entry["args"]
    for entry in map(json.loads, kelso_env.docker_log.read_text().splitlines())
    if entry["args"][0] == "run" and image in entry["args"]
  ]


def _mounts(args: list[str]) -> list[str]:
  return [args[i + 1] for i, arg in enumerate(args) if arg == "-v"]


def _ps_row(kelso_env, app: str) -> list[str]:
  out = kelso_env.run("ps").stdout
  return next(line for line in out.splitlines() if line.startswith(app)).split()


# --- taking backups -------------------------------------------------------------


def test_a_backup_holds_data_config_and_bundle_at_fixed_paths(kelso_env):
  assert kelso_env.run("load", BASIC).returncode == 0
  volume = kelso_env.volumes_root / "data" / BASIC / "config"
  (volume / "db.txt").write_text("v1")
  _back_up(kelso_env, BASIC)

  backup = next(args for args in _runs(kelso_env, RESTIC_IMAGE) if "backup" in args)
  base = f"/kelso/app/{BASIC}"
  mounts = _mounts(backup)
  assert f"{volume.resolve()}:{base}/data/config:ro" in mounts
  bundle = kelso_env.run_root / BASIC / "app_bundle"
  assert f"{bundle.resolve()}:{base}/app_bundle:ro" in mounts
  # The password reaches restic through the environment, never the arguments.
  assert "-e" in backup and "RESTIC_PASSWORD" in backup
  assert not any(arg.startswith("RESTIC_PASSWORD=") for arg in backup)

  [taken] = _backups(kelso_env, BASIC)
  assert taken.reason == "manual"
  assert taken.kelso_id == _ctx(kelso_env).kelso_db.kelso_id()
  assert len(taken.kelso_id) == 8


def test_the_first_backup_says_the_phrase_is_the_only_way_in(kelso_env):
  assert kelso_env.run("load", BASIC).returncode == 0
  first = kelso_env.run("backup", "run")
  assert first.returncode == 0, first.stderr
  assert "kelso system recovery-phrase" in first.stdout
  assert "Backed up kelso's own state" in first.stdout

  second = kelso_env.run("backup", "run")
  assert "kelso system recovery-phrase" not in second.stdout


def test_a_running_app_is_stopped_for_its_backup_and_started_again(kelso_env):
  app_id = "ports-demo"
  assert kelso_env.run("start", app_id).returncode == 0
  _back_up(kelso_env, app_id)
  assert _ps_row(kelso_env, app_id)[1] == "running"

  calls = [
    json.loads(line)["args"] for line in kelso_env.docker_log.read_text().splitlines()
  ]
  backed_up = next(
    i for i, args in enumerate(calls) if RESTIC_IMAGE in args and "backup" in args
  )
  compose = [args[:2] for args in calls if args[0] == "compose"]
  before = [args[:2] for args in calls[:backed_up] if args[0] == "compose"]
  after = [args[:2] for args in calls[backed_up:] if args[0] == "compose"]
  assert before[-1] in (["compose", "down"], ["compose", "stop"])
  assert after[0] == ["compose", "up"]
  assert compose


def test_an_unloaded_app_with_kept_data_is_backed_up(kelso_env):
  assert kelso_env.run("load", BASIC).returncode == 0
  (kelso_env.volumes_root / "data" / BASIC / "config" / "db.txt").write_text("kept")
  assert kelso_env.run("unload", BASIC, "-y").returncode == 0

  assert kelso_env.run("backup", "run").returncode == 0
  [backup] = _backups(kelso_env, BASIC)
  assert "data" in backup.snapshots


# --- bulk volumes ----------------------------------------------------------------


BULK_APP = """\
[app]
version = "1.0.0"

[volumes]
db     = { kind = "data" }
movies = { kind = "bulk" }

[run.main]
image   = "alpine:latest"
volumes = { db = "/db", movies = "/movies" }
"""


def test_bulk_volumes_are_only_backed_up_when_turned_on(kelso_env):
  bundle = kelso_env.local_repo / "bulk-demo.klso"
  bundle.mkdir()
  (bundle / "manifest.toml").write_text(BULK_APP)
  assert kelso_env.run("load", "bulk-demo").returncode == 0
  movie = kelso_env.volumes_root / "bulk" / "bulk-demo" / "movies" / "film.mkv"
  movie.write_text("frames")

  _back_up(kelso_env, "bulk-demo")
  [first] = _backups(kelso_env, "bulk-demo")
  assert set(first.snapshots) == {"data"}

  listed = kelso_env.run("config", "bulk-demo")
  assert "movies" in listed.stdout and "off" in listed.stdout
  turned = kelso_env.run("config", "bulk-demo", "--backup", "movies=on")
  assert turned.returncode == 0, turned.stderr
  # Not something a running app has to pick up.
  assert _ps_row(kelso_env, "bulk-demo")[2] == "ready"

  backup_id = _back_up(kelso_env, "bulk-demo")
  second = next(b for b in _backups(kelso_env, "bulk-demo") if b.id == backup_id)
  assert set(second.snapshots) == {"data", "bulk"}

  movie.write_text("re-encoded")
  restored = kelso_env.run("restore", "bulk-demo", backup_id, "-y")
  assert restored.returncode == 0, restored.stderr
  assert movie.read_text() == "frames"


def test_only_bulk_volumes_can_be_toggled(kelso_env):
  assert kelso_env.run("load", BASIC).returncode == 0
  refused = kelso_env.run("config", BASIC, "--backup", "config=off")
  assert refused.returncode == 1
  assert "always backed up" in refused.stderr

  missing = kelso_env.run("config", BASIC, "--backup", "nope=on")
  assert missing.returncode == 1
  assert "No volume nope" in missing.stderr


# --- where backups go ---------------------------------------------------------------


def test_a_dangling_backups_link_is_refused(kelso_env, tmp_path):
  backups = kelso_env.root / "backups"
  if backups.exists():
    backups.rmdir()
  backups.symlink_to(tmp_path / "nas" / "kelso")
  assert kelso_env.run("load", BASIC).returncode == 0

  refused = kelso_env.run("backup", "run")
  assert refused.returncode == 1
  assert "not there" in refused.stderr
  # Nothing was written in its place on the local disk.
  assert backups.is_symlink()


def test_a_linked_destination_holds_the_repository(kelso_env, tmp_path):
  elsewhere = tmp_path / "nas" / "kelso"
  elsewhere.mkdir(parents=True)
  backups = kelso_env.root / "backups"
  if backups.exists():
    backups.rmdir()
  backups.symlink_to(elsewhere)
  assert kelso_env.run("load", BASIC).returncode == 0
  _back_up(kelso_env, BASIC)
  assert (elsewhere / "config").is_file()


def test_require_mount_refuses_a_destination_on_the_kelso_disk(kelso_env):
  config = kelso_env.config
  config.write_text(config.read_text() + "\n[backup]\nrequire_mount = true\n")
  assert kelso_env.run("load", BASIC).returncode == 0
  refused = kelso_env.run("backup", "run")
  assert refused.returncode == 1
  assert "require_mount" in refused.stderr


def test_a_bad_schedule_is_a_config_error(kelso_env):
  config = kelso_env.config
  config.write_text(config.read_text() + '\n[backup]\nschedule = "every night"\n')
  result = kelso_env.run("ps")
  assert result.returncode == 1
  assert "[backup] schedule" in result.stderr


# --- restoring ----------------------------------------------------------------------


def test_restore_brings_a_data_volume_back(kelso_env):
  assert kelso_env.run("load", BASIC).returncode == 0
  volume = kelso_env.volumes_root / "data" / BASIC / "config"
  (volume / "db.txt").write_text("v1")
  (volume / "current").symlink_to("db.txt")
  backup_id = _back_up(kelso_env, BASIC)

  (volume / "db.txt").write_text("v2")
  (volume / "stray.txt").write_text("written since")

  restored = kelso_env.run("restore", BASIC, backup_id, "-y")
  assert restored.returncode == 0, restored.stderr
  assert (volume / "db.txt").read_text() == "v1"
  assert not (volume / "stray.txt").exists()
  assert os.readlink(volume / "current") == "db.txt"

  # One container removes the live volume and copies the backup's in, so a
  # container that cannot start has deleted nothing.
  put_back = next(
    args for args in _runs(kelso_env, ROOTFS_IMAGE) if args[-1].startswith("set -e;")
  )
  data_root = (kelso_env.volumes_root / "data" / BASIC).resolve()
  assert put_back[-1].startswith(f"set -e; rm -rf -- {data_root / 'config'}; cp -a -- ")
  # And the scratch space restic restored into is gone afterwards.
  assert not (kelso_env.root / "var" / "temp" / "restore" / BASIC).exists()


def test_restore_rebuilds_a_removed_app(kelso_env):
  app_id = "ports-demo"
  assert kelso_env.run("start", app_id).returncode == 0
  backup_id = _back_up(kelso_env, app_id)

  kelso_env.run("stop", app_id)
  assert kelso_env.run("rm", app_id, "-y").returncode == 0
  assert not (kelso_env.run_root / app_id).exists()

  restored = kelso_env.run("restore", app_id, backup_id, "-y")
  assert restored.returncode == 0, restored.stderr
  assert kelso_env.app_logtab(app_id).is_file()

  # compose.yml is regenerated rather than copied back, so the host ports it
  # publishes are the ones kelsodb has just re-allocated.
  compose = yaml.safe_load((kelso_env.run_root / app_id / "compose.yml").read_text())
  assert compose["services"]["main"]["ports"] == ["41000:8080", "9000:80"]
  assert read_last_app_action(app_id, _ctx(kelso_env)) == "restored"
  assert kelso_env.run("start", app_id).returncode == 0


def test_restore_replaces_config_and_backs_up_what_was_there(kelso_env):
  app_id = "routes-demo"
  assert kelso_env.run("start", app_id, "--set", "subdomain=photos").returncode == 0
  backup_id = _back_up(kelso_env, app_id)
  assert kelso_env.run("stop", app_id).returncode == 0
  assert kelso_env.run("config", app_id, "--set", "subdomain=albums").returncode == 0

  restored = kelso_env.run("restore", app_id, backup_id, "-y")
  assert restored.returncode == 0, restored.stderr
  shown = kelso_env.run("config", app_id, "--get", "subdomain")
  assert shown.stdout.strip() == "photos"
  assert [b.reason for b in _backups(kelso_env, app_id)] == ["manual", "pre-restore"]


def test_restore_no_backup_takes_none_first(kelso_env):
  app_id = "ports-demo"
  assert kelso_env.run("load", app_id).returncode == 0
  backup_id = _back_up(kelso_env, app_id)
  restored = kelso_env.run("restore", app_id, backup_id, "-y", "--no-backup")
  assert restored.returncode == 0, restored.stderr
  assert [b.reason for b in _backups(kelso_env, app_id)] == ["manual"]


def test_undoing_the_last_restore_takes_no_new_pre_restore_backup(kelso_env):
  app_id = "ports-demo"
  assert kelso_env.run("load", app_id).returncode == 0
  backup_id = _back_up(kelso_env, app_id)
  assert kelso_env.run("restore", app_id, backup_id, "-y").returncode == 0
  [pre] = [b for b in _backups(kelso_env, app_id) if b.reason == "pre-restore"]

  undo = kelso_env.run("restore", app_id, pre.id, "-y")
  assert undo.returncode == 0, undo.stderr
  assert [b.id for b in _backups(kelso_env, app_id) if b.reason == "pre-restore"] == [
    pre.id
  ]


def test_restore_refuses_while_containers_are_running(kelso_env):
  app_id = "ports-demo"
  assert kelso_env.run("start", app_id).returncode == 0
  backup_id = _back_up(kelso_env, app_id)
  refused = kelso_env.run("restore", app_id, backup_id, "-y")
  assert refused.returncode == 1
  assert f"kelso stop {app_id}" in refused.stderr


def test_restore_names_the_backups_it_could_have_used(kelso_env):
  app_id = "ports-demo"
  assert kelso_env.run("load", app_id).returncode == 0
  backup_id = _back_up(kelso_env, app_id)
  missing = kelso_env.run("restore", app_id, "19990101-000000", "-y")
  assert missing.returncode == 1
  assert backup_id in missing.stderr


def test_restore_declined_at_the_prompt_changes_nothing(kelso_env):
  app_id = "ports-demo"
  assert kelso_env.run("load", app_id).returncode == 0
  backup_id = _back_up(kelso_env, app_id)
  marker = kelso_env.run_root / app_id / "app_bundle" / "marker.txt"
  marker.write_text("still here")
  declined = kelso_env.run("restore", app_id, backup_id, input="n\n")
  assert declined.returncode == 0, declined.stderr
  assert "Nothing restored." in declined.stdout
  assert marker.exists()


def test_backup_list_shows_newest_first(kelso_env):
  assert kelso_env.run("load", "ports-demo").returncode == 0
  assert kelso_env.run("load", BASIC).returncode == 0
  assert kelso_env.run("backup", "run").returncode == 0
  listed = kelso_env.run("backup", "list")
  assert listed.returncode == 0, listed.stderr
  apps = [line.split()[1] for line in listed.stdout.splitlines()[2:]]
  assert sorted(apps) == sorted([BASIC, "ports-demo"])


# --- update -----------------------------------------------------------------------


def test_update_without_a_backup_disk_changes_nothing_unless_told(kelso_env, tmp_path):
  assert kelso_env.run("load", BASIC).returncode == 0
  backups = kelso_env.root / "backups"
  if backups.exists():
    backups.rmdir()
  backups.symlink_to(tmp_path / "gone")
  refused = kelso_env.run("update", BASIC, "-y")
  assert refused.returncode == 1
  assert "--no-backup" in refused.stderr


# --- retention ---------------------------------------------------------------------


def _b(name: str, reason: str, time: str) -> Backup:
  """A backup whose run id sorts by `time`, as real ones do; `name` is kept
  in the data snapshot id so a test can say which it means."""
  run = time.replace("-", "").replace(":", "").replace("T", "-").rstrip("Z")
  return Backup(None, run, time, reason, "", "", {"data": name})


def _names(found: list[Backup]) -> set[str]:
  return {b.snapshots["data"] for b in found}


def _days_ago(n: int, hour: int = 3) -> str:
  moment = datetime(2026, 10, 31, hour) - timedelta(days=n)
  return moment.isoformat() + "Z"


def test_scheduled_backups_thin_out_by_day_week_and_month():
  scheduled = [_b(f"s{n}", "scheduled", _days_ago(n)) for n in range(60)]
  gone = {b.snapshots["data"] for b in expired(scheduled, BackupKeep())}
  kept = sorted(
    {b.snapshots["data"] for b in scheduled} - gone, key=lambda r: int(r[1:])
  )
  # 3 daily: today and the two days before. 2 weekly: the newest of this week
  # and of last week. 1 monthly: the newest of this month.
  this_week_newest = "s0"
  last_week_newest = next(
    f"s{n}"
    for n in range(60)
    if datetime.fromisoformat(_days_ago(n)).isocalendar().week
    == datetime.fromisoformat(_days_ago(0)).isocalendar().week - 1
  )
  assert kept == sorted(
    {"s0", "s1", "s2", this_week_newest, last_week_newest}, key=lambda r: int(r[1:])
  )


def test_manual_backups_keep_the_newest_five():
  manual = [_b(f"m{n}", "manual", _days_ago(n)) for n in range(8)]
  kept = _names(manual) - _names(expired(manual, BackupKeep()))
  assert kept == {"m0", "m1", "m2", "m3", "m4"}


def test_update_and_pre_restore_backups_never_push_out_manual_ones():
  found = [
    *(_b(f"m{n}", "manual", _days_ago(n + 10)) for n in range(5)),
    *(_b(f"u{n}", "update", _days_ago(n)) for n in range(3)),
    *(_b(f"p{n}", "pre-restore", _days_ago(n)) for n in range(3)),
  ]
  kept = _names(found) - _names(expired(found, BackupKeep()))
  assert kept == {"m0", "m1", "m2", "m3", "m4", "u0", "p0"}


def test_a_run_forgets_what_the_policy_no_longer_wants(kelso_env):
  config = kelso_env.config
  config.write_text(config.read_text() + "\n[backup]\nkeep = { manual = 2 }\n")
  assert kelso_env.run("load", "ports-demo").returncode == 0
  taken = [_back_up(kelso_env, "ports-demo") for _ in range(3)]
  assert [b.id for b in _backups(kelso_env, "ports-demo")] == taken[1:]


def test_a_second_backup_in_the_same_second_does_nothing(kelso_env, monkeypatch):
  assert kelso_env.run("load", "ports-demo").returncode == 0
  frozen = backup_lib.now()
  monkeypatch.setattr(backup_lib, "now", lambda: frozen)
  _back_up(kelso_env, "ports-demo")
  again = kelso_env.run("backup", "run", "ports-demo")
  assert again.returncode == 1
  assert "within this second; no action taken" in again.stderr
  assert len(_backups(kelso_env, "ports-demo")) == 1


# --- the schedule -------------------------------------------------------------------


def test_the_schedule_is_due_once_its_time_passes(kelso_env):
  ctx = _ctx(kelso_env)
  started = datetime(2026, 10, 7, 12, 0)
  assert not backup_due(ctx, since=started, now=datetime(2026, 10, 8, 2, 59))
  assert backup_due(ctx, since=started, now=datetime(2026, 10, 8, 3, 0))


def test_a_scheduled_run_resets_the_clock(kelso_env):
  ctx = _ctx(kelso_env)
  ctx.kelso_db.record_backup_run(
    {
      "run": "x",
      "time": datetime(2026, 10, 8, 3, 0).astimezone().isoformat(),
      "reason": "scheduled",
      "everything": True,
      "backed_up": [],
      "failed": [],
    }
  )
  started = datetime(2026, 10, 1)
  assert not backup_due(ctx, since=started, now=datetime(2026, 10, 8, 23, 0))
  assert backup_due(ctx, since=started, now=datetime(2026, 10, 9, 3, 0))


# --- what does and does not need a container ------------------------------------


def test_a_colon_in_a_path_is_refused(tmp_path):
  """`-v host:guest` is colon-delimited, so a colon would silently bind
  something other than what was asked for."""
  odd = tmp_path / "we:ird"
  odd.mkdir()
  with pytest.raises(ValueError) as raised:
    run_as_root("do the thing", "true", [odd])
  assert "colon" in str(raised.value)


def test_doctor_notes_backups_on_kelsos_own_disk(kelso_env):
  quiet = kelso_env.run("system", "doctor")
  assert "same disk as kelso" not in quiet.stdout + quiet.stderr

  (kelso_env.root / "backups").mkdir(exist_ok=True)
  noted = kelso_env.run("system", "doctor")
  assert "same disk as kelso" in noted.stdout + noted.stderr
  assert noted.returncode == 0


def test_the_config_form_turns_a_bulk_volume_on(kelso_env):
  from kelso.lib.configflow import ConfigResponse
  from kelso.lib.configflow.app import app_config_request, apply_app_config

  bundle = kelso_env.local_repo / "bulk-demo.klso"
  bundle.mkdir()
  (bundle / "manifest.toml").write_text(BULK_APP)
  assert kelso_env.run("load", "bulk-demo").returncode == 0
  ctx = _ctx(kelso_env)
  spec = ctx.loaded_spec("bulk-demo")
  assert app_config_request(spec, ctx).field("backup.movies").value == "off"

  written = apply_app_config(spec, ConfigResponse({"backup.movies": "on"}), ctx)
  assert written == ["backup.movies"]
  assert ctx.app_store("bulk-demo").backed_up_bulk() == {"movies"}


def test_restic_hands_the_repository_back_to_kelsos_user(kelso_env):
  assert kelso_env.run("load", "ports-demo").returncode == 0
  _back_up(kelso_env, "ports-demo")
  for args in _runs(kelso_env, RESTIC_IMAGE):
    assert f"KELSO_UID={os.getuid()}" in args
    assert f"KELSO_GID={os.getgid()}" in args
    script = args[args.index("-c") + 1]
    assert script.startswith('restic "$@"; status=$?;')
    assert 'chown -R "$KELSO_UID:$KELSO_GID" /repo /cache' in script
    assert script.endswith("exit $status")


def test_a_backup_says_what_restic_is_doing(kelso_env):
  assert kelso_env.run("load", "ports-demo").returncode == 0
  taken = kelso_env.run("backup", "run", "ports-demo")
  assert "restic: back up ports-demo" in taken.stderr
  assert "files," in taken.stderr and " new of " in taken.stderr
  # The containers kelso runs as root are its own business.
  assert "throwaway" not in taken.stderr
