import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import yaml

from kelso.lib.apps import AppID, record_app_action
from kelso.lib.bundle import app_id_from_path, is_pathlike, load_bundle
from kelso.lib.config import NONE_ROUTE_PROVIDER_TAG
from kelso.lib.kelso import (
  CatalogEntry,
  KelsoCtx,
  LoadedAppPaths,
  ambiguity_message,
)
from kelso.lib.lifecycle._common import logger, managed_volume_dirs
from kelso.lib.lifecycle.routes import take_down_routes
from kelso.lib.options import validate_option
from kelso.lib.run_layout import (
  KELSO_CMD,
  KELSO_GUEST_DIR,
  AppRunData,
  AssignedRoute,
  kelso_cmd,
  load_run_data,
  make_compose_dict,
  resolved_subdomain,
  unit_commands,
)
from kelso.lib.secrets import SecretGenerationError, generate_secret
from kelso.lib.spec import AppSpec
from kelso.lib.util import now_ts, same_path

# Scratch names used while swapping in a new loaded copy. Both are inside the run
# dir so the swap is a rename on one filesystem rather than a second copy.
INCOMING = ".app_bundle.incoming"
OUTGOING = ".app_bundle.outgoing"


def _has_volume_data(app_id: AppID, ctx: KelsoCtx) -> bool:
  """Whether any managed volume holds something an app could depend on."""
  for app_dir in managed_volume_dirs(app_id, ctx):
    if not app_dir.is_dir():
      continue
    if any(not entry.is_dir() for entry in app_dir.rglob("*")):
      return True
  return False


def _load_incoming(source: Path, run_path: Path) -> Path:
  """Extract the bundle into ``var/run/<id>/.app_bundle.incoming`` (not yet live)."""
  bundle = load_bundle(source)
  incoming = run_path / INCOMING
  outgoing = run_path / OUTGOING
  for scratch in (incoming, outgoing):
    if scratch.exists():
      shutil.rmtree(scratch)

  bundle.extract_to(incoming)
  return incoming


def _commit_incoming(paths: LoadedAppPaths, incoming: Path) -> None:
  """Promote a validated incoming copy to ``app_bundle/``."""
  outgoing = paths.run_path / OUTGOING
  loaded = paths.bundle_path_in_run
  if loaded.exists():
    os.replace(loaded, outgoing)
  os.replace(incoming, loaded)
  if outgoing.exists():
    shutil.rmtree(outgoing)


def _discard_incoming(run_path: Path) -> None:
  """Drop a failed incoming copy; remove an empty run dir left behind."""
  incoming = run_path / INCOMING
  if incoming.exists():
    shutil.rmtree(incoming)
  if run_path.is_dir() and not any(run_path.iterdir()):
    run_path.rmdir()


def _generate_missing_config(spec: AppSpec, ctx: KelsoCtx) -> None:
  """Fill in defaults and generate secrets for all keys possible"""
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
  """Claim pinned host ports and allocate free ones in kelsodb."""
  if spec.network_mode == "host" or not spec.routes:
    return

  app_subdomain = resolved_subdomain(spec, ctx)
  if not app_subdomain:
    raise ValueError(
      f"App {spec.app} needs a subdomain for its routes; "
      f"run `kelso config {spec.app} --set subdomain=<label>`"
    )

  hdb = ctx.kelso_db
  store = ctx.app_store(spec.app)
  hdb.clear_routes(spec.app)
  for route_name, route in spec.routes.items():
    if route.needs_allocation:
      host_port = hdb.next_free_port()
    else:
      host_port = route.host_port

    assigned = AssignedRoute(
      name=route_name,
      subdomain=route.subdomain(app_subdomain),
      run_unit_name=route.run_unit_name,
      host_port=host_port,
      container_port=route.container_port,
      proto=route.proto,
      scheme=route.scheme,
      provider=store.get_route_assignment(route_name) or NONE_ROUTE_PROVIDER_TAG,
    )

    hdb.set_route(spec.app, route_name, assigned.__dict__)


def _apply_default_route_assignments(spec: AppSpec, ctx: KelsoCtx) -> None:
  """Write default_route_provider for non-private routes with no assignment yet."""
  store = ctx.app_store(spec.app)
  default = ctx.config.default_route_provider
  for route_name, route in spec.routes.items():
    if store.has_route_assignment(route_name):
      continue
    if not route.private:
      store.set_route_assignment(route_name, default)


def _existing_volume_kinds(volumes_root: Path) -> dict[str, str]:
  """volume name -> kind, read back from the links a previous load left."""
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
  """Point `volumes/<kind>/<name>` at the current manifest's volumes.

  host/ volumes are linked at run time instead: they can change between load
  and start.
  """
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
      link.source.mkdir(parents=True, exist_ok=True)
    if not link.source.exists():
      raise ValueError(f"volume {volume_name} source does not exist: {link.source}")
    _make_link(link.destination, link.target)

  return tuple(sorted(name for name in existing if name not in spec.volumes))


