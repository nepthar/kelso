import kelso.lib.lifecycle as lifecycle
from kelso.jobs.job import Job, logger
from kelso.lib.kelso import KelsoCtx


class StopJob(Job):
  name = "stop"
  description = "Stop a running app"
  required_args = ("app",)

  def init(self, ctx: KelsoCtx, kwargs: dict[str, str]) -> None:
    app = ctx.resolve_app_id(kwargs["app"], prefer_loaded=True)
    self.app = str(app)
    self.app_id = app

  def run(self, ctx: KelsoCtx) -> None:
    app = self.app_id
    lifecycle.stop(app, ctx)
    logger.info("Stopped %s", app)
