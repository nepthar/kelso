"""Tests for the config flow: the request/response model and the terminal form.

A ConfigRequest is what a front end renders; a ConfigResponse is what it hands
back. These pin down which fields a request exposes, what makes a response
invalid, and how the terminal form drives both.
"""

import pytest

from kelso.cli.configform import run_form
from kelso.lib.config import load_config_file
from kelso.lib.configflow import ConfigField, ConfigRequest, ConfigResponse
from kelso.lib.configflow.route_provider import (
  apply_route_provider_config,
  resolve_route_provider,
  route_provider_config_request,
  secret_ref,
)
from kelso.lib.kelso import KelsoCtx
from kelso.lib.routes import PROVIDERS, CloudflareTunnelRouteProvider


class _ScriptedConn:
  """A Conn whose `read` replays a script; anything past the end is EOF."""

  def __init__(self, *script: str):
    self.script = list(script)
    self.out_lines: list[str] = []
    self.err_lines: list[str] = []

  def out(self, data: str) -> None:
    self.out_lines.append(data)

  def err(self, data: str) -> None:
    self.err_lines.append(data)

  def read(self, prompt: str = "") -> str:
    if not self.script:
      raise EOFError
    return self.script.pop(0)

  @property
  def output(self) -> str:
    return "\n".join(self.out_lines)


def _request(**overrides) -> ConfigRequest:
  fields = overrides.pop(
    "fields",
    (
      ConfigField(name="admin_email", desc="Login for the web interface"),
      ConfigField(name="timezone", default="UTC", desc="IANA timezone"),
      ConfigField(name="api_key", secret=True),
      ConfigField(name="pool_size", default="5", advanced=True),
    ),
  )
  return ConfigRequest(title="demo", fields=fields, **overrides)


# ── the model ──────────────────────────────────────────────────────────────
def test_display_says_what_is_on_file():
  assert ConfigField(name="a", value="set-value").display() == "set-value"
  assert ConfigField(name="a", default="UTC").display() == "UTC (default)"
  assert ConfigField(name="a").display() == "(required)"
  assert ConfigField(name="a", required=False).display() == "(unset)"
  assert ConfigField(name="a", secret=True).display() == "(not set)"
  assert ConfigField(name="a", secret=True, secret_set=True).display() == "(set)"


def test_missing_lists_required_fields_with_nothing_to_fall_back_on():
  assert _request().missing() == ["admin_email", "api_key"]


def test_a_set_secret_is_not_missing():
  request = _request(
    fields=(ConfigField(name="api_key", secret=True, secret_set=True),)
  )
  assert request.missing() == []


def test_validate_refuses_unknown_names_and_empty_values():
  request = _request()
  errors = request.validate({"nope": "x", "timezone": ""})

  assert "no config named 'nope'" in errors[0]
  assert "timezone cannot be empty" in errors[1]


def test_a_partial_answer_is_valid():
  """`start` refuses an under-configured app; the form should not also refuse."""
  assert _request().validate({"admin_email": "a@b.c"}) == []


def test_response_refuses_values_it_cannot_apply():
  request = _request()
  response = request.response({"admin_email": "a@b.c", "api_key": "k"})

  assert response == ConfigResponse(values={"admin_email": "a@b.c", "api_key": "k"})
  with pytest.raises(ValueError, match="no config named 'nope'"):
    request.response({"nope": "x"})


# ── the terminal form ──────────────────────────────────────────────────────
# The wizard asks each basic field in order (admin_email, timezone, api_key),
# then offers the advanced ones, then loops on the review screen.
def test_wizard_walks_the_fields_then_submits():
  conn = _ScriptedConn("a@b.c", "", "secret-value", "n", "s")

  response = run_form(_request(), conn)

  assert response is not None
  assert response.values == {"admin_email": "a@b.c", "api_key": "secret-value"}


def test_enter_keeps_what_is_already_there():
  conn = _ScriptedConn("", "", "", "n", "s")

  response = run_form(_request(), conn)

  assert response is not None
  assert response.values == {}


def test_advanced_fields_are_offered_but_skipped_by_default():
  # Declining the offer means the extra "20" is never consumed as an answer.
  conn = _ScriptedConn("a@b.c", "", "k", "n", "20", "s")
  response = run_form(_request(), conn)
  assert response is not None
  assert "pool_size" not in response.values

  conn = _ScriptedConn("a@b.c", "", "k", "y", "20", "s")
  response = run_form(_request(), conn)
  assert response is not None
  assert response.values["pool_size"] == "20"


