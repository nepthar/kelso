"""The shell around every page: JS, nav, and shared HTML fragments.

The CSS is static/kelso.css; this module only links it.
"""

import html
import json
from hashlib import blake2s
from pathlib import Path

NAV = (
  ("/", "Dashboard", "home-outline"),
  ("/catalog", "Repos", "book-multiple-outline"),
  ("/volumes", "Volumes", "database-outline"),
  ("/snapshots", "Snapshots", "camera-outline"),
  ("/routes", "Routes", "network-outline"),
  ("/activity", "Activity", "file-document-multiple-outline"),
)

# The stylesheet lives in static/kelso.css, served like any other asset.
# Pages themselves are `no-store`, but this file is not, so its URL carries a
# digest of its contents: change the CSS, reinstall, and the URL changes with
# it rather than a browser holding on to the old one.
_STATIC = Path(__file__).parent / "static"


def _asset_version(name):
  """Short digest of a static file. "dev" when it cannot be read."""
  try:
    return blake2s((_STATIC / name).read_bytes(), digest_size=6).hexdigest()
  except OSError:
    return "dev"


STYLE_HREF = f"/static/kelso.css?v={_asset_version('kelso.css')}"


def fmt_size(n):
  if n is None:
    return "&mdash;"
  size = float(n)
  for unit in ("B", "KB", "MB", "GB", "TB"):
    if size < 1024:
      return f"{size:.1f} {unit}"
    size /= 1024
  return f"{size:.1f} PB"


def esc(value):
  return html.escape("" if value is None else str(value))


def nav_active(path):
  """Which nav entry a path belongs to.

  App detail pages have no nav entry of their own -- the list they belong to
  lives on the dashboard now -- so they light Dashboard instead of nothing.
  """
  if path.startswith("/apps"):
    return "/"
  for href, _, _ in NAV:
    if path == href or (href != "/" and path.startswith(href + "/")):
      return href
  return None


def _head(title):
  """Everything from `<!doctype>` to `<body>`. Shared by every page."""
  return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} · kelso</title>
<script>if (localStorage.getItem("kelso-nav") === "collapsed") document.documentElement.classList.add("nav-collapsed");
var t = localStorage.getItem("kelso-theme");
if (t === "mojave" || t === "mojave-day") document.documentElement.dataset.theme = t;</script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="{STYLE_HREF}"></head>
<body>"""


def page(path, title, body, version="", actions=""):
  active = nav_active(path)
  links = "".join(
    f'<a href="{href}" title="{esc(label)}"'
    f"{' class="active"' if href == active else ''}>"
    f'{mdi(icon)}<span class="label">{esc(label)}</span></a>'
    for href, label, icon in NAV
  )
  sub = f'<span class="ver">kelso {esc(version)}</span>' if version else ""
  extra = actions
  refresh = (
    ""
    if path.startswith("/apps/") and path != "/apps"
    else f'<a href="{esc(path)}">Refresh</a>'
  )
  return f"""{_head(title)}
<div class="app">
<nav>
  <div class="brand"><span class="name">Kelso</span><span class="mark" aria-hidden="true">K</span>{sub}</div>
  {links}
  <div class="nav-foot">
  <button type="button" class="nav-theme" title="rally">
    <span class="label">theme</span><span class="mark" aria-hidden="true">{mdi("palette-outline")}</span>
  </button>
  <form class="nav-out" method="post" action="/logout">
    <button type="submit" title="Sign out">
      <span class="label">sign out</span><span class="mark" aria-hidden="true">⏻</span>
    </button>
  </form>
  </div>
  <button type="button" class="nav-toggle" aria-label="Collapse sidebar">‹</button>
</nav>
<main>
  <div class="head"><h1>{esc(title)}</h1>{refresh}{extra}</div>
  {body}
