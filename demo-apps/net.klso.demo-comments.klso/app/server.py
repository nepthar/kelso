"""A page of comments, kept in sqlite on the app's data volume."""

import html
import sqlite3
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs

db = sqlite3.connect("/data/comments.db")
db.execute(
  "CREATE TABLE IF NOT EXISTS comments"
  " (at TEXT DEFAULT CURRENT_TIMESTAMP, text TEXT NOT NULL)"
)

PAGE = """\
<!doctype html>
<title>Comments</title>
<style>
  body {{ font: 16px sans-serif; max-width: 40em; margin: 2em auto; padding: 0 1em; }}
  input {{ font: inherit; padding: 0.3em; }}
  input[name=text] {{ width: 70%; }}
  li {{ margin: 0.5em 0; }}
  small {{ color: gray; }}
</style>
<h1>Comments</h1>
<form method="post">
  <input name="text" placeholder="Say something" required autofocus>
  <input type="submit" value="Post">
</form>
<ul>
{comments}
</ul>
"""


class Comments(BaseHTTPRequestHandler):
  def do_GET(self):
    rows = db.execute("SELECT at, text FROM comments ORDER BY rowid DESC")
    comments = "\n".join(
      f"<li>{html.escape(text)} <small>{at}</small></li>" for at, text in rows
    )
    self.send_response(200)
    self.send_header("Content-Type", "text/html; charset=utf-8")
    self.end_headers()
    self.wfile.write(PAGE.format(comments=comments).encode())

  def do_POST(self):
    form = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
    text = form.get("text", [""])[0].strip()
    if text:
      db.execute("INSERT INTO comments (text) VALUES (?)", (text,))
      db.commit()
    self.send_response(303)
    self.send_header("Location", "/")
    self.end_headers()


HTTPServer(("0.0.0.0", 8080), Comments).serve_forever()
