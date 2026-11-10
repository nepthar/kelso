"""The colour tokens: one complete, legible set.

The contrast floors are WCAG's: 4.5:1 for text, 3:1 for accents that mark
rather than carry reading.
"""

import re
from pathlib import Path

STATIC = Path(__file__).parent.parent / "ui" / "static"
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

# What theme.css must set, each with one job. kelso.css derives the rest
# (--bad, --warn, the terminal's foreground and cursor).
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
  # meanings: destructive, the accent, attention, healthy
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


def _theme():
  """theme.css's one rule as {property: value}."""
  text = re.sub(r"/\*.*?\*/", "", (STATIC / "theme.css").read_text(), flags=re.S)
  rules = re.findall(r"([^{}]+)\{([^{}]*)\}", text)
  assert len(rules) == 1, f"theme.css should be one rule, found {len(rules)}"
  selector, body = rules[0]
  assert selector.strip() == ":root"
  decls = {}
  for decl in filter(None, (d.strip() for d in body.split(";"))):
    name, _, value = decl.partition(":")
    decls[name.strip()] = value.strip()
  return decls


def _luminance(hex_colour):
  def channel(v):
    v /= 255
    return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

  r, g, b = (int(hex_colour[i : i + 2], 16) for i in (1, 3, 5))
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a, b):
  hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
  return (hi + 0.05) / (lo + 0.05)


def test_the_theme_sets_every_token_and_nothing_else():
  decls = _theme()
  assert decls.get("color-scheme") in ("dark", "light")
  assert set(decls) - {"color-scheme"} == set(TOKENS)
  for name in TOKENS:
    # Hex so this file can check contrast.
    assert HEX.match(decls[name]), f"{name}: {decls[name]}"


def test_the_theme_is_legible():
  t = _theme()
  floors = {"--fg": 7.0, "--dim": 4.5, "--muted": 4.5}
  floors |= dict.fromkeys(("--coral", "--rosewood", "--gold", "--ok"), 3.0)
  low = {
    name: round(contrast(t[name], t["--bg"]), 2)
    for name, floor in floors.items()
    if contrast(t[name], t["--bg"]) < floor
  }
  assert not low, f"on --bg: {low}"
  assert contrast(t["--ink"], t["--coral"]) >= 4.5, "--ink on a --coral fill"


def test_the_terminal_is_legible():
  """Colours 1-7 and 9-15 are text; bright black (8) is dim text; black (0)
  is the terminal's own dark and exempt."""
  t = _theme()
  low = {}
  for i in range(1, 16):
    floor = 3.0 if i == 8 else 4.5
    ratio = contrast(t[f"--ansi-{i}"], t["--void"])
    if ratio < floor:
      low[f"--ansi-{i}"] = round(ratio, 2)
  assert not low, f"on --void: {low}"


def test_the_layout_stylesheet_has_no_colours_of_its_own():
  """kelso.css is layout over the tokens; the palette is theme.css and classic.css."""
  css = re.sub(r"/\*.*?\*/", "", (STATIC / "kelso.css").read_text(), flags=re.S)
  assert not re.findall(r"#[0-9a-fA-F]{3,8}\b", css)


def test_every_page_links_the_theme(client, anon, fake):
  for page in (client.get("/volumes"), anon.get("/login")):
    head = page.text.split("</head>")[0]
    assert "/static/theme.css?v=" in head
