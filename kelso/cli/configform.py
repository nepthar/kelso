"""Fill in a ConfigRequest at the terminal: walk the fields, then approve."""

from __future__ import annotations

import sys
from getpass import getpass

from tabulate import tabulate

from kelso.lib.configflow import ConfigField, ConfigRequest, ConfigResponse
from kelso.lib.util import Conn

REVIEW = "'s' to submit, a name or number to change, 'q' to cancel"


def _ask(entry: ConfigField, edits: dict[str, str], conn: Conn) -> None:
  """Prompt for one field; empty input keeps whatever is already there."""
  current = edits.get(entry.name) or entry.display()
  shown = "(set)" if entry.secret and entry.name in edits else current
  prompt = f"{entry.name} [{shown}]: "
  if entry.desc:
    conn.out(f"  {entry.desc}")

  if entry.secret and sys.stdin.isatty():
    # Keep a secret off the screen; Conn.read cannot turn echo off.
    value = getpass(prompt).strip()
  else:
    value = conn.read(prompt).strip()

  if value:
    edits[entry.name] = value


def _confirm(prompt: str, conn: Conn) -> bool:
  return conn.read(prompt).strip().lower() in ("y", "yes")


def _value(entry: ConfigField, edits: dict[str, str]) -> str:
  if entry.name not in edits:
    return entry.display()
  return "(set)" if entry.secret else edits[entry.name]


def _review(request: ConfigRequest, edits: dict[str, str], conn: Conn) -> None:
  rows = [
    [f"{i}.", f.name, _value(f, edits)] for i, f in enumerate(request.fields, start=1)
  ]
  conn.out("")
  conn.out(tabulate(rows, headers=["", "name", "value"]))
  needed = request.still_needed(edits)
  if needed:
    conn.out(f"Still needed before this can start: {', '.join(needed)}")


def _pick(request: ConfigRequest, choice: str) -> ConfigField | None:
  if choice.isdigit():
    index = int(choice)
    fields = request.fields
    return fields[index - 1] if 1 <= index <= len(fields) else None
  return request.field(choice)


def run_form(request: ConfigRequest, conn: Conn) -> ConfigResponse | None:
  """Walk the fields, then review; None when the operator cancels."""
  edits: dict[str, str] = {}
  missing = request.missing()
  basic = [f for f in request.fields if not f.advanced or f.name in missing]
  advanced = [f for f in request.fields if f not in basic]

  conn.out(request.title)
  if request.note:
    conn.out(request.note)
  conn.out("Enter keeps the current value.")

  try:
    for entry in basic:
      _ask(entry, edits, conn)

    if advanced and _confirm(f"Show {len(advanced)} advanced settings? [y/N] ", conn):
      for entry in advanced:
        _ask(entry, edits, conn)

    while True:
      _review(request, edits, conn)
      choice = conn.read(f"[{REVIEW}] ").strip()

      if choice in ("q", "quit"):
        return None
      if choice in ("s", "submit", ""):
        errors = request.validate(edits)
        if not errors:
          return ConfigResponse(values=edits)
        for err in errors:
          conn.err(f"  - {err}")
        continue

      entry = _pick(request, choice)
      if entry is None:
        conn.err(f"No field {choice!r}. {REVIEW}")
        continue
      _ask(entry, edits, conn)
  except EOFError:
    return None
