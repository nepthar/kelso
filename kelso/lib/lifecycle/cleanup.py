"""`kelso cleanup`: what kelso holds that nothing needs any more, planned first.

It never stops an app: a running app's temp volumes are left alone, and an
image goes only when no loaded app names it and no container uses it.
"""

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from kelso.lib.apps import AppID
from kelso.lib.docker import (
  DockerError,
  DockerImage,
  container_images,
  list_images,
  remove_image,
)
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle._common import logger
from kelso.lib.lifecycle.restore import snapshot_names, snapshotted_app_ids
from kelso.lib.lifecycle.rm import TEMP, removal_plan, rm
from kelso.lib.lifecycle.rootfs import ROOTFS_IMAGE
from kelso.lib.lifecycle.snapshot import (
  SNAPSHOT_TAR_SUFFIX,
  delete_snapshot,
  remove_snapshot_dir,
  snapshot_archive,
)
from kelso.lib.observations import observe

Kind = Literal["snapshot", "incomplete snapshot", "image", "temp and logs", "routes"]


@dataclass(frozen=True)
class Reclaim:
  """One thing cleanup deletes, and roughly the space that frees."""

  kind: Kind
  app: AppID | None
  target: Path | DockerImage | None
  # None when nothing has measured it.
  size: int | None

  @property
  def detail(self) -> str:
    match self.target:
      case DockerImage(refs=refs, id=image_id):
        return ", ".join(refs) or image_id
      case Path() as path if self.kind == "snapshot":
        return path.name.removesuffix(SNAPSHOT_TAR_SUFFIX)
      case Path() as path:
        return str(path)
    return "port and subdomain" if self.kind == "routes" else ""


@dataclass(frozen=True)
class CleanupPlan:
  items: tuple[Reclaim, ...]
  # Running apps whose temp and logs `--temp` leaves alone.
  skipped: tuple[AppID, ...] = ()

  @property
  def size(self) -> int:
    return sum(item.size or 0 for item in self.items)


def excess_snapshots(app: AppID, ctx: KelsoCtx) -> list[Path]:
  """Archives beyond the app's snapshot_max_count, oldest first."""
  # A purged app has no config, and reading an option would create one.
  if not ctx.config.app_config_path(app).is_file():
    return []
  keep = int(ctx.app_option(app, "snapshot_max_count"))
  names = snapshot_names(app, ctx)
  if not keep or len(names) <= keep:
    return []
  root = ctx.config.snapshot_root
  return [snapshot_archive(root, app, name) for name in names[:-keep]]


def prune_snapshots(app: AppID, ctx: KelsoCtx) -> None:
  """Delete the archives beyond snapshot_max_count. The caller holds the app lock."""
  for archive in excess_snapshots(app, ctx):
    delete_snapshot(app, archive.name, ctx)
    logger.info("Deleted snapshot %s of %s", archive.name, app)


def cleanup_plan(ctx: KelsoCtx, *, temp: bool = False) -> CleanupPlan:
  """What cleanup would delete, without deleting it."""
  items = [*_snapshots(ctx), *_incomplete_snapshots(ctx)]
  skipped: list[AppID] = []
  observations = ctx.observations()
  if temp:
    recorded = _recorded_temp_sizes(ctx)
    for observation in observations:
      if not observation.loaded:
        continue
      if observation.running_count:
        skipped.append(observation.app_id)
        continue
      if removal_plan(observation.app_id, ctx, mode=TEMP).volume_paths:
        app = observation.app_id
        items.append(Reclaim("temp and logs", app, None, recorded.get(app)))
  items += [
    Reclaim("routes", o.app_id, None, None) for o in observations if o.orphaned_routes
  ]
  items += [Reclaim("image", None, image, image.size) for image in _unused_images(ctx)]
  return CleanupPlan(tuple(items), tuple(skipped))


def cleanup(plan: CleanupPlan, ctx: KelsoCtx) -> None:
  """Carry out `plan`; an item that is no longer safe to delete is skipped."""
  for item in plan.items:
    try:
      _reclaim(item, ctx)
    except (ValueError, DockerError) as e:
      logger.warning("Skipped %s %s: %s", item.kind, item.app or item.detail, e)


