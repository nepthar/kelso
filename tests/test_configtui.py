"""Tests for the full-screen config form, driven headless through Textual's pilot."""

import asyncio

from textual.widgets import Static

from kelso.cli.configtui import ConfigApp
from kelso.lib.configflow import EMPTY_CONFIG_RESPONSE, ConfigField, ConfigRequest


def _request() -> ConfigRequest:
  return ConfigRequest(
    title="demo",
    note="A demo app",
    fields=(
      ConfigField(name="admin_email", desc="Login for the web interface"),
      ConfigField(name="timezone", value="UTC", desc="IANA timezone"),
      ConfigField(name="api_key", secret=True, secret_set=True),
      ConfigField(name="pool_size", default="5", advanced=True),
    ),
  )


def _drive(request: ConfigRequest, steps) -> ConfigApp:
  """Run `steps(app, pilot)` against a headless app and return it once exited."""

  async def run() -> ConfigApp:
    app = ConfigApp(request)
    async with app.run_test() as pilot:
      await steps(app, pilot)
      await pilot.pause()
    return app

  return asyncio.run(run())


def _shown(widget) -> bool:
  return all(node.styles.display != "none" for node in widget.ancestors_with_self)


async def _type(app: ConfigApp, pilot, name: str, text: str) -> None:
  field = app.control(name)
  field.focus()
  field.value = ""
  await pilot.press(*text)


def test_submit_returns_only_what_changed():
  async def steps(app, pilot):
    await _type(app, pilot, "admin_email", "a@b.c")
    await pilot.press("ctrl+s")

  app = _drive(_request(), steps)

  assert app.return_value.values == {"admin_email": "a@b.c"}


def test_current_values_are_prefilled_and_secrets_are_not():
  async def steps(app, pilot):
    assert app.control("timezone").value == "UTC"
    secret = app.control("api_key")
    assert secret.value == ""
    assert secret.password is True
    assert secret.placeholder == "(set)"
    await pilot.press("escape")

  _drive(_request(), steps)


def test_a_typed_secret_is_submitted():
  async def steps(app, pilot):
    await _type(app, pilot, "api_key", "new-key")
    await pilot.press("ctrl+s")

  app = _drive(_request(), steps)

  assert app.return_value.values == {"api_key": "new-key"}


def test_advanced_fields_stay_hidden_until_ctrl_o():
  async def steps(app, pilot):
    pool = app.control("pool_size")
    help_line = app.query_one("#help", Static)
    assert not _shown(pool)
    assert "^o to show advanced" in str(help_line.render())

    await pilot.press("ctrl+o")
    assert _shown(pool)
    assert "^o to hide advanced" in str(help_line.render())

    await pilot.press("ctrl+o")
    assert not _shown(pool)
    await pilot.press("escape")

  _drive(_request(), steps)


def test_hidden_advanced_fields_are_not_tab_stops():
  async def steps(app, pilot):
    for _ in range(4):
      await pilot.press("tab")
      assert app.focused is not app.control("pool_size")
    await pilot.press("escape")

  _drive(_request(), steps)


def test_ctrl_a_still_moves_to_the_start_of_a_field():
  async def steps(app, pilot):
    await pilot.press(*"b.c", "ctrl+a", *"a@")
    await pilot.press("ctrl+s")

  app = _drive(_request(), steps)

  assert app.return_value.values == {"admin_email": "a@b.c"}


def test_ctrl_q_quits_without_saving():
  async def steps(app, pilot):
    await pilot.press(*"a@b.c", "ctrl+q")

  assert _drive(_request(), steps).return_value == EMPTY_CONFIG_RESPONSE


def test_help_leaves_out_advanced_when_there_is_none():
  request = ConfigRequest(title="plain", fields=(ConfigField(name="only"),))

  async def steps(app, pilot):
    help_text = str(app.query_one("#help", Static).render())
    assert "advanced" not in help_text
    assert "^s save" in help_text
    await pilot.press("escape")

  _drive(request, steps)


def test_fields_sit_in_a_box_titled_configuration():
  async def steps(app, pilot):
    assert app.query_one("#config").border_title == "Configuration"
    await pilot.press("escape")

  _drive(_request(), steps)


def test_escape_cancels():
  async def steps(app, pilot):
    await _type(app, pilot, "admin_email", "a@b.c")
    await pilot.press("escape")

  app = _drive(_request(), steps)

  assert app.return_value == EMPTY_CONFIG_RESPONSE


def test_there_is_no_command_palette():
  async def steps(app, pilot):
    await pilot.press("ctrl+p")
    await pilot.pause()
    assert len(app.screen_stack) == 1
    await pilot.press("escape")

  _drive(_request(), steps)


def test_the_first_field_has_focus_so_typing_goes_straight_in():
  async def steps(app, pilot):
    assert app.focused is app.control("admin_email")
    await pilot.press(*"a@b.c", "ctrl+s")

  app = _drive(_request(), steps)

  assert app.return_value.values == {"admin_email": "a@b.c"}


def test_tab_moves_between_fields_in_order():
  async def steps(app, pilot):
    assert app.focused is app.control("admin_email")
    await pilot.press("tab")
    assert app.focused is app.control("timezone")
    await pilot.press("tab")
    assert app.focused is app.control("api_key")
    await pilot.press("escape")

  _drive(_request(), steps)


def _choice_request() -> ConfigRequest:
  return ConfigRequest(
    title="demo",
    fields=(
      ConfigField(name="volume.media", value="media", choices=("media", "other")),
      ConfigField(name="route.main", required=False, choices=("none", "web")),
      ConfigField(name="volume.empty", choices=()),
    ),
  )


def test_a_choice_field_is_a_select_showing_what_is_on_file():
  async def steps(app, pilot):
    bound = app.control("volume.media")
    assert bound.value == "media"
    assert app.control("route.main").is_blank()
    assert app.control("volume.empty").disabled
    await pilot.press("escape")

  _drive(_choice_request(), steps)


def test_picking_a_choice_submits_it_and_an_untouched_one_does_not():
  async def steps(app, pilot):
    app.control("route.main").value = "web"
    await pilot.press("ctrl+s")

  app = _drive(_choice_request(), steps)

  assert app.return_value.values == {"route.main": "web"}
