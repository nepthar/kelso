"""The console: its page, and the websocket relayed to kelsod behind the front door."""

import json
import re

import api
import pytest
import server
from fakekelsod import APP_DETAIL, FakeConsole
from starlette.routing import WebSocketRoute
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


def _socket_paths(routes):
  for route in routes:
    if isinstance(route, WebSocketRoute):
      yield route.path
    # FastAPI keeps an included router whole, behind a wrapper.
    inner = getattr(route, "original_router", route)
    yield from _socket_paths(getattr(inner, "routes", []))


# Every websocket route, path parameters filled in. The front door middleware
# sees only HTTP, so each socket has to refuse for itself, new ones included.
SOCKETS = [
  BASE + re.sub(r"{[^}]+}", "kelso-ui", path)
  for path in _socket_paths(server.app.routes)
]


def test_every_socket_is_covered():
  assert f"{BASE}/apps/kelso-ui/console/ws" in SOCKETS
  assert f"{BASE}/host/console/ws" in SOCKETS


@pytest.mark.parametrize("url", SOCKETS)
def test_every_socket_needs_a_session(anon, kelsod, url):
  with anon.websocket_connect(url) as ws:
    _, closed = drain(ws)
  assert closed.code == 4003
  assert "Session expired" in closed.reason
  assert kelsod.paths == []


@pytest.mark.parametrize("url", SOCKETS)
def test_every_socket_refuses_another_site(client, kelsod, url):
  with client.websocket_connect(
    url, headers={"origin": "https://jellyfin.kelso.test"}
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


def test_the_host_shell_is_relayed_too(client, kelsod):
  with client.websocket_connect(f"{BASE}/host/console/ws") as ws:
    ws.send_bytes(b"hi")
    assert ws.receive_bytes() == b"hi"
  assert kelsod.paths == ["/host/console"]


def test_the_dashboard_names_the_host_and_offers_its_shell(client, fake):
  text = client.get("/").text
  assert "tycho " in text
  assert 'href="/host/console"' in text
  assert (
    "unsafe-inline" in client.get("/host/console").headers["content-security-policy"]
  )
