"""JSON projections of kelso state.

Note that secrets should never be rendered to the user through this module.
"""

from __future__ import annotations

import difflib
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from kelso.lib import activity
from kelso.lib.apps import AppID
from kelso.lib.bundle import load_bundle, manifest_text
from kelso.lib.config import NONE_ROUTE_PROVIDER_TAG
from kelso.lib.configflow import ConfigRequest
from kelso.lib.kelso import CatalogEntry, KelsoCtx
from kelso.lib.lifecycle.restore import snapshot_names, snapshotted_app_ids
from kelso.lib.lifecycle.run import logs_text
from kelso.lib.lifecycle.snapshot import snapshot_archive, split_snapshot_name
from kelso.lib.metric import KELSO_DIRS
from kelso.lib.observations import AppObservation
from kelso.lib.receipt import published_route_urls
from kelso.lib.repo import LOCAL_REPO, bound_apps
from kelso.lib.run_layout import AppRunData, load_run_data
from kelso.lib.spec import AppSpec
from kelso.lib.store import AppStore


def apps_view(ctx: KelsoCtx) -> list[dict[str, Any]]:
  """Every installed app, in the shape a dashboard list wants.

  Uninstalled apps belong to the catalog listing, which reports their state.
  """
  return [
    _summary(observation, ctx)
    for observation in ctx.observations()
    if observation.installed
  ]


def metrics_view(ctx: KelsoCtx, prefix: str, hours: int) -> dict[str, Any]:
  """Gauge history for keys starting `gauge/{prefix}` over the last `hours` hours."""
  until = int(datetime.now(UTC).timestamp())
  since = until - hours * 60 * 60
  metrics: dict[str, list[dict[str, int | float]]] = {}
  for key, entries in ctx.history_gauges(prefix, since).items():
    metrics[key.removeprefix("gauge/")] = [
      {"t": e.unix_seconds, "v": float(e.value)} for e in entries
    ]
  return {"since": since, "until": until, "metrics": metrics}


def catalog_view(ctx: KelsoCtx) -> list[dict[str, Any]]:
  """Every configured repo, with the bundles currently in it."""
  catalogs: dict[str, list[dict[str, Any]]] = {name: [] for name in ctx.config.repos}
  catalog = ctx.app_catalog()
  for app_id in sorted(catalog):
    for entry in catalog[app_id]:
      catalogs.setdefault(entry.source, []).append(_catalog_app(entry, ctx))
  return [{"name": name, "apps": apps} for name, apps in catalogs.items()]


def _catalog_app(entry: CatalogEntry, ctx: KelsoCtx) -> dict[str, Any]:
  spec = _catalog_spec(entry)
  # The logtab is what makes an id more than a catalog listing, and AppStore
  # creates it on contact -- so this is a file check, never a store lookup.
  has_config = ctx.config.app_config_path(entry.app_id).is_file()
  store = ctx.app_store(spec.app) if spec is not None and has_config else None
  manifest = manifest_text(entry.path)
  return {
    "app_id": entry.app_id,
    "display_name": spec.display_name if spec else "",
    "version": spec.version if spec else None,
    "description": spec.description if spec else "",
    "repo": entry.source,
    "state": ctx.app_state(entry.app_id),
    "configured": config_status(spec, store) if spec else None,
    "manifest": manifest,
    "manifest_stale": _catalog_manifest_stale(entry, manifest, ctx),
    "warnings": compose_warnings_view(spec) if spec else [],
  }


def compose_warnings_view(spec: AppSpec) -> list[dict[str, Any]]:
  """`[run.<unit>.compose]` keys kelso does not model, for the UI to show.

  Sent whether or not the app is installed: it is a property of the manifest,
  and the point is to be readable *before* deciding to install.
  """
  return [
    {
      "run_unit": w.run_unit,
      "message": w.message(),
      "options": list(w.option_lines()),
    }
    for w in spec.compose_warnings
  ]


def _manifest_diff(current: str, remote: str) -> str:
  return "".join(
    difflib.unified_diff(
      current.splitlines(keepends=True),
      remote.splitlines(keepends=True),
      fromfile="installed",
      tofile="remote",
    )
  )


