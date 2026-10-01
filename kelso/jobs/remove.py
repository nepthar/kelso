from kelso.jobs.job import Job, logger
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle import PURGE, RM, UNLOAD, removal_plan, rm


class _RemovalJob(Job):
  required_args = ("app",)

  def init(self, ctx: KelsoCtx, kwargs: dict[str, str]) -> None:
    app = ctx.resolve_app(kwargs["app"])
    self.app = str(app)
    self.app_id = app
    self.mode = self.mode_for(kwargs)

  def run(self, ctx: KelsoCtx) -> None:
    app = self.app_id
    with ctx.locked(f"{self.mode} {app}", app):
      # Planning resolves what is about to go before anything is deleted.
      rm(removal_plan(app, ctx, mode=self.mode), ctx)
    logger.info(self.done, app)


class UnloadJob(_RemovalJob):
  name = "unload"
  description = "Stop an app and remove its loaded copy, keeping data and config"
  done = "Unloaded %s. Configuration and volume data were kept"

  def mode_for(self, kwargs: dict[str, str]) -> str:
    return UNLOAD


class RmJob(_RemovalJob):
  name = "rm"
  description = "Unload an app and delete its data, keeping its config unless purged"
  optional_args = ("purge",)
  done = "Removed %s"

  def mode_for(self, kwargs: dict[str, str]) -> str:
    return PURGE if self._bool_arg(kwargs, "purge") else RM
