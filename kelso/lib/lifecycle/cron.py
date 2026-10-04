"""Cron: running each app's `[cron]` commands on schedule, one at a time.

A job is due once a scheduled time has passed since it last ran or, never
having run, since its app was loaded; a missed stretch runs once, not once per
miss. A tick runs the due jobs of running apps in turn and skips the rest,
which stay due. A tick that finds another still running does nothing, so cron
is best effort.
"""

import shlex
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from filelock import FileLock, Timeout

from kelso.lib.activity import Activity
from kelso.lib.apps import AppID
from kelso.lib.cronexpr import CronSchedule
from kelso.lib.kelso import KelsoCtx, write_lock_holder
from kelso.lib.lifecycle._common import logger
from kelso.lib.lifecycle.run import run_command

SCHEDULED = "scheduled"
SKIP = "skip"


@dataclass(frozen=True)
class CronRun:
  """One cron job's next run."""

  app_id: AppID
  name: str
  command: str
  args: str
  timeout: int
  schedule: str
  # Aware, in the host's local zone.
  next_at: datetime
  # Whether the unit its command runs in is up; a tick skips it otherwise.
  runnable: bool

  @property
  def status(self) -> str:
    return SCHEDULED if self.runnable else SKIP


def cron_runs(ctx: KelsoCtx) -> list[CronRun]:
  """Every loaded app's cron jobs, soonest first."""
  last_ran = {
    key.removeprefix("apps/"): entry.value
    for key, entry in ctx.activity_log.scan("apps/").items()
    if "/cron/" in key
  }
  now = datetime.now(UTC)
  runs = []
  for observation in ctx.observations():
    spec = ctx.loaded_spec(observation.app_id) if observation.loaded else None
    if spec is None or not spec.manifest.cron:
      continue
    app = observation.app_id
    up = {c.run_unit for c in observation.containers if c.state.lower() == "running"}
    loaded_at = ctx.app_store(app).get_meta("loaded_at")
    for name, entry in spec.manifest.cron.items():
      since = last_ran.get(f"{app}/cron/{name}") or loaded_at
      runs.append(
        CronRun(
          app_id=app,
          name=name,
          command=entry.command,
          args=entry.args,
          timeout=entry.timeout or spec.commands[entry.command].timeout,
          schedule=entry.schedule,
          next_at=_next(entry.schedule, _instant(since) if since else now),
          runnable=spec.commands[entry.command].run_unit in up,
        )
      )
  return sorted(runs, key=lambda run: (run.next_at, run.app_id, run.name))


def tick(
  ctx: KelsoCtx, *, by: str, started_by: str, echo: TextIO | None = None
) -> list[CronRun] | None:
  """Run every due job of a running app, in turn. None if a tick is already running."""
  path = cron_lock_path(ctx)
  lock = FileLock(path)
  try:
    lock.acquire(timeout=0)
  except Timeout:
    return None
  try:
    write_lock_holder(path, by)
    now = datetime.now(UTC)
    ran = []
    for run in cron_runs(ctx):
      if run.next_at > now or not run.runnable:
        continue
      try:
        _run(run, ctx, echo, started_by)
      except Exception as e:  # noqa: BLE001 - one failed job must not end the tick
        logger.warning("cron %s of %s failed: %s", run.name, run.app_id, e)
      ran.append(run)
    return ran
  finally:
    lock.release()


def cron_lock_path(ctx: KelsoCtx) -> Path:
  return ctx.config.lock_root / "cron.lock"


def _run(run: CronRun, ctx: KelsoCtx, echo: TextIO | None, started_by: str) -> None:
  by = f"cron {run.app_id} {run.name}"
  with ctx.app_lock(run.app_id, by):
    # Recorded before it runs, so a job that crashes waits for its next time
    # rather than running again every tick.
    with ctx.kelso_lock(by):
      ctx.activity_log.write(
        f"apps/{run.app_id}/cron/{run.name}",
        datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
      )
    args = {"job": run.name, "command": run.command}
    with Activity(
      ctx, "cron", app=run.app_id, args=args, echo=echo, started_by=started_by
    ):
      code = run_command(
        run.app_id, run.command, shlex.split(run.args), ctx, timeout=run.timeout
      )
      if code != 0:
        raise RuntimeError(f"{run.command!r} exited with status {code}")


def _instant(text: str) -> datetime:
  return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _next(schedule: str, since: datetime) -> datetime:
  """The schedule's next time after `since`, read on the host's local clock."""
  local = since.astimezone().replace(tzinfo=None)
  return CronSchedule.parse(schedule).next_after(local).astimezone()
