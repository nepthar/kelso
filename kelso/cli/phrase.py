import sys

from kelso.lib import recovery

SAVE_IT = """\
Your recovery phrase:

{grid}

Save these twelve words in a password manager now. They are the only way to
read this kelso's secrets and backups on another machine: without them a
backup cannot be restored, and nobody can recover them for you."""


def show(words: list[str]) -> None:
  # Never through logging: log lines are recorded to the activity log on disk.
  print(SAVE_IT.format(grid=recovery.format_phrase(words)))
  sys.stdout.flush()
