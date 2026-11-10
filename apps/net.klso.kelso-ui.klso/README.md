# Kelso UI

The kelso web interface, as a Windows 2000 style desktop. `/` is the desktop,
a taskbar and a start menu. Every loaded app and every reachable route is an
icon, and so is each section. Double click opens a window, which is an iframe
of an ordinary server-rendered page, so nothing about a page changes to live in
one. Right-click an app for its logs and shell.

| Kelso | On the desktop |
|---|---|
| A loaded app | An icon; its badge is green, amber or grey for healthy, troubled or stopped |
| An unloaded app | No icon. It is in Repos, with its data kept |
| A route | A shortcut opening the app in a sandboxed window, or a new tab where it cannot |
| Host, Repos, Volumes, Backups, Routes, Cron, Activity | Icons and start menu entries |
| Logs | An icon listing apps; also an app's toolbar and right-click |
| The host shell | The Shell icon; a container's shell is in its app's toolbar and right-click |
| A job | A dialog inside the window that started it |
| Sign out | The start menu's foot |
| kelsod reachable | The tray's dot |
| Which machine this is | The desktop's colours: drawn from the install's id, or picked in Settings |

Some apps refuse to be framed (`X-Frame-Options`), which a page cannot detect,
so a route window always offers Open in new tab. A browser will not show http
inside https, so an http route under this https page goes straight to a tab.

The interface reads kelso state over the admin socket that
`kelsod` publishes and renders it server-side — no polling, no client
framework, no build step. The `ui/` directory is the application. `start.sh`
runs `uv sync` into a `temp` volume at `/venv`, writes a self-signed
certificate if there isn't one, then serves https with uvicorn. The venv
survives container recreate; uv rebuilds it if the image Python no longer
matches.

| Path | What it is |
|---|---|
| `start.sh` | Container entrypoint: `uv sync`, certificate, then uvicorn |
| `server.py` | Assembles the app: front door, feature routers, static files |
| `frontdoor.py` | Rate limits, the origin check, the session gate, security headers |
| `web.py` | Rendering: the Jinja environment, `Page`, redirects, form fields |
| `pages/` | One router per feature: its pages, form posts and JSON |
| `templates/` | All markup. `desktop.html` is the desktop, `frame.html` what a window shows, `macros.html` the components |
| `static/js/desktop.js` | Windows, icons, the taskbar and start menu |
| `static/kelso.css` | How pages are laid out; no colours of its own |
| `static/classic.css` | The desktop, the type, and kelso's components as classic controls |
| `static/icons/` | Original artwork, drawn for this app |
| `static/theme.css` | The colour tokens, and nothing else |
| `settings.py` | The desktop's own settings (its background), kept in the `settings` volume |
| `static/js/` | One script per behaviour, loaded by the pages that need it |
| `static/vendor/` | Third-party browser code, pinned and served from here |
| `icons.py` | The Material Design icon paths the templates draw |
| `api.py` | Client for kelsod (unix socket or TCP) |
| `auth.py` | The password, the signed session cookie |
| `tls.py` | The self-signed certificate, written once |
| `pyproject.toml` / `uv.lock` | Dependencies, frozen for `uv sync --frozen` |
| `../tests/` | pytest against a fake kelsod; not shipped in the container |

## Setup

It needs `kelsod` running on the host. Its manifest asks for a `kelso.admin`
connection, so kelso mounts the admin socket in and tells the app where it is;
there is nothing to bind:

```
kelso start kelso-ui --set admin_pass=<password>
```

On a host whose bind mounts cannot carry a unix socket — Docker Desktop on
macOS, where the socket is visible in the container and unusable — run
`kelsod --port N` and point the app at it over TCP instead. Docker Desktop
reaches the host's loopback, so kelsod stays off the network:

```
kelso config kelso-ui --set api_address=host.docker.internal:N
kelso load kelso-ui
```

## Signing in

One password, no accounts. It is the `admin_pass` config value, so it is set
and reset the way every other kelso secret is. The first time, pass it to
`kelso start` as above. To change it later, set it and reload, which restarts
the app with the new value:

```
kelso config kelso-ui --set admin_pass=<password>
kelso load kelso-ui
```

The app will not start until it has one. Signing in mints a cookie signed
with a key derived from the password and good for seven days, which means
resetting the password signs every browser out — and is the only way to,
since nothing about a session is stored server-side. There is nowhere to
change the password from inside the UI, on purpose: the shell you administer
kelso from is the one place that can.

## Rate limiting

Two fixed-window buckets. A window opens on the first request after an idle
gap; what fits in it, fits, and the rest of that window is refused.

| Bucket | Allowed | Window | Applies to |
|---|---|---|---|
| general | 10 | 1 second | everything |
| auth | 10 | 1 hour | `POST /login`, on top of the general one |

Ten a second absorbs a burst — a cold page load is four requests in the same
millisecond — while capping the sustained rate. Ten an hour is 240 sign-in
attempts a day, which is not a dictionary attack. A sign-in spends from both,
so a flood exhausts itself against the per-second budget before it can eat far
into the hour's guesses.

`/static` is outside both: a refused stylesheet renders a page that looks
broken rather than one that says it is limited.

One budget for the whole app, not one per client: behind a reverse proxy every
request arrives from the proxy's address, so per-client counting would be
counting one client. That means anyone who can reach this app can bring it to
a halt, and anyone who can reach the sign-in form can spend the hour's
attempts — the accepted trade for a LAN interface. Sessions already issued are
unaffected, and `kelso load kelso-ui` clears both counts. A refusal does
not move the window, so service resumes when the flooding stops rather than
staying shut.

Over the limit, a browser gets a page saying so and the job modal's `fetch`
gets the same sentence as JSON, both with an honest `Retry-After`. Which one
you get is decided by the `Accept` header, not the method — the sign-in form
is a POST too.

## Trust

Anything that can open the admin socket can run every verb the API exposes, so
this app is exactly as privileged as `kelsod` is. It is the one app that
should be bound to it, which is why it serves TLS itself rather than trusting
whatever is in front of it — the password is never on the wire in the clear,
even between a reverse proxy and this container.

Everything a page needs comes from this server, since kelso may run on a
network with no internet at all. The Content-Security-Policy says as much:
scripts only from `/static`, never inline, and only this server's own pages may
frame it. A script that got into a page some other way would not run. It does
allow this page to frame any http(s) URL, since a route window opens an app
wherever its route points. That iframe is sandboxed, so the app inside cannot
navigate the desktop around it.

The session cookie is `SameSite=Lax`, which stops other *sites* from sending
it, but kelso publishes every app on a subdomain beside this one and sibling
subdomains count as the same site. So every request that changes something is
also checked against its `Origin` header (or `Referer`), and refused with a 403
unless it names this server. A proxy that rewrites `Host` is fine as long as it
passes `X-Forwarded-Host`.

The certificate is self-signed and written into the `tls` data volume on first
start, so a browser warns once and the fingerprint holds across restarts. The
route declares `scheme = "https"`, which is what tells a proxy to dial the
container over TLS; nginx-proxy-manager accepts a self-signed upstream as it
is, and a Traefik-backed provider needs its `serversTransport` set to skip
verification. Removing the volume regenerates the pair on the next start.
