import os
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import yaml

from kelso.lib import kproxy
from kelso.lib.app_inventory import no_bundle_message
from kelso.lib.apps import AppID, record_app_action
from kelso.lib.bundle import app_id_from_path, is_pathlike, load_bundle
from kelso.lib.config import NONE_ROUTE_PROVIDER_TAG, parse_route_providers
from kelso.lib.kelso import KelsoCtx, LoadedAppPaths
from kelso.lib.lifecycle._common import logger, managed_volume_dirs
from kelso.lib.lifecycle.routes import take_down_routes
from kelso.lib.manifest import NETWORK_MODES, PROXIED_MODES
from kelso.lib.options import validate_option
from kelso.lib.run_layout import (
  AppRunData,
  AssignedPort,
  AssignedRoute,
  load_run_data,
  make_compose_dict,
  network_mode,
  resolved_subdomain,
)
from kelso.lib.secrets import SecretGenerationError, generate_secret
from kelso.lib.spec import AppSpec
from kelso.lib.util import now_ts, same_path

INCOMING = ".app_bundle.incoming"


def _has_volume_data(app_id: AppID, ctx: KelsoCtx) -> bool:
  for app_dir in managed_volume_dirs(app_id, ctx):
    if not app_dir.is_dir():
      continue
    if any(not entry.is_dir() for entry in app_dir.rglob("*")):
      return True
  return False


def _load_incoming(source: Path, run_path: Path) -> Path:
  bundle = load_bundle(source)
  incoming = run_path / INCOMING
  if incoming.exists():
    shutil.rmtree(incoming)

  bundle.extract_to(incoming)
  return incoming


def _commit_incoming(paths: LoadedAppPaths, incoming: Path) -> None:
  loaded = paths.bundle_path_in_run
  if loaded.is_dir():
    _sync_into(incoming, loaded)
    shutil.rmtree(incoming)
  else:
    os.replace(incoming, loaded)


def _remove(path: Path) -> None:
  if path.is_dir() and not path.is_symlink():
    shutil.rmtree(path)
  else:
    path.unlink()


def _sync_into(src: Path, dst: Path) -> None:
  # Docker Desktop's VM caches lookups of bind sources by name. Replacing a
  # folder or file under app_bundle/ leaves that cache pointing at the deleted
  # one, and the next bind fails with "bind source path does not exist".
  for old in dst.iterdir():
    if not (src / old.name).exists(follow_symlinks=False):
      _remove(old)
  for new in src.iterdir():
    target = dst / new.name
    if new.is_dir() and not new.is_symlink():
      if target.is_symlink() or (target.exists() and not target.is_dir()):
        _remove(target)
      target.mkdir(exist_ok=True)
      _sync_into(new, target)
      continue
    if target.is_symlink() or new.is_symlink() or target.is_dir():
      if target.exists(follow_symlinks=False):
        _remove(target)
    shutil.copy2(new, target, follow_symlinks=False)


def _discard_incoming(run_path: Path) -> None:
  incoming = run_path / INCOMING
  if incoming.exists():
    shutil.rmtree(incoming)
  if run_path.is_dir() and not any(run_path.iterdir()):
    run_path.rmdir()


def _generate_missing_config(spec: AppSpec, ctx: KelsoCtx) -> None:
  store = ctx.app_store(spec.app)
  for config_name, config in spec.config.items():
    if config.default is None or store.has_config(config_name):
      continue
    try:
      value = generate_secret(config.default) if config.secret else config.default
      store.set_config(config_name, config.secret, value)
    except SecretGenerationError as e:
      logger.error(f"{config_name}: {e}")


