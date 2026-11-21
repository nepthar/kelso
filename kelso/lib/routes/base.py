from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from kelso.lib.apps import AppID
from kelso.lib.config import RouteProviderEntry
from kelso.lib.configflow import ConfigField
from kelso.lib.store import KelsoStore

if TYPE_CHECKING:
  from kelso.lib.kelso import KelsoCtx

logger = logging.getLogger("kelso.routes")


class RouteProviderError(RuntimeError):
  pass


class RouteProviderUnreachable(RouteProviderError):
  pass


def refuse_foreign_route(domain_name: str, owner: str | None) -> RouteProviderError:
  owner_desc = f"app {owner!r}" if owner else "a non-Kelso proxy host"
  return RouteProviderError(
    f"Refusing to replace {domain_name}; it is already owned by {owner_desc}"
  )


class RouteProvider:
  # The config `kind` that selects this provider. `get_route_provider` builds
  # its dispatch table from these, so a new provider only has to set one.
  KIND: str = ""
  # `args` keys that must be present and non-empty in the provider's block.
  REQUIRED_ARGS: tuple[str, ...] = ()

  @classmethod
  def config_fields(cls) -> tuple[ConfigField, ...]:
    return ()

  @classmethod
  def from_config(
    cls,
    tag: str,
    conf: RouteProviderEntry,
    ctx: KelsoCtx,
  ) -> RouteProvider:
    raise NotImplementedError

  @classmethod
  def _args(cls, tag: str, conf: RouteProviderEntry) -> dict[str, str]:
    missing = [name for name in cls.REQUIRED_ARGS if not conf.args.get(name)]
    if missing:
      raise RouteProviderError(
        f"route_provider.{tag}.args: missing {missing}; needs {cls.REQUIRED_ARGS}"
      )
    return dict(conf.args)

  @staticmethod
  def _secret(kelso_db: KelsoStore, ref: str) -> str:
    value = kelso_db.get_secret(ref)
    if not value:
      raise RouteProviderError(
        f"Missing host secret {ref!r}. Set it with `kelso secret set {ref}`, "
        f"which asks for the value"
      )
    return value

  def register_route(
    self,
    app: AppID,
    port: int,
    subdomain: str,
    domain: str,
    scheme: str = "http",
  ):
    raise NotImplementedError

  def unregister_route(self, subdomain: str, domain: str):
    raise NotImplementedError

  def list_routes(self) -> list[tuple[str, str]]:
    raise NotImplementedError

  def route_owners(self) -> dict[str, str | None]:
    raise NotImplementedError

  def validate(self) -> list[str]:
    raise NotImplementedError


class NoopRouteProvider(RouteProvider):
  KIND = "noop"

  @classmethod
  def from_config(
    cls,
    tag: str,
    conf: RouteProviderEntry,
    ctx: KelsoCtx,
  ) -> NoopRouteProvider:
    return cls(domain=conf.domain)

  def __init__(self, domain: str = ""):
    self.domain = domain
    self.routes: dict[str, str] = {}
    self.owners: dict[str, str] = {}

  def register_route(
    self,
    app: AppID,
    port: int,
    subdomain: str,
    domain: str,
    scheme: str = "http",
  ):
    route = f"{subdomain}.{domain}"
    owner = self.owners.get(subdomain)
    if owner is not None and owner != app:
      raise refuse_foreign_route(route, owner)
    self.routes[subdomain] = f":{port}"
    self.owners[subdomain] = app
    logger.warning(f"Noop route provider - {app} registered {route}")

  def unregister_route(self, subdomain: str, domain: str):
    route = f"{subdomain}.{domain}"
    logger.debug(f"Noop route provider - Unregister {route}")
    self.routes.pop(subdomain, None)
    self.owners.pop(subdomain, None)

  def list_routes(self) -> list[tuple[str, str]]:
    return sorted(self.routes.items())

  def route_owners(self) -> dict[str, str | None]:
    return {subdomain: self.owners.get(subdomain) for subdomain in self.routes}

  def validate(self) -> list[str]:
    return []
