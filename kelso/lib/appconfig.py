"""An app's `[config]` as a HasConfig target."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from kelso.lib.configreq import ConfigField, ConfigRequest, ConfigResponse
from kelso.lib.spec import AppSpec

if TYPE_CHECKING:
  from kelso.lib.kelso import KelsoCtx


@dataclass(frozen=True)
class AppTarget:
  """The `[config]` of one app, read from its manifest and store."""

  spec: AppSpec
  ctx: KelsoCtx

  def config_request(self) -> ConfigRequest:
    store = self.ctx.app_store(self.spec.app)
    fields = []
    for name, config in self.spec.config.items():
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
      title=str(self.spec.app),
      fields=tuple(fields),
      note=self.spec.description,
    )

  def apply_config(self, response: ConfigResponse) -> list[str]:
    from kelso.lib.lifecycle import apply_config_sets

    sets = [(name, value) for name, value in response.values.items() if value]
    if sets:
      apply_config_sets(self.spec, sets, self.ctx)
    return [name for name, _ in sets]
