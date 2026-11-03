# Kelso UI 2000

A desktop over the same pages as kelso-ui. `/` is `templates/desktop.html` and
`static/js/desktop.js`; every other page is an ordinary page shown in a window's
iframe, and does not know it. A new section is a page plus a `NAV` entry in
`web.py` and an icon in `static/icons/`.

**Kelso's words stay.** The desktop borrows Windows 2000's shapes, not its names: Host,
not My Computer. A new metaphor has to name something kelso already has.

**Pages frame only each other,** and a window may frame any http(s) route, so
`frontdoor.py` allows both and `desktop.js` sandboxes the route. A page opened
outside a window works as a plain page; only route windows offer a tab of their own.

Server-rendered HTML over the kelsod admin socket. No client framework, no
build step. Read the root `AGENTS.md` first — everything there applies.

## Where things go

**No HTML in Python.** Markup lives in `ui/templates/`, which Jinja
autoescapes; Python gathers data and names a template. A page is a router in
`ui/pages/<feature>.py` whose GET handlers take `page: PageDep` and return
`page.render("pages/<name>.html", title, **data)`, and whose POST handlers
return `see(path, ok=..., err=...)`. An `ApiError` needs no handling in a GET:
it renders as the error page inside the frame. Set `page.title` before the
first `api()` call if the nav label is the wrong name for that error page.

**Components are macros.** `templates/macros.html` holds `job_button`,
`icon_button`, `pill` and the rest; `templates/configform.html` holds the
config form. Reach for one before writing the markup again, and add one when
a second page needs the same thing.

**No inline script.** The Content-Security-Policy refuses it. Behaviour goes in
`ui/static/js/<name>.js`, one file per behaviour, hooked to elements by class
or `data-*` attribute, and a page loads it from its `scripts` block with
`asset('js/<name>.js')`. Third-party code is vendored under `static/vendor/`
at a pinned version, never loaded from a CDN: kelso-ui must work on a host
with no internet access. Data a script needs goes in a
`<script type="application/json">` block, which the policy allows. The
console page is the one exception on styles: xterm.js writes its layout into
`<style>` elements, so that page alone sends `TERMINAL_CSP`, which allows
inline styles and never inline script.

**A websocket route calls `frontdoor.socket_refusal` first.** The front door
is HTTP middleware and never sees a websocket, so the route has to ask for the
rate limit, origin and session checks itself.

**Tests** are in `../tests`, against a fake kelsod: `cd ui && uv run pytest`.
They plant hostile strings in every fixture field, so a missing escape
fails a test. A new page goes in `PAGES` in `test_pages.py`, and a new
kelsod endpoint gets a fixture in `fakekelsod.py`.

## The visual language

98.css (`static/vendor/`) draws windows and controls; `static/classic.css`
turns them into Windows 2000 (the gradient title bar) set in IBM Plex Sans, and draws
the desktop. Reach for 98.css's classes and elements first: a page's sections
are `fieldset.group` with a `legend`. Type is IBM Plex Sans at 13px in its Text weight (450), and 600 for anything bold; IBM Plex Mono, also Text, at 12px.
`static/kelso.css` lays pages out from the tokens in `static/theme.css` and
holds no colour of its own; a property classic.css sets is not set there too,
and 98.css's bevels are classic.css's `--raised`, `--pressed`, `--sunken`,
`--frame` and `--etched`. A surface's one committing button is 98.css's
`default`. No per-page `<style>`, no inline `style=`: scripts set only
positions, sizes and custom properties, through the CSSOM.

**Color carries meaning.** `--coral` is the accent, highlight navy here.
`--gold` wants attention, `--rosewood` destroys, `--ok` is healthy. The token
names are kelso-ui's, kept so its pages need no changes.

**Artwork is ours.** Icons are drawn for this app. No Microsoft
assets.

**A window's verbs are its command bar.** A page fills `commands` with the
`command` macro, or `job_button(..., bar=true)` for a verb that runs a job.
Something that needs a form opens as a dialog (`command(..., dialog=true)` or
`data-dialog` on a link); its POST answers `see("/done", ok=..., then=...)` and
the desktop closes it and refreshes its opener. Cancel is `data-close`. Groups
of settings that are not read together are tabs (`menu[role=tablist]`, js/tabs.js).

**Row actions are icons.** Where exactly one of a pair can apply, draw that one. Label an action
for what it is about to do. Mono is for identifiers, sizes and paths; prose
never is.

## Working on it

Run it against your real kelso rather than guessing — it takes two commands
and it is the only way to see whether a change landed:

```
KELSO_ROOT=$HOME/kelso uv run kelsod --port 9797 --host 127.0.0.1
cd apps/net.klso.kelso-ui-2k.klso/ui && KELSO_API=127.0.0.1:9797 KELSO_UI_NO_AUTH=1 \
  uv run uvicorn server:app --port 9798 --reload
```

`KELSO_UI_NO_AUTH=1` signs every request in, so an agent can drive the UI
without typing a password. Without it, `ADMIN_PASSWORD` is required —
`auth.py` refuses to import without one.
Plain http is fine here: the session cookie only asks to be `Secure` when the
request that minted it arrived over TLS, which in the container it does.

Run the tests, then look at the desktop and every window you touched: drag,
resize, minimize, maximize, the start menu, right-click, one job dialog, and
phone width, where every window is maximized. Layout regressions here are
invisible in a diff and obvious on screen.
