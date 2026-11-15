"""What an app behind kproxy reaches, and the hosts the operator decided on.

Decisions are kelso's, kept in the app store and written into kproxy's rules at
every start. Traffic and pending hosts are kproxy's, read live from the running
proxy, and reset whenever it restarts.
"""

import json
import logging
from typing import Any

from kelso.lib import kproxy
from kelso.lib.apps import AppID
from kelso.lib.docker import DockerError, docker_output
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle.run import compose_env
from kelso.lib.manifest import PROXIED_MODES
from kelso.lib.run_layout import load_run_data, network_mode
from kelso.lib.spec import AppSpec

logger = logging.getLogger("kelso.net")

_ADMIN = ["/kproxy", "admin", "-admin", f"{kproxy.KPROXY_GUEST_DIR}/admin.sock"]


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
  name = kproxy.endpoint_name(host)
  ctx.app_store(spec.app).set_host(name, verdict)
  if is_proxied_and_running(spec, ctx):
    restricted = load_run_data(spec, ctx).restricted
    assert restricted is not None
    kproxy_admin(spec.app, ctx, "PUT", "/rules", kproxy.rules_json(restricted.rules))
  return name


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
