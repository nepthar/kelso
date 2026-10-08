"""Kelso scripts: found, listed, run with their arguments, and replaced by yours."""

import pytest

from kelso.lib.config import load_config_file
from kelso.lib.kelso import KelsoCtx
from kelso.script.v1 import Kelso

HELLO = """\
from kelso.script.v1 import KelsoScript


class Hello(KelsoScript):
  desc = "Say hello"

  def run(self, args: list[str]) -> None:
    print(f"hello {self.kelso().id} {' '.join(args)}")
"""


def _write(kelso_env, name: str, text: str) -> None:
  scripts = kelso_env.root / "scripts"
  scripts.mkdir(exist_ok=True)
  (scripts / f"{name}.py").write_text(text)


def test_a_script_runs_with_its_arguments_and_reaches_kelso(kelso_env):
  _write(kelso_env, "hello", HELLO)
  result = kelso_env.run("script", "hello", "a", "--b")
  assert result.returncode == 0, result.stderr
  assert result.stdout.startswith("hello ")
  assert result.stdout.strip().endswith("a --b")


def test_the_list_shows_kelsos_scripts_and_yours(kelso_env):
  _write(kelso_env, "hello", HELLO)
  listed = kelso_env.run("script").stdout
  assert "doctor" in listed and "kelso" in listed
  assert "hello" in listed and "yours" in listed


def test_yours_replaces_kelsos_by_name(kelso_env):
  _write(kelso_env, "doctor", HELLO)
  result = kelso_env.run("script", "doctor")
  assert result.stdout.startswith("hello ")


def test_an_unknown_script_names_where_yours_go(kelso_env):
  result = kelso_env.run("script", "nope")
  assert result.returncode == 1
  assert str(kelso_env.root / "scripts") in result.stderr


def test_a_file_without_a_script_class_is_refused(kelso_env):
  _write(kelso_env, "empty", "x = 1\n")
  result = kelso_env.run("script", "empty")
  assert result.returncode == 1
  assert "exactly one KelsoScript" in result.stderr


# --- kelso.script.v1.Kelso ----------------------------------------------------

BASIC = "io.p2net.basic-features"


def _kelso(kelso_env) -> Kelso:
  return Kelso(KelsoCtx(load_config_file(kelso_env.config)))


def test_kelso_starts_an_app_with_config_and_lists_it(kelso_env):
  kelso = _kelso(kelso_env)
  assert kelso.app(BASIC) is None
  kelso.start(BASIC, {"admin_user": "me", "admin_pass": "hunter2"})
  [app] = [a for a in kelso.apps() if a.id == BASIC]
  assert app.running
  ctx = KelsoCtx(load_config_file(kelso_env.config))
  assert ctx.app_store(BASIC).get_config("admin_pass") == (True, "hunter2")

  kelso.set_config(BASIC, {"admin_pass": "swordfish"})
  assert ctx.app_store(BASIC).get_config("admin_pass") == (True, "swordfish")
  kelso.stop(BASIC)
  assert not kelso.app(BASIC).running


def test_kelso_writes_a_route_provider_and_makes_it_the_default(kelso_env):
  kelso = _kelso(kelso_env)
  kelso.set_address("10.0.0.5")
  kelso.set_route_provider(
    "cf",
    "cloudflare_tunnel",
    "example.com",
    args={"account_id": "acct", "tunnel_id": "tun"},
    secrets={"api_token": "tok"},
  )
  kelso.set_default_route_provider("cf")

  assert kelso.default_route_provider == "cf"
  assert ("cf", "cloudflare_tunnel", "example.com") in [
    (p.tag, p.kind, p.domain) for p in kelso.route_providers()
  ]
  assert kelso.secret("route_provider.cf.api_token") == "tok"
  config = load_config_file(kelso_env.config)
  assert config.route_providers["cf"].args["api_token_secret"] == (
    "route_provider.cf.api_token"
  )


def test_kelso_refuses_an_unknown_route_provider_kind(kelso_env):
  with pytest.raises(ValueError, match="Unknown route provider kind"):
    _kelso(kelso_env).set_route_provider("x", "carrier_pigeon", "example.com")
