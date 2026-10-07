"""Showing a recovery phrase and checking it was saved. Never through logging:
kelso records its log lines in the activity log, and the phrase must not land
on disk anywhere but the key file."""

import sys
from collections.abc import Callable

from kelso.lib import recovery

SAVE_IT = """\
Your recovery phrase:

{grid}

Save these twelve words in a password manager now. They are the only way to
read this kelso's secrets and backups on another machine: without them a
backup cannot be restored, and nobody can recover them for you."""


def show(words: list[str]) -> None:
  print(SAVE_IT.format(grid=recovery.format_phrase(words)))
  sys.stdout.flush()


def check(words: list[str], ask: Callable[[str], str] = input) -> bool:
  """Ask for two of the words back, allowing one retry. False on a wrong
  answer twice, or no answer at all."""
  print("\nTo check you saved them:")
  for attempt in range(2):
    try:
      answers = [
        (ask(f"Enter word {n}: ").strip().lower(), words[n - 1])
        for n in recovery.quiz_positions()
      ]
    except EOFError:
      return False
    if all(given == expected for given, expected in answers):
      print("That matches.")
      return True
    if attempt == 0:
      print("That does not match. Once more:")
  return False
