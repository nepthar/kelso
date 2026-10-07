"""Recovering a whole kelso: `init --with-phrase`, `system purge`, and
`restore <backups directory>`."""

import os

from kelso.lib import recovery
from kelso.lib.config import load_config_file
from kelso.lib.kelso import KelsoCtx
from tests.conftest import TEST_SEED

BASIC = "io.p2net.basic-features"
WORDS = " ".join(recovery.phrase(TEST_SEED))
OTHER_WORDS = " ".join(recovery.phrase(bytes(range(50, 66))))


def run_at(kelso_env, root, *args, **kwargs):
  """Run one kelso command with KELSO_ROOT pointing at `root`."""
  saved = os.environ["KELSO_ROOT"]
  os.environ["KELSO_ROOT"] = str(root)
  try:
    return kelso_env.run(*args, **kwargs)
  finally:
    os.environ["KELSO_ROOT"] = saved


def _ctx_at(root) -> KelsoCtx:
  return KelsoCtx(load_config_file(root / "config.toml"))


def _init_with(kelso_env, root, words: str):
  return run_at(
    kelso_env, root, "init", "--yes", "--no-mirror", "--with-phrase", input=words + "\n"
  )


def _backed_up_root(kelso_env):
  """An old machine: two apps, data, a secret, and a full backup of it all."""
  assert kelso_env.run("start", BASIC, "--set", "admin_user=alice").returncode == 0
  (kelso_env.volumes_root / "data" / BASIC / "config" / "db.txt").write_text("rows")
  assert kelso_env.run("load", "ports-demo").returncode == 0
  taken = kelso_env.run("backup", "run")
  assert taken.returncode == 0, taken.stderr
  return kelso_env.root / "backups"


# --- init --with-phrase ---------------------------------------------------------


def test_init_with_phrase_keeps_the_phrase_it_is_given(kelso_env, tmp_path):
  root = tmp_path / "new"
  made = _init_with(kelso_env, root, WORDS)
  assert made.returncode == 0, made.stderr
  assert recovery.read_keyfile(root / "conf" / "master.key").seed == TEST_SEED
  # It tells you what to do next, and does not print the phrase back.
  assert "kelso restore <backups directory>" in made.stdout
  assert recovery.phrase(TEST_SEED)[0] not in made.stdout.split("Recovery phrase")[-1]


def test_init_with_a_wrong_phrase_makes_nothing(kelso_env, tmp_path):
  root = tmp_path / "new"
  words = WORDS.split()
  refused = _init_with(kelso_env, root, " ".join([words[1], words[0], *words[2:]]))
  assert refused.returncode == 1
  assert "checksum" in refused.stderr
  assert not root.exists()


# --- purge ------------------------------------------------------------------


def test_purge_clears_every_app_and_keeps_the_rest(kelso_env, tmp_path):
  _backed_up_root(kelso_env)
  bulk_disk = tmp_path / "big-disk"
  bulk_disk.mkdir()
  bulk = kelso_env.volumes_root / "bulk"
  if bulk.exists():
    bulk.rmdir()
  bulk.symlink_to(bulk_disk)

  declined = kelso_env.run("system", "purge", input="no\n")
  assert "Nothing purged." in declined.stdout
  assert kelso_env.app_logtab(BASIC).is_file()

  purged = kelso_env.run("system", "purge", input="purge\n")
  assert purged.returncode == 0, purged.stderr
  assert f"Purged {BASIC}" in purged.stdout
  assert "Purged ports-demo" in purged.stdout
  assert not kelso_env.app_logtab(BASIC).exists()
  assert not (kelso_env.run_root / BASIC).exists()
  assert not (kelso_env.volumes_root / "data" / BASIC).exists()
  # What a restore needs, and what you set up, are left alone.
  assert (kelso_env.root / "config.toml").is_file()
  assert recovery.read_keyfile(kelso_env.master_keyfile).seed == TEST_SEED
  assert (kelso_env.root / "backups" / "config").is_file()
  assert bulk.is_symlink() and bulk.resolve() == bulk_disk.resolve()
  assert kelso_env.run("backup", "list").returncode == 0


# --- restoring everything -------------------------------------------------------


def test_restore_everything_refuses_a_kelso_that_holds_apps(kelso_env):
  backups = _backed_up_root(kelso_env)
  refused = kelso_env.run("restore", str(backups))
  assert refused.returncode == 1
  assert "kelso system purge" in refused.stderr
  assert "kelso restore <app> <backup>" in refused.stderr


def test_restore_under_another_phrase_says_to_rekey(kelso_env, tmp_path):
  backups = _backed_up_root(kelso_env)
  root = tmp_path / "new"
  assert _init_with(kelso_env, root, OTHER_WORDS).returncode == 0
  refused = run_at(kelso_env, root, "restore", str(backups))
  assert refused.returncode == 1
  assert "different recovery phrase" in refused.stderr
  assert "kelso system rekey --phrase" in refused.stderr


