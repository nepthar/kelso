from kelso.jobs.job import Job
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle import updown
from kelso.lib.options import validate_option


def _group(kwargs: dict[str, str], default: int) -> int:
  raw = kwargs.get("group", "")
  if not raw:
    return default
  validate_option("start_group", raw)
  return int(raw)


class UpJob(Job):
  name = "up"
  description = "Start apps by start_group, 1 to N (default 9); stops nothing"
  optional_args = ("group", "timeout", "resume")

  def init(self, ctx: KelsoCtx, kwargs: dict[str, str]) -> None:
    self.group = _group(kwargs, 9)
    raw = kwargs.get("timeout", "")
    try:
      self.wait = float(raw) if raw else updown.DEFAULT_WAIT
    except ValueError:
      raise ValueError(f"timeout {raw!r} is not a number of seconds") from None
    self.resume = self._bool_arg(kwargs, "resume")

  def run(self, ctx: KelsoCtx) -> None:
    problems = updown.up(ctx, self.group, wait=self.wait, resume=self.resume)
    if problems:
      raise RuntimeError("; ".join(problems))


class DownJob(Job):
  name = "down"
  description = "Stop apps by start_group, 9 down to N+1 (default 0); starts nothing"
  optional_args = ("group",)

  def init(self, ctx: KelsoCtx, kwargs: dict[str, str]) -> None:
    self.group = _group(kwargs, 0)

  def run(self, ctx: KelsoCtx) -> None:
    problems = updown.down(ctx, self.group)
    if problems:
      raise RuntimeError("; ".join(problems))