def test_a_missing_required_field_is_asked_even_when_advanced():
  request = _request(fields=(ConfigField(name="token", secret=True, advanced=True),))
  conn = _ScriptedConn("t", "s")

  response = run_form(request, conn)

  assert response is not None
  assert response.values == {"token": "t"}


def test_review_can_send_you_back_to_one_field():
  conn = _ScriptedConn("a@b.c", "", "k", "n", "timezone", "America/Denver", "s")

  response = run_form(_request(), conn)

  assert response is not None
  assert response.values["timezone"] == "America/Denver"
  assert response.values["admin_email"] == "a@b.c"


def test_a_partial_answer_can_be_submitted():
  conn = _ScriptedConn("a@b.c", "", "", "n", "s")

  response = run_form(_request(), conn)

  assert response is not None
  assert response.values == {"admin_email": "a@b.c"}


def test_the_form_never_echoes_a_secret():
  conn = _ScriptedConn("a@b.c", "", "hunter2", "n", "s")

  run_form(_request(), conn)

  assert "hunter2" not in conn.output


def test_form_cancels_on_quit_and_on_eof():
  assert run_form(_request(), _ScriptedConn("a@b.c", "", "k", "n", "q")) is None
  assert run_form(_request(), _ScriptedConn()) is None


def test_an_unknown_choice_at_review_says_so_and_keeps_going():
  conn = _ScriptedConn("a@b.c", "", "k", "n", "nope", "s")

  response = run_form(_request(), conn)

  assert any("No field 'nope'" in line for line in conn.err_lines)
  assert response is not None


# ── route providers ────────────────────────────────────────────────────────
CF = CloudflareTunnelRouteProvider

CF_ANSWERS = {
  "domain": "example.com",
  "kelso_address": "10.0.0.5",
  "account_id": "acct-1",
  "tunnel_id": "tun-1",
  "api_token": "cf-token",
}


def ctx_for(kelso_env) -> KelsoCtx:
  """A fresh ctx per call: config.toml is re-read after every write."""
  return KelsoCtx(load_config_file(kelso_env.config))


def _configure_cf(kelso_env, values: dict[str, str]) -> list[str]:
  ctx = ctx_for(kelso_env)
  request = route_provider_config_request("cf", CF, ctx)
  return apply_route_provider_config("cf", CF, request.response(values), ctx)


def test_provider_fields_cover_required_args():
  """config_fields is what an operator is asked; REQUIRED_ARGS is what the block
  needs. A secret field named X becomes `X_secret` in args, so the two have to
  line up or the flow would write a block its own provider then refuses.
  """
  for kind, provider in PROVIDERS.items():
    offered = {
      f"{f.name}_secret" if f.secret else f.name
      for f in provider.config_fields()
      if f.required
    }
    assert set(provider.REQUIRED_ARGS) <= offered, kind


def test_resolving_a_new_tag_needs_a_kind_and_refuses_an_unknown_one(kelso_env):
  ctx = ctx_for(kelso_env)

  assert resolve_route_provider("cf", ctx, "cloudflare_tunnel") is CF
  with pytest.raises(ValueError, match="needs a kind"):
    resolve_route_provider("cf", ctx)
  with pytest.raises(ValueError, match="Unknown route provider kind 'nope'"):
    resolve_route_provider("cf", ctx, "nope")


def test_resolving_an_existing_tag_reads_its_kind_and_refuses_a_different_one(
  kelso_env,
):
  _configure_cf(kelso_env, CF_ANSWERS)
  ctx = ctx_for(kelso_env)

  assert resolve_route_provider("cf", ctx) is CF
  with pytest.raises(ValueError, match="already 'cloudflare_tunnel'"):
    resolve_route_provider("cf", ctx, "pangolin")


def test_provider_request_asks_for_the_secret_not_its_reference(kelso_env):
  request = route_provider_config_request("cf", CF, ctx_for(kelso_env))

  names = [f.name for f in request.fields]
  assert names[:2] == ["domain", "kelso_address"]
  assert "api_token" in names
  assert "api_token_secret" not in names


