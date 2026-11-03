"""kelso-ui-2k's own settings: one JSON file in its `settings` volume.

Only the desktop background so far. Without a saved choice the background is
drawn from the install's id, so each machine running kelso looks its own.
"""

import colorsys
import json
import os
import re
from hashlib import blake2s
from pathlib import Path

PATH = Path(os.environ.get("KELSO_UI_SETTINGS", "/settings/settings.json"))
HEX = re.compile(r"^#[0-9a-f]{6}$")
# Windows 2000's own desktop blue: what an install with no id gets.
PLAIN = ("#3a6ea5", "#3a6ea5")


def load():
  """The saved settings. Raises RuntimeError naming the fix if the file is bad."""
  try:
    values = json.loads(PATH.read_text())
  except FileNotFoundError:
    return {}
  except ValueError as e:
    values = e
  if not isinstance(values, dict):
    raise RuntimeError(
      f"{PATH} is not a settings file. Save in Settings to replace it, or delete it."
    )
  return values


def save(values):
  """Replace the file with `values`. Raises OSError naming the fix."""
  temp = PATH.with_suffix(".tmp")
  try:
    temp.write_text(json.dumps(values, indent=2))
    temp.replace(PATH)
  except OSError as e:
    raise OSError(
      f"Cannot write {PATH}: {e.strerror}. Set KELSO_UI_SETTINGS to a writable file."
    ) from e


def _hex(r, g, b):
  return "#" + "".join(f"{round(c * 255):02x}" for c in (r, g, b))


def id_colors(instance_id):
  """Top and bottom of a gradient that only this id draws; calm enough for white labels."""
  if not instance_id:
    return PLAIN
  digest = blake2s(instance_id.encode(), digest_size=2).digest()
  hue = digest[0] / 256
  shift = (20 + digest[1] % 40) / 360
  top = colorsys.hls_to_rgb(hue, 0.40, 0.45)
  bottom = colorsys.hls_to_rgb((hue + shift) % 1, 0.24, 0.50)
  return _hex(*top), _hex(*bottom)


def background(instance_id):
  """The desktop's (top, bottom) colours: the saved custom pair, or the id's.

  A bad settings file paints the id's colours; the Settings window says why.
  """
  try:
    chosen = load().get("background") or {}
  except RuntimeError:
    chosen = {}
  pair = (chosen.get("top"), chosen.get("bottom"))
  if chosen.get("mode") == "custom" and all(HEX.match(c or "") for c in pair):
    return pair
  return id_colors(instance_id)
