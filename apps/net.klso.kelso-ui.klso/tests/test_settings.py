"""The Settings window and the desktop colors it sets."""

import json
import re

import settings
from conftest import SETTINGS


def _colors(text):
  block = re.search(
    r'<script type="application/json" id="kelso-desktop">(.*?)</script>', text
  )
  return json.loads(block.group(1))


def test_an_id_draws_its_own_calm_gradient():
  assert settings.id_colors("beef") == settings.id_colors("beef")
  assert settings.id_colors("beef") != settings.id_colors("cafe")
  assert settings.id_colors("") == settings.PLAIN
  for color in settings.id_colors("beef"):
    assert settings.HEX.match(color)


def test_every_page_carries_the_colors(client, fake, monkeypatch):
  monkeypatch.setattr(settings, "id_colors", lambda _: ("#111111", "#222222"))
  assert _colors(client.get("/volumes").text) == ["#111111", "#222222"]
  assert _colors(client.get("/login").text) == ["#111111", "#222222"]


def test_custom_colors_are_saved_and_painted(client, fake):
  form = {"mode": "custom", "top": "#AA0000", "bottom": "#00aa00"}
  response = client.post("/settings", data=form)
  assert response.headers["location"] == "/settings?ok=Saved"
  saved = json.loads(SETTINGS.read_text())["background"]
  assert saved == {"mode": "custom", "top": "#aa0000", "bottom": "#00aa00"}
  assert _colors(client.get("/").text) == ["#aa0000", "#00aa00"]
  assert (
    'class="desk-colors">["#aa0000", "#00aa00"]' in client.get("/desktop/icons").text
  )


def test_a_theme_is_saved_and_linked_everywhere(client, fake):
  assert "/static/themes/win9x.css?v=" in client.get("/volumes").text
  form = {"theme": "rally", "mode": "id", "top": "", "bottom": ""}
  assert client.post("/settings", data=form).headers["location"] == "/settings?ok=Saved"
  assert "/static/themes/rally.css?v=" in client.get("/volumes").text
  assert 'class="desk-theme">"/static/themes/rally.css?v=' in (
    client.get("/desktop/icons").text
  )


def test_a_theme_that_does_not_exist_is_refused(client, fake):
  form = {"theme": "../kelso", "mode": "id", "top": "", "bottom": ""}
  assert "err=" in client.post("/settings", data=form).headers["location"]
  assert not SETTINGS.exists()


def test_a_color_that_is_not_hex_is_refused(client, fake):
  form = {"mode": "custom", "top": "red;}</style>", "bottom": "#00aa00"}
  response = client.post("/settings", data=form)
  assert "err=" in response.headers["location"]
  assert not SETTINGS.exists()


def test_going_back_to_the_id(client, fake):
  client.post(
    "/settings", data={"mode": "custom", "top": "#aa0000", "bottom": "#00aa00"}
  )
  client.post("/settings", data={"mode": "id", "top": "#aa0000", "bottom": "#00aa00"})
  assert _colors(client.get("/").text) == list(settings.id_colors(""))


def test_a_bad_settings_file_is_named_and_never_breaks_a_page(client, fake):
  SETTINGS.write_text("{not json")
  assert _colors(client.get("/login").text) == list(settings.id_colors(""))
  text = client.get("/settings").text
  assert "is not a settings file" in text
  client.post("/settings", data={"mode": "id", "top": "", "bottom": ""})
  assert json.loads(SETTINGS.read_text())["background"]["mode"] == "id"
