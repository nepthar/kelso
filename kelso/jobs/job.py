from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any, TextIO

from kelso.lib.activity import Activity
from kelso.lib.bundle import is_pathlike
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle.load import LoadTarget, load_target

logger = logging.getLogger("kelso.jobs")

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"


def _now() -> str:
  return datetime.now(UTC).isoformat(timespec="seconds")


def app_target(ctx: KelsoCtx, raw: str, *, force: bool = False) -> LoadTarget:
  # A path defines what an app is, which is root: verbs never take one.
  if is_pathlike(raw):
    raise ValueError(f'No app matching "{raw}" found: verbs name apps, not paths')
  return load_target(ctx, raw, force=force)


class Job:
  name: str
  description: str
  required_args: tuple[str, ...] = ()
  optional_args: tuple[str, ...] = ()
  record_activity: bool = True
  app: str | None = None

  def __init__(self, started_by: str) -> None:
    self.id: str = uuid.uuid4().hex[:12]
    self.started_by = started_by
    self.args: dict[str, str] = {}
    self.state: str = QUEUED
    self.error: str | None = None
    self.created_at: str = _now()
    self.started_at: str | None = None
    self.finished_at: str | None = None
    self.log: str | None = None
    self._activity: Activity | None = None

  def as_dict(self) -> dict[str, Any]:
    return {
      "id": self.id,
      "verb": self.name,
      "args": dict(self.args),
      "state": self.state,
      "error": self.error,
      "created_at": self.created_at,
      "started_at": self.started_at,
      "finished_at": self.finished_at,
      "log": self.log,
      "started_by": self.started_by,
    }

  @classmethod
  def prepare(cls, args: dict[str, str], ctx: KelsoCtx, *, started_by: str) -> Job:
    job = cls(started_by)
    job.args = dict(args)
    cls._check_args(args)
    job.init(ctx, args)
    return job

  @classmethod
  def call(
    cls,
    args: dict[str, str],
    ctx: KelsoCtx,
    *,
    started_by: str,
    echo: TextIO | None = None,
  ) -> Job:
    job = cls.prepare(args, ctx, started_by=started_by)
    job.execute(ctx, echo=echo)
    return job

  def execute(self, ctx: KelsoCtx, *, echo: TextIO | None = None) -> None:
    self.state = RUNNING
    self.started_at = _now()
    if not self.record_activity:
      self._execute_quiet(ctx)
      return
    activity = Activity(
      ctx,
      self.name,
      app=self.app,
      args=self.args,
      echo=echo,
      started_by=self.started_by,
    )
    ok = False
    try:
      with activity:
        self._activity = activity
        self.log = activity.log
        self.run(ctx)
      ok = True
    finally:
      self._activity = None
      self.log = activity.log
      self.error = activity.error
      self.state = DONE if ok else FAILED
      self.finished_at = _now()

  def _execute_quiet(self, ctx: KelsoCtx) -> None:
    ok = False
    try:
      self.run(ctx)
      ok = True
    except Exception as e:
      self.error = str(e)
      raise
    finally:
      self.state = DONE if ok else FAILED
      self.finished_at = _now()

  def init(self, ctx: KelsoCtx, kwargs: dict[str, str]) -> None:
    pass

  def run(self, ctx: KelsoCtx) -> None:
    raise NotImplementedError

  def subprocess(self, cmd: list[str], **kwargs: Any) -> Any:
    if self._activity is None:
      raise RuntimeError(
        f"{self.name} subprocess is only available while the job is running"
      )
    return self._activity.subprocess(cmd, **kwargs)

  @classmethod
  def _check_args(cls, args: dict[str, str]) -> None:
    allowed = set(cls.required_args) | set(cls.optional_args)
    for name in args:
      if name not in allowed:
        names = ", ".join(sorted(allowed)) if allowed else "(none)"
        raise ValueError(
          f"Verb {cls.name!r} takes no argument {name!r}; it accepts: {names}"
        )
    for name in cls.required_args:
      if not args.get(name):
        raise ValueError(f"Verb {cls.name!r} requires argument {name!r}")

  @staticmethod
  def _bool_arg(kwargs: dict[str, str], name: str) -> bool:
    return kwargs.get(name, "") in ("1", "true", "yes")
