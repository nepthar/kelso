"""kelsod's end of a console: a PTY between a websocket and a shell, either
`docker compose exec` into an app's unit or a login shell on the host.

In, binary frames are keystrokes and text frames are JSON control, only
`{"resize": [cols, rows]}` so far. Out is binary terminal output. The close
code says how it ended: EXITED with reason "exited N", IDLE, or REFUSED with
the refusal also written to the terminal, since browsers drop long reasons.
"""

import asyncio
import fcntl
import ipaddress
import json
import logging
import os
import signal
import struct
import subprocess
import sys
import termios
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from starlette.websockets import WebSocket

from kelso.lib.docker import DOCKER, docker_run_command
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle.console import (
  PID_MARKER,
  ConsoleCommand,
  ConsoleRecord,
  hangup_args,
  host_shell,
)

logger = logging.getLogger("kelsod.console")

IDLE_SECONDS = 15 * 60

EXITED = 1000
IDLE = 4000
REFUSED = 4001

# Run by the new session leader: take the PTY on stdin as its controlling
# terminal, then become the real program, so job control, window-size signals
# and hangup work as in any terminal. preexec_fn would do it, but is not safe in
# a threaded process.
_CTTY = """\
import fcntl, os, sys, termios
fcntl.ioctl(0, termios.TIOCSCTTY, 0)
try:
    os.execvp(sys.argv[1], sys.argv[1:])
except OSError as e:
    sys.exit(f"{sys.argv[1]}: {e.strerror}")
"""


def shell_argv(cmd: ConsoleCommand) -> list[str]:
  return [DOCKER, *cmd.docker_args]


def hang_up(cmd: ConsoleCommand, pid: int) -> None:
  """End the shell an abandoned session left running in its container."""
  code = docker_run_command(
    hangup_args(cmd, pid), cwd=cmd.cwd, json_output=False, check=False, env=cmd.env
  ).returncode
  if code:
    logger.warning("could not hang up shell %d in %s (%d)", pid, cmd.app_id, code)


def network_refusal(ws: WebSocket) -> str | None:
  """Why a console may not open on this connection, or None.

  Only the admin socket and loopback stand in front of an API with no
  authentication, and a console is a shell. So one that arrived over the
  network is refused, whatever `kelsod --host` was told.
  """
  server = ws.scope.get("server")
  if server and server[1] is None:
    return None  # The admin socket: uvicorn gives its path and no port.
  try:
    if server and ipaddress.ip_address(server[0]).is_loopback:
      return None
  except ValueError:
    pass
  return "Consoles open only over kelsod's admin socket or loopback."


async def refuse(ws: WebSocket, message: str) -> None:
  await ws.accept()
  await ws.send_bytes(message.replace("\n", "\r\n").encode() + b"\r\n")
  # A close reason is capped at 123 bytes.
  await ws.close(REFUSED, message.encode()[:120].decode(errors="ignore"))


async def serve(ws: WebSocket, cmd: ConsoleCommand, ctx: KelsoCtx) -> None:
  """A shell in an app's unit, filed in the activity log."""
  args = {"unit": cmd.unit, "via": "web"}
  record = await asyncio.to_thread(ConsoleRecord, ctx, cmd.app_id, args)
  await _session(
    ws,
    record,
    shell_argv(cmd),
    cwd=cmd.cwd,
    env=cmd.env,
    hangup=lambda pid: hang_up(cmd, pid),
  )


async def serve_host(ws: WebSocket, ctx: KelsoCtx) -> None:
  """A login shell on the host as kelsod's own user, filed in the activity log."""
  argv, home = host_shell()
  args = {"shell": argv[0], "via": "web"}
  record = await asyncio.to_thread(ConsoleRecord, ctx, None, args)
  # Nothing else sets TERM here: docker does it for a container's shell.
  env = {"TERM": "xterm-256color"}
  await _session(ws, record, argv, cwd=home, env=env, hangup=None)


async def _session(
  ws: WebSocket,
  record: ConsoleRecord,
  argv: list[str],
  *,
  cwd: Path,
  env: dict[str, str],
  hangup: Callable[[int], None] | None,
) -> None:
  ending = _Ending()
  try:
    await ws.accept()
    await _relay(ws, argv, cwd=cwd, env=env, hangup=hangup, ending=ending)
  finally:
    await asyncio.shield(asyncio.to_thread(record.close, ending.text, ok=ending.ok))


@dataclass
class _Ending:
  """How a session ended. Set before closing: a cancelled handler loses returns."""

  text: str = "disconnected"
  ok: bool = True


def _set_size(fd: int, cols: int, rows: int) -> None:
  fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def _end(
  proc: subprocess.Popen,
  master: int,
  pid: int | None,
  hangup: Callable[[int], None] | None,
) -> None:
  """Hang up the terminal, reap what ran on it, and end the container side too."""
  running = proc.poll() is None
  # Closing our end is the terminal hanging up: the kernel sends the session
  # SIGHUP. It goes first because a shell exiting from a terminal waits for its
  # output to drain, which nothing would read.
  os.close(master)
  if not running:
    return
  try:
    proc.wait(5)
  except subprocess.TimeoutExpired:
    os.killpg(proc.pid, signal.SIGKILL)
    proc.wait()
  if pid is not None and hangup is not None:
    hangup(pid)


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
  hangup: Callable[[int], None] | None,
  ending: _Ending,
) -> None:
  """Relay `ws` to `argv` on a fresh PTY until one side ends, noting how in `ending`.

  `hangup` gets the pid `PID_MARKER` announced if the session is abandoned.
  """
  loop = asyncio.get_running_loop()
  master, slave = os.openpty()
  _set_size(master, 80, 24)
  proc = subprocess.Popen(
    [sys.executable, "-c", _CTTY, *argv],
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
          # The kernel tells the terminal's foreground job, as for any terminal.
          _set_size(master, int(size[0]), int(size[1]))

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
      ending.text, ending.ok = f"exited {code}", code == 0
      await ws.close(EXITED, ending.text)
    elif idle_task in done:
      minutes = round(IDLE_SECONDS / 60)
      ending.text = f"closed after {minutes} minutes idle"
      await ws.send_bytes(f"\r\n[closed after {minutes} minutes idle]\r\n".encode())
      await ws.close(IDLE, "idle")
  finally:
    for task in tasks:
      task.cancel()
    # One blocking call, shielded: a cancelled handler (kelsod stopping) re-raises
    # at every await, and must still not leave a shell running.
    await asyncio.shield(asyncio.to_thread(_end, proc, master, pid, hangup))
