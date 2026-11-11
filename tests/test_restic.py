"""Restic in its pinned container, against a real docker daemon."""

import os

import pytest

from kelso.lib import recovery
from kelso.lib.restic import Restic, ResticError, _first_error
from tests.conftest import TEST_SEED


def test_the_backup_password_is_its_own_key():
  password = recovery.backup_password_from(TEST_SEED)
  assert password == recovery.backup_password_from(TEST_SEED)
  assert password != recovery.master_key_from(TEST_SEED)


@pytest.mark.docker
def test_a_backup_round_trips(tmp_path):
  data = tmp_path / "data"
  data.mkdir()
  (data / "db.txt").write_text("v1")
  config = tmp_path / "app.logtab"
  config.write_text("cfg")
  sources = {data: "/kelso/app/demo/data/vol", config: "/kelso/app/demo/config.logtab"}
  restic = Restic(tmp_path / "repo", "pw", tmp_path / "cache")
  assert restic.init()
  assert not restic.init()  # Already there: nothing to do.

  first = restic.backup(sources, {"app": "demo", "run": "a"}, what="back up demo")
  (data / "db.txt").write_text("v2")
  second = restic.backup(sources, {"app": "demo", "run": "b"}, what="back up demo")
  found = restic.snapshots({"app": "demo"})
  assert [s.tags["run"] for s in found] == ["a", "b"]
  assert found[0].paths == (
    "/kelso/app/demo/config.logtab",
    "/kelso/app/demo/data/vol",
  )
  assert restic.snapshots({"app": "other"}) == []

  # Restored at the fixed paths, under the target.
  restic.restore(first, tmp_path / "out", what="restore demo")
  out = tmp_path / "out" / "kelso" / "app" / "demo"
  assert (out / "data" / "vol" / "db.txt").read_text() == "v1"
  assert (out / "config.logtab").read_text() == "cfg"

  restic.forget([second], what="forget")
  restic.prune()
  assert [s.id for s in restic.snapshots()] == [first]

  # Everything restic wrote, as root, is left to the user kelso runs as.
  owners = {
    (p.stat().st_uid, p.stat().st_gid)
    for root in (tmp_path / "repo", tmp_path / "cache")
    for p in [root, *root.rglob("*")]
  }
  assert owners == {(os.getuid(), os.getgid())}

  restic.change_password("pw2", tmp_path / "scratch")
  assert len(Restic(tmp_path / "repo", "pw2", tmp_path / "cache").snapshots()) == 1
  with pytest.raises(ResticError, match="list backups"):
    Restic(tmp_path / "repo", "pw", tmp_path / "cache").snapshots()


@pytest.mark.parametrize(
  "raw, utc",
  [
    ("2026-10-07T17:39:53.123456789+02:00", "2026-10-07T15:39:53.123456+00:00"),
    ("2026-10-07T17:39:53.123456789-07:00", "2026-10-08T00:39:53.123456+00:00"),
    ("2026-10-07T17:39:53.5Z", "2026-10-07T17:39:53.500000+00:00"),
    ("2026-10-07T17:39:53Z", "2026-10-07T17:39:53+00:00"),
  ],
)
def test_restic_times_come_back_in_utc(raw, utc):
  from kelso.lib.restic import _timestamp

  assert _timestamp(raw) == utc[:19] + "Z"


def test_a_restic_failure_is_one_line_however_much_restic_said():
  one = (
    '{"message_type":"error","error":{"message":"failed to save /a: '
    'input/output error"},"during":"archival","item":"/a"}'
  )
  stderr = "\n".join([one] * 400 + ['{"message_type":"exit_error","code":3}'])
  assert _first_error(stderr) == (
    "failed to save /a: input/output error (and 399 more errors)"
  )
  assert _first_error("Fatal: wrong password\n") == "Fatal: wrong password"
  assert _first_error("") == ""
