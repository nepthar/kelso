from kelso.jobs.job import Job
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle.updown import DEFAULT_WAIT, down, up


class UpJob(Job):
  name = "up"
  description = "Start every loaded app in start_order groups 1 to 9"
  optional_args = ("timeout", "resume")

  def init(self, ctx: KelsoCtx, kwargs: dict[str, str]) -> None:
    raw = kwargs.get("timeout", "")
    try:
      self.wait = float(raw) if raw else DEFAULT_WAIT
    except ValueError:
      raise ValueError(f"timeout {raw!r} is not a number of seconds") from None
    self.resume = self._bool_arg(kwargs, "resume")

  def run(self, ctx: KelsoCtx) -> None:
    _finish(up(ctx, wait=self.wait, resume=self.resume))


class DownJob(Job):
  name = "down"
  description = "Stop every running app in start_order groups 9 down to 1"

  def run(self, ctx: KelsoCtx) -> None:
    _finish(down(ctx))


def _finish(problems: list[str]) -> None:
  if problems:
    raise RuntimeError("; ".join(problems))