def _catalog_manifest_stale(entry: CatalogEntry, manifest: str, ctx: KelsoCtx) -> bool:
  """Whether the catalog's manifest has moved on from the staged copy."""
  staged = ctx.staged_paths(entry.app_id).manifest_path
  if not manifest or not staged.is_file():
    return False
  try:
    return staged.read_text() != manifest
  except OSError:
    return False


def config_status(spec: AppSpec, store: AppStore | None) -> str:
  """ "ready" when every config key is set or will be filled, else "missing"."""
  for name, cfg in spec.config.items():
    if cfg.default is not None:
      continue
    if store is not None and store.has_config(name):
      continue
    return "missing"
  return "ready"


def repos_view(ctx: KelsoCtx) -> list[dict[str, Any]]:
  """Every configured repo: what it is, and what it last mirrored."""
  catalog = ctx.app_catalog()
  out = []
  for name, repo in ctx.config.repos.items():
    state = ctx.kelso_db.get_repo_state(name) if repo.mirrored else None
    out.append(
      {
        "name": name,
        "kind": repo.kind,
        "location": repo.describe(),
        "url": repo.remote.url if repo.remote else None,
        "path": str(repo.path),
        "exists": repo.path.is_dir(),
        "removable": name != LOCAL_REPO,
        "apps": sum(
          1 for entries in catalog.values() for e in entries if e.source == name
        ),
        "bound_apps": list(bound_apps(ctx, name)),
        "sha": state["sha"] if state else None,
        "updated_at": state["at"] if state else None,
      }
    )
  return out


def contested_view(ctx: KelsoCtx) -> dict[str, list[str]]:
  """App ids more than one repo carries, and which repos those are."""
  return {app_id: sorted(repos) for app_id, repos in ctx.contested_app_ids().items()}


def _catalog_spec(entry: CatalogEntry) -> AppSpec | None:
  """The bundle's schema, or None when the bundle on disk does not parse."""
  try:
    return load_bundle(entry.path).app_spec()
  except (ValueError, RuntimeError):
    return None


def _gauge_bytes(gauges: dict[str, Any], name: str) -> int | None:
  entry = gauges.get("gauge/" + name)
  if entry is None:
    return None
  try:
    return int(float(entry.value))
  except ValueError:
    return None


def volumes_view(ctx: KelsoCtx) -> list[dict[str, Any]]:
  """Every kelso-managed volume on disk, whatever declared it."""
  running = {
    observation.app_id
    for observation in ctx.observations()
    if observation.running_count
  }
  declared: dict[str, set[str]] = {}
  volumes = []
  gauges = ctx.read_gauges("volume_size_bytes/")

  for kind, root in sorted(ctx.config.volume_roots.items()):
    if not root.is_dir():
      continue
    for app_dir in sorted(root.iterdir()):
      if not app_dir.is_dir():
        continue
      app_id = app_dir.name
      if app_id not in declared:
        spec = ctx.staged_spec(app_id)
        declared[app_id] = set(spec.volumes) if spec else set()
      for volume_dir in sorted(app_dir.iterdir()):
        if not volume_dir.is_dir():
          continue
        volumes.append(
          {
            "app_id": app_id,
            "name": volume_dir.name,
            "kind": kind,
            "path": str(volume_dir),
            "in_use": app_id in running,
            # False means the data outlived whatever declared it: either the
            # app is gone, or its manifest stopped naming this volume.
            "declared": volume_dir.name in declared[app_id],
            "bytes": _gauge_bytes(
              gauges, f"volume_size_bytes/{app_id}/{kind}/{volume_dir.name}"
            ),
          }
        )
  return volumes


def host_volumes_view(ctx: KelsoCtx) -> list[dict[str, Any]]:
  """The `[host_volume]` entries config.toml declares, in tag order."""
  gauges = ctx.read_gauges("volume_size_bytes//host/")
  return [
    {
      "tag": tag,
      "path": str(volume.path),
      "readonly": volume.readonly,
      "require_mount": volume.require_mount,
      "exists": volume.path.is_dir(),
      "bytes": _gauge_bytes(gauges, f"volume_size_bytes//host/{tag}"),
    }
    for tag, volume in sorted(ctx.config.host_volumes.items())
  ]


def config_request_view(request: ConfigRequest) -> dict[str, Any]:
  """A ConfigRequest as JSON. Fields never carry a secret's value to begin with."""
  return {**asdict(request), "missing": request.missing()}


