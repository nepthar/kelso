"""The themes. This list is the only place one is named.

A theme is a file, `static/themes/<id>.css`, that sets every token in `TOKENS`
and `color-scheme`, and nothing else. Everything else is derived: the
stylesheet links in <head>, the ids boot.js accepts from localStorage, and the
theme menu in the nav. tests/test_themes.py holds each theme to the contract,
including the contrast every token needs to stay legible.

The first theme is the default. Its file selects `:where(:root)` as well, so
it applies before -- or without -- any script choosing one.
"""

from typing import NamedTuple


class Theme(NamedTuple):
  id: str
  name: str


THEMES = (
  Theme("rally", "rally"),
  Theme("mojave", "mojave night"),
  Theme("mojave-day", "mojave day"),
)

DEFAULT = THEMES[0]

# What a theme must set, each with one job. kelso.css documents the ladder and
# derives the rest (--bad, --warn, the terminal's foreground and cursor).
TOKENS = (
  # surfaces, back to front
  "--void",
  "--bg",
  "--panel",
  "--line",
  "--hair",
  # text: content, secondary, metadata
  "--fg",
  "--dim",
  "--muted",
  # the ribbon, reused as meaning
  "--rosewood",
  "--coral",
  "--gold",
  "--ok",
  # type on a coral fill, and the marker for "nothing"
  "--ink",
  "--off",
  # the terminal's sixteen colours: black red green yellow blue magenta cyan
  # white, then the bright eight. Drawn on --void.
  *(f"--ansi-{i}" for i in range(16)),
)
