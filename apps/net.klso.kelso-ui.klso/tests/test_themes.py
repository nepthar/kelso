"""The color tokens: every theme is one complete, legible set.

The contrast floors are WCAG's: 4.5:1 for text, 3:1 for accents that mark
rather than carry reading.
"""

import re
from pathlib import Path

import pytest
import settings

STATIC = Path(__file__).parent.parent / "ui" / "static"
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
each_theme = pytest.mark.parametrize("name", settings.THEMES)

# What a theme must set, each with one job. kelso.css derives the rest
# (--bad, --warn, the terminal's foreground and cursor).
TOKENS = (
  # surfaces, back to front, and text in the well
  "--void",
  "--void-fg",
  "--bg",
  "--panel",
  "--hair",
  # borders
  "--line",
  # text: content, secondary, metadata, disabled
  "--fg",
  "--dim",
  "--muted",
  "--off",
  # the accent and type on it; then destructive, attention, healthy
  "--coral",
  "--ink",
  "--rosewood",
  "--gold",
  "--ok",
  "--link",
  "--note",
  # the terminal's sixteen colors: black red green yellow blue magenta cyan
  # white, then the bright eight. Drawn on --void.
  *(f"--ansi-{i}" for i in range(16)),
)


def _theme(name):
  """A theme file's one rule as {property: value}."""
  text = (STATIC / "themes" / f"{name}.css").read_text()
  text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
  rules = re.findall(r"([^{}]+)\{([^{}]*)\}", text)
  assert len(rules) == 1, f"{name}.css should be one rule, found {len(rules)}"
  selector, body = rules[0]
  assert selector.strip() == ":root"
  decls = {}
  for decl in filter(None, (d.strip() for d in body.split(";"))):
    prop, _, value = decl.partition(":")
    decls[prop.strip()] = value.strip()
  return decls


def _luminance(hex_color):
  def channel(v):
    v /= 255
    return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

  r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a, b):
  hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
  return (hi + 0.05) / (lo + 0.05)


def test_every_theme_has_a_file_and_every_file_is_a_theme():
  assert {p.stem for p in (STATIC / "themes").glob("*.css")} == set(settings.THEMES)


@each_theme
def test_a_theme_sets_every_token_and_nothing_else(name):
  decls = _theme(name)
  assert decls.get("color-scheme") in ("dark", "light")
  assert set(decls) - {"color-scheme"} == set(TOKENS)
  for token in TOKENS:
    # Hex so this file can check contrast.
    assert HEX.match(decls[token]), f"{token}: {decls[token]}"


@each_theme
def test_a_theme_is_legible(name):
  t = _theme(name)
  floors = {
    ("--fg", "--bg"): 7.0,
    ("--fg", "--panel"): 7.0,
    ("--fg", "--note"): 7.0,
    ("--void-fg", "--void"): 7.0,
    ("--dim", "--bg"): 4.5,
    ("--muted", "--bg"): 4.5,
    ("--muted", "--panel"): 4.5,
    ("--link", "--bg"): 4.5,
    ("--link", "--panel"): 4.5,
    ("--ink", "--coral"): 4.5,
  }
  for accent in ("--coral", "--rosewood", "--gold", "--ok"):
    floors[accent, "--bg"] = 3.0
  low = {
    f"{a} on {b}": round(contrast(t[a], t[b]), 2)
    for (a, b), floor in floors.items()
    if contrast(t[a], t[b]) < floor
  }
  assert not low, low


@each_theme
def test_a_themes_terminal_is_legible(name):
  """The six colors are text, and their bright halves emphasis. Black and
  white are text against the well's other end, and exempt at its own."""
  t = _theme(name)
  own = {7, 15} if _luminance(t["--void"]) > 0.5 else {0}
  low = {}
  for i in set(range(16)) - own:
    floor = 3.0 if i >= 8 else 4.5
    ratio = contrast(t[f"--ansi-{i}"], t["--void"])
    if ratio < floor:
      low[f"--ansi-{i}"] = round(ratio, 2)
  assert not low, f"on --void: {low}"


def test_the_layout_stylesheet_has_no_colors_of_its_own():
  """kelso.css is layout over the tokens; the palette is a theme's."""
  css = re.sub(r"/\*.*?\*/", "", (STATIC / "kelso.css").read_text(), flags=re.S)
  assert not re.findall(r"#[0-9a-fA-F]{3,8}\b", css)


def test_every_page_links_the_theme(client, anon, fake):
  for page in (client.get("/volumes"), anon.get("/login")):
    head = page.text.split("</head>")[0]
    assert "/static/themes/light.css?v=" in head
