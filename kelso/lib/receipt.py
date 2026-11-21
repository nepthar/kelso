from collections.abc import Mapping, Sequence

from kelso.lib.config import Config
from kelso.lib.connections import (
  HttpConnection,
  MountConnection,
  WebConnection,
  network_connections,
)
from kelso.lib.describe import PortFact, RouteFact, VolumeFact, describe
from kelso.lib.kelso import KelsoCtx
from kelso.lib.run_layout import AppRunData, resolved_subdomain
from kelso.lib.spec import AppSpec
from kelso.lib.util import EnvTemplate

# Every label in these receipts pads to at least this, so the capability block
# and the location block under it line up in one `kelso start`.
LABEL_WIDTH = len("Containers:")


def published_urls(spec: AppSpec, run_data: AppRunData, ctx: KelsoCtx) -> list[str]:
  routes = describe(spec, run_data, ctx).routes
  return [url for route in routes for _, url in route.providers]


def route_lines(
  routes: Sequence[RouteFact], published: Mapping[str, Sequence[str]] | None = None
) -> list[str]:
  lines: list[str] = []
  for route in routes:
    where = route.host_url or "(no host port allocated)"
    line = f"{route.name}: {route.unit}:{route.container_port} <- {where}"
    if published is None:
      urls: Sequence[str] = [url for _, url in route.providers]
    else:
      urls = published.get(route.name, ())
    if urls:
      line += f" <- {', '.join(urls)}"
    if route.admin:
      line += " (admin)"
    lines.append(line)
  return lines


def port_lines(ports: Sequence[PortFact], host: str) -> list[str]:
  lines: list[str] = []
  for port in ports:
    host_port = (
      port.host_port if port.host_port is not None else port.declared_host_port
    )
    where = "(no host port allocated)" if host_port is None else f"{host}:{host_port}"
    lines.append(
      f"{port.name}: {port.unit}:{port.container_port}/{port.proto} <- {where}"
    )
  return lines


def route_receipt_lines(routes: Sequence[RouteFact]) -> list[str]:
  return [
    _labeled_line("Routes:" if i == 0 else "", line)
    for i, line in enumerate(route_lines(routes))
  ]


_NETWORK_HEADS = {
  "restrict": "restrict: reaches only these",
  "ask": "ask: reaches these, and holds anything else for you to decide",
  "watch": "watch: reaches anything, logging each host; these are listed",
}


def network_lines(spec: AppSpec, run_data: AppRunData | None) -> list[str]:
  network = network_connections(spec.connections)
  mode = spec.network_mode if run_data is None else run_data.network_mode
  if mode in _NETWORK_HEADS:
    head = _NETWORK_HEADS[mode]
    if mode == "restrict" and not network:
      head = "restrict: reaches nothing"
  elif network:
    head = "open: reaches anything; web connections only apply behind kproxy"
  else:
    return []
  if mode != spec.network_mode:
    head += f" (set by you; the manifest says {spec.network_mode})"
  restricted = None if run_data is None else run_data.restricted
  forwards = {} if restricted is None else restricted.forwards
  lines = [head]
  for name, entry in network.items():
    if isinstance(entry, WebConnection):
      lines.append(f"{name}: {', '.join(entry.hosts)}")
    elif isinstance(entry, HttpConnection) and restricted is not None:
      lines.append(f"{name}: http://{name} -> {forwards.get(name, entry.address)}")
    elif isinstance(entry, HttpConnection):
      conn = run_data.connections.get(name) if run_data else None
      lines.append(f"{name}: {(conn and conn.properties.get('url')) or entry.address}")
  return lines


def config_lines(spec: AppSpec, ctx: KelsoCtx, *, loaded: bool) -> list[str]:
  if not spec.config:
    return []
  store = None
  if loaded and ctx.config.app_config_path(spec.app).is_file():
    store = ctx.app_store(spec.app)
  lines: list[str] = []
  for name, entry in spec.config.items():
    if store is None:
      if entry.secret:
        display = "(secret)"
      elif entry.has_default():
        display = f"{entry.default} (default)"
      else:
        display = "(required)"
    else:
      secret, value = store.get_config(name)
      if value is None:
        if entry.has_default():
          display = f"{entry.default} (default)"
        else:
          display = "(required)"
      elif secret:
        display = "(secret)"
      else:
        display = value
    lines.append(f"{name}: {display}")
  return lines


def _volume_where(volume: VolumeFact) -> str | None:
  if volume.path is not None:
    return volume.path
  return "(unbound)" if volume.kind == "host" else volume.default_path


def volume_lines(volumes: Sequence[VolumeFact]) -> list[str]:
  lines = []
  for volume in volumes:
    kind = f"{volume.kind}, read-only" if volume.readonly else volume.kind
    if (where := _volume_where(volume)) is not None:
      lines.append(f"{volume.name} ({kind}): {where}")
  return lines


def volume_root_lines(config: Config) -> list[str]:
  lines = []
  for kind, root in config.volume_roots.items():
    line = f"{kind + ':':<6} {root}"
    if root.is_symlink():
      line += f" -> {root.readlink()}"
      if config.dangling_volume_root(kind) is not None:
        line += " (missing)"
    lines.append(line)
  return lines