def _clear_and_reallocate_ports(spec: AppSpec, ctx: KelsoCtx) -> None:
  hdb = ctx.kelso_db
  hdb.clear_routes(spec.app)
  hdb.clear_ports(spec.app)
  if spec.network_mode == "host":
    return

  app_subdomain = resolved_subdomain(spec, ctx)
  if spec.routes and not app_subdomain:
    raise ValueError(
      f"App {spec.app} needs a subdomain for its routes; "
      f"run `kelso config {spec.app} subdomain=<label>`"
    )

  store = ctx.app_store(spec.app)
  for route_name, route in spec.routes.items():
    assigned = AssignedRoute(
      name=route_name,
      subdomain=route.subdomain(app_subdomain),
      run_unit_name=route.run_unit_name,
      host_port=hdb.next_free_port() if route.needs_allocation else route.host_port,
      container_port=route.container_port,
      scheme=route.scheme,
      providers=tuple(store.get_route_assignment(route_name) or ()),
    )
    hdb.set_route(spec.app, route_name, assigned.record())

  for port_name, port in spec.ports.items():
    assigned_port = AssignedPort(
      name=port_name,
      run_unit_name=port.run_unit_name,
      host_port=hdb.next_free_port() if port.needs_allocation else port.host_port,
      container_port=port.container_port,
      proto=port.proto,
    )
    hdb.set_port(spec.app, port_name, assigned_port.record())


def _apply_default_route_assignments(spec: AppSpec, ctx: KelsoCtx) -> None:
  store = ctx.app_store(spec.app)
  default = ctx.config.default_route_provider
  for route_name, route in spec.routes.items():
    if store.has_route_assignment(route_name):
      continue
    if not route.admin:
      store.set_route_assignment(
        route_name, [] if default == NONE_ROUTE_PROVIDER_TAG else [default]
      )


def _existing_volume_kinds(volumes_root: Path) -> dict[str, str]:
  found: dict[str, str] = {}
  if not volumes_root.is_dir():
    return found
  for kind_dir in volumes_root.iterdir():
    if not kind_dir.is_dir():
      continue
    for link in kind_dir.iterdir():
      found[link.name] = kind_dir.name
  return found


def _make_link(destination: Path, target: Path) -> None:
  destination.parent.mkdir(parents=True, exist_ok=True)
  destination.symlink_to(target)


def _rebuild_volume_links(spec: AppSpec, run_data: AppRunData) -> tuple[str, ...]:
  volumes_root = run_data.run_path / "volumes"
  existing = _existing_volume_kinds(volumes_root)

  for name, kind in existing.items():
    volume = spec.volumes.get(name)
    if volume is not None and volume.kind != kind:
      raise ValueError(
        f"App {spec.app} - volume {name} changed kind from {kind} to "
        f"{volume.kind}, but its data lives under the {kind} root. Move it by "
        f"hand, or run `kelso rm --purge {spec.app}` to delete it."
      )

  # Only links live here; the data they point at is outside the run dir, or (for
  # app volumes) under app_bundle/. So the whole tree can be torn down and rebuilt.
  if volumes_root.exists():
    shutil.rmtree(volumes_root)

  for volume_name, link in run_data.volume_links.items():
    if spec.volumes[volume_name].kind == "host":
      continue
    logger.debug("volume %s: %s -> %s", volume_name, link.destination, link.target)
    if link.mkdir:
      _make_shared_dirs(link.source.parent, link.source)
    if not link.source.exists():
      raise ValueError(f"volume {volume_name} source does not exist: {link.source}")
    _make_link(link.destination, link.target)

  _make_nested_mountpoints(spec, run_data)
  return tuple(sorted(name for name in existing if name not in spec.volumes))


# Sticky and writable by everyone, as /tmp is: a container running as any uid can
# make its own folders in a new volume, and only remove what it made.
SHARED_DIR_MODE = 0o1777


def _make_shared_dirs(root: Path, path: Path) -> None:
  root.mkdir(parents=True, exist_ok=True)
  for part in reversed((path, *path.parents)[: len(path.relative_to(root).parts)]):
    if not part.exists():
      part.mkdir()
      part.chmod(SHARED_DIR_MODE)


