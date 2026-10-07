from kelso.jobs.job import Job, logger
from kelso.lib.backup import MANUAL, SCHEDULED, run_backups
from kelso.lib.kelso import KelsoCtx


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
