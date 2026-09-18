"""A route provider's settings as a ConfigRequest.

Answers land in two places: secrets in the kelso db, everything else in
`[route_provider.<tag>]` in config.toml. The `<name>_secret` indirection in the
block is generated here, so an operator is only ever asked for the secret itself.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from kelso.lib.config_edit import set_kelso_address, set_route_provider
from kelso.lib.configflow import ConfigField, ConfigRequest, ConfigResponse
from kelso.lib.routes import PROVIDERS, RouteProvider

if TYPE_CHECKING:
  from kelso.lib.kelso import KelsoCtx

DOMAIN_FIELD = ConfigField(
  name="domain",
  desc="Domain routes are published under, e.g. example.com",
)

# Global rather than per-provider, but a provider cannot be written without it.
ADDRESS_FIELD = ConfigField(
  name="kelso_address",
  desc="LAN address this host is reachable at, e.g. 10.0.0.5",
)


def secret_ref(tag: str, name: str) -> str:
  return f"route_provider.{tag}.{name}"


def resolve_route_provider(
  tag: str, ctx: KelsoCtx, kind: str = ""
) -> type[RouteProvider]:
  """The provider class `tag` is, or is about to become.

  An existing tag takes its kind from config.toml; a new one needs `kind`.
  """
  existing = ctx.config.route_providers.get(tag)
  if existing and kind and kind != existing.kind:
    raise ValueError(
      f"Route provider {tag!r} is already {existing.kind!r}; remove it from "
      f"config.toml before configuring it as {kind!r}"
    )
  resolved = kind or (existing.kind if existing else "")
  if not resolved:
    raise ValueError(f"Route provider {tag!r} is new, so it needs --kind")

  provider = PROVIDERS.get(resolved)
  if provider is None:
    known = ", ".join(sorted(PROVIDERS))
    raise ValueError(
      f"Unknown route provider kind {resolved!r}; expected one of {known}"
    )
  return provider


def route_provider_config_request(
  tag: str, provider: type[RouteProvider], ctx: KelsoCtx
) -> ConfigRequest:
  """What `provider` needs to publish routes, filled in from `tag` if it exists."""
  existing = ctx.config.route_providers.get(tag)
  fields = []
  for declared in (DOMAIN_FIELD, ADDRESS_FIELD, *provider.config_fields()):
    if declared.name == "domain":
      value = existing.domain if existing else None
    elif declared.name == "kelso_address":
      value = ctx.config.kelso_address or None
    elif declared.secret:
      value = None
    else:
      value = existing.args.get(declared.name) if existing else None

    secret_set = bool(
      declared.secret and ctx.kelso_db.get_secret(secret_ref(tag, declared.name))
    )
    fields.append(
      ConfigField(
        name=declared.name,
        value=value,
        default=declared.default,
        secret=declared.secret,
        secret_set=secret_set,
        desc=declared.desc,
        advanced=declared.advanced,
        required=declared.required,
      )
    )

  return ConfigRequest(
    title=f"route provider {tag} ({provider.KIND})",
    fields=tuple(fields),
    note=f"Check it afterwards with `kelso routes check {tag}`",
  )


def apply_route_provider_config(
  tag: str, provider: type[RouteProvider], response: ConfigResponse, ctx: KelsoCtx
) -> list[str]:
  """Store secrets and write `[route_provider.<tag>]`, returning the names written."""
  request = route_provider_config_request(tag, provider, ctx)
  errors = request.validate(response.values)
  if errors:
    raise ValueError("; ".join(errors))

  existing = ctx.config.route_providers.get(tag)
  domain = response.values.get("domain") or (existing.domain if existing else "")
  if not domain:
    raise ValueError(f"route_provider.{tag} needs a domain")

  written = []
  address = response.values.get("kelso_address")
  if address and address != ctx.config.kelso_address:
    set_kelso_address(ctx, address)
    written.append("kelso_address")

  args = dict(existing.args) if existing else {}
  for field in request.fields:
    value = response.values.get(field.name)
    if not value or field.name in ("domain", "kelso_address"):
      continue
    if field.secret:
      ref = secret_ref(tag, field.name)
      ctx.kelso_db.set_secret(ref, value)
      args[f"{field.name}_secret"] = ref
    else:
      args[field.name] = value
    written.append(field.name)

  if response.values.get("domain"):
    written.append("domain")
  set_route_provider(ctx, tag, kind=provider.KIND, domain=domain, args=args)
  return written
