from kelso.jobs.job import Job
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle.updown import DEFAULT_WAIT, to_group


class StartGroupJob(Job):
  name = "start_group"
  description = "Run every loaded app in start_group 1 to N; stop those above N"
  required_args = ("group",)
  optional_args = ("timeout", "resume")

  def init(self, ctx: KelsoCtx, kwargs: dict[str, str]) -> None:
    raw = kwargs["group"]
    if raw not in tuple(str(n) for n in range(10)):
      raise ValueError(f"group {raw!r} is not a whole number from 0 to 9")
    self.group = int(raw)
    raw = kwargs.get("timeout", "")
    try:
      self.wait = float(raw) if raw else DEFAULT_WAIT
    except ValueError:
      raise ValueError(f"timeout {raw!r} is not a number of seconds") from None
    self.resume = self._bool_arg(kwargs, "resume")

  def run(self, ctx: KelsoCtx) -> None:
    problems = to_group(ctx, self.group, wait=self.wait, resume=self.resume)
    if problems:
      raise RuntimeError("; ".join(problems))
