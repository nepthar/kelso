from kelso.jobs.job import Job, logger
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle import DATA, PURGE, RM, TEMP, UNLOAD, removal_plan, rm

_TIERS = {"": RM, "temp": TEMP, "data": DATA, "purge": PURGE}


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
    logger.info("%s: %s", self.mode, app)


class UnloadJob(_RemovalJob):
  name = "unload"
  description = "Stop an app and remove its loaded copy, keeping data and config"

  def mode_for(self, kwargs: dict[str, str]) -> str:
    return UNLOAD


class RmJob(_RemovalJob):
  name = "rm"
  description = "Remove a stopped app; tier temp, data or purge takes more or less"
  optional_args = ("tier",)

  def mode_for(self, kwargs: dict[str, str]) -> str:
    tier = kwargs.get("tier", "")
    if tier not in _TIERS:
      known = ", ".join(t for t in _TIERS if t)
      raise ValueError(f"tier {tier!r} is not one of: {known}")
    return _TIERS[tier]
