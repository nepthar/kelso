"""The recovery phrase, the keys derived from it, and rekeying onto one."""

import base64
import hashlib
import json

import pytest
from cryptography.fernet import Fernet, InvalidToken

from kelso.lib import recovery
from kelso.lib.config import load_config_file
from kelso.lib.kelso import KelsoCtx
from kelso.lib.logtab import LogTab
from tests.conftest import TEST_SEED
from tests.test_restore import _snapshot

BASIC = "io.p2net.basic-features"
LEGACY_KEY = "0" * 64
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


def _make_legacy(kelso_env) -> None:
  """A root from before recovery phrases: a bare `master_key` record."""
  kelso_env.master_keyfile.unlink()
  LogTab(kelso_env.master_keyfile).write("master_key", LEGACY_KEY)


def _answer_quiz(monkeypatch, seed: bytes) -> str:
  """Fix the phrase and the words asked for; return what to type."""
  monkeypatch.setattr(recovery, "new_entropy", lambda: seed)
  monkeypatch.setattr(recovery, "quiz_positions", lambda count=2: [3, 11])
  words = recovery.phrase(seed)
  return f"{words[2]}\n{words[10]}\n"


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
  assert keyfile.retired == ()
  assert not keyfile.confirmed
  assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_a_new_seed_retires_the_old_key_and_needs_confirming(tmp_path):
  path = tmp_path / "master.key"
  LogTab(path).write("master_key", LEGACY_KEY)
  recovery.write_seed(path, TEST_SEED)
  recovery.mark_confirmed(path)
  recovery.write_seed(path, NEW_SEED)

  keyfile = recovery.read_keyfile(path)
  assert keyfile.master_key == recovery.master_key_from(NEW_SEED)
  assert keyfile.retired == (recovery.master_key_from(TEST_SEED), LEGACY_KEY)
  assert not keyfile.confirmed


def test_rewrite_keeps_every_line_but_the_values_it_changes(tmp_path):
  path = tmp_path / "t.logtab"
  table = LogTab(path)
  table.write("a", "1")
  table.write("b", "2")
  table.delete("a")
  table.write("a", "3")
  before = path.read_text().splitlines()

  assert table.rewrite(lambda key, value: value + "x" if key == "a" else value) == 2
  after = path.read_text().splitlines()
  assert len(after) == len(before)
  for old, new in zip(before, after, strict=True):
    if "\ta\t" in old and "\tset\t" in old:
      assert new == old + "x"
    else:
      assert new == old
  assert table.load()["a"].value == "3x"


# --- rekey ------------------------------------------------------------------


def test_rekey_moves_every_secret_onto_the_new_key(kelso_env, monkeypatch):
  _make_legacy(kelso_env)
  assert kelso_env.run("load", BASIC).returncode == 0
  assert kelso_env.run("system", "secret", "--set", "api=hunter2").returncode == 0
  ctx = _ctx(kelso_env)
  _, admin_pass = ctx.app_store(BASIC).get_config("admin_pass")
  app_log = ctx.config.app_config_path(BASIC)
  assert _secret_blobs(app_log)

  typed = _answer_quiz(monkeypatch, NEW_SEED)
  result = kelso_env.run("system", "rekey", input=typed)
  assert result.returncode == 0, result.stderr
  # Shown on stdout, never through logging into the activity log.
  assert recovery.phrase(NEW_SEED)[0] in result.stdout
  assert recovery.phrase(NEW_SEED)[0] not in result.stderr

  ctx = _ctx(kelso_env)
  assert ctx.config.master_key == recovery.master_key_from(NEW_SEED)
  assert ctx.config.retired_master_keys == (LEGACY_KEY,)
  assert ctx.app_store(BASIC).get_config("admin_pass") == (True, admin_pass)
  assert ctx.kelso_db.get_secret("api") == "hunter2"

  # Nothing on file, history included, is readable under the old key alone.
  old = _fernet(LEGACY_KEY)
  for blob in _secret_blobs(app_log):
    with pytest.raises(InvalidToken):
      old.decrypt(blob.encode())

  keyfile = recovery.read_keyfile(kelso_env.master_keyfile)
  assert keyfile.confirmed
  doctor = kelso_env.run("system", "doctor")
  assert "recovery phrase" not in doctor.stdout + doctor.stderr
  assert "predates" not in doctor.stdout + doctor.stderr