def location_receipt(
  spec: AppSpec,
  run_data: AppRunData,
  ctx: KelsoCtx,
  *,
  heading: str | None = None,
) -> str:
  app_id = spec.app
  title = heading if heading is not None else f"Running {app_id}"
  rows: list[tuple[str, str]] = []

  description = describe(spec, run_data, ctx)
  for i, line in enumerate(route_lines(description.routes)):
    rows.append(("Routes:" if i == 0 else "", line))
  for i, line in enumerate(port_lines(description.ports, ctx.config.link_address)):
    rows.append(("Ports:" if i == 0 else "", line))
  for volume in description.volumes:
    if volume.kind == "data" and not volume.readonly:
      rows.append(("Data:", f"{volume.name}: {_volume_where(volume)}"))

  rows.append(("Logs:", f"kelso logs -f {app_id}"))

  return _format_labeled(title, rows)


def capability_receipt(
  spec: AppSpec,
  run_data: AppRunData | None,
  ctx: KelsoCtx,
  *,
  compact: bool = True,
  notes: tuple[str, ...] = (),
  bundle_line: str | None = None,
  state_line: str | None = None,
  last_action: str | None = None,
  show_logs: bool = False,
) -> str:
  app_id = spec.app
  lines: list[str] = [f"{app_id}"]

  if not compact:
    about = [
      ("About:", spec.description),
      ("Version:", spec.version),
      ("Author:", spec.author),
      ("URL:", spec.url),
    ]
    lines += [_labeled_line(label, value) for label, value in about if value]

  if bundle_line is not None:
    lines.append(_labeled_line("Bundle:", bundle_line))
  if state_line is not None:
    lines.append(_labeled_line("State:", state_line))

  containers = [f"{name}, image={unit.image}" for name, unit in spec.run_units.items()]
  if containers:
    lines.append(_labeled_line("Containers:", containers[0]))
    for extra in containers[1:]:
      lines.append(_labeled_line("", extra))

  for i, line in enumerate(network_lines(spec, run_data)):
    lines.append(_labeled_line("Network:" if i == 0 else "", line))

  if not compact:
    description = describe(spec, run_data, ctx)
    if run_data is not None:
      lines += route_receipt_lines(description.routes)
    elif spec.routes and (subdomain := resolved_subdomain(spec, ctx)):
      lines.append(_labeled_line("Routes:", f"subdomain={subdomain}"))

    ports = port_lines(description.ports, ctx.config.link_address)
    for i, line in enumerate(ports):
      lines.append(_labeled_line("Ports:" if i == 0 else "", line))

    vols = volume_lines(description.volumes)
    if vols:
      lines.append(_labeled_line("Volumes:", vols[0]))
      for extra in vols[1:]:
        lines.append(_labeled_line("", extra))

    configs = config_lines(spec, ctx, loaded=run_data is not None)
    if configs:
      lines.append(_labeled_line("Config:", configs[0]))
      for extra in configs[1:]:
        lines.append(_labeled_line("", extra))

    if last_action is not None:
      lines.append(_labeled_line("Last action:", last_action))
    if show_logs:
      lines.append(_labeled_line("Logs:", f"kelso logs -f {app_id}"))

  dangers = danger_callouts(spec)
  for danger in dangers:
    lines.append(_labeled_line("Danger:", danger))

  for note in notes:
    lines.append(_labeled_line("Note:", note))

  if compact:
    # Drop the title line when embedding under Running …
    return "\n".join(lines[1:] if lines[0] == app_id else lines)

  return "\n".join(lines)


def danger_callouts(spec: AppSpec) -> list[str]:
  callouts: list[str] = []
  if spec.network_mode == "host":
    callouts.append("host networking (no port isolation)")
  for name, volume in spec.volumes.items():
    if volume.kind == "host" and not volume.readonly:
      callouts.append(f"writable host bind '{name}'")
  for name, connection in spec.connections.items():
    if isinstance(connection, MountConnection):
      callouts.append(f"connection '{name}' to {connection.kind.desc}")
    elif isinstance(connection, HttpConnection):
      defaults = {n: c.default for n, c in spec.config.items() if c.has_default()}
      address = EnvTemplate(connection.address)
      if set(address.get_identifiers()) <= defaults.keys():
        chosen = address.safe_substitute(defaults)
        callouts.append(f"connection '{name}' to {chosen}, an address the app chose")
  # Unmodelled compose passthrough is the same kind of claim as a writable host
  # bind.
  for warning in spec.compose_warnings:
    callouts.append(
      f"free-form docker options on {warning.run_unit}: "
      + ", ".join(warning.option_lines())
    )
  return callouts


def _labeled_line(label: str, value: str) -> str:
  return f"  {label:<{LABEL_WIDTH}}  {value}"


def _format_labeled(title: str, rows: list[tuple[str, str]]) -> str:
  width = max((len(label) for label, _ in rows if label), default=0)
  width = max(width, LABEL_WIDTH)
  lines = [title]
  for label, value in rows:
    lines.append(f"  {label:<{width}}  {value}")
  return "\n".join(lines)


__all__ = [
  "capability_receipt",
  "config_lines",
  "danger_callouts",
  "location_receipt",
  "port_lines",
  "published_urls",
  "route_lines",
  "volume_lines",
  "volume_root_lines",
]
