"""Consoles: `kelso shell`, and kelsod's PTY behind a websocket.

kelsod's tests swap `docker compose exec` for a local `sh`, so the PTY, the
relay and the process handling are real and only the container is not.
"""

import json
import os
import pwd
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from kelso.daemon import console
from kelso.daemon.api import create_app
from kelso.jobs import JobRunner
from kelso.lib.config import load_config
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle.console import host_shell

APP = "io.p2net.basic-features"
# Absolute: TestClient opens a relative websocket URL on `testserver`, which is
# not loopback. This is kelso-ui arriving through Docker Desktop.
LOOPBACK = "ws://127.0.0.1"
URL = f"{LOOPBACK}/apps/{APP}/console?started_by=test"


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
  assert json.loads(log[-1])["args"] == [
    "compose",
    "exec",
    "main",
    "/bin/sh",
    "-c",
    "exec /bin/sh -i",
  ]

  run = client.get("/activity").json()["activity"][0]
  assert (run["verb"], run["app_id"], run["status"]) == ("console", APP, "ok")
  body = client.get(f"/activity/{run['log']}").json()["text"]
  assert run["started_by"] == "cli"
  assert "via" not in body


def test_a_unit_with_commands_opens_with_them_on_path(kelso_env):
  app = kelso_env.local_repo / "cmd-demo.klso"
  app.mkdir()
  (app / "manifest.toml").write_text(
    '[app]\nversion = "1"\n\n[run.main]\nimage = "alpine"\n\n'
    '[commands.ping]\ncmd = "echo pong"\n'
  )
  assert kelso_env.run("start", "cmd-demo").returncode == 0
  assert kelso_env.run("shell", "cmd-demo").returncode == 0
  log = kelso_env.docker_log.read_text().splitlines()
  script = json.loads(log[-1])["args"][-1]
  assert script == 'export PATH="$PATH:/kelso/bin"; kelso_cmd; exec /bin/sh -i'


def test_shell_refuses_a_stopped_app(kelso_env):
  kelso_env.run("load", "basic-features")
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
  with client.websocket_connect(f"{URL}&unit=worker") as ws:
    out, closed = drain(ws)
  assert closed.code == console.REFUSED
  assert b"worker is not running" in out
  assert b"running units: main" in out


def test_consoles_are_refused_over_the_network(client):
  for path in (f"/apps/{APP}/console", "/host/console"):
    with client.websocket_connect(f"ws://192.0.2.7{path}") as ws:
      out, closed = drain(ws)
    assert closed.code == console.REFUSED
    assert b"admin socket or loopback" in out


@pytest.mark.parametrize(
  ("query", "expected"),
  [("", b"started_by is required"), ("?started_by=a+b", b"started_by")],
)
def test_consoles_need_to_know_who_opened_them(client, query, expected):
  for path in (f"/apps/{APP}/console", "/host/console"):
    with client.websocket_connect(f"{LOOPBACK}{path}{query}") as ws:
      out, closed = drain(ws)
    assert closed.code == console.REFUSED
    assert expected in out


def test_consoles_open_on_the_admin_socket_and_loopback():
  def arriving_at(server):
    return SimpleNamespace(scope={"server": server})

  assert console.network_refusal(arriving_at(("/k/var/conn/admin.sock", None))) is None
  assert console.network_refusal(arriving_at(("127.0.0.1", 9000))) is None
  assert console.network_refusal(arriving_at(("192.0.2.7", 9000)))
  assert console.network_refusal(arriving_at(None))


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


def test_the_host_shell_is_a_login_shell_in_home():
  user = pwd.getpwuid(os.getuid())
  assert host_shell() == ([user.pw_shell, "-l"], Path(user.pw_dir))


def test_the_host_console_gets_a_real_terminal(
  kelso_env, client, monkeypatch, tmp_path
):
  # /dev/tty only opens for a process whose controlling terminal this is. macOS
  # hands a session leader its terminal by itself, so only Linux can fail this.
  script = 'echo x > /dev/tty && echo has-ctty; echo "TERM=$TERM"; pwd -P'
  monkeypatch.setattr(
    console, "host_shell", lambda: (["/bin/sh", "-c", script], tmp_path)
  )
  with client.websocket_connect(f"{LOOPBACK}/host/console?started_by=test") as ws:
    out, closed = drain(ws)

  text = out.decode()
  assert "has-ctty" in text
  assert "TERM=xterm-256color" in text
  assert str(tmp_path.resolve()) in text
  assert closed.code == console.EXITED

  run = client.get("/activity").json()["activity"][0]
  assert (run["verb"], run["app_id"]) == ("console", None)


def test_leaving_ends_a_shell_that_is_still_writing(running, client, monkeypatch):
  # Exiting from a terminal waits for unread output to drain: kelsod has to let
  # go of the terminal before it waits, or the two wait on each other.
  shell(monkeypatch, "echo pid:$$; exec yes")
  with client.websocket_connect(URL) as ws:
    out = b""
    while b"pid:" not in out or b"\n" not in out.split(b"pid:")[1]:
      out += ws.receive_bytes()
  pid = int(out.split(b"pid:")[1].split(b"\n")[0])

  deadline = time.monotonic() + 10
  while time.monotonic() < deadline:
    try:
      os.kill(pid, 0)
    except ProcessLookupError:
      return
    time.sleep(0.1)
  pytest.fail(f"shell {pid} is still there, stuck exiting")
