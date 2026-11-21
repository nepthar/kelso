from kelso.jobs.job import Job, app_target, logger
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle import start
from kelso.lib.receipt import published_urls


class StartJob(Job):
  name = "start"
  description = "Start an app, loading it first if needed"
  required_args = ("app",)
  optional_args = ("force",)

  def init(self, ctx: KelsoCtx, kwargs: dict[str, str]) -> None:
    self.target = app_target(ctx, kwargs["app"], force=self._bool_arg(kwargs, "force"))
    self.app = str(self.target.app_id)
    self.app_id = self.target.app_id

  def run(self, ctx: KelsoCtx) -> None:
    app = self.app_id
    result = start(app, ctx, self.target.bundle)
    lines = [f"Started {app}"]
    lines += [f"  {url}" for url in published_urls(result.spec, result.run_data, ctx)]
    logger.info("\n".join(lines))
