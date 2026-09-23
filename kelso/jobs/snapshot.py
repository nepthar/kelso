from kelso.jobs.job import Job, logger
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle import (
  delete_snapshot,
  resolve_snapshot_app,
  snapshot,
  snapshot_names,
  start,
  stop,
)
from kelso.lib.lifecycle.snapshot import SNAPSHOT_TAR_SUFFIX


class SnapshotJob(Job):
  name = "snapshot"
  description = "Copy an app's volumes and run state to a snapshot archive"
  required_args = ("app",)
  optional_args = ("label",)

  def init(self, ctx: KelsoCtx, kwargs: dict[str, str]) -> None:
    app = ctx.resolve_app(kwargs["app"])
    self.app = str(app)
    self.app_id = app
    self.label = kwargs.get("label", "")

  def run(self, ctx: KelsoCtx) -> None:
    """Stop if running, copy, start if we stopped.

    Holds the app lock throughout; the kelso lock only around stop and start, so
    other apps proceed while volumes copy.
    """
    app = self.app_id
    by = f"snapshot {app}"
    running = 0
    with ctx.app_lock(app, by):
      with ctx.kelso_lock(by):
        try:
          running = ctx.run_state(app).running_count
        except ValueError:
          running = 0
        if running:
          stop(app, ctx)
      try:
        path = snapshot(app, ctx, label=self.label)
      finally:
        if running:
          with ctx.kelso_lock(by):
            start(app, ctx.config.app_run_path(app), ctx)
    logger.info("Snapshot of %s written to %s", app, path)


class DeleteSnapshotJob(Job):
  name = "snapshot-delete"
  description = "Delete a snapshot archive"
  required_args = ("app", "snapshot")

  def init(self, ctx: KelsoCtx, kwargs: dict[str, str]) -> None:
    """The app is resolved against snapshots/, so a removed app's archive can go too."""
    app = resolve_snapshot_app(ctx, kwargs["app"])
    name = kwargs["snapshot"].removesuffix(SNAPSHOT_TAR_SUFFIX)
    available = snapshot_names(app, ctx)
    if name not in available:
      detail = "\n".join(f"  {n}" for n in available) if available else "  (none)"
      raise ValueError(f"No snapshot {name} for {app}. Available:\n{detail}")
    self.app = str(app)
    self.app_id = app
    self.snapshot = name

  def run(self, ctx: KelsoCtx) -> None:
    app = self.app_id
    with ctx.app_lock(app, f"snapshot-delete {app}"):
      delete_snapshot(app, self.snapshot, ctx)
    logger.info("Deleted snapshot %s of %s", self.snapshot, app)
