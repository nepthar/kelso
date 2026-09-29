"""A shell in an app's running container: the page, and the websocket it opens.

The socket is relayed frame for frame to kelsod, which owns the PTY and the
protocol (kelso/daemon/console.py); this side adds only the front door.
"""

import asyncio
from urllib.parse import quote, urlencode

from api import ApiError, api, open_socket
from fastapi import APIRouter, WebSocket
from frontdoor import TERMINAL_CSP, socket_refusal
from web import PageDep
from websockets.exceptions import ConnectionClosed, InvalidHandshake

router = APIRouter()

REFUSED = 4003
# Reserved codes a close frame may not carry.
UNSENDABLE = {1005, 1006, 1015}


@router.get("/apps/{app_id}/console")
def console_page(page: PageDep, app_id: str, unit: str = ""):
  page.title = app_id
  app = api(f"/apps/{quote(app_id)}")
  running = [u["name"] for u in app.get("units", []) if u.get("state") == "running"]
  if unit not in running:
    unit = "main" if "main" in running else (running[0] if running else "")
  title = f"{app.get('display_name') or app_id} console"
  response = page.render(
    "pages/app_console.html", title, app_id=app_id, unit=unit, running=running
  )
  response.headers["Content-Security-Policy"] = TERMINAL_CSP
  return response


@router.websocket("/apps/{app_id}/console/ws")
async def console_socket(ws: WebSocket, app_id: str, unit: str = "main"):
  refusal = socket_refusal(ws)
  await ws.accept()
  if refusal:
    await ws.close(REFUSED, refusal)
    return
  path = f"/apps/{quote(app_id)}/console?{urlencode({'unit': unit})}"
  try:
    async with open_socket(path) as upstream:
      code, reason = await _relay(ws, upstream)
  except (OSError, InvalidHandshake, ApiError) as e:
    code, reason = 1011, f"Cannot reach kelsod: {e}"
  if code is not None:
    code = 1011 if code in UNSENDABLE else code
    # A close reason is capped at 123 bytes.
    await ws.close(code, reason.encode()[:120].decode(errors="ignore"))


async def _relay(ws, upstream):
  """Pass frames both ways until one side goes; how kelsod closed, if it did."""

  async def up():
    while True:
      message = await ws.receive()
      if message["type"] == "websocket.disconnect":
        return
      await upstream.send(message.get("bytes") or message.get("text") or "")

  async def down():
    try:
      async for message in upstream:
        if isinstance(message, bytes):
          await ws.send_bytes(message)
        else:
          await ws.send_text(message)
    except ConnectionClosed:
      pass

  browser, kelsod = asyncio.create_task(up()), asyncio.create_task(down())
  done, pending = await asyncio.wait(
    {browser, kelsod}, return_when=asyncio.FIRST_COMPLETED
  )
  for task in pending:
    task.cancel()
  await asyncio.gather(browser, kelsod, return_exceptions=True)
  if kelsod in done:
    return upstream.close_code or 1011, upstream.close_reason or ""
  return None, ""
