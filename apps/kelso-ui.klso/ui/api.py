import errno
import http.client
import json
import os
import socket

from websockets.asyncio.client import connect as ws_connect
from websockets.asyncio.client import unix_connect as ws_unix_connect

SOCKET = os.environ.get("KELSO_SOCKET", "/kelso/conn/admin.sock")
# host:port wins over the socket when set. Docker Desktop's bind mounts cannot
# carry AF_UNIX, so a mac host serves this over TCP instead.
API = os.environ.get("KELSO_API", "").strip()


class UnixHTTPConnection(http.client.HTTPConnection):
  """http.client over AF_UNIX. The Host header is a formality here."""

  def __init__(self, path, timeout=10):
    super().__init__("localhost", timeout=timeout)
    self._unix_path = path

  def connect(self):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(self.timeout)
    sock.connect(self._unix_path)
    self.sock = sock


class ApiError(Exception):
  """kelsod could not be reached, or refused. Rendered, never raised at a user."""


def where():
  """Human-readable name for whichever transport is in play."""
  return API if API else SOCKET


def _tcp_address():
  address = API.split("://", 1)[-1].rstrip("/")
  host, _, port = address.rpartition(":")
  if not port.isdigit():
    raise ApiError(f"KELSO_API must be host:port, got {API!r}")
  return host or "127.0.0.1", int(port)


def connect(timeout=10):
  if not API:
    return UnixHTTPConnection(SOCKET, timeout=timeout)
  return http.client.HTTPConnection(*_tcp_address(), timeout=timeout)


def open_socket(path):
  """A websocket to kelsod at `path`, for `async with`."""
  if not API:
    return ws_unix_connect(SOCKET, f"ws://localhost{path}", compression=None)
  host, port = _tcp_address()
  return ws_connect(f"ws://{host}:{port}{path}", compression=None)


def api(path, method="GET", payload=None, timeout=10):
  conn = connect(timeout)
  body = json.dumps(payload).encode() if payload is not None else None
  headers = {"Content-Type": "application/json"} if body else {}
  try:
    conn.request(method, path, body=body, headers=headers)
    response = conn.getresponse()
    raw = response.read()
    status = response.status
  except FileNotFoundError as e:
    raise ApiError(
      f"No socket at {SOCKET}. Is kelsod running, and is $kelso/var/conn bound "
      f"into this container?"
    ) from e
  except OSError as e:
    hint = ""
    # EOPNOTSUPP on a socket that is plainly there is Docker Desktop: its bind
    # mounts cannot carry AF_UNIX, and no amount of rebinding will fix it.
    if not API and e.errno == errno.EOPNOTSUPP:
      hint = (
        " This host's bind mounts cannot carry a unix socket (Docker Desktop "
        "does not support it). Run `kelsod --port N --host 0.0.0.0` and set "
        "`kelso config kelso-ui --set api_address=host.docker.internal:N`."
      )
    raise ApiError(f"Cannot reach kelsod at {where()}: {e}.{hint}") from e
  finally:
    conn.close()

  try:
    body = json.loads(raw)
  except ValueError as e:
    raise ApiError(f"kelsod sent something that is not JSON ({status})") from e
  if status >= 400:
    raise ApiError(str(body.get("error", body)))
  return body
