import difflib
import os
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psutil

from kelso.lib import activity
from kelso.lib import backup as backup_lib
from kelso.lib.app_inventory import CatalogEntry
from kelso.lib.apps import AppID
from kelso.lib.bundle import KLSO_MD_SUFFIX, bundle_text, load_bundle, manifest_text
from kelso.lib.config import NONE_ROUTE_PROVIDER_TAG
from kelso.lib.configflow import ConfigRequest
from kelso.lib.connections import MountConnection
from kelso.lib.describe import AppDescription, describe
from kelso.lib.docker import KelsoRunUnitStatus
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle.cron import cron_runs
from kelso.lib.lifecycle.run import logs_text
from kelso.lib.lifecycle.volumes import volumes_on_disk
from kelso.lib.metric import KELSO_DIRS, filesystem_of, kelso_disks
from kelso.lib.observations import AppObservation, observe
from kelso.lib.repo import LOCAL_REPO, bound_apps
from kelso.lib.routes import get_route_provider
from kelso.lib.run_layout import (
  AppRunData,
  load_run_data,
  resolved_subdomain,
  shown_environment,
)
from kelso.lib.spec import AppSpec
from kelso.lib.store import AppStore


def apps_view(ctx: KelsoCtx) -> list[dict[str, Any]]:
  return [
    _summary(observation, ctx)
    for observation in ctx.observations()
    if observation.loaded
  ]


def metrics_view(ctx: KelsoCtx, prefix: str, hours: int) -> dict[str, Any]:
  until = int(datetime.now(UTC).timestamp())
  since = until - hours * 60 * 60
  metrics: dict[str, list[dict[str, int | float]]] = {}
  for key, entries in ctx.history_gauges(prefix, since).items():
    metrics[key.removeprefix("gauge/")] = [
      {"t": e.unix_seconds, "v": float(e.value)} for e in entries
    ]
  return {"since": since, "until": until, "metrics": metrics}


def catalog_view(ctx: KelsoCtx) -> list[dict[str, Any]]:
  catalog = ctx.inventory.catalog
  return [
    {"name": name, "apps": [_catalog_app(e, ctx) for e in catalog if e.repo == name]}
    for name in ctx.config.repos
  ]


def _catalog_app(entry: CatalogEntry, ctx: KelsoCtx) -> dict[str, Any]:
  spec, error = _catalog_spec(entry)
  # The logtab is what makes an id more than a catalog listing, and AppStore
  # creates it on contact -- so this is a file check, never a store lookup.
  has_config = ctx.config.app_config_path(entry.app_id).is_file()
  store = ctx.app_store(spec.app) if spec is not None and has_config else None
  manifest = manifest_text(entry.path)
  return {
    "app_id": entry.app_id,
    "display_name": spec.display_name if spec else "",
    "version": spec.version if spec else None,
    "error": error,
    "description": spec.description if spec else "",
    "author": spec.author if spec else "",
    "url": spec.url if spec else "",
    "repo": entry.repo,
    "state": ctx.app_state(entry.app_id),
    "configured": config_status(spec, store) if spec else None,
    "manifest": manifest,
    # What the card shows: a .klso.md is shown whole.
    "text": bundle_text(entry.path),
    "markdown": entry.path.name.endswith(KLSO_MD_SUFFIX),
    "manifest_stale": _catalog_manifest_stale(entry, manifest, ctx),
    "warnings": compose_warnings_view(spec) if spec else [],
  }


def compose_warnings_view(spec: AppSpec) -> list[dict[str, Any]]:
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
      fromfile="loaded",
      tofile="remote",
    )
  )


def _catalog_manifest_stale(entry: CatalogEntry, manifest: str, ctx: KelsoCtx) -> bool:
  loaded = ctx.loaded_paths(entry.app_id).manifest_path
  if not manifest or not loaded.is_file():
    return False
  try:
    return loaded.read_text() != manifest
  except OSError:
    return False


def config_status(spec: AppSpec, store: AppStore | None) -> str:
  for name, cfg in spec.config.items():
    if cfg.default is not None:
      continue
    if store is not None and store.has_config(name):
      continue
    return "missing"
  return "ready"


def repos_view(ctx: KelsoCtx) -> list[dict[str, Any]]:
  catalog = ctx.inventory.catalog
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
        "apps": sum(e.repo == name for e in catalog),
        "bound_apps": list(bound_apps(ctx, name)),
        "sha": state["sha"] if state else None,
        "updated_at": state["at"] if state else None,
      }
    )
  return out


