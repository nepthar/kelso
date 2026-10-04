"""Updating a loaded app from the bundle it was last loaded from."""

from dataclasses import dataclass
from pathlib import Path

from kelso.lib.apps import AppID, record_app_action
from kelso.lib.bundle import load_bundle
from kelso.lib.docker import pull_image
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle._common import logger
from kelso.lib.lifecycle.cleanup import prune_snapshots
from kelso.lib.lifecycle.load import load
from kelso.lib.lifecycle.run import start, stop
from kelso.lib.lifecycle.snapshot import SNAPSHOT_TAR_SUFFIX, snapshot

PRE_UPDATE_LABEL = "pre-update"


@dataclass(frozen=True)
class UpdateResult:
  previous: str | None
  version: str
  snapshot: str
  was_running: bool

  def summary(self, app: AppID) -> str:
    lines = [
      f"Updated {app} from {self.previous or 'an unrecorded version'} to "
      f"{self.version}",
      f"  snapshot {self.snapshot} holds the version it replaced",
    ]
    if self.was_running:
      lines.append(f"Restarted {app}")
    return "\n".join(lines)


def update_source(app: AppID, ctx: KelsoCtx) -> Path:
  """Where a loaded app was loaded from. Raises ValueError if that is gone."""
  if not ctx.is_loaded(app):
    raise ValueError(f"App {app} is not loaded; run `kelso load {app}` first")
  origin = ctx.loaded_origin(app)
  if origin is None or not origin.exists():
    raise ValueError(
      f"App {app} was loaded from {origin or 'a source kelso did not record'}, "
      f"which is gone. Load it again from where it lives now with `kelso load`."
    )
  return origin


def update(app: AppID, ctx: KelsoCtx) -> UpdateResult:
  """Pull the new version's images, then stop, snapshot, load, and start again.

  Takes the app and kelso locks itself. A load that fails after the snapshot
  leaves the app stopped, and the error names the snapshot to restore.
  """
  source = update_source(app, ctx)
  try:
    spec = load_bundle(source).app_spec()
  except ValueError as e:
    raise ValueError(f"Nothing changed: {app}'s source does not parse. {e}") from e

  for image in sorted({unit.image for unit in spec.run_units.values()}):
    logger.info("Pulling %s", image)
    pull_image(image)

  by = f"update {app}"
  previous = ctx.app_store(app).get_meta("loaded_version")
  with ctx.app_lock(app, by):
    with ctx.kelso_lock(by):
      running = bool(ctx.run_state(app).running_count)
      if running:
        stop(app, ctx)
    try:
      archive = snapshot(app, ctx, label=PRE_UPDATE_LABEL)
      prune_snapshots(app, ctx)
    except Exception:
      if running:
        with ctx.kelso_lock(by):
          start(app, ctx.config.app_run_path(app), ctx)
      raise
    name = archive.name.removesuffix(SNAPSHOT_TAR_SUFFIX)

    with ctx.kelso_lock(by):
      try:
        load(app, source, ctx, version_change=True)
      except Exception as e:
        raise RuntimeError(
          f"{e}\n{app} is stopped. To go back to {previous or 'the old version'}, "
          f"run `kelso snapshot restore {app} {name}`."
        ) from e
      record_app_action(
        "updated",
        app,
        ctx,
        version=spec.version,
        previous=previous or "",
        snapshot=name,
      )
      if running:
        start(app, ctx.config.app_run_path(app), ctx)
  return UpdateResult(previous, spec.version, name, running)
