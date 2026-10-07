"""The recovery phrase, the keys derived from it, and rekeying onto one."""

import base64
import hashlib
import importlib
import json
import shutil
import tarfile
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet, InvalidToken

from kelso.lib import logtab, recovery
from kelso.lib.config import load_config_file
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle.snapshot import snapshot_archive
from kelso.lib.logtab import LogTab
from tests.conftest import TEST_SEED
from tests.test_restore import _snapshot

# The package exports a `snapshot` function that hides the module's name.
snapshot_lifecycle = importlib.import_module("kelso.lib.lifecycle.snapshot")

BASIC = "io.p2net.basic-features"
OLD_KEY = recovery.master_key_from(TEST_SEED)
NEW_SEED = bytes(range(100, 116))


def _fernet(master_key: str) -> Fernet:
  digest = hashlib.sha256(master_key.encode()).digest()
  return Fernet(base64.urlsafe_b64encode(digest))


def _ctx(kelso_env) -> KelsoCtx:
  return KelsoCtx(load_config_file(kelso_env.config))


def _secret_blobs(path) -> list[str]:
  """Every secret ciphertext in an app logtab, history included."""
  blobs = []
  for _key, entry in LogTab(path).history(prefix="config/"):
    value = json.loads(entry.value)
    if value.get("secret"):
      blobs.append(value["value"])
  return blobs


def _fix_phrase(monkeypatch, seed: bytes) -> None:
  """Make the next new phrase this one."""
  monkeypatch.setattr(recovery, "new_entropy", lambda: seed)


# --- the phrase -------------------------------------------------------------


def test_a_phrase_round_trips_to_its_entropy():
  words = recovery.phrase(NEW_SEED)
  assert len(words) == 12
  assert recovery.entropy_from_phrase(words) == NEW_SEED
  assert recovery.entropy_from_phrase([w.upper() for w in words]) == NEW_SEED


@pytest.mark.parametrize(
  "mangle, message",
  [
    (lambda w: w[:11], "is 12 words"),
    (lambda w: [*w[:11], "kelsoish"], "Not a recovery phrase word"),
    (lambda w: [w[1], w[0], *w[2:]], "checksum"),
  ],
)
def test_a_wrong_phrase_is_refused(mangle, message):
  words = recovery.phrase(NEW_SEED)
  with pytest.raises(ValueError, match=message):
    recovery.entropy_from_phrase(mangle(words))


def test_each_label_derives_its_own_key():
  assert recovery.derive(NEW_SEED, "a") == recovery.derive(NEW_SEED, "a")
  assert recovery.derive(NEW_SEED, "a") != recovery.derive(NEW_SEED, "b")
  assert recovery.master_key_from(NEW_SEED) != recovery.master_key_from(TEST_SEED)


def test_the_master_key_comes_from_the_seed_not_a_stored_key(tmp_path):
  path = tmp_path / "master.key"
  recovery.write_seed(path, NEW_SEED)
  keyfile = recovery.read_keyfile(path)
  assert keyfile.master_key == recovery.master_key_from(NEW_SEED)
  assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_the_key_file_answers_for_any_moment_in_its_history(tmp_path):
  path = tmp_path / "master.key"
  table = LogTab(path)
  table.write_entry(path, "seed", "set", TEST_SEED.hex())
  # Hand-written timestamps, so the test does not depend on the clock.
  lines = path.read_text().splitlines()
  lines[-1] = "2026-01-01T00:00:00Z" + lines[-1][lines[-1].index("\t") :]
  path.write_text(
    "\n".join([*lines, f"2026-06-01T00:00:00Z\tset\tseed\t{NEW_SEED.hex()}"]) + "\n"
  )

  assert recovery.master_key_at(path, "2025-12-31T00:00:00Z") == ""
  assert recovery.master_key_at(path, "2026-03-01T00:00:00Z") == OLD_KEY
  assert recovery.master_key_at(
    path, "2026-06-01T00:00:00Z"
  ) == recovery.master_key_from(NEW_SEED)
  assert recovery.read_keyfile(path).master_key == recovery.master_key_from(NEW_SEED)


# --- rekey ------------------------------------------------------------------