</main>
</div>
{confirm_modal()}
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.11.1/highlight.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.11.1/languages/toml.min.js"></script>
<script>
(function () {{
  var root = document.documentElement;
  var btn = document.querySelector(".nav-toggle");
  function sync() {{
    var on = root.classList.contains("nav-collapsed");
    btn.setAttribute("aria-label", on ? "Expand sidebar" : "Collapse sidebar");
    btn.textContent = on ? "›" : "‹";
  }}
  sync();
  btn.addEventListener("click", function () {{
    var on = root.classList.toggle("nav-collapsed");
    if (on) localStorage.setItem("kelso-nav", "collapsed");
    else localStorage.removeItem("kelso-nav");
    sync();
  }});
  var themeBtn = document.querySelector(".nav-theme");
  var themes = ["", "mojave", "mojave-day"];
  var themeNames = {{ "": "rally", mojave: "mojave night", "mojave-day": "mojave day" }};
  function syncTheme() {{
    if (themeBtn) themeBtn.title = themeNames[root.dataset.theme || ""] || "rally";
  }}
  syncTheme();
  if (themeBtn) themeBtn.addEventListener("click", function () {{
    var i = themes.indexOf(root.dataset.theme || "");
    var next = themes[(i + 1) % themes.length];
    if (next) {{
      root.dataset.theme = next;
      localStorage.setItem("kelso-theme", next);
    }} else {{
      delete root.dataset.theme;
      localStorage.removeItem("kelso-theme");
    }}
    syncTheme();
  }});
  document.querySelectorAll(".cfg-edit, .cfg-form").forEach(function (form) {{
    var controls = form.querySelectorAll("input:not([type=hidden]), select");
    var save = form.querySelector(".cfg-save");
    if (!controls.length || !save) return;
    var initial = Array.prototype.map.call(controls, function (c) {{ return c.value; }});
    function dirty() {{
      var on = Array.prototype.some.call(controls, function (c, i) {{
        return c.value !== initial[i];
      }});
      form.classList.toggle("is-dirty", on);
      save.disabled = !on;
    }}
    controls.forEach(function (c) {{
      c.addEventListener("input", dirty);
      c.addEventListener("change", dirty);
    }});
  }});
  var fetchShade = document.getElementById("fetch-shade");
  if (fetchShade) {{
    fetchShade.addEventListener("click", function (event) {{
      if (event.target === fetchShade) {{
        window.location = fetchShade.getAttribute("data-close");
      }}
    }});
  }}
  var shade = document.getElementById("catalog-shade");
  if (shade) {{
    function closeCard() {{
      var closeTo = shade.getAttribute("data-close");
      if (closeTo) {{ window.location = closeTo; return; }}
      shade.hidden = true;
      shade.querySelectorAll(".app-card").forEach(function (card) {{
        card.hidden = true;
      }});
    }}
    document.querySelectorAll(".catalog-row").forEach(function (row) {{
      row.addEventListener("click", function () {{
        var card = document.getElementById(row.getAttribute("data-card"));
        if (!card) return;
        shade.querySelectorAll(".app-card").forEach(function (other) {{
          other.hidden = true;
        }});
        card.hidden = false;
        shade.hidden = false;
      }});
    }});
    shade.addEventListener("click", function (event) {{
      if (event.target === shade) closeCard();
    }});
  }}
  // Timestamps ship as UTC in `datetime`; only the browser knows the viewer's
  // zone, so the friendly text is filled in here. Absolute local time stays on
  // the tooltip, and the ISO fallback survives with no JS.
  function relTime(then, now) {{
    var secs = Math.round((now - then) / 1000);
    if (secs < 0) return "just now";
    if (secs < 45) return "just now";
    var mins = Math.round(secs / 60);
    if (mins < 60) return mins + "m ago";
    var hours = Math.round(mins / 60);
    if (hours < 24) return hours + "h ago";
    var days = Math.round(hours / 24);
    if (days < 30) return days + "d ago";
    return then.toLocaleDateString(undefined,
      {{ year: "numeric", month: "short", day: "numeric" }});
  }}
  document.querySelectorAll("time[datetime]").forEach(function (el) {{
    var then = new Date(el.getAttribute("datetime"));
    if (isNaN(then.getTime())) return;
    el.textContent = relTime(then, new Date());
    el.title = then.toLocaleString();
  }});
  if (window.hljs) {{
    document.querySelectorAll("pre.app-card-manifest code").forEach(function (el) {{
      hljs.highlightElement(el);
    }});
  }}
}})();
</script>
</body></html>"""


MDI = {
  "home-outline": (
    "M12 5.69L17 10.19V18H15V12H9V18H7V10.19L12 5.69M12 3L2 "
    "12H5V20H11V14H13V20H19V12H22"
  ),
  "book-multiple-outline": (
    "M19 2A2 2 0 0 1 21 4V16A2 2 0 0 1 19 18H9A2 2 0 0 1 7 16V4A2 2 0 0 1 9 2H19M19 "
    "4H16V10L13.5 7.75L11 10V4H9V16H19M3 20A2 2 0 0 0 5 22H17V20H5V6H3Z"
  ),
  "database-outline": (
    "M12 3C7.58 3 4 4.79 4 7V17C4 19.21 7.59 21 12 21S20 19.21 20 17V7C20 4.79 "
    "16.42 3 12 3M18 17C18 17.5 15.87 19 12 19S6 17.5 6 17V14.77C7.61 15.55 9.72 "
    "16 12 16S16.39 15.55 18 14.77V17M18 12.45C16.7 13.4 14.42 14 12 14C9.58 14 "
    "7.3 13.4 6 12.45V9.64C7.47 10.47 9.61 11 12 11C14.39 11 16.53 10.47 18 "
    "9.64V12.45M12 9C8.13 9 6 7.5 6 7S8.13 5 12 5C15.87 5 18 6.5 18 7S15.87 9 12 9Z"
  ),
  "camera-outline": (
    "M20,4H16.83L15,2H9L7.17,4H4A2,2 0 0,0 2,6V18A2,2 0 0,0 4,20H20A2,2 0 0,0 "
    "22,18V6A2,2 0 0,0 20,4M20,18H4V6H8.05L9.88,4H14.12L15.95,6H20V18M12,7A5,5 0 "
    "0,0 7,12A5,5 0 0,0 12,17A5,5 0 0,0 17,12A5,5 0 0,0 12,7M12,15A3,3 0 0,1 "
    "9,12A3,3 0 0,1 12,9A3,3 0 0,1 15,12A3,3 0 0,1 12,15Z"
  ),
  "network-outline": (
    "M15,20A1,1 0 0,0 14,19H13V17H17A2,2 0 0,0 19,15V5A2,2 0 0,0 17,3H7A2,2 0 "
    "0,0 5,5V15A2,2 0 0,0 7,17H11V19H10A1,1 0 0,0 9,20H2V22H9A1,1 0 0,0 "
    "10,23H14A1,1 0 0,0 15,22H22V20H15M7,15V5H17V15H7Z"
  ),
  "file-document-multiple-outline": (
    "M16 0H8C6.9 0 6 .9 6 2V18C6 19.1 6.9 20 8 20H20C21.1 20 22 19.1 22 18V6L16 "
    "0M20 18H8V2H15V7H20V18M4 4V22H20V24H4C2.9 24 2 23.1 2 22V4H4M10 10V12H18V10H10"
    "M10 14V16H15V14H10Z"
  ),
  "camera-plus-outline": (
    "M21 6H17.8L16 4H10V6H15.1L17 8H21V20H5V11H3V20C3 21.1 3.9 22 5 22H21C22.1 "
    "22 23 21.1 23 20V8C23 6.9 22.1 6 21 6M8 14C8 18.45 13.39 20.69 16.54 "
    "17.54C19.69 14.39 17.45 9 13 9C10.24 9 8 11.24 8 14M13 11C14.64 11.05 15.95 "
    "12.36 16 14C15.95 15.64 14.64 16.95 13 17C11.36 16.95 10.05 15.64 10 "
    "14C10.05 12.36 11.36 11.05 13 11M5 6H8V4H5V1H3V4H0V6H3V9H5"
  ),
  "camera-retake-outline": (
    "M20,5H16.83L15,3H9L7.17,5H4A2,2 0 0,0 2,7V19A2,2 0 0,0 4,21H20A2,2 0 0,0 "
    "22,19V7A2,2 0 0,0 20,5M20,19H4V7H8.05L9.88,5H14.12L16,7H20V19M12,18C10.92,18 "
    "9.86,17.65 9,17L10.44,15.56C10.91,15.85 11.45,16 12,16A3,3 0 0,0 15,13A3,3 0 "
    "0,0 12,10C10.74,10 9.6,10.8 9.18,12H11L8,15L5,12H7.1C7.65,9.29 10.29,7.55 "
    "13,8.1C15.7,8.65 17.45,11.29 16.9,14C16.42,16.33 14.38,18 12,18Z"
  ),
  "palette-outline": (
    "M12,22A10,10 0 0,1 2,12A10,10 0 0,1 12,2C17.5,2 22,6 22,11A6,6 0 0,1 "
    "16,17H14.2C13.9,17 13.7,17.2 13.7,17.5C13.7,17.6 13.8,17.7 13.8,17.8C14.2,"
    "18.3 14.4,18.9 14.4,19.5C14.5,20.9 13.4,22 12,22M12,4A8,8 0 0,0 4,12A8,8 0 "
    "0,0 12,20C12.3,20 12.5,19.8 12.5,19.5C12.5,19.3 12.4,19.2 12.4,19.1C12,18.6 "
    "11.8,18.1 11.8,17.5C11.8,16.1 12.9,15 14.3,15H16A4,4 0 0,0 20,11C20,7.1 "
    "16.4,4 12,4M6.5,10C7.3,10 8,10.7 8,11.5C8,12.3 7.3,13 6.5,13C5.7,13 5,12.3 "
    "5,11.5C5,10.7 5.7,10 6.5,10M9.5,6C10.3,6 11,6.7 11,7.5C11,8.3 10.3,9 9.5,"
    "9C8.7,9 8,8.3 8,7.5C8,6.7 8.7,6 9.5,6M14.5,6C15.3,6 16,6.7 16,7.5C16,8.3 "
    "15.3,9 14.5,9C13.7,9 13,8.3 13,7.5C13,6.7 13.7,6 14.5,6M17.5,10C18.3,10 "
    "19,10.7 19,11.5C19,12.3 18.3,13 17.5,13C16.7,13 16,12.3 16,11.5C16,10.7 "
    "16.7,10 17.5,10Z"
  ),
  "play": "M8,5.14V19.14L19,12.14L8,5.14Z",
  "stop": "M18,18H6V6H18V18Z",
  "refresh": (
    "M17.65,6.35C16.2,4.9 14.21,4 12,4A8,8 0 0,0 4,12A8,8 0 0,0 12,20C15.73,20 "
    "18.84,17.45 19.73,14H17.65C16.83,16.33 14.61,18 12,18A6,6 0 0,1 6,12A6,6 0 "
    "0,1 12,6C13.66,6 15.14,6.69 16.22,7.78L13,11H20V4L17.65,6.35Z"
  ),
  "database-export": (
    "M12,3C7.58,3 4,4.79 4,7C4,9.21 7.58,11 12,11C12.5,11 13,10.97 13.5,10.92V9.5"
    "H16.39L15.39,8.5L18.9,5C17.5,3.8 14.94,3 12,3M18.92,7.08L17.5,8.5L20,11H15V13"
    "H20L17.5,15.5L18.92,16.92L23.84,12M4,9V12C4,14.21 7.58,16 12,16C13.17,16 "
    "14.26,15.85 15.25,15.63L16.38,14.5H13.5V12.92C13,12.97 12.5,13 12,13C7.58,13 "
    "4,11.21 4,9M4,14V17C4,19.21 7.58,21 12,21C14.94,21 17.5,20.2 18.9,19L17,17.1"
    "C15.61,17.66 13.9,18 12,18C7.58,18 4,16.21 4,14Z"
  ),
  "delete-outline": (
    "M6,19A2,2 0 0,0 8,21H16A2,2 0 0,0 18,19V7H6V19M8,9H16V19H8V9M15.5,4L14.5,3"
    "H9.5L8.5,4H5V6H19V4H15.5Z"
  ),
  "plus-box-outline": (
    "M19,19V5H5V19H19M19,3A2,2 0 0,1 21,5V19A2,2 0 0,1 19,21H5A2,2 0 0,1 3,19V5"
    "C3,3.89 3.9,3 5,3H19M11,7H13V11H17V13H13V17H11V13H7V11H11V7Z"
  ),
  "text-box-outline": (
    "M5,3C3.89,3 3,3.89 3,5V19C3,20.11 3.89,21 5,21H19C20.11,21 21,20.11 21,19V5C21"
    ",3.89 20.11,3 19,3H5M5,5H19V19H5V5M7,7V9H17V7H7M7,11V13H17V11H7M7,15V17H14V15H"
    "7Z"
  ),
  "reload": (
    "M2 12C2 16.97 6.03 21 11 21C13.39 21 15.68 20.06 17.4 18.4L15.9 16.9C14.63 18."
    "25 12.86 19 11 19C4.76 19 1.64 11.46 6.05 7.05C10.46 2.64 18 5.77 18 12H15L19 "
    "16H19.1L23 12H20C20 7.03 15.97 3 11 3C6.03 3 2 7.03 2 12Z"
  ),
}


def mdi(name):
  path = MDI.get(name)
  if path is None:
    raise ValueError(f"unknown icon {name!r}")
  return (
    f'<svg class="mdi" viewBox="0 0 24 24" aria-hidden="true"><path d="{path}"/></svg>'
  )


def icon_button(label, icon, *, submit=False, danger=False):
  kind = "submit" if submit else "button"
  klass = "icon danger" if danger else "icon"
  return (
    f'<button type="{kind}" class="{klass}" title="{esc(label)}" '
    f'aria-label="{esc(label)}">{mdi(icon)}</button>'
  )


def job_button(
  label,
  verb="",
  *,
  title,
  desc="",
  args=None,
  fields=(),
  choices=(),
  enabled=True,
  autorun=False,
  done="",
  icon="",
  danger=False,
):
  """A button that opens the job modal. See `job_modal` for the attributes.

  `choices` offers several verbs behind one button, each with its own
  wording; the operator picks before Run is live. `icon` is an MDI name;
  `label` is then the hover tooltip rather than the button text.
  """
  extra = "" if enabled else " disabled"
  landing = f' data-done="{esc(done)}"' if done else ""
  if icon:
    klass = "job-open icon"
    tip = f' title="{esc(label)}" aria-label="{esc(label)}"'
    content = mdi(icon)
  else:
    klass = "job-open"
    tip = ""
    content = esc(label)
  if danger:
    klass += " danger"
  return (
    f'<button type="button" class="{klass}"{extra}{tip}'
    f' data-verb="{esc(verb)}" data-title="{esc(title)}" data-desc="{esc(desc)}"'
    f" data-args='{esc(json.dumps(args or {}))}'"
    f" data-fields='{esc(json.dumps(list(fields)))}'"
    f" data-choices='{esc(json.dumps(list(choices)))}'"
    f"{landing}{' data-autorun=1' if autorun else ''}>{content}</button>"
  )


def log_button(label, log, status, *, title, klass="link"):
  """A button that opens a finished run in the job modal, read-only."""
  return (
    f'<button type="button" class="job-open {esc(klass)}"'
    f' data-title="{esc(title)}" data-log="{esc(log)}"'
    f' data-status="{esc(status)}">{esc(label)}</button>'
  )


def job_modal():
  """The dialog every job verb runs through: describe, Run, tail the log.

  One per page. Buttons opt in with class `job-open` and carry the verb, the
  wording, fixed args, and any fields the operator fills in.
  """
  return (
    '<div id="job-shade" class="shade" hidden>'
    '<div class="job-modal" role="dialog" aria-modal="true" aria-labelledby="job-title">'
    '<div class="row between">'
    '<h2 id="job-title"></h2>'
    '<button type="button" id="job-dismiss" hidden>Close</button>'
    "</div>"
    '<p class="muted" id="job-desc"></p>'
    '<div class="job-choices" id="job-choices" hidden></div>'
    '<div class="row job-fields" id="job-fields"></div>'
    '<div class="row job-bar" id="job-bar">'
    '<button type="button" id="job-go">ok</button>'
    '<button type="button" id="job-close">cancel</button>'
    "</div>"
    '<pre id="job-out" class="job-out" hidden></pre>'
    "</div></div>" + _JOB_SCRIPT
  )


_JOB_SCRIPT = """
<script>
(function () {
  var shade = document.getElementById("job-shade");
  if (!shade) return;
  var titleEl = document.getElementById("job-title");
  var descEl = document.getElementById("job-desc");
  var fieldsEl = document.getElementById("job-fields");
  var choicesEl = document.getElementById("job-choices");
  var outEl = document.getElementById("job-out");
  var bar = document.getElementById("job-bar");
  var go = document.getElementById("job-go");
  var close = document.getElementById("job-close");
  var dismiss = document.getElementById("job-dismiss");
  var timer = null, jobId = null, verb = null, fixed = {}, ran = false, done = null;
  var choices = [];

  function stopPoll() { if (timer) { clearInterval(timer); timer = null; } }

  function hide() {
    stopPoll();
    shade.hidden = true;
    // The page behind is stale once a job has run: its status, config and
    // last-action all moved. Come back with the job id so the page can say
    // how it ended.
    if (ran) {
      // Status, config and last-action all moved; the page behind is stale.
      window.location.href = done || window.location.href;
    }
  }

  function showText(text) {
    outEl.hidden = false;
    outEl.textContent = text || "";
    outEl.scrollTop = outEl.scrollHeight;
  }

  function pullLog(log) {
    if (!log) return Promise.resolve();
    return fetch("/activity/" + encodeURIComponent(log)).then(function (r) {
      if (!r.ok) return;
      return r.json().then(function (body) {
        if (body && body.text != null) showText(body.text);
      });
    }).catch(function () {});
  }

  function poll() {
    if (!jobId) return;
    fetch("/jobs/" + encodeURIComponent(jobId)).then(function (r) {
      return r.json();
    }).then(function (job) {
      var done = job.state === "done" || job.state === "failed";
      return pullLog(job.log).then(function () {
        if (!done) return;
        stopPoll();
        if (!job.log && job.error) showText(job.error);
        outEl.classList.add(job.state === "done" ? "ok" : "bad");
      });
    }).catch(function () {});
  }

  function chosen() {
    if (!choices.length) return { verb: verb, args: fixed };
    var picked = choicesEl.querySelector("input[name=job-choice]:checked");
    return choices[picked ? Number(picked.value) : 0];
  }

  function submit() {
    var pick = chosen();
    var args = {};
    Object.keys(pick.args || {}).forEach(function (k) { args[k] = pick.args[k]; });
    fieldsEl.querySelectorAll("input").forEach(function (input) {
      if (input.value.trim()) args[input.name] = input.value;
    });
    ran = true;
    fieldsEl.querySelectorAll("input").forEach(function (i) { i.disabled = true; });
    choicesEl.querySelectorAll("input").forEach(function (i) { i.disabled = true; });
    // Nothing left to cancel, so the bar goes and Close moves up beside the
    // title, clear of the output.
    bar.hidden = true;
    dismiss.hidden = false;
    showText("queued\u2026");
    fetch("/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ verb: pick.verb, args: args })
    }).then(function (r) {
      return r.json().then(function (body) { return { ok: r.ok, body: body }; });
    }).then(function (res) {
      if (!res.ok) { showText(res.body.error || "failed"); return; }
      jobId = res.body.id;
      timer = setInterval(poll, 1000);
      poll();
    }).catch(function (err) { showText(String(err)); });
  }

  document.querySelectorAll(".job-open").forEach(function (btn) {
    btn.addEventListener("click", function () {
      stopPoll();
      jobId = null; ran = false;
      var log = btn.getAttribute("data-log");
      verb = btn.getAttribute("data-verb");
      fixed = JSON.parse(btn.getAttribute("data-args") || "{}");
      done = btn.getAttribute("data-done");
      titleEl.textContent = btn.getAttribute("data-title") || verb;
      descEl.textContent = btn.getAttribute("data-desc") || "";
      descEl.hidden = !descEl.textContent;
      fieldsEl.innerHTML = "";
      (JSON.parse(btn.getAttribute("data-fields") || "[]")).forEach(function (f) {
        var input = document.createElement("input");
        input.name = f.name;
        input.placeholder = f.placeholder || f.name;
        input.className = "grow";
        input.autocomplete = "off";
        fieldsEl.appendChild(input);
      });
      fieldsEl.hidden = fieldsEl.children.length === 0;
      choices = JSON.parse(btn.getAttribute("data-choices") || "[]");
      choicesEl.innerHTML = "";
      choices.forEach(function (c, i) {
        var label = document.createElement("label");
        label.className = "job-choice";
        var radio = document.createElement("input");
        radio.type = "radio";
        radio.name = "job-choice";
        radio.value = String(i);
        if (i === 0) radio.checked = true;
        var text = document.createElement("span");
        text.innerHTML = "";
        var strong = document.createElement("b");
        strong.textContent = c.label;
        var note = document.createElement("span");
        note.className = "sub";
        note.textContent = c.desc || "";
        text.appendChild(strong);
        text.appendChild(note);
        label.appendChild(radio);
        label.appendChild(text);
        choicesEl.appendChild(label);
      });
      choicesEl.hidden = choices.length === 0;
      outEl.textContent = "";
      outEl.classList.remove("ok", "bad");
      outEl.hidden = true;
      bar.hidden = false;
      go.hidden = false;
      go.disabled = false;
      dismiss.hidden = true;
      shade.hidden = false;
      if (log) {
        bar.hidden = true;
        dismiss.hidden = false;
        outEl.classList.add(btn.getAttribute("data-status") === "ok" ? "ok" : "bad");
        pullLog(log);
        return;
      }
      if (btn.getAttribute("data-autorun")) {
        submit();
      } else if (fieldsEl.children.length) {
        fieldsEl.querySelector("input").focus();
      } else {
        go.focus();
      }
    });
  });

  go.addEventListener("click", submit);
  close.addEventListener("click", hide);
  dismiss.addEventListener("click", hide);
  shade.addEventListener("click", function (event) {
    if (event.target === shade) hide();
  });
})();
</script>
"""


def confirm_modal():
  """The gate on any form carrying `data-confirm`. One per page."""
  return (
    '<div id="ask-shade" class="shade" hidden>'
    '<div class="job-modal" role="dialog" aria-modal="true" aria-labelledby="ask-title">'
    '<h2 id="ask-title">Are you sure?</h2>'
    '<p class="muted" id="ask-text"></p>'
    '<div class="row job-bar">'
    '<button type="button" id="ask-go">Yes, continue</button>'
    '<button type="button" id="ask-close">Cancel</button>'
    "</div></div></div>" + _ASK_SCRIPT
  )


_ASK_SCRIPT = """
<script>
(function () {
  var shade = document.getElementById("ask-shade");
  if (!shade) return;
  var textEl = document.getElementById("ask-text");
  var go = document.getElementById("ask-go");
  var close = document.getElementById("ask-close");
  var pending = null;

  function hide() { shade.hidden = true; pending = null; }

  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      if (form.dataset.confirmed) return;
      event.preventDefault();
      pending = form;
      textEl.textContent = form.getAttribute("data-confirm");
      shade.hidden = false;
      go.focus();
    });
  });

  go.addEventListener("click", function () {
    if (!pending) return;
    pending.dataset.confirmed = "1";
    pending.submit();
    hide();
  });
  close.addEventListener("click", hide);
  shade.addEventListener("click", function (e) { if (e.target === shade) hide(); });
})();
</script>
"""


def error_card(message):
  return (
    f'<div class="error"><h2>Cannot reach kelsod</h2>'
    f"<p>{esc(message)}</p>"
    f'<p class="muted">The socket is bound with '
    f"<code>kelso config kelso-ui --bind conn=&lt;host_volume&gt;</code>.</p></div>"
  )


def kv_table(pairs):
  rows = "".join(
    f'<tr><td class="key">{esc(k)}</td><td class="muted path">{esc(v)}</td></tr>'
    for k, v in pairs
  )
  return f'<div class="scroll"><table class="kv"><tbody>{rows}</tbody></table></div>'


RATE_LIMITED = "Rate limiter hit - something is hitting this page too often."


def _solo(title, sub, body):
  """A page with no nav: the frame, the rule under the wordmark, and one thing."""
  return f"""{_head(title)}
