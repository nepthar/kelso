from kelso.jobs.job import Job, logger
from kelso.lib.apps import AppID
from kelso.lib.backup import repository
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle import find_backup, restore, restore_plan


class RestoreJob(Job):
  name = "restore"
  description = "Put an app back as one of its backups holds it"
  required_args = ("app", "backup")

  def init(self, ctx: KelsoCtx, kwargs: dict[str, str]) -> None:
    """Not resolved against the catalog: an app removed since can come back."""
    app = AppID(kwargs["app"])
    find_backup(app, kwargs["backup"], repository(ctx))
    self.app = str(app)
    self.app_id = app
    self.backup = kwargs["backup"]

  def run(self, ctx: KelsoCtx) -> None:
    app = self.app_id
    restic = repository(ctx)
    plan = restore_plan(app, self.backup, ctx, restic)
    with ctx.locked(f"restore {app}", app):
      restore(plan, ctx, restic)
    logger.info("Restored %s from backup %s", app, plan.backup.id)