def route_providers_view(ctx: KelsoCtx) -> list[dict[str, Any]]:
  """Configured route providers in tag order, less the built-in no-op."""
  return [
    {"tag": tag, "kind": entry.kind, "domain": entry.domain}
    for tag, entry in sorted(ctx.config.route_providers.items())
    if tag != NONE_ROUTE_PROVIDER_TAG
  ]


def kelso_dirs_view(ctx: KelsoCtx) -> list[dict[str, Any]]:
  """Kelso's own directories, as last recorded by volume-metrics.

  The list comes from `metric.KELSO_DIRS`, so a caller renders whatever
  kelsod names rather than knowing the set in advance. `bytes` is None until
  volume-metrics has run over that directory at least once.
  """
  return [
    {
      "name": entry.name,
      "description": entry.description,
      "bytes": _gauge_bytes(ctx.read_gauges(entry.gauge), entry.gauge),
    }
    for entry in KELSO_DIRS
  ]


def snapshots_view(ctx: KelsoCtx) -> list[dict[str, Any]]:
  """Every snapshot archive, newest name first."""
  rows = []
  for app_id in snapshotted_app_ids(ctx):
    for name in snapshot_names(app_id, ctx):
      taken_at, tag = split_snapshot_name(name)
      archive = snapshot_archive(ctx.config.snapshot_root, app_id, name)
      rows.append(
        {
          "app_id": str(app_id),
          "name": name,
          "taken_at": taken_at,
          "tag": tag,
          "bytes": archive.stat().st_size if archive.is_file() else None,
        }
      )
  rows.sort(key=lambda row: row["name"], reverse=True)
  return rows


def activity_view(
  ctx: KelsoCtx, *, app: str | None = None, limit: int = 20
) -> list[dict[str, Any]]:
  """Recorded unattended runs, newest first; see `lib/activity.py`."""
  return activity.list_runs(ctx, app=app, limit=limit)


def activity_log_view(ctx: KelsoCtx, filename: str) -> dict[str, Any]:
  """One run's output file, as `list_runs` named it in `log`."""
  text = activity.read_run_log(ctx, filename)
  middle = filename.removesuffix(".log").split(".")[1:-1]
  return {
    "app_id": ".".join(middle) or None,
    "file": filename,
    "text": text,
  }


def app_logs_view(app_id: AppID, ctx: KelsoCtx, *, tail: int) -> dict[str, Any]:
  """One app's container logs as of now; poll it again for live output."""
  return {
    "app_id": str(app_id),
    "tail": tail,
    "text": logs_text(app_id, ctx, tail=tail),
  }


def app_view(app_id: AppID, ctx: KelsoCtx) -> dict[str, Any]:
  """One app in full: what `kelso inspect` shows, as data."""
  observation = _observation(app_id, ctx)
  spec = ctx.staged_spec(app_id)
  view = _summary(observation, ctx, spec=spec)

  if spec is None:
    return view

  run_data = load_run_data(spec, ctx)
  volumes = _volumes(spec, run_data, ctx)
  view.update(
    {
      "description": spec.description,
      # The whole `[app]` table, extras included -- the section allows unknown
      # keys precisely so a bundle can carry author, source, license and the
      # like, and a viewer should show whatever the author wrote.
      "metadata": {
        key: value
        for key, value in spec.manifest.app.model_dump().items()
        if value not in (None, "", {})
      },
      "subdomain": spec.subdomain,
      "network_mode": spec.network_mode,
      "run_path": str(ctx.staged_paths(app_id).run_path),
      "manifest_stale": ctx.manifest_stale(app_id),
      "units": _units(spec, observation),
      "routes": _routes(spec, run_data, ctx),
      "volumes": volumes,
      "volume_bytes": sum(v["bytes"] for v in volumes if v["bytes"] is not None),
      "commands": [
        {"name": name, "desc": command.desc, "unit": command.run_unit}
        for name, command in spec.commands.items()
      ],
      "issues": [
        {"problem": issue.problem, "fix": issue.fix}
        for issue in run_data.start_blockers
      ],
    }
  )
  return view


def _observation(app_id: AppID, ctx: KelsoCtx) -> AppObservation:
  for observation in ctx.observations():
    if observation.app_id == app_id:
      return observation
  raise ValueError(f'No app state found for "{app_id}"')


