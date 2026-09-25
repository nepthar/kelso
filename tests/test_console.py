"""Consoles: `kelso shell`, and kelsod's PTY behind a websocket.

kelsod's tests swap `docker compose exec` for a local `sh`, so the PTY, the
relay and the process handling are real and only the container is not.
"""

import json
import os
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from kelso.daemon import console
from kelso.daemon.api import create_app
from kelso.jobs import JobRunner
from kelso.lib.config import load_config
from kelso.lib.kelso import KelsoCtx

APP = "io.p2net.basic-features"
URL = f"/apps/{APP}/console"


def ctx() -> KelsoCtx:
  config = load_config()
  assert config is not None
  return KelsoCtx(config)


@pytest.fixture
def client() -> TestClient:
  return TestClient(create_app(ctx, JobRunner(ctx)))


@pytest.fixture
def running(kelso_env):
  kelso_env.run("start", "basic-features", "--set", "admin_user=root")


def shell(monkeypatch, script: str) -> None:
  monkeypatch.setattr(console, "shell_argv", lambda cmd: ["/bin/sh", "-c", script])


def drain(ws) -> tuple[bytes, WebSocketDisconnect]:
  """Everything the terminal printed, and how the session closed."""
  out = b""
  with pytest.raises(WebSocketDisconnect) as closed:
    while True:
      out += ws.receive_bytes()
  return out, closed.value


def test_shell_execs_into_the_running_unit(kelso_env, running, client):
  result = kelso_env.run("shell", "basic-features")
  assert result.returncode == 0, result.stderr

  log = kelso_env.docker_log.read_text().splitlines()
  assert json.loads(log[-1])["args"][:5] == ["compose", "exec", "main", "/bin/sh", "-c"]

  run = client.get("/activity").json()["activity"][0]
  assert (run["verb"], run["app_id"], run["status"]) == ("console", APP, "ok")
  body = client.get(f"/activity/{run['log']}").json()["text"]
  assert "via" in body and "cli" in body


def test_shell_refuses_a_stopped_app(kelso_env):
  kelso_env.run("install", "basic-features")
  result = kelso_env.run("shell", "basic-features")
  assert result.returncode == 1
  assert f"run `kelso start {APP}` first" in result.stderr


def test_console_relays_a_pty_and_reports_the_exit(
  kelso_env, running, client, monkeypatch
):
  shell(monkeypatch, 'read line; stty size; echo "got:$line"; exit 3')
  with client.websocket_connect(URL) as ws:
    ws.send_text(json.dumps({"resize": [100, 30]}))
    ws.send_bytes(b"hello\n")
    out, closed = drain(ws)

  text = out.decode()
  assert "30 100" in text
  assert "got:hello" in text
  assert (closed.code, closed.reason) == (console.EXITED, "exited 3")

  run = client.get("/activity").json()["activity"][0]
  assert (run["verb"], run["app_id"], run["status"]) == ("console", APP, "error")
  body = client.get(f"/activity/{run['log']}").json()["text"]
  assert "Session exited 3" in body
  assert "got:hello" not in body
  assert hangups(kelso_env) == []


def test_a_resize_reaches_the_whole_process_group(running, client, monkeypatch):
  # The size watcher is a grandchild, as the compose plugin is under `docker`;
  # the trailing `true` stops sh from exec'ing it in its own place. Idle ends a
  # session whose watcher never hears. macOS delivers SIGWINCH to sh's group
  # by itself, so only Linux can fail this.
  monkeypatch.setattr(console, "IDLE_SECONDS", 2)
  shell(
    monkeypatch,
    "sh -c 'trap \"stty size; exit\" WINCH; echo ready; while :; do sleep 0.05; done'"
    "; true",
  )
  with client.websocket_connect(URL) as ws:
    out = b""
    while b"ready" not in out:
      out += ws.receive_bytes()
    ws.send_text(json.dumps({"resize": [120, 40]}))
    out, _ = drain(ws)
  assert b"40 120" in out


def test_console_refuses_a_unit_that_is_not_running(running, client):
  with client.websocket_connect(f"{URL}?unit=worker") as ws:
    out, closed = drain(ws)
  assert closed.code == console.REFUSED
  assert b"worker is not running" in out
  assert b"running units: main" in out


def test_console_refuses_past_the_session_cap(running, client, monkeypatch):
  monkeypatch.setattr(console, "MAX_SESSIONS", 0)
  with client.websocket_connect(URL) as ws:
    out, closed = drain(ws)
  assert closed.code == console.REFUSED
  assert b"already open" in out


def test_console_closes_an_idle_session(running, client, monkeypatch):
  monkeypatch.setattr(console, "IDLE_SECONDS", 0.2)
  shell(monkeypatch, "exec sleep 30")
  with client.websocket_connect(URL) as ws:
    out, closed = drain(ws)
  assert closed.code == console.IDLE
  assert b"idle" in out


def hangups(kelso_env) -> list[list[str]]:
  calls = [
    json.loads(line)["args"] for line in kelso_env.docker_log.read_text().splitlines()
  ]
  return [args for args in calls if args[-1].startswith("kill -HUP")]


def test_leaving_hangs_up_the_shell_here_and_in_the_container(
  kelso_env, running, client, monkeypatch
):
  shell(
    monkeypatch, "printf '\\033]697;kelso-pid=%s\\007' $$; echo ready; exec sleep 30"
  )
  with client.websocket_connect(URL) as ws:
    out = b""
    while b"ready" not in out:
      out += ws.receive_bytes()
  assert b"kelso-pid" not in out

  deadline = time.monotonic() + 5
  while not hangups(kelso_env):
    assert time.monotonic() < deadline, "the container's shell was never hung up"
    time.sleep(0.05)
  [args] = hangups(kelso_env)
  assert args[:4] == ["compose", "exec", "-T", "main"]
  pid = int(args[-1].removeprefix("kill -HUP "))
  with pytest.raises(ProcessLookupError):
    os.kill(pid, 0)
