from __future__ import annotations

from typing import TYPE_CHECKING

from kelso.lib.config import NONE_ROUTE_PROVIDER_TAG, format_route_providers
from kelso.lib.configflow import ConfigField, ConfigRequest, ConfigResponse
from kelso.lib.manifest import NETWORK_MODES
from kelso.lib.options import APP_OPTIONS
from kelso.lib.run_layout import resolved_subdomain
from kelso.lib.spec import PRIMARY_ROUTE_NAME, AppSpec
from kelso.lib.util import PUBLIC_ROUTE_SCHEME

if TYPE_CHECKING:
  from kelso.lib.kelso import KelsoCtx

VOLUME_PREFIX = "volume."
ROUTE_PREFIX = "route."
BACKUP_PREFIX = "backup."
BACKUP_CHOICES = ("on", "off")
NETWORK_FIELD = "network.mode"


def _config_fields(spec: AppSpec, ctx: KelsoCtx) -> list[ConfigField]:
  store = ctx.app_store(spec.app)
  fields = []
  for name, config in spec.config.items():
    secret, value = store.get_config(name)
    option = APP_OPTIONS.get(name)
    fields.append(
      ConfigField(
        name=name,
        value=None if config.secret else value,
        default=config.default if config.has_default() else None,
        secret=config.secret,
        secret_set=bool(secret) and value is not None,
        desc=config.desc or "",
        section=config.section,
        # A secret's default is a generator kelso runs at load, so nobody
        # has to supply it.
        required=not (config.secret and config.default is not None),
        choices=option.choices if option else None,
        labels=option.labels if option else (),
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
  tags = tuple(
    tag for tag in sorted(ctx.config.route_providers) if tag != NONE_ROUTE_PROVIDER_TAG
  )
  app_subdomain = resolved_subdomain(spec, ctx)
  return [
    ConfigField(
      name=f"{ROUTE_PREFIX}{name}",
      value=None if picked is None else format_route_providers(picked),
      desc=route.desc + (" (admin: not published by default)" if route.admin else ""),
      # Unassigned is a valid state: the route stays on its host port.
      required=False,
      section="route",
      choices=tags,
      multi=True,
      empty_value=NONE_ROUTE_PROVIDER_TAG,
      primary_desc=_primary_route_desc(name),
      choice_urls=tuple(
        f"{PUBLIC_ROUTE_SCHEME}://{route.subdomain(app_subdomain)}."
        f"{ctx.config.provider_domain(tag)}"
        for tag in tags
      )
      if app_subdomain
      else (),
    )
    for name, route in spec.routes.items()
    for picked in [assignments.get(name)]
  ]


def _primary_route_desc(route_name: str) -> str:
  given = f"${{routes.{route_name}}}"
  if route_name == PRIMARY_ROUTE_NAME:
    given += " and ${klso.domain}"
  return f"Primary route: passed to the app as {given}"


def _backup_fields(spec: AppSpec, ctx: KelsoCtx) -> list[ConfigField]:
  on = ctx.app_store(spec.app).backed_up_bulk()
  return [
    ConfigField(
      name=f"{BACKUP_PREFIX}{name}",
      value="on" if name in on else "off",
      desc=f"Back up {name}" + (f": {volume.desc}" if volume.desc else ""),
      required=False,
      choices=BACKUP_CHOICES,
    )
    for name, volume in spec.volumes.items()
    if volume.kind == "bulk"
  ]


def _network_field(spec: AppSpec, ctx: KelsoCtx) -> ConfigField:
  host = spec.network_mode == "host"
  return ConfigField(
    name=NETWORK_FIELD,
    value=ctx.app_store(spec.app).get_network_mode() or spec.network_mode,
    default=spec.network_mode,
    desc=f"Override the manifest's network mode, {spec.network_mode}",
    section="option",
    required=False,
    choices=("host",) if host else tuple(m for m in NETWORK_MODES if m != "host"),
  )


def app_config_request(spec: AppSpec, ctx: KelsoCtx) -> ConfigRequest:
  return ConfigRequest(
    title=str(spec.app),
    fields=(
      *_config_fields(spec, ctx),
      *_bind_fields(spec, ctx),
      *_route_fields(spec, ctx),
      *_backup_fields(spec, ctx),
      _network_field(spec, ctx),
    ),
    note=spec.description,
    config_title="App config",
  )


def apply_app_config(
  spec: AppSpec, response: ConfigResponse, ctx: KelsoCtx
) -> list[str]:
  from kelso.lib.lifecycle import apply_config_sets, assign_route, bind, set_network

  request = app_config_request(spec, ctx)
  errors = request.validate(response.values)
  if errors:
    raise ValueError("; ".join(errors))

  # An unset field sent back as its default stays unset, so it keeps following
  # the manifest.
  changed = [
    (name, value)
    for name, value in response.values.items()
    if (field := request.field(name)).value != value
    and not (field.value is None and value == field.default)
  ]
  sets = [(n, v) for n, v in changed if "." not in n]
  if sets:
    apply_config_sets(spec, sets, ctx)
  for name, value in changed:
    if name.startswith(VOLUME_PREFIX):
      bind(spec, name.removeprefix(VOLUME_PREFIX), value, ctx)
    elif name.startswith(ROUTE_PREFIX):
      assign_route(spec, name.removeprefix(ROUTE_PREFIX), value, ctx)
    elif name.startswith(BACKUP_PREFIX):
      volume = name.removeprefix(BACKUP_PREFIX)
      ctx.app_store(spec.app).set_backup_bulk(volume, value == "on")
    elif name == NETWORK_FIELD:
      set_network(spec, value, ctx)
  return [name for name, _ in changed]
