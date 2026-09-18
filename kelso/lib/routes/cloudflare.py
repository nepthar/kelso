from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import requests

from kelso.lib.apps import AppID
from kelso.lib.config import RouteProviderEntry
from kelso.lib.configflow import ConfigField

from .base import RouteProvider, RouteProviderError, refuse_foreign_route

if TYPE_CHECKING:
  from kelso.lib.kelso import KelsoCtx

logger = logging.getLogger("kelso.routes")

# Addresses that resolve to the cloudflared container rather than the host.
LOOPBACK_ADDRESSES = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})


class CloudflareTunnelRouteProvider(RouteProvider):
  """Publish kelso app routes through a remotely-managed Cloudflare Tunnel.

  Two records make one route: an ingress rule on the tunnel's configuration,
  which tells cloudflared where to send a hostname, and a proxied CNAME in the
  zone, which points that hostname at the tunnel. Ownership is marked in the
  DNS record's comment, the only field Cloudflare lets us write on either side.
  """

  KIND = "cloudflare_tunnel"
  REQUIRED_ARGS = ("account_id", "tunnel_id", "api_token_secret")

  API_BASE = "https://api.cloudflare.com/client/v4"
  COMMENT_PREFIX = "kelso:"
  # Every tunnel config ends with a catch-all; ingress rules go before it.
  CATCH_ALL = {"service": "http_status:404"}

  @classmethod
  def config_fields(cls) -> tuple[ConfigField, ...]:
    return (
      ConfigField(name="account_id", desc="Account id from the dashboard URL"),
      ConfigField(name="tunnel_id", desc="Tunnel id from Zero Trust > Networks"),
      ConfigField(
        name="api_token",
        secret=True,
        desc="Token with Cloudflare Tunnel: Edit and DNS: Edit",
      ),
      ConfigField(
        name="zone_id",
        required=False,
        advanced=True,
        desc="Only needed when the token cannot list zones",
      ),
    )

  @classmethod
  def from_config(
    cls,
    tag: str,
    conf: RouteProviderEntry,
    ctx: KelsoCtx,
  ) -> CloudflareTunnelRouteProvider:
    args = cls._args(tag, conf)
    api_token = cls._secret(ctx.kelso_db, args.pop("api_token_secret"))
    return cls(
      api_token=api_token,
      kelso_domain=conf.domain,
      kelso_address=ctx.config.kelso_address,
      **args,
    )

  def __init__(
    self,
    account_id: str,
    tunnel_id: str,
    api_token: str,
    kelso_domain: str,
    kelso_address: str,
    zone_id: str = "",
    timeout: float = 30.0,
  ):
    self.account_id = account_id
    self.tunnel_id = tunnel_id
    self._api_token = api_token
    self.kelso_domain = kelso_domain
    self.kelso_address = kelso_address
    self._zone_id = zone_id or None
    self._timeout = timeout
    self._session = requests.Session()

  # ── low-level request helper ──────────────────────────────────────────

  def _failure(self, method: str, path: str, resp: requests.Response) -> str:
    try:
      body = resp.json()
    except ValueError:
      return f"Cloudflare {method} {path} returned {resp.status_code}: {resp.reason}"

    errors = (body or {}).get("errors") or []
    detail = "; ".join(
      f"{e.get('code', '?')}: {e.get('message', '')}".strip() for e in errors
    )
    if resp.status_code in (401, 403):
      return (
        f"Cloudflare refused {method} {path} ({resp.status_code}: {detail}). The "
        f"API token needs Account > Cloudflare Tunnel: Edit and Zone > DNS: Edit "
        f"on {self.kelso_domain}"
      )
    return f"Cloudflare {method} {path} returned {resp.status_code}: {detail}"

  def _request(self, method: str, path: str, **kwargs) -> Any:
    """Call the v4 API and unwrap Cloudflare's response envelope."""
    if not self._api_token:
      raise RouteProviderError("A Cloudflare API token is required to authenticate")

    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"Bearer {self._api_token}"
    try:
      resp = self._session.request(
        method,
        f"{self.API_BASE}{path}",
        headers=headers,
        timeout=self._timeout,
        **kwargs,
      )
    except requests.RequestException as e:
      raise RouteProviderError(f"Cloudflare {method} {path} failed: {e}") from e

    if not resp.ok:
      raise RouteProviderError(self._failure(method, path, resp))

    body = resp.json() if resp.content else {}
    if isinstance(body, dict) and not body.get("success", True):
      raise RouteProviderError(self._failure(method, path, resp))
    return body.get("result") if isinstance(body, dict) else body

  # ── zone ──────────────────────────────────────────────────────────────

  def zone_id(self) -> str:
    """The zone carrying the kelso domain, looked up once by name."""
    if self._zone_id is None:
      zones = self._request("GET", "/zones", params={"name": self.kelso_domain}) or []
      if not zones:
        raise RouteProviderError(
          f"Cloudflare has no zone {self.kelso_domain!r} on this account; add the "
          f"domain to Cloudflare, or set args.zone_id if the token cannot list zones"
        )
      self._zone_id = str(zones[0]["id"])
    return self._zone_id

  # ── tunnel ingress ────────────────────────────────────────────────────

  def _config(self) -> dict:
    """The tunnel's remote configuration, as Cloudflare stores it."""
    result = (
      self._request(
        "GET",
        f"/accounts/{self.account_id}/cfd_tunnel/{self.tunnel_id}/configurations",
      )
      or {}
    )
    return result.get("config") or {}

  def _put_config(self, config: dict) -> None:
    self._request(
      "PUT",
      f"/accounts/{self.account_id}/cfd_tunnel/{self.tunnel_id}/configurations",
      json={"config": config},
    )

  @staticmethod
  def _rules(config: dict) -> list[dict]:
    """Ingress rules with the catch-all dropped; it is re-added on write."""
    return [rule for rule in config.get("ingress") or [] if rule.get("hostname")]

  def _write_rules(self, config: dict, rules: list[dict]) -> None:
    self._put_config({**config, "ingress": [*rules, self.CATCH_ALL]})

  # ── dns ───────────────────────────────────────────────────────────────

  def _dns_record(self, hostname: str) -> dict | None:
    records = (
      self._request(
        "GET",
        f"/zones/{self.zone_id()}/dns_records",
        params={"name": hostname},
      )
      or []
    )
    return records[0] if records else None

  def _dns_owner(self, record: dict) -> str | None:
    comment = record.get("comment") or ""
    if comment.startswith(self.COMMENT_PREFIX):
      return comment[len(self.COMMENT_PREFIX) :]
    return None

  def _upsert_dns(self, app: AppID, hostname: str, existing: dict | None) -> None:
    payload = {
      "type": "CNAME",
      "name": hostname,
      "content": f"{self.tunnel_id}.cfargotunnel.com",
      "proxied": True,
      "comment": f"{self.COMMENT_PREFIX}{app}",
    }
    zone = self.zone_id()
    if existing:
      self._request("PUT", f"/zones/{zone}/dns_records/{existing['id']}", json=payload)
    else:
      self._request("POST", f"/zones/{zone}/dns_records", json=payload)

  def _hostname(self, subdomain: str | None, domain: str) -> str:
    return f"{subdomain}.{domain}" if subdomain else domain

  # ── RouteProvider interface ───────────────────────────────────────────

  def validate(self) -> list[str]:
    try:
      self._request("GET", "/user/tokens/verify")
    except RouteProviderError as e:
      return [str(e)]

    errors: list[str] = []
    try:
      self.zone_id()
    except RouteProviderError as e:
      errors.append(str(e))

    try:
      tunnel = (
        self._request("GET", f"/accounts/{self.account_id}/cfd_tunnel/{self.tunnel_id}")
        or {}
      )
      status = str(tunnel.get("status") or "").lower()
      if tunnel.get("deleted_at"):
        errors.append(f"Cloudflare tunnel {self.tunnel_id} is deleted")
      elif status in ("down", "inactive") or (
        not status and not tunnel.get("connections")
      ):
        errors.append(
          f"Cloudflare tunnel {self.tunnel_id} is {status or 'not connected'}; "
          f"start the connector with `kelso start cloudflared`"
        )
      if tunnel.get("remote_config") is False:
        errors.append(
          f"Cloudflare tunnel {self.tunnel_id} is locally managed, so it reads a "
          f"config.yml and ignores the ingress rules kelso writes. Recreate it "
          f"in the dashboard (Zero Trust > Networks > Tunnels)"
        )
    except RouteProviderError as e:
      errors.append(f"Cloudflare tunnel {self.tunnel_id} is not usable: {e}")

    if not self.kelso_address:
      errors.append("kelso_address is unset, so routes have nowhere to point")
    elif self.kelso_address in LOOPBACK_ADDRESSES:
      errors.append(
        f"kelso_address is {self.kelso_address!r}, which inside the cloudflared "
        f"container means the container itself. Set it to the LAN address of the "
        f"host running the apps (or host.docker.internal on Docker Desktop)"
      )
    return errors

  def register_route(
    self,
    app: AppID,
    port: int,
    subdomain: str,
    domain: str,
    scheme: str = "http",
  ):
    """Route hostname -> kelso_address:port, over the tunnel."""
    hostname = self._hostname(subdomain, domain)

    record = self._dns_record(hostname)
    if record is not None and self._dns_owner(record) != app:
      raise refuse_foreign_route(hostname, self._dns_owner(record))

    service = f"{scheme}://{self.kelso_address}:{port}"
    config = self._config()
    rules = [rule for rule in self._rules(config) if rule.get("hostname") != hostname]
    logger.info("routing %s -> %s over tunnel %s", hostname, service, self.tunnel_id)
    self._write_rules(config, [*rules, {"hostname": hostname, "service": service}])
    self._upsert_dns(app, hostname, record)

  def unregister_route(self, subdomain: str, domain: str):
    hostname = self._hostname(subdomain, domain)
    config = self._config()
    rules = self._rules(config)
    remaining = [rule for rule in rules if rule.get("hostname") != hostname]
    if len(remaining) != len(rules):
      logger.info("dropping tunnel ingress for %s", hostname)
      self._write_rules(config, remaining)

    record = self._dns_record(hostname)
    if record is None:
      return
    if self._dns_owner(record) is None:
      logger.info("leaving DNS record for %s alone; kelso does not own it", hostname)
      return
    self._request("DELETE", f"/zones/{self.zone_id()}/dns_records/{record['id']}")

  def _kelso_rules(self) -> list[tuple[str, dict]]:
    """Yield (subdomain, rule) for single-label hostnames under the kelso domain."""
    suffix = f".{self.kelso_domain}"
    out: list[tuple[str, dict]] = []
    for rule in self._rules(self._config()):
      hostname = rule.get("hostname") or ""
      if not hostname.endswith(suffix):
        continue
      subdomain = hostname[: -len(suffix)]
      if not subdomain or "." in subdomain:
        continue
      out.append((subdomain, rule))
    return out

  def list_routes(self) -> list[tuple[str, str]]:
    return [
      (subdomain, rule.get("service") or "(no service)")
      for subdomain, rule in self._kelso_rules()
    ]

  def route_owners(self) -> dict[str, str | None]:
    owners: dict[str, str | None] = {}
    for subdomain, _ in self._kelso_rules():
      record = self._dns_record(f"{subdomain}.{self.kelso_domain}")
      owners[subdomain] = self._dns_owner(record) if record else None
    return owners
