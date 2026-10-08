"""Kelso scripts: found, listed, run with a context, and replaced by yours."""

HELLO = """\
from kelso.script import KelsoCtx, KelsoScript


class Hello(KelsoScript):
  desc = "Say hello"

  def run(self, ctx: KelsoCtx) -> None:
    print(f"hello {ctx.kelso_db.kelso_id()} {' '.join(self.argv)}")
"""


def _write(kelso_env, name: str, text: str) -> None:
  scripts = kelso_env.root / "scripts"
  scripts.mkdir(exist_ok=True)
  (scripts / f"{name}.py").write_text(text)


def test_a_script_runs_with_a_context_and_its_arguments(kelso_env):
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
