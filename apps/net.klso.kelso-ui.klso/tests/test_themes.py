"""The theme contract: every registered theme is one complete, legible file.

A new theme passes these or it does not ship. The contrast floors are WCAG's:
4.5:1 for text, 3:1 for accents that mark rather than carry reading.
"""

import json
import re
from pathlib import Path

import pytest
from themes import DEFAULT, THEMES, TOKENS

STATIC = Path(__file__).parent.parent / "ui" / "static"
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _rule(theme):
  """(selector, {property: value}) of a theme file's one rule."""
  text = re.sub(
    r"/\*.*?\*/", "", (STATIC / "themes" / f"{theme.id}.css").read_text(), flags=re.S
  )
  rules = re.findall(r"([^{}]+)\{([^{}]*)\}", text)
  assert len(rules) == 1, f"{theme.id}.css should be one rule, found {len(rules)}"
  selector, body = rules[0]
  decls = {}
  for decl in filter(None, (d.strip() for d in body.split(";"))):
    name, _, value = decl.partition(":")
    decls[name.strip()] = value.strip()
  return " ".join(selector.split()), decls


def _luminance(hex_colour):
  def channel(v):
    v /= 255
    return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

  r, g, b = (int(hex_colour[i : i + 2], 16) for i in (1, 3, 5))
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a, b):
  hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
  return (hi + 0.05) / (lo + 0.05)


def test_ids_are_unique():
  ids = [t.id for t in THEMES]
  assert len(ids) == len(set(ids))
  assert DEFAULT == THEMES[0]


def test_every_file_is_registered_and_every_theme_has_a_file():
  on_disk = {p.stem for p in (STATIC / "themes").glob("*.css")}
  assert on_disk == {t.id for t in THEMES}


@pytest.mark.parametrize("theme", THEMES, ids=lambda t: t.id)
def test_a_theme_sets_every_token_and_nothing_else(theme):
  selector, decls = _rule(theme)
  expected = f'[data-theme="{theme.id}"]'
  if theme == DEFAULT:
    expected = f":where(:root), {expected}"
  assert selector == expected
  assert decls.get("color-scheme") in ("dark", "light")
  assert set(decls) - {"color-scheme"} == set(TOKENS)
  for name in TOKENS:
    # Hex so this file can check contrast; rgba() in shell.js takes anything.
    assert HEX.match(decls[name]), f"{theme.id} {name}: {decls[name]}"


@pytest.mark.parametrize("theme", THEMES, ids=lambda t: t.id)
def test_a_theme_is_legible(theme):
  _, t = _rule(theme)
  floors = {"--fg": 7.0, "--dim": 4.5, "--muted": 4.5}
  floors |= dict.fromkeys(("--coral", "--rosewood", "--gold", "--ok"), 3.0)
  low = {
    name: round(contrast(t[name], t["--bg"]), 2)
    for name, floor in floors.items()
    if contrast(t[name], t["--bg"]) < floor
  }
  assert not low, f"{theme.id} on --bg: {low}"
  assert contrast(t["--ink"], t["--coral"]) >= 4.5, "--ink on a --coral fill"


@pytest.mark.parametrize("theme", THEMES, ids=lambda t: t.id)
def test_a_theme_terminal_is_legible(theme):
  """Colours 1-7 and 9-15 are text; bright black (8) is dim text; black (0)
  is the terminal's own dark and exempt."""
  _, t = _rule(theme)
  low = {}
  for i in range(1, 16):
    floor = 3.0 if i == 8 else 4.5
    ratio = contrast(t[f"--ansi-{i}"], t["--void"])
    if ratio < floor:
      low[f"--ansi-{i}"] = round(ratio, 2)
  assert not low, f"{theme.id} on --void: {low}"


def test_the_stylesheet_has_no_colours_of_its_own():
  """A hex value in kelso.css would be one theme's colour in all of them."""
  css = re.sub(r"/\*.*?\*/", "", (STATIC / "kelso.css").read_text(), flags=re.S)
  assert not re.findall(r"#[0-9a-fA-F]{3,8}\b", css)
  assert "data-theme" not in css.replace(".theme-swatch", "")


def test_pages_carry_the_registry(client, fake):
  text = client.get("/volumes").text
  head = text.split("</head>")[0]
  links = re.findall(r'href="/static/themes/([\w-]+)\.css', head)
  assert links == [t.id for t in THEMES]
  ids = re.search(
    r'<script type="application/json" id="kelso-themes">(.*?)</script>', head
  )
  assert json.loads(ids.group(1)) == [t.id for t in THEMES]
  assert head.index('id="kelso-themes"') < head.index("js/boot.js")
  menu = text.split('id="theme-menu"')[1].split("</div>")[0]
  for theme in THEMES:
    assert f'data-theme-id="{theme.id}"' in menu
    assert f'class="theme-swatch" data-theme="{theme.id}"' in menu
    assert theme.name in menu


def test_sign_in_is_themed_too(anon):
  """No nav, so no menu, but the saved theme still applies before paint."""
  head = anon.get("/login").text.split("</head>")[0]
  assert "js/boot.js" in head and 'id="kelso-themes"' in head
