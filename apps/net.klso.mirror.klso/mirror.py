#!/usr/bin/env python3
"""Mirror: keeps bare mirrors of git repositories up to date.

One worker thread does every git operation, so no two ever touch the same repo
at once. It runs whichever mirror is due soonest, then makes that mirror due
again INTERVAL_MINUTES after the run finished, whether it succeeded or not.
A run of a mirror forgotten while it was underway deletes what it wrote.
"""

import html
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(os.environ.get("PORT", "8080"))
STATE = Path(os.environ.get("MIRROR_STATE", "/state"))
MIRRORS = Path(os.environ.get("MIRROR_DIR", "/mirrors"))
INTERVAL = float(os.environ.get("INTERVAL_MINUTES", "60")) * 60
HISTORY = 24
GIT_TIMEOUT = 3600


def log(msg):
  print(msg, file=sys.stderr, flush=True)


def db():
  conn = sqlite3.connect(STATE / "mirror.db", timeout=30)
  conn.row_factory = sqlite3.Row
  return conn


def init_db():
  with db() as c:
    c.executescript("""
      CREATE TABLE IF NOT EXISTS mirrors (
        id INTEGER PRIMARY KEY, url TEXT UNIQUE NOT NULL, dir TEXT NOT NULL,
        next_at REAL NOT NULL DEFAULT 0);
      CREATE TABLE IF NOT EXISTS runs (
        mirror_id INTEGER NOT NULL, at REAL NOT NULL, ok INTEGER NOT NULL,
        msg TEXT NOT NULL);
    """)


def git(*args):
  r = subprocess.run(
    ["git", *args],
    capture_output=True,
    text=True,
    timeout=GIT_TIMEOUT,
    env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
  )
  if r.returncode != 0:
    raise RuntimeError(r.stderr.strip() or f"git exited {r.returncode}")


def remove_files(path):
  shutil.rmtree(path, ignore_errors=True)
  shutil.rmtree(path.with_name(path.name + ".partial"), ignore_errors=True)


def sync_one(mirror_id):
  with db() as c:
    m = c.execute("SELECT * FROM mirrors WHERE id = ?", (mirror_id,)).fetchone()
  if m is None:
    return
  path = MIRRORS / m["dir"]
  try:
    if path.exists():
      git("-C", str(path), "remote", "update", "--prune")
    else:
      partial = path.with_name(path.name + ".partial")
      shutil.rmtree(partial, ignore_errors=True)
      git("clone", "--mirror", "--", m["url"], str(partial))
      partial.rename(path)
    ok, msg = True, ""
  except Exception as e:
    ok, msg = False, str(e)
    log(f"{m['url']}: {msg}")
  now = time.time()
  with db() as c:
    kept = c.execute(
      "INSERT INTO runs SELECT ?, ?, ?, ? WHERE EXISTS "
      "(SELECT 1 FROM mirrors WHERE id = ?)",
      (mirror_id, now, ok, msg, mirror_id),
    ).rowcount
    c.execute(
      "UPDATE mirrors SET next_at = ? WHERE id = ?", (now + INTERVAL, mirror_id)
    )
  if not kept:
    remove_files(path)


wake = threading.Event()


def worker():
  while True:
    wake.clear()
    with db() as c:
      due = c.execute("SELECT id, next_at FROM mirrors ORDER BY next_at").fetchone()
    if due is None:
      wake.wait()
    elif due["next_at"] > time.time():
      wake.wait(due["next_at"] - time.time())
    else:
      sync_one(due["id"])


def slug(url):
  name = url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
  return re.sub(r"[^A-Za-z0-9._-]", "_", name).lstrip(".") or "repo"


def add(url):
  url = url.strip()
  if not url or url.startswith("-"):
    raise ValueError("Enter a git URL, such as https://github.com/owner/repo.git")
  with db() as c:
    if c.execute("SELECT 1 FROM mirrors WHERE url = ?", (url,)).fetchone():
      raise ValueError(f"{url} is already mirrored")
    cur = c.execute("INSERT INTO mirrors (url, dir) VALUES (?, '')", (url,))
    mirror_id = cur.lastrowid
    c.execute(
      "UPDATE mirrors SET dir = ? WHERE id = ?",
      (f"{mirror_id}-{slug(url)}.git", mirror_id),
    )
  wake.set()


def forget(mirror_id):
  with db() as c:
    m = c.execute("SELECT dir FROM mirrors WHERE id = ?", (mirror_id,)).fetchone()
    if m is None:
      return
    c.execute("DELETE FROM mirrors WHERE id = ?", (mirror_id,))
    c.execute("DELETE FROM runs WHERE mirror_id = ?", (mirror_id,))
  remove_files(MIRRORS / m["dir"])


def du(path):
  total = 0
  for root, _, files in os.walk(path):
    for f in files:
      try:
        total += os.lstat(os.path.join(root, f)).st_size
      except OSError:
        pass
  return total


def human_size(n):
  for unit in ("B", "KB", "MB", "GB"):
    if n < 1024:
      return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
    n /= 1024
  return f"{n:.1f} TB"