def link_host_volumes(spec: AppSpec, run_data: AppRunData) -> None:
  """Build `volumes/host/` from the binds on file. Clobber existing links."""
  unlink_host_volumes(run_data.run_path)
  for volume_name, link in run_data.volume_links.items():
    if spec.volumes[volume_name].kind != "host":
      continue
    logger.debug("host volume %s: %s -> %s", volume_name, link.destination, link.target)
    _make_link(link.destination, link.target)


def unlink_host_volumes(run_path: Path) -> None:
  """Drop `volumes/host/`. Only links are in there."""
  host_root = run_path / "volumes" / "host"
  if host_root.exists():
    shutil.rmtree(host_root)


@dataclass(frozen=True)
class LoadResult:
  spec: AppSpec
  run_data: AppRunData
  dropped_volumes: tuple[str, ...] = ()


def materialize(spec: AppSpec, ctx: KelsoCtx) -> tuple[AppRunData, tuple[str, ...]]:
  """Rebuild everything derived from the loaded copy in the run dir."""
  _clear_and_reallocate_ports(spec, ctx)

  run_data = load_run_data(spec, ctx)
  if run_data.load_blockers:
    raise ValueError("\n".join(i.problem for i in run_data.load_blockers))

  dropped = _rebuild_volume_links(spec, run_data)
  _rebuild_kelso_dirs(spec, run_data.run_path)
  with open(ctx.loaded_paths(spec.app).compose_path, "w") as f:
    yaml.safe_dump(make_compose_dict(spec, run_data), f, sort_keys=False)

  return run_data, dropped


def _rebuild_kelso_dirs(spec: AppSpec, run_path: Path) -> None:
  """`kelso/<unit>/`, what a unit with commands sees at /kelso."""
  root = run_path / "kelso"
  shutil.rmtree(root, ignore_errors=True)
  for unit_name in spec.run_units:
    if not unit_commands(spec, unit_name):
      continue
    script = root / unit_name / Path(KELSO_CMD).relative_to(KELSO_GUEST_DIR)
    script.parent.mkdir(parents=True)
    script.write_text(kelso_cmd(spec, unit_name))
    script.chmod(0o755)


# Lives in the app's config store, so `--purge` clears it and nothing else does.
BOUND_TO_META = "bound_to"


@dataclass(frozen=True)
class LoadTarget:
  """What a `load`/`start` argument named, and where it came from."""

  app_id: AppID
  # None when the argument was a bare id; `ctx.bundle_path` answers that.
  bundle: Path | None
  # None for a bundle named by path, which belongs to no repo.
  repo: str | None

  @property
  def bound_to(self) -> str | None:
    """The source to record, or None to leave the recorded one alone."""
    if self.repo:
      return f"repo {self.repo}"
    return str(self.bundle) if self.bundle else None


def bound_to(app: AppID | str, ctx: KelsoCtx) -> str | None:
  """What an app is recorded as loaded from, or None if nothing is."""
  if not ctx.config.app_config_path(app).is_file():
    return None
  return ctx.app_store(app).get_meta(BOUND_TO_META)


def load_target(ctx: KelsoCtx, target: str, *, force: bool = False) -> LoadTarget:
  """Resolve a load/start argument -- a path, `<id>@<repo>`, or a bare id.

  Raises ValueError if the target is ambiguous, or if it would load over an
  id already bound to another source and `force` is not set.
  """
  if is_pathlike(target):
    bundle = Path(target).expanduser().resolve()
    resolved = LoadTarget(app_id_from_path(bundle), bundle, None)
  else:
    name, _, repo = target.partition("@")
    resolved = catalog_target(ctx, name, repo or None)

  _check_binding(ctx, resolved, force=force)
  return resolved


def catalog_target(ctx: KelsoCtx, name: str, repo: str | None) -> LoadTarget:
  """The catalog entry a bare id or `<id>@<repo>` names."""
  app = ctx.resolve_app(name)
  entries = ctx.app_catalog().get(str(app), ())

  if repo is not None:
    for entry in entries:
      if entry.source == repo:
        return LoadTarget(app, entry.path, repo)
    carried = ", ".join(sorted(entry.source for entry in entries))
    raise ValueError(
      f"Repo {repo!r} does not carry {app}."
      + (f" It is in: {carried}." if carried else " No repo carries it.")
    )

  if not entries:
    # No catalog entry but a loaded copy: `start` runs that copy as-is.
    if ctx.is_loaded(app):
      return LoadTarget(app, None, None)
    raise ValueError(f'No app found for "{app}"')
  if len(entries) > 1:
    entry = bound_entry(ctx, app, entries)
    if entry is None:
      raise ValueError(ambiguity_message(app, entries))
    return LoadTarget(app, entry.path, entry.source)
  return LoadTarget(app, entries[0].path, entries[0].source)


