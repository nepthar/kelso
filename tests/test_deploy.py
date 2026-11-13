"""`kelso deploy`: the commands it runs on the box, with ssh and rsync faked."""

import subprocess
from types import SimpleNamespace

import pytest

import kelso.cli.deploy

APP = "dep-app"
ROOT = "/srv/kelso"


@pytest.fixture
def remote(kelso_env, monkeypatch):
  """Records each command; `source` is what the box says the app is loaded from."""
  box = SimpleNamespace(calls=[], cwds=[], source="")

  def run(argv, **kwargs):
    box.calls.append(argv)
    box.cwds.append(kwargs.get("cwd"))
    code, out = 0, ""
    if argv[-1] == "kelso root":
      out = f"{ROOT}\n"
    elif argv[-1].startswith("kelso app source"):
      out = f"{box.source}\n"
    return subprocess.CompletedProcess(argv, code, out, "")

  monkeypatch.setattr(kelso.cli.deploy, "subprocess", SimpleNamespace(run=run))
  bundle = kelso_env.root / f"{APP}.klso"
  bundle.mkdir()
  (bundle / "manifest.toml").write_text(
    '[app]\nversion = "1.0"\n\n[run.main]\nimage = "alpine"\n\n'
    '[volumes.src]\nkind = "app"\n\n[volumes.conf]\nkind = "app"\nsrc = "etc/app.toml"\n'
  )
  (bundle / "src").mkdir()
  (bundle / "etc").mkdir()
  (bundle / "etc" / "app.toml").touch()
  (bundle / "node_modules").mkdir()
  box.bundle = bundle
  return box


def test_first_deploy_copies_then_loads_and_starts(kelso_env, remote):
  done = kelso_env.run("deploy", str(remote.bundle), "box")
  assert done.returncode == 0, done.stderr
  rsync = remote.calls[2]
  assert rsync[0] == "rsync"
  assert rsync[-5:] == [
    "--relative",
    "manifest.toml",
    "src",
    "etc/app.toml",
    f"box:{ROOT}/repos/local/{APP}.klso/",
  ]
  assert remote.cwds[2] == remote.bundle
  assert remote.calls[1][-1] == f"kelso app source {APP}"
  assert remote.calls[3][-1] == (f"kelso load {APP}@local --yes && kelso start {APP}")


def test_deploy_to_a_loaded_app_updates_it(kelso_env, remote):
  remote.source = "repo local"
  done = kelso_env.run("deploy", str(remote.bundle), "box", "--no-backup")
  assert done.returncode == 0, done.stderr
  assert remote.calls[-1][-1] == f"kelso update {APP} --yes --no-backup"


def test_deploy_refuses_a_volume_whose_files_are_missing(kelso_env, remote):
  (remote.bundle / "src").rmdir()
  done = kelso_env.run("deploy", str(remote.bundle), "box")
  assert done.returncode == 1
  assert "[volumes.src] names src" in done.stderr
  assert remote.calls == []


def test_deploy_refuses_an_app_loaded_from_elsewhere(kelso_env, remote):
  remote.source = "repo demos"
  done = kelso_env.run("deploy", str(remote.bundle), "box")
  assert done.returncode == 1
  assert f"box already has {APP} loaded from repo demos" in done.stderr
  assert len(remote.calls) == 2


def test_app_source_prints_the_repo_of_a_loaded_app_only(kelso_env):
  assert kelso_env.run("app", "source", "upd-app").stdout == ""
  bundle = kelso_env.local_repo / "upd-app.klso"
  bundle.mkdir()
  (bundle / "manifest.toml").write_text(
    '[app]\nversion = "1.0"\n\n[run.main]\nimage = "alpine"\n'
  )
  assert kelso_env.run("load", "upd-app").returncode == 0
  assert kelso_env.run("app", "source", "upd-app").stdout == "repo local\n"
