import shlex

from kelso.jobs.job import Job, logger
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle import run_command
from kelso.lib.lifecycle.run import require_command


class CmdJob(Job):
  name = "cmd"
  description = "Run a command declared in an app's manifest"
  required_args = ("app", "command")
  optional_args = ("args",)

  def init(self, ctx: KelsoCtx, kwargs: dict[str, str]) -> None:
    app = ctx.resolve_app_id(kwargs["app"])
    extra = []
    raw = kwargs.get("args", "")
    if raw.strip():
      try:
        extra = shlex.split(raw)
      except ValueError as e:
        raise ValueError(f"Could not parse arguments: {e}") from e

    require_command(app, kwargs["command"], ctx)

    self.app = str(app)
    self.app_id = app
    self.command = kwargs["command"]
    self.extra = extra

  def run(self, ctx: KelsoCtx) -> None:
    app = self.app_id
    code = run_command(app, self.command, self.extra, ctx)
    if code != 0:
      raise ValueError(f"Command {self.command!r} for {app} exited with status {code}")
    logger.info("Ran command %r for %s", self.command, app)
