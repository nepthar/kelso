"""`kelso update`: pull, stop, back up, re-load from the source, start again."""

import json

APP = "upd-app"

MANIFEST = """\
[app]
version = "{version}"

[run.main]
image = "alpine:{tag}"
cmd   = ["sleep", "infinity"]
"""


def write_bundle(kelso_env, version: str, tag: str = "latest") -> None:
  bundle = kelso_env.local_repo / f"{APP}.klso"
  bundle.mkdir(exist_ok=True)
  (bundle / "manifest.toml").write_text(MANIFEST.format(version=version, tag=tag))


def docker_calls(kelso_env) -> list[list[str]]:
  lines = kelso_env.docker_log.read_text().splitlines()
  return [json.loads(line)["args"] for line in lines]


def test_update_pulls_first_then_backs_up_and_restarts(kelso_env):
  write_bundle(kelso_env, "1.0")
  assert kelso_env.run("start", APP).returncode == 0
  write_bundle(kelso_env, "1.1", tag="3.20")
  assert "1.0 (1.1 available)" in kelso_env.run("ps").stdout

  updated = kelso_env.run("update", APP)
  assert updated.returncode == 0, updated.stderr
  assert "Updated upd-app from 1.0 to 1.1" in updated.stdout
  assert "Restarted upd-app" in updated.stdout

  calls = docker_calls(kelso_env)
  pull = calls.index(["pull", "alpine:3.20"])
  down = next(i for i, c in enumerate(calls) if c[:2] == ["compose", "down"])
  assert pull < down
  assert calls[-1][:3] == ["compose", "up", "-d"]

  listed = kelso_env.run("backup", "list", APP).stdout.splitlines()
  (row,) = [line.split() for line in listed[2:]]
  assert row[2:4] == ["1.0", "update"]
  assert f"backup {row[0]} holds the version it replaced" in updated.stdout
  assert kelso_env.run("ps").stdout.count("available") == 0


def test_update_keeps_one_update_backup(kelso_env):
  write_bundle(kelso_env, "1.0")
  assert kelso_env.run("load", APP).returncode == 0
  for version in ("1.1", "1.2"):
    write_bundle(kelso_env, version)
    assert kelso_env.run("update", APP, "-y").returncode == 0
  listed = kelso_env.run("backup", "list", APP).stdout.splitlines()
  assert [line.split()[2] for line in listed[2:]] == ["1.1"]


def test_update_no_backup_skips_it(kelso_env):
  write_bundle(kelso_env, "1.0")
  assert kelso_env.run("load", APP).returncode == 0
  write_bundle(kelso_env, "1.1")
  updated = kelso_env.run("update", APP, "-y", "--no-backup")
  assert updated.returncode == 0, updated.stderr
  assert "holds the version" not in updated.stdout


def test_update_refuses_a_source_that_no_longer_parses(kelso_env):
  write_bundle(kelso_env, "1.0")
  assert kelso_env.run("start", APP).returncode == 0
  (kelso_env.local_repo / f"{APP}.klso" / "manifest.toml").write_text("[app\n")

  refused = kelso_env.run("update", APP)
  assert refused.returncode == 1
  assert "Nothing changed" in refused.stderr
  assert not (kelso_env.root / "backups" / "config").exists()
  assert not any(c[:2] == ["compose", "down"] for c in docker_calls(kelso_env))
  assert "invalid" in kelso_env.run("repo", "list").stdout


def test_an_update_to_the_same_version_says_it_reloaded(kelso_env):
  write_bundle(kelso_env, "1.0")
  assert kelso_env.run("load", APP).returncode == 0
  write_bundle(kelso_env, "1.0", tag="3.20")
  updated = kelso_env.run("update", APP, "-y")
  assert updated.returncode == 0, updated.stderr
  assert "Reloaded upd-app at 1.0: its source changed without a new" in updated.stdout
