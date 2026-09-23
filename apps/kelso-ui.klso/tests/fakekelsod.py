"""A stand-in kelsod: canned JSON over real HTTP, so the UI's client is exercised.

The fixtures are shaped after a live kelsod's responses and bent to hit every
branch the pages have -- an app that is running, one that is uninstalled, one
with issues; a mirrored repo and one that is not; a secret that is set and one
that is not. `EVIL` is planted in every free-text field: if it ever reaches a
page unescaped, a test fails.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

EVIL = '<i data-evil="1">\'"&amp;</i>'


def _field(name, **kw):
  base = {
    "name": name,
    "value": None,
    "default": None,
    "secret": False,
    "secret_set": False,
    "desc": "",
    "advanced": False,
    "required": False,
    "choices": None,
  }
  base.update(kw)
  return base


APPS = [
  {
    "app_id": "kelso-ui",
    "display_name": "Kelso UI",
    "version": "0.6.0",
    "status": "running",
    "state": "installed",
    "containers": {"running": 1, "total": 1},
    "configured": "ready",
    "config_pending": False,
    "volume_count": 4,
    "last_action": "started",
  },
  {
    "app_id": "jellyfin",
    "display_name": f"Jellyfin {EVIL}",
    "version": None,
    "status": "stopped",
    "state": "installed",
    "containers": {"running": 0, "total": 0},
    "configured": "missing",
    "config_pending": True,
    "volume_count": 0,
    "last_action": None,
  },
  {
    "app_id": "mealie",
    "display_name": "Mealie",
    "version": "1.2.0",
    "status": "exited",
    "state": "uninstalled",
    "containers": {"running": 0, "total": 2},
    "configured": None,
    "config_pending": False,
    "volume_count": 1,
    "last_action": "uninstalled",
  },
]

APP_DETAIL = {
  **APPS[0],
  "description": f"Web interface {EVIL}",
  "metadata": {
    "version": "0.6.0",
    "subdomain": "kelso",
    "display_name": "Kelso UI",
    "description": "ignored",
    "main": "main",
    "note": EVIL,
  },
  "manifest_stale": True,
  "config_pending": True,
  "units": [
    {
      "name": "main",
      "image": "ghcr.io/astral-sh/uv:python3.12-bookworm-slim",
      "state": "running",
      "environment": {"PORT": "8080", "EVIL": EVIL},
      "command": ["/app/start.sh", EVIL],
      "volumes": [
        {"name": "ui", "path": "/app", "kind": "app", "readonly": True, "desc": EVIL},
        {"name": "tls", "path": "/tls", "kind": "data", "readonly": False, "desc": ""},
      ],
    },
    {
      "name": "worker",
      "image": "busybox",
      "state": None,
      "environment": {},
      "command": [],
      "volumes": [],
    },
  ],
  "routes": [
    {
      "name": "main",
      "unit": "main",
      "container_port": 8080,
      "host_port": 10001,
      "url": "https://kelso.example.test",
      "published_url": "https://kelso.example.test",
      "provider": "web",
    },
    {
      "name": "admin",
      "unit": "main",
      "container_port": 9000,
      "host_port": None,
      "url": None,
      "published_url": None,
      "provider": None,
    },
  ],
  "volumes": [
    {
      "name": "ui",
      "kind": "app",
      "readonly": True,
      "path": "/k/ui",
      "bytes": 79549702,
      "bind": None,
    },
    {
      "name": "conn",
      "kind": "host",
      "readonly": False,
      "path": None,
      "bytes": None,
      "bind": None,
    },
    {
      "name": "media",
      "kind": "host",
      "readonly": False,
      "path": "/mnt/media",
      "bytes": 0,
      "bind": "media",
    },
  ],
  "commands": [
    {"name": "test", "desc": f"Run a test {EVIL}", "unit": "main"},
    {"name": "bare", "desc": "", "unit": "main"},
  ],
  "issues": [
    {
      "problem": f"volume conn is not bound {EVIL}",
      "fix": "kelso config kelso-ui --bind conn=x",
    },
    {"problem": "no fix offered"},
  ],
}

# An app with nothing declared, uninstalled, stopped: the empty branches.
APP_BARE = {
  **APPS[2],
  "description": "",
  "metadata": {"app_id": "mealie"},
  "manifest_stale": False,
  "units": [],
  "routes": [],
  "volumes": [],
  "commands": [],
  "issues": [],
}

APP_CONFIG = {
  "title": "kelso-ui",
  "note": "",
  "missing": ["admin_pass", "tuning"],
  "fields": [
    _field("api_address", value=EVIL, default="", desc=f"host:port {EVIL}"),
    _field("admin_pass", secret=True, secret_set=False, desc="password"),
    _field("token", secret=True, secret_set=True),
    _field("subdomain", value="kelso", default="kelso"),
    _field("volume.conn", value="harbor_conn", choices=["harbor_conn", EVIL]),
    _field("route.none", choices=[]),
    _field("tuning", advanced=True),
    _field("debug", advanced=True, default="0"),
  ],
}

CATALOG = {
  "contested": {"mealie": ["examples", "github"]},
  "catalogs": [
    {"name": "local", "apps": []},
    {
      "name": "examples",
      "apps": [
        {
          "app_id": "kelso-ui",
          "display_name": "Kelso UI",
          "version": "0.6.0",
          "description": f"Web interface {EVIL}",
          "repo": "examples",
          "state": "installed",
          "configured": "ready",
          "manifest": f'[app]\nversion = "0.6.0"\n# {EVIL}\n',
          "manifest_stale": True,
          "warnings": [
            {
              "run_unit": "main",
              "message": f"Free-form options {EVIL}",
              "options": ["privileged", EVIL],
            }
          ],
        },
        {
          "app_id": "mealie",
          "display_name": "Mealie",
          "version": None,
          "description": "",
          "repo": "examples",
          "state": "available",
          "configured": "missing",
          "manifest": "",
          "manifest_stale": False,
          "warnings": [],
        },
        {
          "app_id": "broken",
          "display_name": None,
          "version": None,
          "description": None,
          "repo": "examples",
          "state": "available",
          "configured": None,
          "manifest": None,
          "manifest_stale": False,
          "warnings": [],
        },
      ],
    },
    {
      "name": "github",
      "apps": [
        {
          "app_id": "mealie",
          "display_name": "Mealie",
          "version": "2.0",
          "description": "Recipes",
          "repo": "github",
          "state": "uninstalled",
          "configured": "ready",
          "manifest": "[app]\n",
          "manifest_stale": False,
          "warnings": [],
        },
      ],
    },
  ],
}

REPOS = {
  "repos": [
    {
      "name": "local",
      "location": "/k/repos/local",
      "url": None,
      "exists": True,
      "removable": False,
      "bound_apps": [],
      "sha": None,
      "updated_at": None,
    },
    {
      "name": "examples",
      "location": f"/code/{EVIL}",
      "url": None,
      "exists": True,
      "removable": True,
      "bound_apps": ["kelso-ui", "mealie"],
      "sha": None,
      "updated_at": None,
    },
    {
      "name": "github",
      "location": "github://u/r/main/apps",
      "url": "github://u/r/main/apps",
      "exists": False,
      "removable": True,
      "bound_apps": [],
      "sha": "0123456789abcdef",
      "updated_at": "2026-09-23T15:03:00Z",
    },
  ]
}

VOLUMES = {
  "volumes": [
    {
      "app_id": "kelso-ui",
      "name": "tls",
      "kind": "data",
      "in_use": True,
      "declared": True,
      "bytes": 864,
    },
    {
      "app_id": "mealie",
      "name": EVIL,
      "kind": "data",
      "in_use": False,
      "declared": False,
      "bytes": None,
    },
    {
      "app_id": "jellyfin",
      "name": "cache",
      "kind": "temp",
      "in_use": False,
      "declared": True,
      "bytes": 0,
    },
    {
      "app_id": "jellyfin",
      "name": "config",
      "kind": "data",
      "in_use": False,
      "declared": True,
      "bytes": 601653,
    },
  ],
  "kelso_dirs": [
    {"name": "run", "description": f"Installed apps {EVIL}", "bytes": 1024**3},
  ],
}

HOST_VOLUMES = {
  "host_volumes": [
    {
      "tag": "harbor_conn",
      "path": "/k/var/conn",
      "readonly": False,
      "require_mount": False,
      "exists": True,
      "bytes": 0,
    },
    {
      "tag": "media",
      "path": f"/mnt/{EVIL}",
      "readonly": True,
      "require_mount": True,
      "exists": False,
      "bytes": None,
    },
  ]
}

SNAPSHOTS = {
  "snapshots": [
    {
      "app_id": "kelso-ui",
      "name": "2026-09-23_15-03Z_test",
      "taken_at": "2026-09-23T15:03:00Z",
      "tag": EVIL,
      "bytes": 4851,
    },
    {
      "app_id": "mealie",
      "name": "old-style-name",
      "taken_at": None,
      "tag": None,
      "bytes": None,
    },
  ]
}

ROUTE_PROVIDERS = {
  "route_providers": [
    {"tag": "web", "kind": "nginx_proxy_manager", "domain": "example.test"},
    {"tag": "odd name", "kind": "pangolin", "domain": EVIL},
  ],
  "kinds": ["cloudflare_tunnel", "nginx_proxy_manager", "pangolin"],
}

PROVIDER_CONFIG = {
  "title": "route provider web (nginx_proxy_manager)",
  "note": f"Check it with `kelso routes check web` {EVIL}",
  "missing": ["password"],
  "fields": [
    _field("domain", value="example.test", required=True),
    _field("password", secret=True, required=True),
  ],
}

NEW_PROVIDER_CONFIG = {
  "title": "route provider fresh (pangolin)",
  "note": None,
  "missing": [],
  "fields": [],
}

ACTIVITY = {
  "activity": [
    {
      "ts": "2026-09-23T15:03:04Z",
      "app_id": "kelso-ui",
      "verb": "snapshot",
      "status": "ok",
      "duration_ms": 1505,
      "log": "2026-09-23T150302Z.kelso-ui.snapshot.log",
      "available": True,
    },
    {
      "ts": "2026-09-23T03:26:05Z",
      "app_id": None,
      "verb": "repo-update",
      "status": "failed",
      "duration_ms": None,
      "log": "x.log",
      "available": False,
    },
    {
      "ts": "2026-09-22T03:26:05Z",
      "app_id": EVIL,
      "verb": EVIL,
      "status": EVIL,
      "duration_ms": 4,
      "log": EVIL,
      "available": True,
    },
  ]
}

METRICS = {
  "since": 1790181950,
  "until": 1790185550,
  "metrics": {
    "host_cpu_used_ratio": [{"t": 1790182164, "v": 0.016}, {"t": 1790182464, "v": 0.5}],
    "host_mem_used_ratio": [],
  },
}

LOGS = {"app_id": "kelso-ui", "tail": 200, "text": f"main-1  | started\n{EVIL}\n"}

GET = {
  "/version": {"kelso": "0.1.0", "api": 19},
  "/apps": {"apps": APPS},
  "/apps/kelso-ui": APP_DETAIL,
  "/apps/kelso-ui/config-request": APP_CONFIG,
  "/apps/kelso-ui/logs": LOGS,
  "/apps/mealie": APP_BARE,
  "/apps/mealie/config-request": {"title": "mealie", "fields": [], "missing": []},
  "/catalog": CATALOG,
  "/repos": REPOS,
  "/volumes": VOLUMES,
  "/host-volumes": HOST_VOLUMES,
  "/snapshots": SNAPSHOTS,
  "/route-providers": ROUTE_PROVIDERS,
  "/route-providers/web/config-request": PROVIDER_CONFIG,
  "/route-providers/fresh/config-request?kind=pangolin": NEW_PROVIDER_CONFIG,
  "/activity?limit=100": ACTIVITY,
  "/activity/x.log": {"text": "log text"},
  "/metrics?prefix=host_&hours=1": METRICS,
  "/jobs/j1": {"id": "j1", "state": "done", "log": "x.log"},
}


class FakeKelsod:
  """Serve `GET` on a free port. `posts` records what the UI sent.

  `fail` set to a message makes every request answer 500 with it -- kelsod
  refusing, as the UI sees it.
  """

  def __init__(self):
    self.posts = []
    self.fail = None
    self.api = 19
    fake = self

    class Handler(BaseHTTPRequestHandler):
      def log_message(self, *args):
        pass

      def _send(self, status, body):
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

      def do_GET(self):
        if fake.fail:
          return self._send(500, {"error": fake.fail})
        path = unquote(self.path)
        if path == "/version":
          return self._send(200, {"kelso": "0.1.0", "api": fake.api})
        if path in GET:
          return self._send(200, GET[path])
        return self._send(404, {"error": f"no fixture for {path}"})

      def _write(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"null")
        fake.posts.append((self.command, unquote(self.path), body))
        if fake.fail:
          return self._send(500, {"error": fake.fail})
        if self.path == "/jobs":
          return self._send(202, {"id": "j1", "state": "queued"})
        return self._send(200, {"ok": True})

      do_POST = _write
      do_DELETE = _write

    self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    self.address = f"127.0.0.1:{self.server.server_address[1]}"
    self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

  def start(self):
    self.thread.start()
    return self

  def stop(self):
    self.server.shutdown()