def bound_entry(
  ctx: KelsoCtx, app: AppID, entries: tuple[CatalogEntry, ...]
) -> CatalogEntry | None:
  """The one of `entries` from the repo `app` is bound to, if any."""
  was = bound_to(app, ctx)
  for entry in entries:
    if was == f"repo {entry.source}":
      return entry
  return None


def _same_source(was: str, now: str) -> bool:
  """Whether two recorded sources name the same repo or the same bundle path."""
  if was == now:
    return True
  if Path(was).is_absolute() and Path(now).is_absolute():
    return same_path(Path(was), Path(now))
  return False


def _check_binding(ctx: KelsoCtx, target: LoadTarget, *, force: bool) -> None:
  """Refuse an id whose surviving config and secrets were made for another source."""
  was = bound_to(target.app_id, ctx)
  now = target.bound_to
  if force or not was or not now or _same_source(was, now):
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
  """Validate and store config values. A loaded app picks them up on reload."""
  store = ctx.app_store(spec.app)
  for name, value in sets:
    config = spec.config.get(name)
    if not config:
      raise ValueError(f"No config {name} in {spec.app}'s manifest")
    if not value:
      raise ValueError(f"Empty value for config {name!r}")
    validate_option(name, value)
    store.set_config(name, config.secret, value)


def assign_route(spec: AppSpec, route_name: str, tag: str, ctx: KelsoCtx) -> None:
  """Record which provider publishes a route. `start` registers it with them."""
  app = spec.app
  if route_name not in spec.routes:
    known = ", ".join(sorted(spec.routes)) or "(none)"
    raise ValueError(
      f"route {route_name!r} is not declared in {app}'s manifest; known routes: {known}"
    )
  if tag not in ctx.config.route_providers:
    known = ", ".join(sorted(ctx.config.route_providers))
    raise ValueError(
      f"route provider {tag!r} is not configured; known tags: {known}. "
      f"Add [route_provider.{tag}] to config.toml"
    )

  ctx.app_store(app).set_route_assignment(route_name, tag)
  # The compose file carries ${routes.*} URLs, so the next start needs it rewritten.
  if ctx.is_loaded(app):
    with open(ctx.loaded_paths(app).compose_path, "w") as f:
      yaml.safe_dump(
        make_compose_dict(spec, load_run_data(spec, ctx)), f, sort_keys=False
      )


def bind(spec: AppSpec, volname: str, host_volume_tag: str, ctx: KelsoCtx) -> None:
  """Record a host-volume bind against the loaded bundle."""
  app = spec.app

  if volname not in spec.volumes:
    raise ValueError(f"App {app} - no such volume {volname}")

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
      f"{host_vol.path}"
    )

  if host_vol.require_mount and not host_vol.path.is_mount():
    raise ValueError(
      f"App {app} - host volume {host_volume_tag!r} is not mounted at "
      f"{host_vol.path}; mount the share, or clear require_mount on "
      f"[host_volume.{host_volume_tag}]"
    )

  ctx.app_store(app).set_bind(volname, host_volume_tag)


def _refuse_version_change(app: AppID, spec: AppSpec, ctx: KelsoCtx) -> None:
  loaded = ctx.loaded_spec(app)
  if loaded is not None and loaded.version != spec.version:
    raise ValueError(
      f"App {app} is loaded at version {loaded.version}, and this would load "
      f"{spec.version}. Run `kelso update {app}`, which snapshots it first."
    )


def check_same_version(app: AppID, bundle: Path, ctx: KelsoCtx) -> None:
  """Raise ValueError if loading `bundle` would change the app's version.

  A bundle that does not parse passes: `load` reports that better.
  """
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
  sets: list[tuple[str, str]] | None = None,
  binds: list[tuple[str, str]] | None = None,
  bound: str | None = None,
  version_change: bool = False,
) -> LoadResult:
  """Load `bundle` into `var/run/<id>/` without starting it.

  `bound` is the source to record; None leaves the recorded one alone. Raises
  ValueError if the app is loaded at another version, unless `version_change`.
  """
  paths = ctx.loaded_paths(app)
  was_loaded = paths.exists()

  try:
    running_count = ctx.run_state(app).running_count
  except ValueError:
    running_count = 0
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
      f"expect. Restore from a snapshot, or run "
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
  if bound is not None:
    store.set_meta(BOUND_TO_META, bound)

  if sets:
    apply_config_sets(spec, sets, ctx)

  if binds:
    for volname, host_volume_tag in binds:
      bind(spec, volname, host_volume_tag, ctx)

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
