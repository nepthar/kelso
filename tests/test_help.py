import argparse
import ast
import io
import re
import shlex
from collections.abc import Iterator
from contextlib import redirect_stderr
from pathlib import Path

from kelso.cli.main import HELP, HELP_ADVANCED, build_parser

REPO_ROOT = Path(__file__).resolve().parents[1]
_FENCED = re.compile(r"```.*?```", re.S)
_INLINE_CMD = re.compile(r"(?<!`)`(kelso\s[^`]+)`(?!`)")
_PLACEHOLDER = re.compile(r"<[^>]+>")


def _listed(page: str) -> list[str]:
  return re.findall(r"^  ([a-z][a-z_-]*)", page, re.M)


def test_every_command_is_on_exactly_one_help_page():
  [commands] = [
    action.choices
    for action in build_parser()._actions
    if isinstance(action, argparse._SubParsersAction)
  ]
  listed = _listed(HELP) + _listed(HELP_ADVANCED)
  assert sorted(listed) == sorted(set(commands) - {"help", "agent-help"})


def test_help_prints_each_page(kelso_env):
  everyday = kelso_env.run("help")
  assert everyday.returncode == 0
  assert "kelso help advanced" in everyday.stdout
  assert "  unload" not in everyday.stdout
  advanced = kelso_env.run("help", "advanced")
  assert advanced.stdout == HELP_ADVANCED


def _py_strings(path: Path) -> Iterator[tuple[int, str]]:
  tree = ast.parse(path.read_text(), filename=str(path))
  # Walk manually so JoinedStr (f-strings) emit once with `{expr}` -> `X`
  # instead of also visiting their inner Constant chunks.
  stack: list[ast.AST] = [tree]
  while stack:
    node = stack.pop()
    if isinstance(node, ast.JoinedStr):
      text = "".join(
        part.value
        if isinstance(part, ast.Constant) and isinstance(part.value, str)
        else "X"
        for part in node.values
      )
      yield node.lineno, text
      continue
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
      yield node.lineno, node.value
      continue
    stack.extend(ast.iter_child_nodes(node))


def _embedded_commands() -> Iterator[tuple[str, str]]:
  roots = [REPO_ROOT / "kelso", REPO_ROOT / "docs", REPO_ROOT / "README.md"]
  for root in roots:
    paths = [root] if root.is_file() else sorted(root.rglob("*"))
    for path in paths:
      if not path.is_file() or path.name == "roadmap.md":
        continue
      rel = path.relative_to(REPO_ROOT)
      if path.suffix == ".py":
        for lineno, text in _py_strings(path):
          for match in _INLINE_CMD.finditer(text):
            yield f"{rel}:{lineno}", " ".join(match.group(1).split())
      elif path.suffix == ".md":
        # Strip fenced blocks so backticks inside code blocks don't pair across prose.
        prose = _FENCED.sub(lambda m: "\n" * m.group(0).count("\n"), path.read_text())
        for match in _INLINE_CMD.finditer(prose):
          lineno = prose.count("\n", 0, match.start()) + 1
          yield f"{rel}:{lineno}", " ".join(match.group(1).split())


def test_every_embedded_kelso_command_parses():
  parser = build_parser()
  bad: list[str] = []
  for loc, raw in _embedded_commands():
    if any(skip in raw for skip in ("...", "…", "&&", "|", "COMMAND")):
      continue
    cleaned = _PLACEHOLDER.sub("X", raw)
    try:
      argv = shlex.split(cleaned)[1:]
    except ValueError:
      continue
    if not argv or argv[0] == "X" or argv == ["--version"]:
      continue
    err = io.StringIO()
    try:
      with redirect_stderr(err):
        parser.parse_args(argv)
    except SystemExit as exit_:
      if exit_.code == 0:
        continue
      msg = err.getvalue().strip().splitlines()[-1] if err.getvalue().strip() else ""
      if (
        "the following arguments are required:" in msg
        or "invalid int value" in msg
        or "invalid float value" in msg
        or "invalid choice: 'X'" in msg
      ):
        continue
      bad.append(f"{loc}: `{raw}` -> {msg}")
  assert not bad, "Stale or invalid `kelso ...` references:\n  " + "\n  ".join(bad)
