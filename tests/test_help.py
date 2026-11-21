"""`kelso help` and `kelso help advanced` between them list every command once."""

import argparse
import re

from kelso.cli.main import HELP, HELP_ADVANCED, build_parser


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