def _make_nested_mountpoints(spec: AppSpec, run_data: AppRunData) -> None:
  # Docker creates a missing mount target as root, and the app can't write there.
  for unit in spec.run_units.values():
    mounts = sorted(
      ((PurePosixPath(b.guest_path), name) for name, b in unit.volumes.items()),
      key=lambda m: len(m[0].parts),
    )
    for i, (inner, _) in enumerate(mounts):
      outers = [(p, n) for p, n in mounts[:i] if inner.is_relative_to(p)]
      if not outers:
        continue
      outer_path, outer_name = outers[-1]
      link = run_data.volume_links.get(outer_name)
      if link is None or not link.mkdir or outer_path == inner:
        continue
      _make_shared_dirs(link.source, link.source / inner.relative_to(outer_path))


def link_host_volumes(spec: AppSpec, run_data: AppRunData) -> None:
  unlink_host_volumes(run_data.run_path)
  for volume_name, link in run_data.volume_links.items():
    if spec.volumes[volume_name].kind != "host":
      continue
    logger.debug("host volume %s: %s -> %s", volume_name, link.destination, link.target)
    _make_link(link.destination, link.target)


def unlink_host_volumes(run_path: Path) -> None:
  host_root = run_path / "volumes" / "host"
  if host_root.exists():
    shutil.rmtree(host_root)


@dataclass(frozen=True)
class LoadResult:
  spec: AppSpec
  run_data: AppRunData
  dropped_volumes: tuple[str, ...] = ()


def materialize(spec: AppSpec, ctx: KelsoCtx) -> tuple[AppRunData, tuple[str, ...]]:
  _clear_and_reallocate_ports(spec, ctx)
  _allocate_subnet(spec, ctx)

  run_data = load_run_data(spec, ctx)
  if run_data.load_blockers:
    raise ValueError(
      "\n".join(
        f"{i.problem}\n  {i.fix}" if i.fix else i.problem
        for i in run_data.load_blockers
      )
    )

  dropped = _rebuild_volume_links(spec, run_data)
  _rebuild_kproxy_dir(spec, run_data)
  with open(ctx.loaded_paths(spec.app).compose_path, "w") as f:
    yaml.safe_dump(make_compose_dict(spec, run_data), f, sort_keys=False)

  return run_data, dropped


def _allocate_subnet(spec: AppSpec, ctx: KelsoCtx) -> None:
  db = ctx.kelso_db
  if network_mode(spec, ctx) not in PROXIED_MODES:
    db.clear_subnet(spec.app)
  elif db.get_subnet(spec.app) is None:
    db.set_subnet(spec.app, db.next_free_subnet())


def _rebuild_kproxy_dir(spec: AppSpec, run_data: AppRunData) -> None:
  if run_data.restricted is None:
    shutil.rmtree(run_data.run_path / kproxy.KPROXY_RUN_DIR, ignore_errors=True)
  write_kproxy_rules(run_data)


def write_kproxy_rules(run_data: AppRunData) -> None:
  if run_data.restricted is None:
    return
  root = run_data.run_path / kproxy.KPROXY_RUN_DIR
  root.mkdir(exist_ok=True)
  (root / "rules.txt").write_text(run_data.restricted.rules)


@dataclass(frozen=True)
class LoadTarget:
  app_id: AppID
  # None when no bundle is on disk; a loaded app then starts from its run copy.
  bundle: Path | None
  # The repo the bundle is in, to say so; None for one named by path.
  repo: str | None

  def require_bundle(self) -> Path:
    if self.bundle is None:
      raise ValueError(no_bundle_message(self.app_id))
    return self.bundle


def load_source(target: LoadTarget, bundle: Path, ctx: KelsoCtx) -> str:
  repo = ctx.config.repos.get(target.repo) if target.repo else None
  if repo is None:
    return str(bundle)
  where = f"repo {repo.name}"
  if repo.mirrored:
    state = ctx.kelso_db.get_repo_state(repo.name)
    at = f" at {state['sha'][:8]}" if state else ""
    where += f" ({repo.describe()}{at})"
  return f"{where}: {bundle}"