def test_rekey_moves_every_secret_onto_the_new_key(kelso_env, monkeypatch):
  assert kelso_env.run("load", BASIC).returncode == 0
  assert kelso_env.run("system", "secret", "--set", "api=hunter2").returncode == 0
  ctx = _ctx(kelso_env)
  _, admin_pass = ctx.app_store(BASIC).get_config("admin_pass")
  app_log = ctx.config.app_config_path(BASIC)
  assert _secret_blobs(app_log)

  _fix_phrase(monkeypatch, NEW_SEED)
  result = kelso_env.run("system", "rekey")
  assert result.returncode == 0, result.stderr
  # Shown on stdout, never through logging into the activity log.
  assert recovery.phrase(NEW_SEED)[0] in result.stdout
  assert recovery.phrase(NEW_SEED)[0] not in result.stderr

  ctx = _ctx(kelso_env)
  assert ctx.config.master_key == recovery.master_key_from(NEW_SEED)
  assert ctx.app_store(BASIC).get_config("admin_pass") == (True, admin_pass)
  assert ctx.kelso_db.get_secret("api") == "hunter2"

  # Appended, not rewritten: the old record is still there as written, and the
  # one now in effect is readable under the new key alone.
  blobs = _secret_blobs(app_log)
  assert _fernet(OLD_KEY).decrypt(blobs[0].encode())
  new = _fernet(recovery.master_key_from(NEW_SEED))
  assert new.decrypt(blobs[-1].encode()).decode() == admin_pass
  with pytest.raises(InvalidToken):
    _fernet(OLD_KEY).decrypt(blobs[-1].encode())

  doctor = kelso_env.run("system", "doctor")
  assert "recovery phrase" not in doctor.stdout + doctor.stderr


def test_rekey_can_adopt_a_phrase_you_already_have(kelso_env):
  words = " ".join(recovery.phrase(NEW_SEED))
  result = kelso_env.run("system", "rekey", "--phrase", input=words + "\n")
  assert result.returncode == 0, result.stderr
  assert _ctx(kelso_env).config.master_key == recovery.master_key_from(NEW_SEED)


def _ticking_clock(monkeypatch) -> None:
  """Each timestamp a second after the last. Logtab timestamps are to the
  second, and restore reads the key file as of the snapshot's."""
  # From the real now, so it follows what the fixture already wrote.
  now = datetime.now(UTC).replace(microsecond=0)

  def tick() -> str:
    nonlocal now
    now += timedelta(seconds=1)
    return now.isoformat().replace("+00:00", "Z")

  monkeypatch.setattr(logtab, "now_ts", tick)
  monkeypatch.setattr(snapshot_lifecycle, "now_ts", tick)


def test_a_snapshot_from_before_a_rekey_restores(kelso_env, monkeypatch):
  _ticking_clock(monkeypatch)
  assert kelso_env.run("load", BASIC).returncode == 0
  _, admin_pass = _ctx(kelso_env).app_store(BASIC).get_config("admin_pass")
  name = _snapshot(kelso_env, BASIC, "before")

  _fix_phrase(monkeypatch, NEW_SEED)
  assert kelso_env.run("system", "rekey").returncode == 0

  restored = kelso_env.run("snapshot", "restore", BASIC, name, "-y")
  assert restored.returncode == 0, restored.stderr
  ctx = _ctx(kelso_env)
  assert ctx.app_store(BASIC).get_config("admin_pass") == (True, admin_pass)
  # And restoring appended it under the current key.
  latest = _secret_blobs(ctx.config.app_config_path(BASIC))[-1]
  _fernet(recovery.master_key_from(NEW_SEED)).decrypt(latest.encode())


# --- showing the phrase -----------------------------------------


def test_recovery_phrase_shows_the_words_on_file(kelso_env):
  result = kelso_env.run("system", "recovery-phrase")
  assert result.returncode == 0, result.stderr
  for word in recovery.phrase(TEST_SEED):
    assert word in result.stdout


def test_a_root_without_a_seed_is_told_to_rekey(kelso_env):
  kelso_env.master_keyfile.write_text("")
  result = kelso_env.run("system", "recovery-phrase")
  assert result.returncode != 0
  assert "kelso system rekey" in result.stderr
  doctor = kelso_env.run("system", "doctor")
  assert "kelso system rekey" in doctor.stdout + doctor.stderr

  # Secrets are refused rather than stored in the clear.
  refused = kelso_env.run("config", BASIC, "--set", "admin_pass=x")
  assert refused.returncode != 0
  assert "No recovery phrase" in refused.stderr


