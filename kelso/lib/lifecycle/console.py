"""A shell in one of an app's running containers.

`console_command` resolves what to run and refuses what cannot be; `kelso
shell` hands it the operator's own terminal and kelsod runs it on a PTY.
`ConsoleRecord` puts each session in the activity log -- that it happened, not
what was typed. The shell opens by listing the manifest's commands for its unit.

docker has no way to end an exec: killing the client leaves its shell running
in the container. So a session kelsod abandons announces its shell's pid
(`PID_MARKER`, an escape sequence no terminal draws), and `hangup_args` kills
it from inside.
"""

import re
import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from kelso.lib.activity import ERROR, OK, begin_run, finish_run
from kelso.lib.apps import AppID
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle.run import compose_env
from kelso.lib.spec import AppCommand, AppSpec

DEFAULT_SHELL = "/bin/sh"
PID_MARKER = re.compile(rb"\x1b\]697;kelso-pid=(\d+)\x07")


@dataclass(frozen=True)
class ConsoleCommand:
  app_id: AppID
  unit: str
  docker_args: list[str]
  cwd: Path
  env: dict[str, str]


def console_command(
  app: str,
  unit: str,
  ctx: KelsoCtx,
  *,
  shell: str = DEFAULT_SHELL,
  announce_pid: bool = False,
) -> ConsoleCommand:
  """The `docker compose exec` that opens a shell in `unit`. Raises if it isn't running."""
  app_id = ctx.resolve_app(app)
  state = ctx.run_state(app_id)
  if not state.compose_exists:
    raise ValueError(
      f"App {app_id} is not installed; run `kelso install {app_id}` first"
    )
  running = sorted(c.run_unit for c in state.containers if c.state.lower() == "running")
  if unit not in running:
    if running:
      raise ValueError(
        f"{unit} is not running in {app_id}; running units: {', '.join(running)}"
      )
    raise ValueError(f"{app_id} is not running; run `kelso start {app_id}` first")
  spec = AppSpec.from_file(ctx.staged_paths(app_id).manifest_path, app_id)
  script = shell_script(unit, spec.commands, shell)
  if announce_pid:
    script = "printf '\\033]697;kelso-pid=%s\\007' $$; " + script
  return ConsoleCommand(
    app_id=app_id,
    unit=unit,
    docker_args=["compose", "exec", unit, "/bin/sh", "-c", script],
    cwd=state.run_path,
    env=compose_env(app_id, ctx),
  )


def shell_script(
  unit: str, commands: Mapping[str, AppCommand], shell: str = DEFAULT_SHELL
) -> str:
  """What `sh -c` runs in `unit`: the commands that run there, listed, then `shell`."""
  start = f"exec {shlex.quote(shell)}"
  here = sorted((name, c) for name, c in commands.items() if c.run_unit == unit)
  if not here:
    return start
  width = max(len(name) for name, _ in here)
  lines = ["The manifest defines these commands:"]
  lines += [f"  {name.ljust(width)}  {c.line()}" for name, c in here]
  banner = " ".join(shlex.quote(line) for line in lines)
  return f"printf '%s\\n' {banner}; {start}"


def hangup_args(cmd: ConsoleCommand, pid: int) -> list[str]:
  """Docker args that hang up the shell `PID_MARKER` named, and its foreground job."""
  return ["compose", "exec", "-T", cmd.unit, "/bin/sh", "-c", f"kill -HUP {pid}"]


class ConsoleRecord:
  """One session in the activity log, filed when it opens and closed by `close`."""

  def __init__(self, ctx: KelsoCtx, cmd: ConsoleCommand, via: str) -> None:
    self.ctx = ctx
    self.app_id = cmd.app_id
    self.started = datetime.now(UTC)
    self.log = begin_run(
      ctx,
      "console",
      {"unit": cmd.unit, "via": via},
      app_id=cmd.app_id,
      started=self.started,
    )

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
    )