def _summary(
  observation: AppObservation,
  ctx: KelsoCtx,
  *,
  spec: AppSpec | None = None,
) -> dict[str, Any]:
  if spec is None:
    spec = ctx.staged_spec(observation.app_id)
  return {
    "app_id": str(observation.app_id),
    "display_name": spec.display_name if spec else "",
    "version": spec.version if spec else None,
    "status": observation.status,
    "state": observation.state,
    "containers": {
      "running": observation.running_count,
      "total": len(observation.containers),
    },
    "configured": _configured(observation, spec, ctx),
    "config_pending": observation.config_pending,
    "volume_count": len(spec.volumes) if spec else 0,
    "last_action": observation.last_action,
  }


def _configured(
  observation: AppObservation, spec: AppSpec | None, ctx: KelsoCtx
) -> str | None:
  """ "ready", "missing", or None when the app has no config store yet."""
  if not observation.config_exists or spec is None:
    return None
  return "missing" if load_run_data(spec, ctx).start_blockers else "ready"


def _units(spec: AppSpec, observation: AppObservation) -> list[dict[str, Any]]:
  """Declared run units joined to whatever containers are actually up."""
  containers = {c.run_unit: c for c in observation.containers}
  units = []
  for name, unit in spec.run_units.items():
    container = containers.get(name)
    units.append(
      {
        "name": name,
        "image": unit.image,
        "restart": unit.restart,
        "state": container.state if container else None,
        "container_name": container.name if container else None,
        "container_id": container.container_id if container else None,
        # As the manifest wrote it, so `${admin_pass}` stays a placeholder.
        # The *resolved* environment is `AppRunData.config_env`, which carries
        # secret values and must never be projected.
        "environment": dict(unit.environment),
        "command": list(unit.command) if unit.command else None,
        "volumes": [
          {
            "name": vol_name,
            "path": bound.guest_path,
            "kind": bound.volume.kind,
            "readonly": bound.readonly,
            "desc": bound.volume.desc,
          }
          for vol_name, bound in unit.volumes.items()
        ],
      }
    )
  return units


def _routes(spec: AppSpec, run_data: AppRunData, ctx: KelsoCtx) -> list[dict[str, Any]]:
  published = published_route_urls(spec, run_data, ctx)
  assignments = ctx.app_store(spec.app).list_route_assignments()
  routes = []
  for name, route in spec.routes.items():
    assigned = run_data.routes.get(name)
    routes.append(
      {
        "name": name,
        "unit": route.run_unit_name,
        "desc": route.desc,
        "private": route.private,
        "scheme": route.scheme,
        "container_port": route.container_port,
        "host_port": assigned.host_port if assigned else None,
        "url": run_data.route_urls.get(name),
        "published_url": published.get(name),
        "provider": assignments.get(name),
      }
    )
  return routes


def _volumes(
  spec: AppSpec, run_data: AppRunData, ctx: KelsoCtx
) -> list[dict[str, Any]]:
  binds = ctx.app_store(spec.app).list_binds()
  # One read for every volume on this page. Sizes come from the gauges
  # volume-metrics records, never from walking the tree here: a `bulk` volume
  # or a bound media library is unbounded, and this runs on every page load.
  gauges = ctx.read_gauges("volume_size_bytes/")
  volumes = []
  for name, volume in spec.volumes.items():
    link = run_data.volume_links.get(name)
    source = link.source if link else None
    # Host volumes are gauged under the tag they are bound to, since two apps
    # binding one host volume are looking at the same directory.
    if volume.kind == "host":
      tag = binds.get(name)
      key = f"volume_size_bytes//host/{tag}" if tag else None
    else:
      key = f"volume_size_bytes/{spec.app}/{volume.kind}/{name}"
    volumes.append(
      {
        "name": name,
        "kind": volume.kind,
        "readonly": volume.readonly,
        "path": str(source) if source else None,
        # None until volume-metrics has run over this volume at least once.
        "bytes": _gauge_bytes(gauges, key) if key else None,
        # Only `host` volumes are bindable; the rest are kelso's to place.
        "bind": binds.get(name) if volume.kind == "host" else None,
      }
    )
  return volumes
