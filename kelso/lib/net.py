"""What an app behind kproxy reaches, and the hosts the operator decided on.

Decisions are kelso's, kept in the app store and written into kproxy's rules at
every start. Traffic and pending hosts are kproxy's, read live from the running
proxy, and reset whenever it restarts.
"""

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
  """One request to the app's running kproxy; its JSON answer, or None if empty.

  Raises RuntimeError naming the fix when kproxy can't be reached.
  """
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
  """Record allow, deny, or (None) forget for `host` and its subdomains, and
  apply it to a running kproxy at once. Returns the domain as stored."""
  return decide_many(spec, {host: verdict}, ctx)[0]


def decide_many(
  spec: AppSpec, decisions: Mapping[str, str | None], ctx: KelsoCtx
) -> list[str]:
  """`decide` for each domain, with one push to a running kproxy.

  Raises ValueError, before anything is written, for a bad domain or verdict.
  """
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
  """One row per domain counted on 80 and 443, waiting, decided, or listed as a
  web host, sorted by domain."""
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
  """The `[connections]` entry that lets `domain` through, if any."""
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
  """The app's network mode and its domains: live from a running kproxy, else
  from its last run, else only what is decided or listed.

  Raises RuntimeError naming the fix when a running app's kproxy can't be reached.
  """
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
  """`2 domains waiting` for a running app in ask mode holding connections, else ''."""
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
  """What the app's kproxy saw before it last stopped, or None if nothing was kept."""
  path = ctx.loaded_paths(app).run_path / kproxy.KPROXY_RUN_DIR / kproxy.STATS_FILE
  try:
    return json.loads(path.read_text())
  except (OSError, ValueError):
    return None


def covers(rule: str, domain: str) -> bool:
  """Whether a domain rule, which takes its subdomains with it, covers `domain`."""
  return domain == rule or domain.endswith("." + rule)
