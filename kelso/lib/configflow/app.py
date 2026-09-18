"""Everything an operator sets on one app, as a ConfigRequest.

That is its `[config]` values, a host volume for each `kind = "host"` volume,
and a provider for each route. Binds and routes are named `volume.<name>`
and `route.<name>`, so they cannot collide with a config name, which has no dots.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from kelso.lib.configflow import ConfigField, ConfigRequest, ConfigResponse
from kelso.lib.spec import AppSpec

if TYPE_CHECKING:
  from kelso.lib.kelso import KelsoCtx

VOLUME_PREFIX = "volume."
ROUTE_PREFIX = "route."


def _config_fields(spec: AppSpec, ctx: KelsoCtx) -> list[ConfigField]:
  store = ctx.app_store(spec.app)
  fields = []
  for name, config in spec.config.items():
    secret, value = store.get_config(name)
    fields.append(
      ConfigField(
        name=name,
        value=None if config.secret else value,
        default=config.default if config.has_default() else None,
        secret=config.secret,
        secret_set=bool(secret) and value is not None,
        desc=config.desc or "",
        advanced=config.advanced,
        # A secret's default is a generator kelso runs at install, so nobody
        # has to supply it.
        required=not (config.secret and config.default is not None),
      )
    )
  return fields


def _bind_fields(spec: AppSpec, ctx: KelsoCtx) -> list[ConfigField]:
  binds = ctx.app_store(spec.app).list_binds()
  return [
    ConfigField(
      name=f"{VOLUME_PREFIX}{name}",
      value=binds.get(name),
      desc=volume.desc or f"Host volume that holds {name}",
      choices=tuple(sorted(ctx.config.host_volumes)),
    )
    for name, volume in spec.volumes.items()
    if volume.kind == "host"
  ]


def _route_fields(spec: AppSpec, ctx: KelsoCtx) -> list[ConfigField]:
  assignments = ctx.app_store(spec.app).list_route_assignments()
  return [
    ConfigField(
      name=f"{ROUTE_PREFIX}{name}",
      value=assignments.get(name),
      desc=route.desc or f"Route provider that publishes {name}",
      # Unassigned is a valid state: a private route stays on its host port.
      required=False,
      choices=tuple(sorted(ctx.config.route_providers)),
    )
    for name, route in spec.routes.items()
  ]


def app_config_request(spec: AppSpec, ctx: KelsoCtx) -> ConfigRequest:
  """What `spec` needs from the operator, with what is on file now."""
  return ConfigRequest(
    title=str(spec.app),
    fields=(
      *_config_fields(spec, ctx),
      *_bind_fields(spec, ctx),
      *_route_fields(spec, ctx),
    ),
    note=spec.description,
  )


def apply_app_config(
  spec: AppSpec, response: ConfigResponse, ctx: KelsoCtx
) -> list[str]:
  """Write the values collected for `spec`, returning the names written.

  A value equal to what is already on file is skipped, so a front end may send
  back every field it showed.
  """
  from kelso.lib.lifecycle import apply_config_sets, assign_route, bind

  request = app_config_request(spec, ctx)
  errors = request.validate(response.values)
  if errors:
    raise ValueError("; ".join(errors))

  changed = [
    (name, value)
    for name, value in response.values.items()
    if value != request.field(name).value
  ]
  sets = [(n, v) for n, v in changed if "." not in n]
  if sets:
    apply_config_sets(spec, sets, ctx)
  for name, value in changed:
    if name.startswith(VOLUME_PREFIX):
      bind(spec, name.removeprefix(VOLUME_PREFIX), value, ctx)
    elif name.startswith(ROUTE_PREFIX):
      assign_route(spec, name.removeprefix(ROUTE_PREFIX), value, ctx)
  return [name for name, _ in changed]
