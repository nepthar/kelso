"""Connections: `[connections]`, attaching them to run units, and `${conn.*}`."""

import shutil

import pytest
import yaml

APP = "conn-app"

MANIFEST = """\
[app]
version = "1"

[connections]
{connections}

[run.main]
image       = "alpine:latest"
cmd         = ["true"]
connections = {attached}

[run.main.env]
{env}
"""


def a_bundle(
  kelso_env,
  *,
  connections: str = 'admin = { kind = "kelso.admin" }',
  attached: str = '["admin"]',
  env: str = 'SOCK = "${conn.admin.socket}"',
) -> None:
  bundle = kelso_env.local_repo / f"{APP}.klso"
  bundle.mkdir(exist_ok=True)
  (bundle / "manifest.toml").write_text(
    MANIFEST.format(connections=connections, attached=attached, env=env)
  )


def service(kelso_env) -> dict:
  compose = (kelso_env.run_root / APP / "compose.yml").read_text()
  return yaml.safe_load(compose)["services"]["main"]


def test_kelso_admin_mounts_the_socket_folder_and_says_where(kelso_env):
  a_bundle(kelso_env)
  loaded = kelso_env.run("load", APP)
  assert loaded.returncode == 0, loaded.stderr

  main = service(kelso_env)
  by_target = {m["target"]: m for m in main["volumes"]}
  mount = by_target["/run/kelso/conn/admin"]
  assert mount["source"] == str(kelso_env.root / "var" / "conn")
  assert mount["bind"] == {"create_host_path": False}
  assert main["environment"]["SOCK"] == "/run/kelso/conn/admin/admin.sock"

  inspected = kelso_env.run("inspect", APP)
  assert "connection 'admin' to kelsod's admin API" in inspected.stdout


def test_a_missing_host_path_blocks_load(kelso_env):
  shutil.rmtree(kelso_env.root / "var" / "conn")
  a_bundle(kelso_env)

  refused = kelso_env.run("load", APP)
  assert refused.returncode == 1
  assert "connection admin: kelso.admin needs" in refused.stderr


@pytest.mark.parametrize(
  "kwargs, message",
  [
    ({"connections": 'admin = { kind = "nope" }'}, "unknown connection kind 'nope'"),
    ({"attached": '["other"]'}, "connection 'other' is not declared in [connections]"),
    ({"attached": "[]"}, "connection 'admin' is not attached to this unit"),
    (
      {"env": 'X = "${conn.admin.address}"'},
      "references ${conn.admin.address}, which is not a known substitution",
    ),
  ],
)
def test_connections_are_checked_against_the_manifest(kelso_env, kwargs, message):
  a_bundle(kelso_env, **kwargs)
  refused = kelso_env.run("load", APP)
  assert refused.returncode == 1
  assert message in refused.stderr
