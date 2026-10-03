"""A shell in one of an app's running containers.

`console_command` resolves what to run and refuses what cannot be; `kelso
shell` hands it the operator's own terminal and kelsod runs it on a PTY.
`ConsoleRecord` puts each session in the activity log -- that it happened, not
what was typed. A unit with commands opens with them listed, and with
/kelso/bin on PATH so `kelso_cmd` runs them.

docker has no way to end an exec: killing the client leaves its shell running
in the container. So a session kelsod abandons announces its shell's pid
(`PID_MARKER`, an escape sequence no terminal draws), and `hangup_args` kills
it from inside.
"""

import os
import pwd
import re
import shlex
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from kelso.lib.activity import ERROR, OK, begin_run, finish_run
from kelso.lib.apps import AppID
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle.run import compose_env
from kelso.lib.run_layout import KELSO_BIN, unit_commands
from kelso.lib.spec import AppSpec

PID_MARKER = re.compile(rb"\x1b\]697;kelso-pid=(\d+)\x07")


@dataclass(frozen=True)
class ConsoleCommand:
  app_id: AppID
  unit: str
  unit_shell: tuple[str, ...]
  docker_args: list[str]
  cwd: Path
  env: dict[str, str]


def console_command(
  app: str,
  unit: str,
  ctx: KelsoCtx,
  *,
  announce_pid: bool = False,
) -> ConsoleCommand:
  """The `docker compose exec` that opens a shell in `unit`. Raises if it isn't running."""
  app_id = ctx.resolve_app(app)
  state = ctx.run_state(app_id)
  if not state.compose_exists:
    raise ValueError(f"App {app_id} is not loaded; run `kelso load {app_id}` first")
  running = sorted(c.run_unit for c in state.containers if c.state.lower() == "running")
  if unit not in running:
    if running:
      raise ValueError(
        f"{unit} is not running in {app_id}; running units: {', '.join(running)}"
      )
    raise ValueError(f"{app_id} is not running; run `kelso start {app_id}` first")
  spec = AppSpec.from_file(ctx.loaded_paths(app_id).manifest_path, app_id)
  unit_shell = spec.run_units[unit].shell
  script = f"exec {shlex.quote(unit_shell[0])} -i"
  if unit_commands(spec, unit):
    script = f'export PATH="$PATH:{KELSO_BIN}"; kelso_cmd; {script}'
  if announce_pid:
    script = "printf '\\033]697;kelso-pid=%s\\007' $$; " + script
  return ConsoleCommand(
    app_id=app_id,
    unit=unit,
    unit_shell=unit_shell,
    docker_args=["compose", "exec", unit, *unit_shell, script],
    cwd=state.run_path,
    env=compose_env(app_id, ctx),
  )


def hangup_args(cmd: ConsoleCommand, pid: int) -> list[str]:
  """Docker args that hang up the shell `PID_MARKER` named, and its foreground job."""
  return ["compose", "exec", "-T", cmd.unit, *cmd.unit_shell, f"kill -HUP {pid}"]


def host_shell() -> tuple[list[str], Path]:
  """A login shell for the user kelso runs as, and the home it starts in."""
  user = pwd.getpwuid(os.getuid())
  return [user.pw_shell or "/bin/sh", "-l"], Path(user.pw_dir)


class ConsoleRecord:
  """One session in the activity log, filed when it opens and closed by `close`."""

  def __init__(
    self,
    ctx: KelsoCtx,
    app_id: AppID | None,
    args: dict[str, str],
    *,
    started_by: str,
  ):
    self.ctx = ctx
    self.app_id = app_id
    self.started_by = started_by
    self.started = datetime.now(UTC)
    self.log = begin_run(ctx, "console", args, app_id=app_id, started=self.started)

  def close(self, ending: str, *, ok: bool = True) -> None:
    with open(self.ctx.config.activity_root / self.log, "a", encoding="utf-8") as f:
      f.write(f"Session {ending}\n")
    finish_run(
      self.ctx,
      self.log,
      "console",
      app_id=self.app_id,
      status=OK if ok else ERROR,
      started=self.started,
      finished=datetime.now(UTC),
      started_by=self.started_by,
    )