def test_a_wrong_answer_changes_nothing(kelso_env, monkeypatch):
  before = kelso_env.master_keyfile.read_text()
  _answer_quiz(monkeypatch, NEW_SEED)
  result = kelso_env.run("system", "rekey", input="wrong\nwrong\nwrong\nwrong\n")
  assert result.returncode != 0
  assert "nothing was changed" in result.stderr
  assert kelso_env.master_keyfile.read_text() == before


def test_rekey_can_adopt_a_phrase_you_already_have(kelso_env):
  words = " ".join(recovery.phrase(NEW_SEED))
  result = kelso_env.run("system", "rekey", "--phrase", input=words + "\n")
  assert result.returncode == 0, result.stderr
  assert _ctx(kelso_env).config.master_key == recovery.master_key_from(NEW_SEED)


def test_a_snapshot_from_before_a_rekey_restores(kelso_env, monkeypatch):
  _make_legacy(kelso_env)
  assert kelso_env.run("load", BASIC).returncode == 0
  _, admin_pass = _ctx(kelso_env).app_store(BASIC).get_config("admin_pass")
  name = _snapshot(kelso_env, BASIC, "before")

  typed = _answer_quiz(monkeypatch, NEW_SEED)
  assert kelso_env.run("system", "rekey", input=typed).returncode == 0

  restored = kelso_env.run("snapshot", "restore", BASIC, name, "-y")
  assert restored.returncode == 0, restored.stderr
  ctx = _ctx(kelso_env)
  assert ctx.app_store(BASIC).get_config("admin_pass") == (True, admin_pass)
  # And restoring moved it onto the current key.
  for blob in _secret_blobs(ctx.config.app_config_path(BASIC)):
    _fernet(recovery.master_key_from(NEW_SEED)).decrypt(blob.encode())


def test_decrypt_reads_a_value_written_under_a_retired_key(kelso_env, monkeypatch):
  _make_legacy(kelso_env)
  blob = _fernet(LEGACY_KEY).encrypt(b"old").decode()
  typed = _answer_quiz(monkeypatch, NEW_SEED)
  assert kelso_env.run("system", "rekey", input=typed).returncode == 0
  result = kelso_env.run("system", "decrypt", input=blob)
  assert result.returncode == 0, result.stderr
  assert result.stdout.strip() == "old"


# --- showing and confirming the phrase -----------------------------------------


def test_recovery_phrase_shows_the_words_on_file(kelso_env):
  result = kelso_env.run("system", "recovery-phrase")
  assert result.returncode == 0, result.stderr
  for word in recovery.phrase(TEST_SEED):
    assert word in result.stdout


def test_a_root_without_a_seed_is_told_to_rekey(kelso_env):
  _make_legacy(kelso_env)
  result = kelso_env.run("system", "recovery-phrase")
  assert result.returncode != 0
  assert "kelso system rekey" in result.stderr
  doctor = kelso_env.run("system", "doctor")
  assert "kelso system rekey" in doctor.stdout + doctor.stderr


def test_confirming_the_phrase_quiets_doctor(kelso_env, monkeypatch):
  kelso_env.master_keyfile.unlink()
  recovery.write_seed(kelso_env.master_keyfile, TEST_SEED)
  doctor = kelso_env.run("system", "doctor")
  assert "--confirm" in doctor.stdout + doctor.stderr

  monkeypatch.setattr(recovery, "quiz_positions", lambda count=2: [1, 12])
  words = recovery.phrase(TEST_SEED)
  confirmed = kelso_env.run(
    "system", "recovery-phrase", "--confirm", input=f"{words[0]}\n{words[11]}\n"
  )
  assert confirmed.returncode == 0, confirmed.stderr
  doctor = kelso_env.run("system", "doctor")
  assert "--confirm" not in doctor.stdout + doctor.stderr