def contested_view(ctx: KelsoCtx) -> dict[str, list[str]]:
  return {app_id: sorted(repos) for app_id, repos in ctx.inventory.contested().items()}


def _catalog_spec(entry: CatalogEntry) -> tuple[AppSpec | None, str | None]:
  try:
    return load_bundle(entry.path).app_spec(), None
  except (ValueError, RuntimeError) as e:
    return None, str(e)


def _gauge_bytes(gauges: dict[str, Any], name: str) -> int | None:
  entry = gauges.get("gauge/" + name)
  if entry is None:
    return None
  try:
    return int(float(entry.value))
  except ValueError:
    return None


def volumes_view(ctx: KelsoCtx) -> list[dict[str, Any]]:
  gauges = ctx.read_gauges("volume_size_bytes/")
  return [
    {
      "app_id": str(v.app_id),
      "name": v.name,
      "kind": v.kind,
      "path": str(v.path),
      "use": v.use,
      "bytes": _gauge_bytes(gauges, f"volume_size_bytes/{v.app_id}/{v.kind}/{v.name}"),
    }
    for v in volumes_on_disk(ctx)
  ]


def host_volumes_view(ctx: KelsoCtx) -> list[dict[str, Any]]:
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
  return {**asdict(request), "missing": request.missing()}


def route_providers_view(ctx: KelsoCtx) -> list[dict[str, Any]]:
  return [
    {"tag": tag, "kind": entry.kind, "domain": entry.domain}
    for tag, entry in sorted(ctx.config.route_providers.items())
    if tag != NONE_ROUTE_PROVIDER_TAG
  ]


def published_routes_view(ctx: KelsoCtx, tag: str) -> list[dict[str, Any]]:
  provider = get_route_provider(ctx, tag)
  domain = ctx.config.provider_domain(tag)
  owners = provider.route_owners()
  return [
    {
      "subdomain": subdomain,
      "url": f"https://{subdomain}.{domain}",
      "destination": destination,
      "app": owners.get(subdomain),
    }
    for subdomain, destination in provider.list_routes()
  ]


def kelso_dirs_view(ctx: KelsoCtx) -> list[dict[str, Any]]:
  return [
    {
      "name": entry.name,
      "description": entry.description,
      "bytes": _gauge_bytes(ctx.read_gauges(entry.gauge), entry.gauge),
    }
    for entry in KELSO_DIRS
  ]


def host_view(ctx: KelsoCtx) -> dict[str, Any]:
  memory = psutil.virtual_memory()
  disks = []
  for disk in kelso_disks(ctx):
    usage = psutil.disk_usage(str(disk.mountpoint))
    disks.append(
      {
        "device": disk.device,
        "mountpoint": str(disk.mountpoint),
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "holds": list(disk.holds),
        "gauge": disk.gauge,
      }
    )
  return {
    "cpus": psutil.cpu_count(),
    "memory_bytes": memory.total,
    "memory_used_bytes": memory.total - memory.available,
    "uptime_seconds": int(time.time() - psutil.boot_time()),
    "load": list(os.getloadavg()),
    "disks": disks,
  }


def volume_roots_view(ctx: KelsoCtx) -> list[dict[str, Any]]:
  totals: dict[str, int] = {}
  for key, entry in ctx.read_gauges("volume_size_bytes/").items():
    _, _, app_id, kind, name = key.split("/", 4)
    root = ctx.config.volume_roots.get(kind)
    # Only volumes still on disk: a removed one keeps its last reading for hours.
    if root is not None and (root / app_id / name).is_dir():
      totals[kind] = totals.get(kind, 0) + int(float(entry.value))
  roots = []
  for kind, root in sorted(ctx.config.volume_roots.items()):
    row: dict[str, Any] = {
      "kind": kind,
      "path": str(root),
      "bytes": totals.get(kind, None if _holds_volumes(root) else 0),
      "device": None,
      "mountpoint": None,
      "used": None,
      "available": None,
    }
    filesystem = filesystem_of(root)
    if filesystem is not None:
      usage = psutil.disk_usage(str(root))
      row |= {
        "device": filesystem[0],
        "mountpoint": str(filesystem[1]),
        "used": usage.used,
        "available": usage.free,
      }
    roots.append(row)
  return roots


def _holds_volumes(root: Path) -> bool:
  return root.is_dir() and any(
    volume.is_dir()
    for app_dir in root.iterdir()
    if app_dir.is_dir()
    for volume in app_dir.iterdir()
  )


