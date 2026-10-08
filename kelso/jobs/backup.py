from kelso.jobs.job import Job, logger
from kelso.lib import backup as backup_lib
from kelso.lib.apps import AppID
from kelso.lib.backup import MANUAL, SCHEDULED, repository, run_backups
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle import find_backup


class BackupJob(Job):
  name = "backup"
  description = "Back up one app, or every app and kelso itself"
  optional_args = ("app",)
  reason = MANUAL

  def init(self, ctx: KelsoCtx, kwargs: dict[str, str]) -> None:
    if kwargs.get("app"):
      app = ctx.resolve_app(kwargs["app"])
      self.app = str(app)

  def run(self, ctx: KelsoCtx) -> None:
    apps = [ctx.resolve_app(self.app)] if self.app else None
    result = run_backups(ctx, reason=self.reason, apps=apps)
    lines = [f"Backup {result.run}"]
    lines += [f"  backed up {app}" for app in result.backed_up]
    if result.created:
      lines.append(
        "  This is the first backup. Only the recovery phrase can read it: "
        "`kelso system recovery-phrase` shows it."
      )
    logger.info("\n".join(lines))
    if result.failed:
      raise ValueError(
        "Could not back up:\n" + "\n".join(f"  {failure}" for failure in result.failed)
      )


class ScheduledBackupJob(BackupJob):
  """The nightly run kelsod starts: everything, kept by the scheduled policy."""

  name = "scheduled-backup"
  description = "Back up every app and kelso itself, on the [backup] schedule"
  optional_args = ()
  reason = SCHEDULED


class DeleteBackupJob(Job):
  name = "delete-backup"
  description = "Delete one of an app's backups"
  required_args = ("app", "backup")

  def init(self, ctx: KelsoCtx, kwargs: dict[str, str]) -> None:
    """Not resolved against the catalog: a removed app's backups can go too."""
    app = AppID(kwargs["app"])
    find_backup(app, kwargs["backup"], repository(ctx))
    self.app = str(app)
    self.app_id = app
    self.backup = kwargs["backup"]

  def run(self, ctx: KelsoCtx) -> None:
    restic = repository(ctx)
    backup_lib.delete(restic, find_backup(self.app_id, self.backup, restic))
    logger.info("Deleted backup %s of %s", self.backup, self.app_id)
