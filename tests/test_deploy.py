"""`kelso deploy`: the commands it runs on the box, with ssh and rsync faked."""

import json
import subprocess
from types import SimpleNamespace

import pytest

import kelso.cli.deploy
from kelso.lib.configflow import ConfigField, ConfigRequest, request_from_view
from kelso.lib.views import config_request_view

APP = "dep-app"
ROOT = "/srv/kelso"


@pytest.fixture
def remote(kelso_env, monkeypatch):
  """Records each command; `source` is the bundle the box has the app loaded from."""
  box = SimpleNamespace(calls=[], cwds=[], inputs=[], source="", fields=[])

  def run(argv, **kwargs):
    if argv[0] == "ssh":
      argv = [*argv[:-1], argv[-1].removeprefix("PATH=~/.local/bin:$PATH; ")]
    box.calls.append(argv)
    box.cwds.append(kwargs.get("cwd"))
    box.inputs.append(kwargs.get("input"))
    code, out = 0, ""
    if argv[-1] == "kelso root":
      out = f"{ROOT}\n"
    elif argv[-1] == f"kelso status {APP} --json":
      loaded = {"app": APP, "loaded": True, "bundle": box.source, "repo": None}
      code, out = (0, json.dumps(loaded)) if box.source else (1, "")
    elif argv[-1].endswith("--json"):
      request = ConfigRequest(APP, tuple(box.fields))
      out = json.dumps(config_request_view(request))
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
  assert remote.calls[1][-1] == f"kelso status {APP} --json"
  assert remote.calls[3][-1] == f"kelso config {APP} --json"
  assert remote.calls[4][-1] == f"kelso start {APP}@local --allow-compose"
  assert len(remote.calls) == 5


NEEDS = [
  ConfigField("admin_pass", secret=True, desc="Password for the admin"),
  ConfigField("volume.media", desc="Where the media is", choices=("media", "nas")),
]


def test_deploy_stops_and_lists_what_the_app_needs(kelso_env, remote):
  remote.fields = NEEDS
  done = kelso_env.run("deploy", str(remote.bundle), "box")
  assert done.returncode == 1
  assert f"{APP} was copied to box but not started. It needs:" in done.stderr
  assert "admin_pass (secret): Password for the admin" in done.stderr
  assert "volume.media: Where the media is One of: media, nas." in done.stderr
  assert "--set NAME=VALUE" in done.stderr
  assert remote.calls[-1][-1] == f"kelso config {APP} --json"


def test_deploy_answers_what_the_app_needs_from_set(kelso_env, remote):
  remote.fields = NEEDS
  done = kelso_env.run(
    "deploy",
    str(remote.bundle),
    "box",
    "--set",
    "admin_pass=hunter2",
    "--set",
    "volume.media=nas",
  )
  assert done.returncode == 0, done.stderr
  assert remote.calls[-2][-1] == f"kelso config {APP} --response"
  assert json.loads(remote.inputs[-2]) == {
    "values": {"admin_pass": "hunter2", "volume.media": "nas"}
  }
  assert remote.calls[-1][-1] == f"kelso start {APP}@local --allow-compose"


def test_deploy_refuses_an_answer_the_box_cannot_take(kelso_env, remote):
  remote.fields = NEEDS
  done = kelso_env.run(
    "deploy", str(remote.bundle), "box", "--set", "volume.media=attic"
  )
  assert done.returncode == 1
  assert "volume.media must be one of: media, nas" in done.stderr
  assert not any(call[-1].endswith("--response") for call in remote.calls)


def test_deploy_to_a_loaded_app_updates_it(kelso_env, remote):
  remote.source = f"{ROOT}/repos/local/{APP}.klso"
  done = kelso_env.run("deploy", str(remote.bundle), "box", "--no-backup")
  assert done.returncode == 0, done.stderr
  assert remote.calls[-1][-1] == (
    f"kelso update {APP} --allow-compose --no-backup && kelso start {APP}"
  )


def test_deploy_refuses_a_volume_whose_files_are_missing(kelso_env, remote):
  (remote.bundle / "src").rmdir()
  done = kelso_env.run("deploy", str(remote.bundle), "box")
  assert done.returncode == 1
  assert "[volumes.src] names src" in done.stderr
  assert remote.calls == []


def test_deploy_refuses_an_app_loaded_from_elsewhere(kelso_env, remote):
  remote.source = f"{ROOT}/repos/demos/{APP}.klso"
  done = kelso_env.run("deploy", str(remote.bundle), "box")
  assert done.returncode == 1
  assert f"box already has {APP} loaded from {remote.source}" in done.stderr
  assert len(remote.calls) == 2


def test_config_speaks_requests_and_responses_as_json(kelso_env):
  asked = kelso_env.run("config", "ports-demo", "--json")
  assert asked.returncode == 0, asked.stderr
  request = request_from_view(json.loads(asked.stdout))
  assert request.field("subdomain") is not None

  answer = json.dumps({"values": {"subdomain": "elsewhere"}})
  done = kelso_env.run("config", "ports-demo", "--response", input=answer)
  assert done.returncode == 0, done.stderr
  assert done.stdout.strip() == "Set subdomain"
  got = kelso_env.run("config", "ports-demo", "--get", "subdomain")
  assert got.stdout.strip() == "elsewhere"


def test_status_says_what_an_app_name_resolves_to(kelso_env):
  assert kelso_env.run("status", "upd-app").returncode == 1
  local = kelso_env.local_repo / "net.local.upd-app.klso"
  other = kelso_env.root / "more" / "net.more.upd-app.klso"
  for bundle in (local, other):
    bundle.mkdir(parents=True)
    (bundle / "manifest.toml").write_text(
      '[app]\nversion = "1.0"\n\n[run.main]\nimage = "alpine"\n'
    )
  assert kelso_env.run("load", str(other)).returncode == 0

  shared = kelso_env.run("status", "upd-app")
  assert shared.returncode == 1
  assert "net.local.upd-app (repo local), net.more.upd-app (loaded)" in shared.stderr

  loaded = kelso_env.run("status", "net.more.upd-app")
  assert loaded.returncode == 0, loaded.stderr
  assert f"loaded from {other.resolve()}" in loaded.stdout
  by_repo = kelso_env.run("status", "upd-app@local")
  assert f"not loaded; `kelso start` loads it from {local} (repo local)" in (
    by_repo.stdout
  )
  as_json = kelso_env.run("status", "upd-app@local", "--json")
  assert json.loads(as_json.stdout) == {
    "app": "net.local.upd-app",
    "loaded": False,
    "bundle": str(local),
    "repo": "local",
  }