<div class="app solo">
<div class="solo-box">
  <div class="head"><h1>Kelso</h1><p class="head-sub">{esc(sub)}</p></div>
  {body}
</div>
</div>
</body></html>"""


def signin_page(next_to, error=""):
  """The password prompt. One field, and the one control you came for."""
  notice = f'<div class="error"><p>{esc(error)}</p></div>' if error else ""
  return _solo(
    "Sign in",
    "sign in",
    f"""{notice}
  <form method="post" action="/login">
    <input type="hidden" name="next" value="{esc(next_to)}">
    <label for="pw">Admin password</label>
    <input id="pw" type="password" name="password" placeholder="password or token"
      autocomplete="current-password" autofocus required>
    <button type="submit" class="signin-go">Sign in</button>
  </form>
  <p class="solo-hint">Set or reset it with
    <code>kelso config kelso-ui --set admin_pass=&lt;password&gt;</code></p>""",
  )


def limited_page(hint, command=""):
  """What a full bucket serves. No reload button: that is more of the problem."""
  fix = f"<br><code>{esc(command)}</code>" if command else ""
  return _solo(
    "Rate limited",
    "rate limited",
    f"""<div class="notice contested"><p>{esc(RATE_LIMITED)}</p></div>
  <p class="solo-hint">{esc(hint)}{fix}</p>""",
  )