def ago(t):
  s = int(time.time() - t)
  if s < 60:
    return "just now"
  if s < 3600:
    return f"{s // 60}m ago"
  if s < 86400:
    return f"{s // 3600}h ago"
  return f"{s // 86400}d ago"


def rows():
  with db() as c:
    mirrors = c.execute("SELECT * FROM mirrors ORDER BY url").fetchall()
    out = []
    for m in mirrors:
      runs = c.execute(
        "SELECT * FROM runs WHERE mirror_id = ? ORDER BY at DESC LIMIT ?",
        (m["id"], HISTORY),
      ).fetchall()
      out.append((m, runs))
  return out


PAGE = """<!doctype html>
<meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Mirror</title>
<style>
  :root { color-scheme: light dark; --dim: #8889; --bad: #c0392b; --good: #2e8b57; }
  body { font: 15px/1.45 ui-sans-serif, -apple-system, "Segoe UI", sans-serif;
    max-width: 60rem; margin: 2rem auto; padding: 0 1rem; }
  form.add { display: flex; gap: .5rem; margin: 1rem 0 2rem; }
  form.add input { flex: 1; padding: .45rem .6rem; font: inherit; }
  button { font: inherit; padding: .4rem .8rem; cursor: pointer; }
  table { width: 100%; border-collapse: collapse; }
  th, td { text-align: left; padding: .55rem .5rem; border-bottom: 1px solid var(--dim);
    vertical-align: top; }
  th { font-weight: 600; font-size: .85rem; color: var(--dim); }
  td.num { white-space: nowrap; font-variant-numeric: tabular-nums; }
  .url { word-break: break-all; }
  .err { color: var(--bad); font-size: .85rem; white-space: pre-wrap; margin-top: .25rem; }
  .ok { color: var(--good); } .bad { color: var(--bad); } .dim { color: var(--dim); }
  .notice { padding: .6rem .8rem; border: 1px solid var(--bad); color: var(--bad); }
</style>
<h1>Mirror</h1>
NOTICE
<form class=add method=post action=/add>
  <input name=url placeholder="https://github.com/owner/repo.git" required>
  <button>Mirror</button>
</form>
TABLE
"""


def render(notice=""):
  body = []
  for m, runs in rows():
    url = html.escape(m["url"])
    path = MIRRORS / m["dir"]
    size = human_size(du(path)) if path.exists() else "—"
    if runs:
      good = sum(r["ok"] for r in runs)
      cls = "ok" if good == len(runs) else "bad"
      rate = f"<span class={cls}>{good}/{len(runs)}</span>"
      last = ago(runs[0]["at"])
    else:
      rate, last = "<span class=dim>pending</span>", "—"
    err = ""
    if runs and not runs[0]["ok"]:
      err = f"<div class=err>{html.escape(runs[0]['msg'][-500:])}</div>"
    body.append(
      f"<tr><td class=url>{url}{err}</td><td class=num>{rate}</td>"
      f"<td class=num>{last}</td><td class=num>{size}</td>"
      f"<td><form method=post action=/forget/{m['id']} "
      f"onsubmit=\"return confirm('Forget {url} and delete its mirror?')\">"
      f"<button>Forget</button></form></td></tr>"
    )
  if body:
    table = (
      f"<table><tr><th>Repository</th><th>Last {HISTORY} runs</th>"
      "<th>Last run</th><th>Size</th><th></th></tr>" + "".join(body) + "</table>"
    )
  else:
    table = "<p class=dim>Nothing mirrored yet.</p>"
  if notice:
    notice = f"<p class=notice>{html.escape(notice)}</p>"
  return PAGE.replace("NOTICE", notice).replace("TABLE", table)


class Handler(BaseHTTPRequestHandler):
  def _send(self, code, body, ctype="text/html; charset=utf-8"):
    body = body.encode()
    self.send_response(code)
    self.send_header("Content-Type", ctype)
    self.send_header("Content-Length", str(len(body)))
    self.end_headers()
    self.wfile.write(body)

  def _home(self):
    self.send_response(303)
    self.send_header("Location", "/")
    self.send_header("Content-Length", "0")
    self.end_headers()

  def do_GET(self):
    if self.path == "/":
      self._send(200, render())
    else:
      self._send(404, "not found\n", "text/plain")

  def do_POST(self):
    length = int(self.headers.get("Content-Length") or 0)
    form = urllib.parse.parse_qs(self.rfile.read(length).decode())
    if self.path == "/add":
      try:
        add(form.get("url", [""])[0])
      except ValueError as e:
        return self._send(400, render(str(e)))
      self._home()
    elif m := re.fullmatch(r"/forget/(\d+)", self.path):
      forget(int(m[1]))
      self._home()
    else:
      self._send(404, "not found\n", "text/plain")

  def log_message(self, format, *args):
    if self.command == "POST":
      log(f"POST {self.path}")


def serve():
  init_db()
  threading.Thread(target=worker, daemon=True).start()
  log(f"mirror on :{PORT}, mirrors in {MIRRORS}")
  ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
  serve()
