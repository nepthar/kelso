"""What an app's routes, ports and volumes are, and where each is on this host.

The facts the CLI receipts and the web UI both show, worked out once. Wording
belongs to `receipt` and `views`.
"""

from dataclasses import dataclass

from kelso.lib.kelso import KelsoCtx
from kelso.lib.run_layout import AppRunData, provider_route_urls
from kelso.lib.spec import AppSpec


@dataclass(frozen=True)
class RouteFact:
  name: str
  unit: str
  desc: str
  admin: bool
  scheme: str
  container_port: int
  # As recorded at load; None when not loaded.
  host_port: int | None
  url: str | None
  host_url: str | None
  # (tag, url), primary first.
  providers: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class PortFact:
  name: str
  unit: str
  desc: str
  proto: str
  container_port: int
  # As recorded at load; None when not loaded.
  host_port: int | None
  # The manifest's fixed host port; None when kelso allocates one.
  declared_host_port: int | None


@dataclass(frozen=True)
class VolumeFact:
  name: str
  kind: str
  readonly: bool
  # Where it is mounted from; None when it has no link yet.
  path: str | None
  # Where kelso places a volume of this kind, linked or not.
  default_path: str | None
  bind: str | None


@dataclass(frozen=True)
class AppDescription:
  routes: tuple[RouteFact, ...]
  ports: tuple[PortFact, ...]
  volumes: tuple[VolumeFact, ...]


def describe(
  spec: AppSpec, run_data: AppRunData | None, ctx: KelsoCtx
) -> AppDescription:
  """`run_data` is None for a bundle that is not loaded."""
  host = ctx.config.link_address
  routes = []
  for name, route in spec.routes.items():
    assigned = run_data.routes.get(name) if run_data else None
    if assigned is not None and assigned.host_port > 0:
      host_url = f"{route.scheme}://{host}:{assigned.host_port}"
    elif spec.network_mode == "host":
      # Host networking maps nothing, so the container port is the host port.
      host_url = f"{route.scheme}://{host}:{route.container_port}"
    else:
      host_url = None
    providers = provider_route_urls(assigned, ctx.config) if assigned else {}
    routes.append(
      RouteFact(
        name=name,
        unit=route.run_unit_name,
        desc=route.desc,
        admin=route.admin,
        scheme=route.scheme,
        container_port=route.container_port,
        host_port=assigned.host_port if assigned else None,
        url=run_data.route_urls.get(name) if run_data else None,
        host_url=host_url,
        providers=tuple(providers.items()),
      )
    )

  ports = []
  for name, port in spec.ports.items():
    assigned_port = run_data.ports.get(name) if run_data else None
    ports.append(
      PortFact(
        name=name,
        unit=port.run_unit_name,
        desc=port.desc,
        proto=port.proto,
        container_port=port.container_port,
        host_port=assigned_port.host_port if assigned_port else None,
        declared_host_port=None if port.needs_allocation else port.host_port,
      )
    )

  binds = ctx.app_store(spec.app).list_binds() if run_data else {}
  volumes = []
  for name, volume in spec.volumes.items():
    link = run_data.volume_links.get(name) if run_data else None
    if volume.kind == "app":
      path = volume.in_bundle
    else:
      path = str(link.source) if link else None
    root = ctx.config.volume_roots.get(volume.kind)
    volumes.append(
      VolumeFact(
        name=name,
        kind=volume.kind,
        readonly=volume.readonly,
        path=path,
        default_path=str(root / spec.app / name) if root else None,
        bind=binds.get(name) if volume.kind == "host" else None,
      )
    )
  return AppDescription(tuple(routes), tuple(ports), tuple(volumes))
