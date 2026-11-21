import sys


class Refused(ValueError):
  pass


def confirm(question: str, *, flag: str = "--yes") -> bool:
  if not sys.stdin.isatty():
    raise Refused(
      f"{question} There is no terminal to ask on; pass {flag} to go ahead."
    )
  try:
    answer = input(f"{question} [y/N] ")
  except EOFError:
    return False
  return answer.strip().lower() in ("y", "yes")
