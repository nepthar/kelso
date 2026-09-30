from kelso.jobs.job import Job, logger
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle.updown import DEFAULT_WAIT, down, up


class UpJob(Job):
  name = "up"
  description = "Start every installed app, one start_order group at a time"
  optional_args = ("timeout",)

  def init(self, ctx: KelsoCtx, kwargs: dict[str, str]) -> None:
    raw = kwargs.get("timeout", "")
    try:
      self.wait = float(raw) if raw else DEFAULT_WAIT
    except ValueError:
      raise ValueError(f"timeout {raw!r} is not a number of seconds") from None

  def run(self, ctx: KelsoCtx) -> None:
    _finish(up(ctx, logger.info, wait=self.wait))


class DownJob(Job):
  name = "down"
  description = "Stop every running app, in reverse start_order"

  def run(self, ctx: KelsoCtx) -> None:
    _finish(down(ctx, logger.info))


def _finish(problems: list[str]) -> None:
  if problems:
    raise RuntimeError("; ".join(problems))
