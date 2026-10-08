"""Kelso scripts: found, listed, run with their arguments, and replaced by yours."""

import pytest

from kelso.lib.config import load_config_file
from kelso.lib.kelso import KelsoCtx
from kelso.script.v1 import Kelso, using

HELLO = """\
from kelso.script.v1 import KelsoScript


class Hello(KelsoScript):
  desc = "Say hello"

  def run(self, args: list[str]) -> None:
    print(f"hello {self.kelso.id} {' '.join(args)}")
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


# --- kelso.script.v1 ------------------------------------------------------------

BASIC = "io.p2net.basic-features"


def _ctx(kelso_env) -> KelsoCtx:
  return KelsoCtx(load_config_file(kelso_env.config))


def test_an_app_starts_with_config_stops_and_unloads(kelso_env):
  with using(_ctx(kelso_env)):
    kelso = Kelso()
    app = kelso.app(BASIC)
    assert not app.loaded
    with app.lock("test"):
      app.start({"admin_user": "me", "admin_pass": "hunter2"})
      assert app.running
      assert [a.id for a in kelso.apps()] == [BASIC]
      app.set_config({"admin_pass": "swordfish"})
      app.stop()
      assert not app.running
      app.unload()
    assert not app.loaded
  assert _ctx(kelso_env).app_store(BASIC).get_config("admin_pass") == (
    True,
    "swordfish",
  )


def test_changing_state_without_the_lock_is_refused(kelso_env):
  with using(_ctx(kelso_env)):
    kelso = Kelso()
    with pytest.raises(RuntimeError, match="app.lock"):
      kelso.app(BASIC).start()
    with pytest.raises(RuntimeError, match="kelso.lock"):
      kelso.set_secret("x", "y")
    with pytest.raises(RuntimeError, match="kelso.lock"):
      with kelso.edit_kelso_config():
        pass


def test_config_edits_are_checked_and_then_seen(kelso_env):
  with using(_ctx(kelso_env)):
    kelso = Kelso()
    with kelso.lock("test"):
      with pytest.raises(ValueError, match="not valid"):
        with kelso.edit_kelso_config() as kconfig:
          kconfig["default_route_provider"] = "nowhere"
      with kelso.edit_kelso_config() as kconfig:
        kconfig["kelso_address"] = "10.0.0.5"
      assert kelso.address == "10.0.0.5"
      # Still held after the reload, so this does not wait on itself.
      kelso.set_secret("x", "y")
    assert kelso.secret("x") == "y"


def test_kelso_outside_a_script_says_how_to_run_one():
  with pytest.raises(RuntimeError, match="kelso script"):
    Kelso().apps()
