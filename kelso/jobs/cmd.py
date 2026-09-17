import shlex

from kelso.jobs.job import Job, logger
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle import run_command
from kelso.lib.spec import AppSpec


class CmdJob(Job):
  name = "cmd"
  description = "Run a command declared in an app's manifest"
  required_args = ("app", "command")
  optional_args = ("args",)

  def init(self, ctx: KelsoCtx, kwargs: dict[str, str]) -> None:
    """`command` names an entry the app's manifest already declares.

    A caller holding an argv list must build `args` with `shlex.join`.
    """
    app = ctx.resolve_app(kwargs["app"])
    extra = []
    raw = kwargs.get("args", "")
    if raw.strip():
      try:
        extra = shlex.split(raw)
      except ValueError as e:
        raise ValueError(f"Could not parse arguments: {e}") from e

    if not ctx.is_staged(app):
      raise ValueError(f"App {app} is not installed; run `kelso install {app}` first")
    spec = AppSpec.from_file(ctx.staged_paths(app).manifest_path, app)
    if kwargs["command"] not in spec.commands:
      available = ", ".join(sorted(spec.commands)) or "(none)"
      raise ValueError(
        f"Unknown command {kwargs['command']!r} for {app}; "
        f"available: {available}. List with `kelso cmd {app}`"
      )

    self.app = str(app)
    self.app_id = app
    self.command = kwargs["command"]
    self.extra = extra

  def run(self, ctx: KelsoCtx) -> None:
    app = self.app_id
    with ctx.app_lock(app, f"cmd {app}"):
      code = run_command(app, self.command, self.extra, ctx)
      if code != 0:
        raise ValueError(
          f"Command {self.command!r} for {app} exited with status {code}"
        )
    logger.info("Ran command %r for %s", self.command, app)
