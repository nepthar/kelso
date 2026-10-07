from kelso.jobs.job import Job, logger
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle import update


class UpdateJob(Job):
  name = "update"
  description = "Update an app from its source, backing it up first"
  required_args = ("app",)

  def init(self, ctx: KelsoCtx, kwargs: dict[str, str]) -> None:
    app = ctx.resolve_app(kwargs["app"])
    self.app = str(app)
    self.app_id = app

  def run(self, ctx: KelsoCtx) -> None:
    result = update(self.app_id, ctx)
    logger.info(result.summary(self.app))
