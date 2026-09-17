"""A route provider's settings as a HasConfig target.

Answers land in two places: secrets in the kelso db, everything else in
`[route_provider.<tag>]` in config.toml. The `<name>_secret` indirection in the
block is generated here, so an operator is only ever asked for the secret itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from kelso.lib.config_edit import set_kelso_address, set_route_provider
from kelso.lib.configreq import ConfigField, ConfigRequest, ConfigResponse

if TYPE_CHECKING:
  from kelso.lib.kelso import KelsoCtx

TARGET_PREFIX = "route_provider:"

DOMAIN_FIELD = ConfigField(
  name="domain",
  desc="Domain routes are published under, e.g. example.com",
)

# Global rather than per-provider, but a provider cannot be written without it.
ADDRESS_FIELD = ConfigField(
  name="kelso_address",
  desc="LAN address this host is reachable at, e.g. 10.0.0.5",
)


def provider_target(tag: str) -> str:
  return f"{TARGET_PREFIX}{tag}"


def secret_ref(tag: str, name: str) -> str:
  return f"route_provider.{tag}.{name}"


@dataclass(frozen=True)
class ProviderTarget:
  """One `[route_provider.<tag>]` block, existing or about to exist."""

  kind: str
  tag: str
  ctx: KelsoCtx

  def _provider(self):
    from kelso.lib.routes import PROVIDERS

    provider = PROVIDERS.get(self.kind)
    if provider is None:
      known = ", ".join(sorted(PROVIDERS))
      raise ValueError(
        f"Unknown route provider kind {self.kind!r}; expected one of {known}"
      )
    return provider

  def config_request(self) -> ConfigRequest:
    provider = self._provider()
    existing = self.ctx.config.route_providers.get(self.tag)
    fields = []
    for declared in (DOMAIN_FIELD, ADDRESS_FIELD, *provider.config_fields()):
      if declared.name == "domain":
        value = existing.domain if existing else None
      elif declared.name == "kelso_address":
        value = self.ctx.config.kelso_address or None
      elif declared.secret:
        value = None
      else:
        value = existing.args.get(declared.name) if existing else None

      secret_set = bool(
        declared.secret
        and self.ctx.kelso_db.get_secret(secret_ref(self.tag, declared.name))
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
      target=provider_target(self.tag),
      title=f"route provider {self.tag} ({self.kind})",
      fields=tuple(fields),
      note=f"Check it afterwards with `kelso routes check {self.tag}`",
    )

  def apply_config(self, response: ConfigResponse) -> list[str]:
    expected = provider_target(self.tag)
    if response.target != expected:
      raise ValueError(
        f"Response is for {response.target!r}, not {expected!r}; "
        f"build a new request for this provider"
      )

    request = self.config_request()
    errors = request.validate(response.values)
    if errors:
      raise ValueError("; ".join(errors))

    existing = self.ctx.config.route_providers.get(self.tag)
    domain = response.values.get("domain") or (existing.domain if existing else "")
    if not domain:
      raise ValueError(f"route_provider.{self.tag} needs a domain")

    written = []
    address = response.values.get("kelso_address")
    if address and address != self.ctx.config.kelso_address:
      set_kelso_address(self.ctx, address)
      written.append("kelso_address")

    args = dict(existing.args) if existing else {}
    for field in request.fields:
      value = response.values.get(field.name)
      if not value or field.name in ("domain", "kelso_address"):
        continue
      if field.secret:
        ref = secret_ref(self.tag, field.name)
        self.ctx.kelso_db.set_secret(ref, value)
        args[f"{field.name}_secret"] = ref
      else:
        args[field.name] = value
      written.append(field.name)

    if response.values.get("domain"):
      written.append("domain")
    set_route_provider(self.ctx, self.tag, kind=self.kind, domain=domain, args=args)
    return written