def _reclaim(item: Reclaim, ctx: KelsoCtx) -> None:
  by = f"cleanup {item.kind}"
  match item:
    case Reclaim(kind="snapshot", app=AppID() as app, target=Path() as archive):
      with ctx.app_lock(app, by):
        if archive.is_file():
          delete_snapshot(app, archive.name, ctx)
          logger.info("Deleted snapshot %s of %s", archive.name, app)
    case Reclaim(kind="incomplete snapshot", target=Path() as folder):
      if item.app is None:
        _remove_incomplete(folder)
      else:
        with ctx.app_lock(item.app, by):
          _remove_incomplete(folder)
    case Reclaim(kind="temp and logs", app=AppID() as app):
      with ctx.locked(by, app):
        rm(removal_plan(app, ctx, mode=TEMP), ctx)
    case Reclaim(kind="routes", app=AppID() as app):
      with ctx.locked(by, app):
        if not observe(app, ctx).orphaned_routes:
          raise ValueError("it is in use again")
        ctx.kelso_db.purge_app(app)
        logger.info("Released the routes of %s", app)
    case Reclaim(kind="image", target=DockerImage() as image):
      with ctx.kelso_lock(by):
        remove_image(image)
        logger.info("Removed image %s", item.detail)


def _remove_incomplete(folder: Path) -> None:
  if folder.exists():
    remove_snapshot_dir(folder)
    logger.info("Removed incomplete snapshot %s", folder)


def _snapshots(ctx: KelsoCtx) -> list[Reclaim]:
  return [
    Reclaim("snapshot", app, archive, archive.stat().st_size)
    for app in snapshotted_app_ids(ctx)
    for archive in excess_snapshots(app, ctx)
  ]


def _incomplete_snapshots(ctx: KelsoCtx) -> list[Reclaim]:
  """Folders a failed snapshot or restore left behind."""
  found = [
    Reclaim("incomplete snapshot", app, entry, None)
    for app in snapshotted_app_ids(ctx)
    for entry in sorted((ctx.config.snapshot_root / app).iterdir())
    if entry.is_dir()
  ]
  scratch = ctx.config.temp_root / "current_snapshot"
  if scratch.is_dir():
    # Without its snapshot.toml there is no app to lock; `snapshot` writes it
    # straight after creating the folder.
    meta = scratch / "snapshot.toml"
    app = AppID(tomllib.loads(meta.read_text())["app_id"]) if meta.is_file() else None
    found.append(Reclaim("incomplete snapshot", app, scratch, None))
  return found


def _recorded_temp_sizes(ctx: KelsoCtx) -> dict[str, int]:
  """Each app's temp and logs volumes, as the metrics job last recorded them."""
  sizes: dict[str, int] = {}
  for key, entry in ctx.metrics_log.scan("gauge/volume_size_bytes/").items():
    _, _, app, kind, _ = key.split("/", 4)
    if kind in ("temp", "logs"):
      sizes[app] = sizes.get(app, 0) + int(float(entry.value))
  return sizes


def _unused_images(ctx: KelsoCtx) -> list[DockerImage]:
  """Images no loaded app names and no container, kelso's or not, was made from."""
  named = {_normal(ROOTFS_IMAGE)}
  for raw_id in ctx.config.run_root.iterdir() if ctx.config.run_root.is_dir() else ():
    spec = ctx.loaded_spec(raw_id.name)
    if spec is not None:
      named |= {_normal(unit.image) for unit in spec.run_units.values()}
  used = container_images()
  keep = named | {_normal(ref) for ref in used}
  # A reference pinned by digest keeps every tag of its repository.
  pinned = {ref.split("@")[0] for ref in keep if "@" in ref}

  unused = []
  for image in list_images():
    if any(ref in keep or ref.rpartition(":")[0] in pinned for ref in image.refs):
      continue
    if any(ref.removeprefix("sha256:")[:12] == image.id[:12] for ref in used):
      continue
    unused.append(image)
  return unused


def _normal(ref: str) -> str:
  """An image reference as `docker image ls` would name it."""
  for prefix in ("docker.io/library/", "docker.io/"):
    ref = ref.removeprefix(prefix)
  if "@" in ref or ":" in ref.rpartition("/")[2]:
    return ref
  return f"{ref}:latest"