def load_target(ctx: KelsoCtx, target: str, *, force: bool = False) -> LoadTarget:
  if is_pathlike(target):
    bundle = Path(target).expanduser().resolve()
    resolved = LoadTarget(app_id_from_path(bundle), bundle, None)
  else:
    name, _, repo = target.partition("@")
    app = ctx.inventory.app_id(name, repo=repo)
    entry = ctx.inventory.bundle(app, repo)
    resolved = LoadTarget(
      app, entry.path if entry else None, entry.repo if entry else None
    )

  _check_binding(ctx, resolved, force=force)
  return resolved


def _check_binding(ctx: KelsoCtx, target: LoadTarget, *, force: bool) -> None:
  was = ctx.loaded_origin(target.app_id)
  now = target.bundle
  if force or was is None or now is None or same_path(was, now):
    return
  raise ValueError(
    f"{target.app_id} was previously loaded from {was}, and this would "
    f"load it from {now}.\n"
    f"Its configuration, secrets and volume data were made for the old source "
    f"and would carry over.\n"
    f"Pass --force if you know that is fine. Otherwise remove it completely "
    f"with `kelso rm --purge {target.app_id}` and load again."
  )


def apply_config_sets(
  spec: AppSpec, sets: list[tuple[str, str]], ctx: KelsoCtx
) -> None:
  store = ctx.app_store(spec.app)
  for name, value in sets:
    config = spec.config.get(name)
    if not config:
      raise ValueError(
        f"No config {name} in {spec.app}'s manifest; "
        f"`kelso config {spec.app}` lists them"
      )
    if not value:
      raise ValueError(f"Empty value for config {name!r}")
    validate_option(name, value)
    store.set_config(name, config.secret, value)


def assign_route(spec: AppSpec, route_name: str, raw: str, ctx: KelsoCtx) -> list[str]:
  app = spec.app
  if route_name not in spec.routes:
    known = ", ".join(sorted(spec.routes)) or "(none)"
    raise ValueError(
      f"route {route_name!r} is not declared in {app}'s manifest; known routes: {known}"
    )
  tags = parse_route_providers(route_name, raw)
  for tag in tags:
    if tag not in ctx.config.route_providers:
      known = ", ".join(sorted(ctx.config.route_providers))
      raise ValueError(
        f"route provider {tag!r} is not configured; known tags: {known}. "
        f"Add [route_provider.{tag}] to config.toml"
      )

  ctx.app_store(app).set_route_assignment(route_name, tags)
  # The compose file carries ${routes.*} URLs, so the next start needs it rewritten.
  if ctx.is_loaded(app):
    with open(ctx.loaded_paths(app).compose_path, "w") as f:
      yaml.safe_dump(
        make_compose_dict(spec, load_run_data(spec, ctx)), f, sort_keys=False
      )
  return tags


def set_network(spec: AppSpec, mode: str | None, ctx: KelsoCtx) -> None:
  if mode is not None and mode not in NETWORK_MODES:
    choices = ", ".join(m for m in NETWORK_MODES if m != "host")
    raise ValueError(f"network mode {mode!r} is not one of {choices}, or default")
  if "host" in (mode, spec.network_mode) and mode not in (None, spec.network_mode):
    raise ValueError(
      f"{spec.app}: host networking can't be switched to or from; "
      f"it is the manifest's to choose"
    )
  ctx.app_store(spec.app).set_network_mode(None if mode == spec.network_mode else mode)


