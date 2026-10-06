from kelso.jobs.job import Job, logger
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle import restart
from kelso.lib.receipt import published_urls


class RestartJob(Job):
  name = "restart"
  description = "Stop an app and start it again, applying any config changes"
  required_args = ("app",)

  def init(self, ctx: KelsoCtx, kwargs: dict[str, str]) -> None:
    app = ctx.resolve_app(kwargs["app"])
    self.app = str(app)
    self.app_id = app

  def run(self, ctx: KelsoCtx) -> None:
    app = self.app_id
    with ctx.locked(f"restart {app}", app):
      result = restart(app, ctx)
    lines = [f"Restarted {app}"]
    lines += [f"  {url}" for url in published_urls(result.spec, result.run_data, ctx)]
    logger.info("\n".join(lines))
