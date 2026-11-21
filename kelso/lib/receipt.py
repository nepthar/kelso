from collections.abc import Mapping, Sequence

from kelso.lib.config import Config
from kelso.lib.connections import (
  HttpConnection,
  MountConnection,
  WebConnection,
  network_connections,
)
from kelso.lib.kelso import KelsoCtx
from kelso.lib.run_layout import AppRunData, provider_route_urls, resolved_subdomain
from kelso.lib.spec import AppSpec
from kelso.lib.util import EnvTemplate

# Every label in these receipts pads to at least this, so the capability block
# and the location block under it line up in one `kelso start`.
LABEL_WIDTH = len("Containers:")


def published_route_urls(
  spec: AppSpec, run_data: AppRunData, ctx: KelsoCtx
) -> dict[str, list[str]]:
  """Route name -> its public URLs, primary first, for routes loaded with a provider."""
  published: dict[str, list[str]] = {}
  for name in spec.routes:
    loaded = run_data.routes.get(name)
    if loaded is None:
      continue
    urls = list(provider_route_urls(loaded, ctx.config).values())
    if urls:
      published[name] = urls
  return published


def published_urls(spec: AppSpec, run_data: AppRunData, ctx: KelsoCtx) -> list[str]:
  """URLs for routes assigned to a provider, in declaration order, primary first."""
  return [
    url for urls in published_route_urls(spec, run_data, ctx).values() for url in urls
  ]


def host_url(
  spec: AppSpec, run_data: AppRunData | None, name: str, host: str
) -> str | None:
  """Where a route answers on the host itself, or None with no port allocated."""
  route = spec.routes[name]
  assigned = run_data.routes.get(name) if run_data else None
  if assigned is not None and assigned.host_port > 0:
    return f"{route.scheme}://{host}:{assigned.host_port}"
  if spec.network_mode == "host":
    # Host networking maps nothing, so the container port is the host port
    # and kelso never allocated one.
    return f"{route.scheme}://{host}:{route.container_port}"
  return None


def route_lines(
  spec: AppSpec,
  run_data: AppRunData | None,
  published: Mapping[str, Sequence[str]],
  *,
  host: str = "localhost",
) -> list[str]:
  """Every declared route, read right to left: where you reach it, then what answers."""
  lines: list[str] = []
  for name, route in spec.routes.items():
    where = host_url(spec, run_data, name, host) or "(no host port allocated)"

    line = f"{name}: {route.run_unit_name}:{route.container_port} <- {where}"
    if published.get(name):
      line += f" <- {', '.join(published[name])}"
    if route.admin:
      line += " (admin)"
    lines.append(line)
  return lines


def port_lines(
  spec: AppSpec, run_data: AppRunData | None, *, host: str = "localhost"
) -> list[str]:
  """Every declared port, read right to left like `route_lines`."""
  lines: list[str] = []
  for name, port in spec.ports.items():
    assigned = run_data.ports.get(name) if run_data else None
    if assigned is not None:
      where = f"{host}:{assigned.host_port}"
    elif not port.needs_allocation:
      where = f"{host}:{port.host_port}"
    else:
      where = "(no host port allocated)"
    lines.append(
      f"{name}: {port.run_unit_name}:{port.container_port}/{port.proto} <- {where}"
    )
  return lines


def route_receipt_lines(
  spec: AppSpec, run_data: AppRunData, ctx: KelsoCtx
) -> list[str]:
  """The `Routes:` block of a receipt; empty for an app with no routes."""
  reach = route_lines(
    spec,
    run_data,
    published_route_urls(spec, run_data, ctx),
    host=ctx.config.link_address,
  )
  return [
    _labeled_line("Routes:" if i == 0 else "", line) for i, line in enumerate(reach)
  ]


_NETWORK_HEADS = {
  "restrict": "restrict: reaches only these",
  "ask": "ask: reaches these, and holds anything else for you to decide",
  "watch": "watch: reaches anything, logging each host; these are listed",
}


def network_lines(spec: AppSpec, run_data: AppRunData | None) -> list[str]:
  """What an app behind kproxy may reach, or that an open one's connections don't."""
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
  """Per-key config status, same wording as `kelso config`."""
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


def _volume_paths(
  spec: AppSpec, run_data: AppRunData | None, ctx: KelsoCtx
) -> list[tuple[str, str, str]]:
  """`(name, kind, where)` for each volume with a place on the host."""
  paths: list[tuple[str, str, str]] = []
  for name, volume in spec.volumes.items():
    kind = f"{volume.kind}, read-only" if volume.readonly else volume.kind
    if volume.kind == "app":
      paths.append((name, kind, volume.in_bundle))
    elif run_data is not None and name in run_data.volume_links:
      paths.append((name, kind, str(run_data.volume_links[name].source)))
    elif volume.kind == "host":
      paths.append((name, kind, "(unbound)"))
    else:
      root = ctx.config.volume_roots.get(volume.kind)
      if root is not None:
        paths.append((name, kind, str(root / spec.app / name)))
  return paths


def volume_lines(
  spec: AppSpec, run_data: AppRunData | None, ctx: KelsoCtx
) -> list[str]:
  """Managed volume dirs and host-volume bind paths, each with its kind."""
  return [
    f"{name} ({kind}): {where}"
    for name, kind, where in _volume_paths(spec, run_data, ctx)
  ]


def volume_root_lines(config: Config) -> list[str]:
  """Where each volume kind lives, following an operator's symlink."""
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
  """Post-up location block (Routes, Ports, Data, Logs)."""
  app_id = spec.app
  title = heading if heading is not None else f"Running {app_id}"
  rows: list[tuple[str, str]] = []

  routes = route_lines(
    spec,
    run_data,
    published_route_urls(spec, run_data, ctx),
    host=ctx.config.link_address,
  )
  for i, line in enumerate(routes):
    rows.append(("Routes:" if i == 0 else "", line))
  for i, line in enumerate(port_lines(spec, run_data, host=ctx.config.link_address)):
    rows.append(("Ports:" if i == 0 else "", line))

  for name, kind, where in _volume_paths(spec, run_data, ctx):
    if kind == "data":
      rows.append(("Data:", f"{name}: {where}"))

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
  """Bundle card for `kelso status APP` / first-run up."""
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
    if run_data is not None:
      lines += route_receipt_lines(spec, run_data, ctx)
    elif spec.routes and (subdomain := resolved_subdomain(spec, ctx)):
      lines.append(_labeled_line("Routes:", f"subdomain={subdomain}"))

    ports = port_lines(spec, run_data, host=ctx.config.link_address)
    for i, line in enumerate(ports):
      lines.append(_labeled_line("Ports:" if i == 0 else "", line))

    vols = volume_lines(spec, run_data, ctx)
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
  "published_route_urls",
  "published_urls",
  "route_lines",
  "volume_lines",
  "volume_root_lines",
]