def bind(spec: AppSpec, volname: str, host_volume_tag: str, ctx: KelsoCtx) -> None:
  app = spec.app

  if volname not in spec.volumes:
    raise ValueError(
      f"App {app} - no such volume {volname}; "
      f"`kelso config {app}` lists its host volumes"
    )

  vol = spec.volumes[volname]
  if vol.kind != "host":
    raise ValueError(
      f"App {app} - volume {volname}, kind={vol.kind}, only host volumes can be bound"
    )

  host_vol = ctx.config.host_volumes.get(host_volume_tag)
  if host_vol is None:
    known = ", ".join(sorted(ctx.config.host_volumes)) or "(none)"
    raise ValueError(
      f"App {app} - host volume {host_volume_tag!r} is not configured; "
      f"known tags: {known}. Add [host_volume.{host_volume_tag}] to config.toml"
    )

  if host_vol.readonly and not vol.readonly:
    raise ValueError(
      f"App {app} - host volume {host_volume_tag!r} is readonly, but volume "
      f"{volname} is writable; declare it with readonly = true, or bind to a "
      f"writable host volume"
    )

  if not host_vol.path.exists():
    raise ValueError(
      f"App {app} - host volume {host_volume_tag!r} path does not exist: "
      f"{host_vol.path}. Create it, or point the host volume elsewhere with "
      f"`kelso system host-volume --set {host_volume_tag}=<path>`"
    )

  if host_vol.require_mount and not host_vol.path.is_mount():
    raise ValueError(
      f"App {app} - host volume {host_volume_tag!r} is not mounted at "
      f"{host_vol.path}; mount the share, or clear require_mount on "
      f"[host_volume.{host_volume_tag}]"
    )

  ctx.app_store(app).set_bind(volname, host_volume_tag)


def _refuse_version_change(app: AppID, spec: AppSpec, ctx: KelsoCtx) -> None:
  try:
    loaded = ctx.loaded_spec(app)
  except ValueError:
    return
  if loaded is not None and loaded.version != spec.version:
    raise ValueError(
      f"App {app} is loaded at version {loaded.version}, and this would load "
      f"{spec.version}. Run `kelso update {app}`, which backs it up first."
    )


def check_same_version(app: AppID, bundle: Path, ctx: KelsoCtx) -> None:
  try:
    spec = load_bundle(bundle).app_spec()
  except ValueError:
    return
  _refuse_version_change(app, spec, ctx)


def load(
  app: AppID,
  bundle: Path,
  ctx: KelsoCtx,
  *,
  version_change: bool = False,
) -> LoadResult:
  with ctx.locked(f"load {app}", app):
    paths = ctx.loaded_paths(app)
    was_loaded = paths.exists()

    running_count = ctx.run_state(app).running_count
    if running_count:
      raise ValueError(
        f"App {app} has {running_count} running Kelso-labeled container(s); "
        f"run `kelso stop {app}` first"
      )

    # Config gone while the data it belongs to is still here means someone
    # deleted the logtab by hand. Loading would generate fresh `auto` secrets
    # against data expecting the old ones, so refuse instead.
    config_path = ctx.config.app_config_path(app)
    if not config_path.is_file() and _has_volume_data(app, ctx):
      raise ValueError(
        f"App {app} has volume data but no config at {config_path}. "
        f"Loading would generate new secrets that its existing data does not "
        f"expect. Restore it from a backup, or run "
        f"`kelso rm --purge {app}` to delete its config and data together."
      )

    # Extract the bundle under var/run/ first, validate *that* copy, then promote it
    # to app_bundle/. AppSpec always comes from the run tree, never the source bundle.
    run_path = paths.run_path
    run_path.mkdir(parents=True, exist_ok=True)
    try:
      incoming = _load_incoming(bundle, run_path)
      spec = AppSpec.from_file(incoming / "manifest.toml", app)
      if not version_change:
        _refuse_version_change(app, spec, ctx)
    except Exception:
      _discard_incoming(run_path)
      raise
    _commit_incoming(paths, incoming)

    store = ctx.app_store(app)
    store.set_meta("origin", str(bundle))

    # On the way down: routes are re-derived below, and may come back under a
    # different name, so what was published goes first.
    if was_loaded:
      take_down_routes(app, ctx)

    _generate_missing_config(spec, ctx)
    _apply_default_route_assignments(spec, ctx)

    try:
      run_data, dropped = materialize(spec, ctx)
    except Exception:
      record_app_action("load-failed", app, ctx)
      raise

    store.set_meta("loaded_at", now_ts())
    store.set_meta("loaded_version", spec.version)
    record_app_action("loaded", app, ctx, version=spec.version)
    return LoadResult(spec, run_data, dropped)