def test_rekey_on_a_root_without_a_seed_gives_it_one(kelso_env, monkeypatch):
  kelso_env.master_keyfile.write_text("")
  _fix_phrase(monkeypatch, NEW_SEED)
  result = kelso_env.run("system", "rekey")
  assert result.returncode == 0, result.stderr
  assert _ctx(kelso_env).config.master_key == recovery.master_key_from(NEW_SEED)


def test_rekey_leaves_a_running_app_current(kelso_env, monkeypatch):
  """The plaintext did not change, so there is nothing to restart for."""
  assert kelso_env.run("start", BASIC, "--set", "admin_user=alice").returncode == 0
  _fix_phrase(monkeypatch, NEW_SEED)
  assert kelso_env.run("system", "rekey").returncode == 0
  ps = kelso_env.run("ps").stdout
  row = next(line for line in ps.splitlines() if line.startswith(BASIC)).split()
  assert row[2] == "ready"


def test_rekey_keeps_a_change_that_was_already_pending(kelso_env, monkeypatch):
  assert kelso_env.run("start", BASIC, "--set", "admin_user=alice").returncode == 0
  assert kelso_env.run("config", BASIC, "--set", "admin_user=bob").returncode == 0
  _fix_phrase(monkeypatch, NEW_SEED)
  assert kelso_env.run("system", "rekey").returncode == 0
  ps = kelso_env.run("ps").stdout
  row = next(line for line in ps.splitlines() if line.startswith(BASIC)).split()
  assert row[2] == "restart"


# --- what counts as a change ---------------------------------------------------


def _store(tmp_path):
  from kelso.lib.crypto import FernetCryptoEngine
  from kelso.lib.store import AppStore

  path = tmp_path / "app.logtab"
  return path, AppStore.from_path(path, FernetCryptoEngine(OLD_KEY))


def test_setting_what_is_on_file_records_no_change(tmp_path):
  path, store = _store(tmp_path)
  store.set_config("pw", True, "a")
  store.set_bind("media", "nas")
  store.set_route_assignment("main", "web")
  store.set_meta("started_at", "now")
  store.set_config("pw", True, "a")
  store.set_bind("media", "nas")
  store.set_route_assignment("main", "web")
  from kelso.lib.observations import changes_since_start

  assert not changes_since_start(path).any


@pytest.mark.parametrize(
  "change, kind",
  [
    (lambda s: s.set_config("pw", True, "b"), "config"),
    (lambda s: s.set_bind("media", "other"), "config"),
    (lambda s: s.set_config("subdomain", False, "lab"), "routes"),
    (lambda s: s.set_route_assignment("main", "cloud"), "routes"),
  ],
)
def test_each_real_change_records_its_kind(tmp_path, change, kind):
  from kelso.lib.observations import changes_since_start

  path, store = _store(tmp_path)
  store.set_meta("started_at", "now")
  change(store)
  pending = changes_since_start(path)
  assert getattr(pending, kind)


def test_a_snapshot_that_does_not_say_when_it_was_taken_is_refused(kelso_env):
  assert kelso_env.run("load", BASIC).returncode == 0
  name = _snapshot(kelso_env, BASIC, "undated")
  root = _ctx(kelso_env).config.snapshot_root
  archive = snapshot_archive(root, BASIC, name)

  # Rebuild the archive without its `taken_at` line.
  with tarfile.open(archive) as tar:
    tar.extractall(archive.parent, filter="data")
  inner = archive.parent / name / "snapshot.toml"
  lines = inner.read_text().splitlines()
  inner.write_text("\n".join(x for x in lines if not x.startswith("taken_at")) + "\n")
  with tarfile.open(archive, "w:gz") as tar:
    tar.add(archive.parent / name, arcname=name)
  shutil.rmtree(archive.parent / name)

  restored = kelso_env.run("snapshot", "restore", BASIC, name, "-y")
  assert restored.returncode != 0
  assert "when it was taken" in restored.stderr
