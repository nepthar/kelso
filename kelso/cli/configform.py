"""Fill in a ConfigRequest at the terminal.

`collect` picks the full-screen form on a terminal and the line-by-line wizard
otherwise, so piped input and tests still work.
"""

import sys
from getpass import getpass

from tabulate import tabulate

from kelso.lib.configflow import (
  EMPTY_CONFIG_RESPONSE,
  ConfigField,
  ConfigRequest,
  ConfigResponse,
)

REVIEW = "'s' to submit, a name or number to change, 'q' to cancel"


def _ask(entry: ConfigField, edits: dict[str, str]) -> None:
  """Prompt for one field; empty input keeps whatever is already there."""
  current = edits.get(entry.name) or entry.display()
  shown = "(set)" if entry.secret and entry.name in edits else current
  prompt = f"{entry.name} [{shown}]: "
  if entry.desc:
    print(f"  {entry.desc}")
  if entry.choices is not None:
    print(f"  one of: {', '.join(entry.choices) or '(none defined)'}")

  if entry.secret and sys.stdin.isatty():
    value = getpass(prompt).strip()
  else:
    value = input(prompt).strip()

  if value:
    edits[entry.name] = value


def _confirm(prompt: str) -> bool:
  return input(prompt).strip().lower() in ("y", "yes")


def _value(entry: ConfigField, edits: dict[str, str]) -> str:
  if entry.name not in edits:
    return entry.display()
  return "(set)" if entry.secret else edits[entry.name]


def _review(request: ConfigRequest, edits: dict[str, str]) -> None:
  rows = [
    [f"{i}.", f.name, _value(f, edits)] for i, f in enumerate(request.fields, start=1)
  ]
  print("")
  print(tabulate(rows, headers=["", "name", "value"]))


def _pick(request: ConfigRequest, choice: str) -> ConfigField | None:
  if choice.isdigit():
    index = int(choice)
    fields = request.fields
    return fields[index - 1] if 1 <= index <= len(fields) else None
  return request.field(choice)


def run_form(request: ConfigRequest) -> ConfigResponse:
  """Walk the fields, then review; EMPTY_CONFIG_RESPONSE on cancel or no change."""
  edits: dict[str, str] = {}
  missing = request.missing()

  print(request.title)
  if request.note:
    print(request.note)
  print("Enter keeps the current value.")

  try:
    for title, fields in request.groups():
      shown = [f for f in fields if f.section == "config" or f.name in missing]
      for entry in shown:
        _ask(entry, edits)
      folded = [f for f in fields if f not in shown]
      if folded and _confirm(f"Show {title.lower()} ({len(folded)})? [y/N] "):
        for entry in folded:
          _ask(entry, edits)

    while True:
      _review(request, edits)
      choice = input(f"[{REVIEW}] ").strip()

      if choice in ("q", "quit"):
        return EMPTY_CONFIG_RESPONSE
      if choice in ("s", "submit", ""):
        errors = request.validate(edits)
        if not errors:
          return ConfigResponse(values=edits) if edits else EMPTY_CONFIG_RESPONSE
        for err in errors:
          print(f"  - {err}")
        continue

      entry = _pick(request, choice)
      if entry is None:
        print(f"No field {choice!r}. {REVIEW}")
        continue
      _ask(entry, edits)
  except EOFError:
    return EMPTY_CONFIG_RESPONSE


def collect(request: ConfigRequest) -> ConfigResponse:
  """Collect a response; EMPTY_CONFIG_RESPONSE when there is nothing to apply."""
  if sys.stdin.isatty() and sys.stdout.isatty():
    from kelso.cli.configtui import run_tui

    return run_tui(request)
  return run_form(request)