def backups_view(ctx: KelsoCtx) -> dict[str, Any]:
  config = ctx.config
  view: dict[str, Any] = {
    "kelso_id": ctx.kelso_db.kelso_id(),
    "schedule": config.backup.schedule,
    "keep": config.backup.keep.model_dump(),
    "last_run": ctx.kelso_db.last_backup_run(),
    "destination": None,
    "problem": None,
    "backups": [],
  }
  try:
    restic = backup_lib.repository(ctx)
  except ValueError as e:
    view["problem"] = str(e)
    return view
  view["destination"] = str(restic.repo)
  if restic.exists():
    view["backups"] = [
      {
        "id": b.id,
        "app_id": str(b.app),
        "app_version": b.version,
        "reason": b.reason,
        "taken_at": b.time,
        "size": b.size,
      }
      for b in reversed(backup_lib.backups(restic))
    ]
  return view


def cron_view(ctx: KelsoCtx) -> list[dict[str, Any]]:
  return [
    {
      "app_id": str(run.app_id),
      "job": run.name,
      "command": run.command,
      "schedule": run.schedule,
      "next_at": run.next_at.astimezone(UTC).isoformat(timespec="seconds"),
      "status": run.status,
    }
    for run in cron_runs(ctx)
  ]


def activity_view(
  ctx: KelsoCtx,
  *,
  app: str | None = None,
  verb: str | None = None,
  limit: int = 20,
) -> list[dict[str, Any]]:
  return activity.list_runs(ctx, app=app, verb=verb, limit=limit)


def activity_log_view(ctx: KelsoCtx, filename: str) -> dict[str, Any]:
  text = activity.read_run_log(ctx, filename)
  middle = filename.removesuffix(".log").split(".")[1:-1]
  return {
    "app_id": ".".join(middle) or None,
    "file": filename,
    "state": activity.run_state(text),
    "text": text,
  }


def app_logs_view(app_id: AppID, ctx: KelsoCtx, *, tail: int) -> dict[str, Any]:
  return {
    "app_id": str(app_id),
    "tail": tail,
    "text": logs_text(app_id, ctx, tail=tail),
  }


def app_view(app_id: AppID, ctx: KelsoCtx) -> dict[str, Any]:
  observation = observe(app_id, ctx)
  try:
    spec = ctx.loaded_spec(app_id)
  except ValueError as e:
    return {**_summary(observation, ctx), "issues": [{"problem": str(e)}]}
  view = _summary(observation, ctx, spec=spec)

  if spec is None:
    return view

  run_data = load_run_data(spec, ctx)
  description = describe(spec, run_data, ctx)
  volumes = _volumes(spec, description, ctx)
  view.update(
    {
      "description": spec.description,
      "author": spec.author,
      "url": spec.url,
      "security": security_notes(spec, run_data, ctx),
      "subdomain": resolved_subdomain(spec, ctx),
      "network_mode": run_data.network_mode,
      "run_path": str(ctx.loaded_paths(app_id).run_path),
      "manifest_stale": ctx.manifest_stale(app_id),
      "manifest_text": ctx.loaded_paths(app_id).manifest_path.read_text(),
      "units": _units(spec, observation, run_data),
      "routes": _routes(description),
      "ports": _ports(description),
      "volumes": volumes,
      "volume_bytes": sum(v["bytes"] for v in volumes if v["bytes"] is not None),
      "commands": [
        {
          "name": name,
          "desc": command.desc,
          "unit": command.run_unit,
          "cmd": command.cmd,
        }
        for name, command in spec.commands.items()
      ],
      "issues": [
        {"problem": issue.problem, "fix": issue.fix}
        for issue in run_data.start_blockers
      ],
    }
  )
  return view


def _summary(
  observation: AppObservation,
  ctx: KelsoCtx,
  *,
  spec: AppSpec | None = None,
) -> dict[str, Any]:
  broken = False
  if spec is None:
    try:
      spec = ctx.loaded_spec(observation.app_id)
    except ValueError:
      broken = True
  return {
    "app_id": str(observation.app_id),
    "display_name": spec.display_name if spec else "",
    "version": spec.version if spec else None,
    "update_version": observation.update_version,
    "status": observation.status(*((spec.run_units, spec.one_shots) if spec else ())),
    "state": observation.state,
    "containers": {
      "running": observation.running_count,
      "total": len(observation.containers),
    },
    "configured": "broken manifest" if broken else _configured(observation, spec, ctx),
    "changes_pending": observation.changes_pending,
    "volume_count": len(spec.volumes) if spec else 0,
    "last_action": observation.last_action,
  }


def _configured(
  observation: AppObservation, spec: AppSpec | None, ctx: KelsoCtx
) -> str | None:
  if not observation.config_exists or spec is None:
    return None
  return "missing" if load_run_data(spec, ctx).start_blockers else "ready"