def test_applying_a_provider_response_writes_the_block_and_stores_the_secret(
  kelso_env,
):
  assert "api_token" in _configure_cf(kelso_env, CF_ANSWERS)

  ctx = ctx_for(kelso_env)
  written = ctx.config.route_providers["cf"]
  assert written.kind == "cloudflare_tunnel"
  assert written.domain == "example.com"
  assert written.args["account_id"] == "acct-1"
  assert written.args["api_token_secret"] == secret_ref("cf", "api_token")
  # The token itself never lands in config.toml.
  assert "cf-token" not in ctx.config.config_path.read_text()
  assert ctx.kelso_db.get_secret(secret_ref("cf", "api_token")) == "cf-token"


def test_a_second_pass_keeps_answers_it_was_not_given_again(kelso_env):
  _configure_cf(kelso_env, CF_ANSWERS)

  request = route_provider_config_request("cf", CF, ctx_for(kelso_env))
  assert request.field("account_id").value == "acct-1"
  assert request.field("api_token").secret_set is True
  assert request.missing() == []

  _configure_cf(kelso_env, {"tunnel_id": "tun-2"})

  written = ctx_for(kelso_env).config.route_providers["cf"]
  assert written.args["tunnel_id"] == "tun-2"
  assert written.args["account_id"] == "acct-1"
  assert written.domain == "example.com"


# ── apps: binds and routes ─────────────────────────────────────────────────
def _app_request(kelso_env, app: str) -> tuple:
  from kelso.lib.bundle import load_bundle
  from kelso.lib.configflow.app import app_config_request

  ctx = ctx_for(kelso_env)
  spec = load_bundle(ctx.bundle_path(ctx.resolve_app(app))).app_spec()
  return spec, ctx, app_config_request(spec, ctx)


def test_choices_are_validated():
  request = ConfigRequest(
    title="demo",
    fields=(
      ConfigField(name="pick", choices=("a", "b")),
      ConfigField(name="empty", choices=()),
    ),
  )

  assert request.validate({"pick": "a"}) == []
  assert request.validate({"pick": "c"}) == ["demo: pick must be one of: a, b"]
  assert request.validate({"empty": "x"}) == [
    "demo: empty must be one of: (none defined)"
  ]


def test_a_host_volume_is_a_choice_of_the_declared_host_volumes(kelso_env):
  _, _, request = _app_request(kelso_env, "host-volumes")

  bind = request.field("volume.hostvol1")
  assert bind.choices == ("media", "other")
  assert bind.desc == "Where your extra stuff lives"
  assert "volume.hostvol1" in request.missing()


def test_every_route_is_a_choice_of_providers_private_ones_included(kelso_env):
  _, _, request = _app_request(kelso_env, "routes-demo")

  routes = {f.name: f for f in request.fields if f.name.startswith("route.")}
  assert set(routes) == {"route.main", "route.api", "route.admin"}
  assert routes["route.admin"].choices == ("none", "web")
  assert all(not field.required for field in routes.values())


def test_applying_binds_and_routes_goes_through_bind_and_assign_route(kelso_env):
  from kelso.lib.configflow.app import apply_app_config

  (kelso_env.root / "external-data").mkdir()
  spec, ctx, _ = _app_request(kelso_env, "host-volumes")
  written = apply_app_config(
    spec, ConfigResponse(values={"volume.hostvol1": "media"}), ctx
  )
  assert written == ["volume.hostvol1"]
  assert ctx.app_store(spec.app).list_binds() == {"hostvol1": "media"}

  spec, ctx, _ = _app_request(kelso_env, "routes-demo")
  apply_app_config(spec, ConfigResponse(values={"route.admin": "web"}), ctx)
  assert ctx.app_store(spec.app).get_route_assignment("admin") == "web"


def test_a_bind_to_an_undeclared_host_volume_is_refused(kelso_env):
  from kelso.lib.configflow.app import apply_app_config

  spec, ctx, _ = _app_request(kelso_env, "host-volumes")
  with pytest.raises(ValueError, match="must be one of: media, other"):
    apply_app_config(spec, ConfigResponse(values={"volume.hostvol1": "nope"}), ctx)


def test_the_line_form_lists_a_fields_choices():
  request = ConfigRequest(
    title="demo",
    fields=(ConfigField(name="volume.files", choices=("media", "other")),),
  )
  conn = _ScriptedConn("media", "s")

  response = run_form(request, conn)

  assert "  one of: media, other" in conn.out_lines
  assert response.values == {"volume.files": "media"}
