"""An app's `[config]` as a ConfigRequest."""

from __future__ import annotations

from typing import TYPE_CHECKING

from kelso.lib.configflow import ConfigField, ConfigRequest, ConfigResponse
from kelso.lib.spec import AppSpec

if TYPE_CHECKING:
  from kelso.lib.kelso import KelsoCtx


def app_config_request(spec: AppSpec, ctx: KelsoCtx) -> ConfigRequest:
  """What `spec` needs from the operator, with what is on file now."""
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
      )
    )
  return ConfigRequest(
    title=str(spec.app),
    fields=tuple(fields),
    note=spec.description,
  )


def apply_app_config(
  spec: AppSpec, response: ConfigResponse, ctx: KelsoCtx
) -> list[str]:
  """Write the values collected for `spec`, returning the names written."""
  from kelso.lib.lifecycle import apply_config_sets

  sets = [(name, value) for name, value in response.values.items() if value]
  if sets:
    apply_config_sets(spec, sets, ctx)
  return [name for name, _ in sets]