def _units(
  spec: AppSpec, observation: AppObservation, run_data: AppRunData
) -> list[dict[str, Any]]:
  containers = {c.run_unit: c for c in observation.containers}
  units = []
  for name, unit in spec.run_units.items():
    container = containers.get(name)
    units.append(
      {
        "name": name,
        "image": unit.image,
        "restart": unit.restart,
        "state": _unit_state(container),
        "container_name": container.name if container else None,
        "container_id": container.container_id if container else None,
        # As the manifest wrote it, and as the container gets it. Never
        # `AppRunData.config_env`, which carries secret values.
        "environment": dict(unit.environment),
        "resolved_environment": shown_environment(spec, unit, run_data),
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


def _unit_state(container: KelsoRunUnitStatus | None) -> str | None:
  if container is None:
    return None
  return "finished" if container.finished else container.state


def _routes(description: AppDescription) -> list[dict[str, Any]]:
  return [
    {
      "name": route.name,
      "unit": route.unit,
      "desc": route.desc,
      "admin": route.admin,
      "scheme": route.scheme,
      "container_port": route.container_port,
      "host_port": route.host_port,
      "url": route.url,
      "published_url": route.providers[0][1] if route.providers else None,
      "host_url": route.host_url,
      "providers": [{"tag": tag, "url": url} for tag, url in route.providers],
    }
    for route in description.routes
  ]


def _ports(description: AppDescription) -> list[dict[str, Any]]:
  return [
    {
      "name": port.name,
      "unit": port.unit,
      "desc": port.desc,
      "proto": port.proto,
      "container_port": port.container_port,
      "host_port": port.host_port,
    }
    for port in description.ports
  ]


def security_notes(
  spec: AppSpec, run_data: AppRunData, ctx: KelsoCtx
) -> list[dict[str, str]]:
  danger, caution, ok = [], [], []
  for name, conn in spec.connections.items():
    if isinstance(conn, MountConnection):
      danger.append(f"Its {name} connection gives it {conn.kind.desc}.")
  for warning in spec.compose_warnings:
    keys = ", ".join(warning.options)
    danger.append(
      f"{warning.run_unit} sets docker options kelso doesn't check: {keys}."
    )
  binds = ctx.app_store(spec.app).list_binds()
  host_volumes = {n: v for n, v in spec.volumes.items() if v.kind == "host"}
  for name, volume in host_volumes.items():
    where = f"host volume {binds[name]}" if name in binds else "a host volume"
    if volume.readonly:
      ok.append(f"It can read, but not change, files in {where} ({name}).")
    else:
      caution.append(f"It can change files on this machine in {where} ({name}).")
  mode = run_data.network_mode
  if mode == "host":
    danger.append(
      "It shares the host's network: it reaches whatever the host can, and can "
      "listen on any of its ports."
    )
  elif mode == "open":
    caution.append("It can reach anything on the internet, and nothing is counted.")
  elif mode == "watch":
    caution.append("It can reach anything on the internet; kelso counts what it does.")
  elif mode == "ask":
    ok.append("It asks you before reaching a domain its manifest doesn't list.")
  else:
    ok.append("It reaches only the domains its manifest lists and those you allow.")
  if not (danger or caution or host_volumes):
    ok.append("It has no access to this machine beyond its own volumes.")
  return [
    {"level": level, "text": text}
    for level, texts in (("danger", danger), ("caution", caution), ("ok", ok))
    for text in texts
  ]


def _volumes(
  spec: AppSpec, description: AppDescription, ctx: KelsoCtx
) -> list[dict[str, Any]]:
  # Sizes come from the gauges volume-metrics records, never from walking the
  # tree here: a `bulk` volume or a bound media library is unbounded, and this
  # runs on every page load.
  gauges = ctx.read_gauges("volume_size_bytes/")
  volumes = []
  for volume in description.volumes:
    # Host volumes are gauged under the tag they are bound to, since two apps
    # binding one host volume are looking at the same directory.
    if volume.kind == "host":
      key = f"volume_size_bytes//host/{volume.bind}" if volume.bind else None
    else:
      key = f"volume_size_bytes/{spec.app}/{volume.kind}/{volume.name}"
    volumes.append(
      {
        "name": volume.name,
        "kind": volume.kind,
        "readonly": volume.readonly,
        "path": volume.path,
        # None until volume-metrics has run over this volume at least once.
        "bytes": _gauge_bytes(gauges, key) if key else None,
        "bind": volume.bind,
      }
    )
  return volumes