def test_a_new_machine_comes_back_from_the_old_ones_backups(kelso_env, tmp_path):
  backups = _backed_up_root(kelso_env)
  old = KelsoCtx(load_config_file(kelso_env.config))
  kelso_id = old.kelso_db.kelso_id()
  _, admin_pass = old.app_store(BASIC).get_config("admin_pass")
  # The old machine is gone. (The fake docker is one daemon both roots see.)
  assert kelso_env.run("stop", BASIC).returncode == 0

  root = tmp_path / "new"
  assert _init_with(kelso_env, root, WORDS).returncode == 0
  # Between init and restore: this machine keeps its data somewhere else.
  data_disk = tmp_path / "fast-disk"
  data_disk.mkdir()
  (root / "volumes" / "data").rmdir()
  (root / "volumes" / "data").symlink_to(data_disk)

  restored = run_at(kelso_env, root, "restore", str(backups), input="y\n")
  assert restored.returncode == 0, restored.stderr
  # What the old machine's root looked like, next to this one's.
  layout = restored.stdout.split("Restored kelso")[0]
  data_row = next(
    line for line in layout.splitlines() if line.startswith("volumes/data")
  )
  assert "(a directory in the root)" in data_row
  assert str(data_disk) in data_row
  assert f"Restored {BASIC}" in restored.stdout
  assert "Restored ports-demo" in restored.stdout
  assert "kelso up" in restored.stdout

  ctx = _ctx_at(root)
  assert ctx.kelso_db.kelso_id() == kelso_id
  assert ctx.app_store(BASIC).get_config("admin_pass") == (True, admin_pass)
  assert ctx.app_store(BASIC).get_config("admin_user") == (False, "alice")
  assert (data_disk / BASIC / "config" / "db.txt").read_text() == "rows"
  assert ctx.is_loaded(BASIC) and ctx.is_loaded("ports-demo")
  # The next backup adds to the ones it came from.
  assert (root / "backups").resolve() == backups.resolve()
  listed = run_at(kelso_env, root, "backup", "list")
  assert BASIC in listed.stdout

  # Restored stopped; a start brings it up.
  started = run_at(kelso_env, root, "start", BASIC)
  assert started.returncode == 0, started.stderr


# --- the layout a state backup records ---------------------------------------------


def test_a_state_backup_records_where_the_roots_links_pointed(kelso_env, tmp_path):
  import tomllib

  from kelso.lib import backup as backup_lib

  big = tmp_path / "big-disk"
  big.mkdir()
  bulk = kelso_env.volumes_root / "bulk"
  if bulk.exists():
    bulk.rmdir()
  bulk.symlink_to(big)
  assert kelso_env.run("load", BASIC).returncode == 0
  assert kelso_env.run("backup", "run").returncode == 0

  ctx = KelsoCtx(load_config_file(kelso_env.config))
  restic = backup_lib.repository(ctx)
  [state] = backup_lib.state_backups(restic)
  recorded = tomllib.loads(restic.dump(state.id, backup_lib.LINKS_FILE, what="read"))
  assert recorded["kelso_root"] == str(kelso_env.root)
  assert recorded["links"]["volumes/bulk"] == str(big)
  assert recorded["links"]["volumes/data"] == ""
  assert "backups" in recorded["links"]


def test_a_layout_that_differs_asks_first(kelso_env, tmp_path):
  backups = _backed_up_root(kelso_env)
  kelso_env.run("stop", BASIC)
  root = tmp_path / "new"
  assert _init_with(kelso_env, root, WORDS).returncode == 0
  elsewhere = tmp_path / "elsewhere"
  elsewhere.mkdir()
  (root / "volumes" / "temp").rmdir()
  (root / "volumes" / "temp").symlink_to(elsewhere)

  declined = run_at(kelso_env, root, "restore", str(backups), input="n\n")
  assert declined.returncode == 0, declined.stderr
  assert "Nothing restored. Re-link" in declined.stdout
  assert not (root / "conf" / "apps").exists() or not any(
    (root / "conf" / "apps").iterdir()
  )

  told = run_at(kelso_env, root, "restore", str(backups), "-y")
  assert told.returncode == 0, told.stderr
  assert "Restore anyway?" not in told.stdout


def test_the_same_layout_restores_without_asking(kelso_env, tmp_path):
  backups = _backed_up_root(kelso_env)
  kelso_env.run("stop", BASIC)
  root = tmp_path / "new"
  assert _init_with(kelso_env, root, WORDS).returncode == 0
  restored = run_at(kelso_env, root, "restore", str(backups))
  assert restored.returncode == 0, restored.stderr
  assert "Restore anyway?" not in restored.stdout
  assert "same here" in restored.stdout


def test_restore_fetches_the_repos_the_restored_config_names(
  kelso_env, tmp_path, monkeypatch
):
  from kelso.lib import repo as repo_lib

  config = kelso_env.config
  config.write_text(
    config.read_text()
    + '\n[repo.demos]\nurl = "github://nepthar/kelso/backups/demo-apps"\n'
    + '\n[repo.gone]\nurl = "github://nepthar/kelso/nowhere/apps"\n'
  )
  backups = _backed_up_root(kelso_env)
  kelso_env.run("stop", BASIC)

  fetched = []

  def mirror(repo, ctx):
    if repo.name == "gone":
      raise ValueError("no such ref")
    fetched.append((repo.name, repo.describe()))

  monkeypatch.setattr(repo_lib, "mirror", mirror)
  root = tmp_path / "new"
  assert _init_with(kelso_env, root, WORDS).returncode == 0
  restored = run_at(kelso_env, root, "restore", str(backups), "-y")
  assert restored.returncode == 0, restored.stderr
  # The branch the restored config names, not whatever init fetched.
  assert fetched == [("demos", "github://nepthar/kelso/backups/demo-apps")]
  assert "Fetched repo demos" in restored.stdout
  assert "Could not fetch repo gone: no such ref" in restored.stdout
  assert f"Restored {BASIC}" in restored.stdout


def test_restoring_from_the_roots_own_backups_says_so(kelso_env):
  backups = _backed_up_root(kelso_env)
  kelso_env.run("stop", BASIC)
  assert kelso_env.run("system", "purge", "-y").returncode == 0
  restored = kelso_env.run("restore", str(backups), "-y")
  assert restored.returncode == 0, restored.stderr
  assert f"New backups keep going to {backups.resolve()}." in restored.stdout
