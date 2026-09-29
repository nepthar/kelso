"""The console: its page, and the websocket relayed to kelsod behind the front door."""

import json

import api
import pytest
from fakekelsod import APP_DETAIL, FakeConsole
from starlette.websockets import WebSocketDisconnect

_console = FakeConsole().start()

# Absolute: TestClient opens a relative websocket URL on `testserver`, which
# neither the origin check nor the cookie jar would take for kelso.test.
BASE = "wss://kelso.test"
WS = f"{BASE}/apps/kelso-ui/console/ws"


@pytest.fixture
def kelsod(monkeypatch):
  monkeypatch.setattr(api, "API", _console.address)
  _console.paths.clear()
  return _console


def drain(ws):
  """Everything the terminal was sent, and how the socket closed."""
  out = b""
  with pytest.raises(WebSocketDisconnect) as closed:
    while True:
      out += ws.receive_bytes()
  return out, closed.value


def test_relays_both_ways_and_passes_the_close_on(client, kelsod):
  with client.websocket_connect(f"{WS}?unit=main") as ws:
    ws.send_bytes(b"hi")
    assert ws.receive_bytes() == b"hi"
    ws.send_text(json.dumps({"resize": [80, 24]}))
    assert ws.receive_bytes() == b'ctl:{"resize": [80, 24]}'
    ws.send_bytes(b"exit\r")
    _, closed = drain(ws)
  assert (closed.code, closed.reason) == (1000, "exited 0")
  assert kelsod.paths == ["/apps/kelso-ui/console?unit=main"]


def test_passes_a_refusal_on(client, kelsod):
  with client.websocket_connect(f"{BASE}/apps/mealie/console/ws") as ws:
    out, closed = drain(ws)
  assert closed.code == 4001
  assert b"not running" in out


def test_socket_needs_a_session(anon, kelsod):
  with anon.websocket_connect(WS) as ws:
    _, closed = drain(ws)
  assert closed.code == 4003
  assert "Session expired" in closed.reason
  assert kelsod.paths == []


def test_socket_refuses_another_site(client, kelsod):
  with client.websocket_connect(
    WS, headers={"origin": "https://jellyfin.kelso.test"}
  ) as ws:
    _, closed = drain(ws)
  assert closed.code == 4003
  assert "another site" in closed.reason
  assert kelsod.paths == []


def test_only_the_console_page_allows_inline_styles(client, fake):
  console = client.get("/apps/kelso-ui/console")
  policy = console.headers["content-security-policy"]
  assert "style-src 'self' 'unsafe-inline'" in policy
  assert "script-src 'self';" in policy
  assert (
    "unsafe-inline"
    not in client.get("/apps/kelso-ui").headers["content-security-policy"]
  )
  assert 'data-src="/apps/kelso-ui/console/ws?unit=main"' in console.text


def test_a_second_running_unit_gets_a_picker(client, fake, monkeypatch):
  units = [dict(u, state="running") for u in APP_DETAIL["units"]]
  monkeypatch.setitem(APP_DETAIL, "units", units)
  text = client.get("/apps/kelso-ui/console?unit=worker").text
  assert 'href="/apps/kelso-ui/console?unit=main">main</a>' in text
  assert 'aria-current="true">worker</a>' in text
  assert 'data-src="/apps/kelso-ui/console/ws?unit=worker"' in text


def test_nothing_running_draws_no_terminal(client, fake):
  text = client.get("/apps/mealie/console").text
  assert "Start it to open a console" in text
  assert 'id="console"' not in text
  assert "xterm.js" not in text
