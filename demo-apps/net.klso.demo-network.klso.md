# Network Demo

A restricted app: it reaches only what its `[connections]` list, through
kproxy, and everything else is refused. `kelso cmd demo-network probe` tries
one destination of each kind from inside the app and says what happened. It
exits non-zero if anything was reached that should have been refused, or the
other way round.

| Destination | Expected |
| --- | --- |
| `https://example.com/`, a `web` host | reached |
| `https://www.example.com/`, a subdomain of one | reached |
| `https://example.org/`, a name it doesn't list | refused: the name doesn't resolve |
| `http://1.1.1.1/`, an IP address | refused: no route out |
| `http://lan/`, the `http` connection | reached, at `lan_address` |
| `lan_address` itself, by address | refused: no route out |

`lan_address` is anything on your own network that answers HTTP, as
`host:port`: a router's admin page, a NAS, another app's port on this machine.

```bash
kelso start demo-network --set lan_address=192.168.1.1:80
kelso cmd demo-network probe
kelso cmd demo-network fetch https://example.org/
```

The `main` route serves a page; kproxy relays it in, since a restricted app's
own network has no way in or out.

```toml klso_path="manifest.toml"
[app]
version      = "0.1.0"
display_name = "Network Demo"
description  = "A restricted app: what it may reach, and what it is refused"
author       = "Kelso Server"
url          = "https://github.com/nepthar/kelso"
network_mode = "restricted"

[config]
lan_address = { desc = "Something on your own network that answers HTTP, as host:port" }

[connections]
example = { kind = "web", hosts = ["example.com"], desc = "a site on the internet" }
lan     = { kind = "http", address = "${lan_address}", desc = "the address you set, by the name lan" }

[volumes]
script = { kind = "app", src = "probe.py" }

[run.main]
image       = "python:3.12-alpine"
cmd         = ["python", "/demo/probe.py", "serve"]
volumes     = { script = "/demo/probe.py" }
connections = ["lan"]
env         = { LAN_URL = "${conn.lan.url}", LAN_ADDRESS = "${lan_address}", PYTHONUNBUFFERED = "1" }
healthcheck = ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8080/', timeout=3)"]

[run.main.routes]
main = { port = "8080" }

[commands]
probe = { cmd = "python /demo/probe.py probe", desc = "Try each kind of destination and say what happened" }
fetch = { cmd = "python /demo/probe.py fetch", desc = "Fetch one URL from inside the app" }
```

`probe.py`, mounted at `/demo/probe.py`:

```python klso_path="probe.py"
import os
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

LAN_URL = os.environ["LAN_URL"]
LAN_ADDRESS = os.environ["LAN_ADDRESS"]

CHECKS = [
  ("web host", "https://example.com/", True),
  ("web host's subdomain", "https://www.example.com/", True),
  ("unlisted name", "https://example.org/", False),
  ("IP address", "http://1.1.1.1/", False),
  ("http connection", LAN_URL + "/", True),
  ("lan_address, directly", f"http://{LAN_ADDRESS}/", False),
]


def fetch(url):
  """(reached, detail): any HTTP answer counts as reached."""
  try:
    with urllib.request.urlopen(url, timeout=8) as r:
      return True, f"HTTP {r.status}"
  except urllib.error.HTTPError as e:
    return True, f"HTTP {e.code}"
  except (urllib.error.URLError, OSError) as e:
    return False, str(getattr(e, "reason", e))


def probe():
  wrong = 0
  for name, url, expected in CHECKS:
    reached, detail = fetch(url)
    mark = "ok " if reached == expected else "BAD"
    wrong += reached != expected
    print(f"{mark} {'reached' if reached else 'refused'}  {name:<22} {url}  ({detail})")
  print("all as expected" if not wrong else f"{wrong} not as expected")
  return 1 if wrong else 0


class Page(BaseHTTPRequestHandler):
  def do_GET(self):
    body = b"demo-network: run `kelso cmd demo-network probe` to see what it can reach\n"
    self.send_response(200)
    self.send_header("Content-Type", "text/plain")
    self.send_header("Content-Length", str(len(body)))
    self.end_headers()
    self.wfile.write(body)

  def log_message(self, *args):
    pass


if __name__ == "__main__":
  what = sys.argv[1] if len(sys.argv) > 1 else "probe"
  if what == "serve":
    HTTPServer(("0.0.0.0", 8080), Page).serve_forever()
  elif what == "fetch":
    reached, detail = fetch(sys.argv[2])
    print(f"{'reached' if reached else 'refused'}: {detail}")
    sys.exit(0 if reached else 1)
  else:
    sys.exit(probe())
```
