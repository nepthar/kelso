import json
import logging
from collections.abc import Iterable, Mapping
from typing import Any

from kelso.lib import kproxy
from kelso.lib.apps import AppID
from kelso.lib.connections import HttpConnection, WebConnection, network_connections
from kelso.lib.docker import DockerError, docker_output
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle.run import compose_env
from kelso.lib.manifest import PROXIED_MODES
from kelso.lib.run_layout import load_run_data, network_mode
from kelso.lib.spec import AppSpec

logger = logging.getLogger("kelso.net")

_ADMIN = ["/kproxy", "admin", "-admin", kproxy.KPROXY_ADMIN_SOCKET]


def kproxy_admin(app: AppID, ctx: KelsoCtx, method: str, path: str, body=None) -> Any:
  cmd = ["compose", "exec", "-T", kproxy.KPROXY_SERVICE, *_ADMIN, method, path]
  if body is not None:
    cmd.append(json.dumps(body))
  try:
    out = docker_output(
      cmd,
      cwd=ctx.loaded_paths(app).run_path,
      env=compose_env(app, ctx),
      timeout=10,
    )
  except DockerError as e:
    raise RuntimeError(
      f"Could not reach {app}'s kproxy: {e.stderr.strip() or e}. "
      f"Is it running? `kelso start {app}`"
    ) from None
  return json.loads(out) if out.strip() else None


def is_proxied_and_running(spec: AppSpec, ctx: KelsoCtx) -> bool:
  data = load_run_data(spec, ctx)
  return data.network_mode in PROXIED_MODES and bool(
    ctx.run_state(spec.app).running_count
  )


def decide(spec: AppSpec, host: str, verdict: str | None, ctx: KelsoCtx) -> str:
  return decide_many(spec, {host: verdict}, ctx)[0]


def decide_many(
  spec: AppSpec, decisions: Mapping[str, str | None], ctx: KelsoCtx
) -> list[str]:
  named = {}
  for host, verdict in decisions.items():
    if verdict not in ("allow", "deny", None):
      raise ValueError(f"{host}: decide allow, deny, or null to forget")
    named[kproxy.endpoint_name(host)] = verdict
  store = ctx.app_store(spec.app)
  for name, verdict in named.items():
    store.set_host(name, verdict)
  if named and is_proxied_and_running(spec, ctx):
    restricted = load_run_data(spec, ctx).restricted
    assert restricted is not None
    kproxy_admin(spec.app, ctx, "PUT", "/rules", kproxy.rules_json(restricted.rules))
  return list(named)


def domain_rows(
  spec: AppSpec,
  stats: Mapping[str, Any],
  pending: Iterable[Mapping[str, Any]],
  decided: Mapping[str, str],
) -> list[dict[str, Any]]:
  waiting = {p["domain"]: sorted(p.get("hosts") or ()) for p in pending}
  listed = {
    host.lower().rstrip(".")
    for entry in network_connections(spec.connections).values()
    if isinstance(entry, WebConnection)
    for host in entry.hosts
  }
  counted = {
    domain
    for domain, listeners in stats.items()
    if any(listener in ("http", "https") for listener in listeners)
  }
  rows = []
  for domain in sorted(counted | waiting.keys() | decided.keys() | listed):
    total = {
      "conns": 0,
      "allowed": 0,
      "refused": 0,
      "bytes_in": 0,
      "bytes_out": 0,
      "last_seen": "",
    }
    for listener, count in (stats.get(domain) or {}).items():
      if listener not in ("http", "https"):
        continue
      for key in ("conns", "allowed", "bytes_in", "bytes_out"):
        total[key] += count.get(key, 0)
      total["refused"] += count.get("denied", 0)
      total["last_seen"] = max(total["last_seen"], count.get("last_seen", ""))
    decided_as = next((n for n in decided if covers(n, domain)), None)
    rows.append(
      {
        "domain": domain,
        "verdict": decided[decided_as] if decided_as else None,
        "decided_as": decided_as,
        "listed": listed_as(spec, domain),
        "waiting": domain in waiting,
        "hosts": waiting.get(domain, []),
        **total,
      }
    )
  return rows


def listed_as(spec: AppSpec, domain: str) -> str | None:
  for name, entry in network_connections(spec.connections).items():
    if isinstance(entry, HttpConnection) and domain == name:
      return name
    if isinstance(entry, WebConnection) and any(
      covers(host.lower().rstrip("."), domain) or covers(domain, host.lower())
      for host in entry.hosts
    ):
      return name
  return None


def net_view(spec: AppSpec, ctx: KelsoCtx) -> dict[str, Any]:
  mode = network_mode(spec, ctx)
  decided = ctx.app_store(spec.app).list_hosts()
  stats: Mapping[str, Any] = {}
  pending: list[Any] = []
  source = None
  if is_proxied_and_running(spec, ctx):
    pending = kproxy_admin(spec.app, ctx, "GET", "/pending") or []
    stats = kproxy_admin(spec.app, ctx, "GET", "/stats") or {}
    source = "live"
  elif mode in PROXIED_MODES and (last := last_run(spec.app, ctx)) is not None:
    stats = last.get("stats") or {}
    source = "last_run"
  return {
    "network_mode": mode,
    "manifest_mode": spec.network_mode,
    "proxied": mode in PROXIED_MODES,
    "source": source,
    "rows": domain_rows(spec, stats, pending, decided),
  }


def waiting_note(spec: AppSpec, ctx: KelsoCtx) -> str:
  if network_mode(spec, ctx) != "ask" or not ctx.run_state(spec.app).running_count:
    return ""
  try:
    count = len(kproxy_admin(spec.app, ctx, "GET", "/pending") or [])
  except RuntimeError as e:
    logger.debug("%s", e)
    return ""
  if not count:
    return ""
  return f"{count} domain{'s' if count > 1 else ''} waiting: kelso net {spec.app}"


def last_run(app: AppID, ctx: KelsoCtx) -> dict[str, Any] | None:
  path = ctx.loaded_paths(app).run_path / kproxy.KPROXY_RUN_DIR / kproxy.STATS_FILE
  try:
    return json.loads(path.read_text())
  except (OSError, ValueError):
    return None


def covers(rule: str, domain: str) -> bool:
  return domain == rule or domain.endswith("." + rule)
