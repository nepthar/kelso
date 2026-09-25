"""kelsod's end of a console: a PTY between a websocket and `docker compose exec`.

In, binary frames are keystrokes and text frames are JSON control, only
`{"resize": [cols, rows]}` so far. Out is binary terminal output. The close
code says how it ended: EXITED with reason "exited N", IDLE, or REFUSED with
the refusal also written to the terminal, since browsers drop long reasons.
"""

import asyncio
import fcntl
import json
import logging
import os
import signal
import struct
import subprocess
import termios
import time
from collections.abc import Callable
from pathlib import Path

from starlette.websockets import WebSocket

from kelso.lib.docker import DOCKER, docker_run_command
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle.console import (
  PID_MARKER,
  ConsoleCommand,
  ConsoleRecord,
  hangup_args,
)

logger = logging.getLogger("kelsod.console")

IDLE_SECONDS = 15 * 60
MAX_SESSIONS = 8

EXITED = 1000
IDLE = 4000
REFUSED = 4001

_sessions = 0


def shell_argv(cmd: ConsoleCommand) -> list[str]:
  return [DOCKER, *cmd.docker_args]


def hang_up(cmd: ConsoleCommand, pid: int) -> None:
  """End the shell an abandoned session left running in its container."""
  code = docker_run_command(
    hangup_args(cmd, pid), cwd=cmd.cwd, json_output=False, check=False, env=cmd.env
  ).returncode
  if code:
    logger.warning("could not hang up shell %d in %s (%d)", pid, cmd.app_id, code)


async def refuse(ws: WebSocket, message: str) -> None:
  await ws.accept()
  await ws.send_bytes(message.replace("\n", "\r\n").encode() + b"\r\n")
  # A close reason is capped at 123 bytes.
  await ws.close(REFUSED, message.encode()[:120].decode(errors="ignore"))


async def serve(ws: WebSocket, cmd: ConsoleCommand, ctx: KelsoCtx) -> None:
  """Run one session for `cmd` on `ws`, filed in the activity log."""
  global _sessions
  if _sessions >= MAX_SESSIONS:
    await refuse(ws, f"{MAX_SESSIONS} consoles are already open; close one first")
    return
  _sessions += 1
  try:
    record = await asyncio.to_thread(ConsoleRecord, ctx, cmd, "web")
    ending, ok = "disconnected", True
    try:
      await ws.accept()
      ending, ok = await _relay(
        ws,
        shell_argv(cmd),
        cwd=cmd.cwd,
        env=cmd.env,
        hangup=lambda pid: hang_up(cmd, pid),
      )
    finally:
      await asyncio.shield(asyncio.to_thread(record.close, ending, ok=ok))
  finally:
    _sessions -= 1


def _set_size(fd: int, cols: int, rows: int) -> None:
  fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def _end(
  proc: subprocess.Popen, master: int, pid: int | None, hangup: Callable[[int], None]
) -> None:
  """Kill what still runs, here and in the container, then close the PTY."""
  if proc.poll() is None:
    os.killpg(proc.pid, signal.SIGHUP)
    try:
      proc.wait(5)
    except subprocess.TimeoutExpired:
      os.killpg(proc.pid, signal.SIGKILL)
      proc.wait()
    if pid is not None:
      hangup(pid)
  os.close(master)


async def _ready(add, remove, fd: int) -> None:
  loop = asyncio.get_running_loop()
  ready = loop.create_future()
  add(fd, lambda: ready.done() or ready.set_result(None))
  try:
    await ready
  finally:
    remove(fd)


async def _relay(
  ws: WebSocket,
  argv: list[str],
  *,
  cwd: Path,
  env: dict[str, str],
  hangup: Callable[[int], None],
) -> tuple[str, bool]:
  """Relay `ws` to `argv` on a fresh PTY until one side ends; how it ended, and if ok.

  `hangup` gets the pid `PID_MARKER` announced if the session is abandoned.
  """
  loop = asyncio.get_running_loop()
  master, slave = os.openpty()
  _set_size(master, 80, 24)
  # No controlling terminal: a resize must signal the group itself, and closing
  # the session must kill it.
  proc = subprocess.Popen(
    argv,
    cwd=cwd,
    env={**os.environ, **env},
    stdin=slave,
    stdout=slave,
    stderr=slave,
    start_new_session=True,
  )
  os.close(slave)
  os.set_blocking(master, False)
  last = time.monotonic()
  pid = None

  async def read() -> bytes:
    while True:
      try:
        return os.read(master, 65536)
      except BlockingIOError:
        await _ready(loop.add_reader, loop.remove_reader, master)
      except OSError:
        # EIO on Linux once every copy of the child's side is closed.
        return b""

  async def write(data: bytes) -> None:
    while data:
      try:
        data = data[os.write(master, data) :]
      except BlockingIOError:
        await _ready(loop.add_writer, loop.remove_writer, master)

  async def output() -> None:
    nonlocal last, pid
    while data := await read():
      last = time.monotonic()
      if pid is None and (found := PID_MARKER.search(data)):
        pid = int(found.group(1))
        data = data[: found.start()] + data[found.end() :]
      if data:
        await ws.send_bytes(data)

  async def keys() -> None:
    nonlocal last
    while True:
      message = await ws.receive()
      if message["type"] == "websocket.disconnect":
        return
      last = time.monotonic()
      if message.get("bytes"):
        await write(message["bytes"])
      elif message.get("text"):
        size = json.loads(message["text"]).get("resize")
        if size:
          _set_size(master, int(size[0]), int(size[1]))
          # The group: `docker compose` is a plugin the docker CLI runs as a child.
          os.killpg(proc.pid, signal.SIGWINCH)

  async def idle() -> None:
    while (left := last + IDLE_SECONDS - time.monotonic()) > 0:
      await asyncio.sleep(left)

  out = asyncio.create_task(output())
  tasks = {out, asyncio.create_task(keys()), idle_task := asyncio.create_task(idle())}
  try:
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
      task.cancel()
    # Before the fd closes, so no cancelled wait unregisters a reused number.
    await asyncio.gather(*tasks, return_exceptions=True)
    if out in done and out.exception() is None:
      code = await asyncio.to_thread(proc.wait)
      await ws.close(EXITED, f"exited {code}")
      return f"exited {code}", code == 0
    if idle_task in done:
      minutes = round(IDLE_SECONDS / 60)
      await ws.send_bytes(f"\r\n[closed after {minutes} minutes idle]\r\n".encode())
      await ws.close(IDLE, "idle")
      return f"closed after {minutes} minutes idle", True
    return "disconnected", True
  finally:
    for task in tasks:
      task.cancel()
    # One blocking call, shielded: a cancelled handler (kelsod stopping) re-raises
    # at every await, and must still not leave a shell running.
    await asyncio.shield(asyncio.to_thread(_end, proc, master, pid, hangup))
