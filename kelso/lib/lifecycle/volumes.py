"""App volumes as they are on disk, and removing the orphaned ones."""

import shlex
from dataclasses import dataclass
from pathlib import Path

from kelso.lib.apps import AppID
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle._common import logger
from kelso.lib.lifecycle.rootfs import run_as_root
from kelso.lib.spec import AppSpec

IN_USE = "in use"
IDLE = "idle"
# The app is not loaded, so no manifest speaks for the volume; kept on purpose.
UNLOADED = "unloaded"
# The app is loaded and its manifest does not name the volume as this kind.
ORPHANED = "orphaned"
# The app is loaded but its manifest no longer parses, so nothing can say.
UNKNOWN = "unknown"


@dataclass(frozen=True)
class VolumeOnDisk:
  app_id: AppID
  name: str
  kind: str
  path: Path
  use: str


def volumes_on_disk(ctx: KelsoCtx) -> list[VolumeOnDisk]:
  """Every kelso-managed volume directory, whatever declared it."""
  running = {o.app_id for o in ctx.observations() if o.running_count}
  specs: dict[str, AppSpec | None] = {}
  found = []
  for kind, root in sorted(ctx.config.volume_roots.items()):
    if not root.is_dir():
      continue
    for app_dir in sorted(p for p in root.iterdir() if p.is_dir()):
      app_id = AppID(app_dir.name)
      if app_id not in specs:
        specs[app_id] = ctx.loaded_spec(app_id)
      for volume_dir in sorted(p for p in app_dir.iterdir() if p.is_dir()):
        use = _use(ctx, app_id, specs[app_id], volume_dir.name, kind, running)
        found.append(VolumeOnDisk(app_id, volume_dir.name, kind, volume_dir, use))
  return found


def _use(
  ctx: KelsoCtx,
  app_id: AppID,
  spec: AppSpec | None,
  name: str,
  kind: str,
  running: set[AppID],
) -> str:
  if spec is None:
    return UNKNOWN if ctx.is_loaded(app_id) else UNLOADED
  declared = spec.volumes.get(name)
  if declared is None or declared.kind != kind:
    return ORPHANED
  return IN_USE if app_id in running else IDLE


def remove_orphaned_volume(app_id: AppID, name: str, ctx: KelsoCtx) -> None:
  """Delete every orphaned volume `name` of `app_id`. The caller holds its locks.

  Raises ValueError when there is no such orphaned volume.
  """
  orphans = [
    v
    for v in volumes_on_disk(ctx)
    if v.app_id == app_id and v.name == name and v.use == ORPHANED
  ]
  if not orphans:
    raise ValueError(
      f"{app_id} has no orphaned volume {name!r}; only a volume its manifest "
      f"no longer names can be removed this way"
    )
  for volume in orphans:
    run_as_root(
      f"remove the orphaned volume {volume.path}",
      f"rm -rf -- {shlex.quote(str(volume.path.resolve()))}",
      [volume.path.parent],
    )
    logger.info("Removed orphaned %s volume %s of %s", volume.kind, name, app_id)
