"""Asking the operator to go ahead, and refusing when there is no one to ask."""

import sys


class Refused(ValueError):
  """Kelso refused before changing anything."""


def confirm(question: str, *, flag: str = "--yes") -> bool:
  """Ask `question` on the terminal; raises Refused when there is none."""
  if not sys.stdin.isatty():
    raise Refused(
      f"{question} There is no terminal to ask on; pass {flag} to go ahead."
    )
  try:
    answer = input(f"{question} [y/N] ")
  except EOFError:
    return False
  return answer.strip().lower() in ("y", "yes")
