# The manifest

Every bundle is a `manifest.toml` plus, optionally, the files it ships with. The
manifest is the whole of what kelso knows about an app: what containers to
run, what storage they need, what the operator has to fill in, and what the
outside world can reach. Everything else — the compose file, the run
directory, the volume links — is generated from it.

This is the reference. For a manifest built from nothing in one sitting, read
the [case study](case_study.md).

A manifest is TOML, and unknown sections and keys are refused rather than
ignored, so a typo is an error at load time instead of a setting that
silently did nothing.

---

## `[app]`

The only required section. Keys kelso does not know are refused.

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `version` | string | **required** | The bundle's version. Yours, not the image's. |
| `app_id` | string | from the filename | Refused if it disagrees with the filename. |
| `display_name` | string | `""` | Shown in the UI and `kelso ps` instead of the id. |
| `description` | string | `""` | One line. Shown in the catalog. |
| `author` | string | `""` | Who made the bundle, e.g. `"Jordan Parker <jordan@parker.sh>"`. Shown in `kelso inspect` and the web UI. |
| `url` | string | `""` | Where to read more. Must start with `https://` or `http://`; the web UI links to it. |
| `main` | identifier | `"main"` | Which `[run]` unit is the app itself. Must exist. |
| `network_mode` | `normal` \| `host` | `normal` | `host` drops port isolation and is called out as dangerous. |

```toml
[app]
version      = "1.4.0"
display_name = "Mealie"
description  = "Manage, save, share recipes and make shopping lists"
```

## `[run.<unit>]`

One container each. A single-container app declares just `[run.main]`; units
reach each other by unit name as hostname, on a private network kelso
creates.

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `image` | string | **required** | Pin a tag. `latest` makes a bundle unreproducible. |
| `cmd` | list of strings | image default | Overrides the image's command. |
| `volumes` | `{ <volume> = "<path in container>" }` | `{}` | Every name must be declared in `[volumes]`. Nothing may go under `/kelso`, which is kelso's. |
| `env` | `{ KEY = "value" }` | `{}` | `${…}` placeholders are substituted; see below. |
| `routes` | table of `[run.<unit>.routes.<name>]` | `{}` | Ports the outside world may reach. |
| `restart` | `"yes"` \| `"no"` | `"yes"` | `"yes"` restarts a container that crashes; `"no"` leaves it stopped, for one-shot commands. Neither brings it back at boot: kelsod starts apps then, in `start_order`. |
| `shell` | list of strings | `["/bin/sh", "-c"]` | How kelso runs anything in this unit: its commands and its console. It must exist in the image; without it they fail with docker's "not found". |
| `connections` | list of names | `[]` | `[connections]` this unit is given. See [`[connections.<name>]`](#connectionsname). |
| `healthcheck` | string or list of strings | the image's own | Run in the container to say it is healthy: exits 0 when it is. A list runs as it is; a string runs in `shell`. See [Healthchecks](#healthchecks). |
| `compose` | table | `{}` | The escape hatch. See [Free-form docker options](#free-form-docker-options). |

```toml
[run.main]
image   = "lscr.io/linuxserver/unifi-network-application:10.4.57"
volumes = { app_config = "/config" }
env     = { MONGO_HOST = "unifi-db", MONGO_PASS = "${mongo_pass}" }

[run.unifi-db]
image   = "docker.io/mongo:8.0.11"
volumes = { db_data = "/data/db" }
```

## `[volumes]`

What the app needs to keep, and what kind of thing it is. The operator decides
*where* each kind lives, once, by linking `volumes/<kind>` in the kelso root
somewhere else (see the README) — the manifest only says which
kind it wants. That split is the point: a bundle that says `kind = "bulk"` lands
on the big disk on a machine that has one and in the default root on a machine
that does not, with no change to the bundle.

| Kind | For |
| --- | --- |
| `data` | State the app must not lose. What gets snapshotted. |
| `bulk` | Large data — media libraries, archives. Usually a separate disk. |
| `logs` | Output that can be rotated away without loss. |
| `temp` | Caches and scratch. Safe to delete when the app is not running. |
| `app` | Files the bundle itself ships. Always mounted read-only. |
| `host` | A directory on the machine, chosen by the operator at load time. |

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `kind` | one of the above | **required** | |
| `desc` | string | `""` | Shown to the operator, and worth writing for `host` volumes. |
| `readonly` | bool | `false` | `app` volumes are always read-only; setting `readonly = false` on one is an error. |
| `src` | string | volume name | `app` volumes only: which file or directory in the bundle to mount. |

```toml
[volumes]
db_data     = { kind = "data", desc = "the recipe database" }
media       = { kind = "host", desc = "where your photo library already lives" }
init_script = { kind = "app", src = "init-mongo.sh" }
```

A `host` volume is not a path — it is a *request*. Kelso will not start the
app until the operator binds it to one of the host volumes they declared:

```
kelso config <app> --bind media=photos
```

## `[connections.<name>]`

Something outside the app that it needs to reach, which kelso provides: the
app names a kind, and kelso works out how to get it there on this machine. A
run unit is given a connection by listing it in its `connections`; kelso then
mounts what the kind needs at `/run/kelso/conn/<name>` and offers what the app
needs to know as `${conn.<name>.<property>}`, which `[run.<unit>.env]` maps
to whatever names the app reads. Kelso sets no environment of its own.

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `kind` | string | **required** | One of the kinds below. |
| `desc` | string | `""` | Shown to the operator. |

| Kind | Gives the app | Properties |
| --- | --- | --- |
| `kelso.admin` | kelsod's admin API: full control of kelso. | `socket`: the admin socket's path in the container. `address`: `host:port` of kelsod when `config.toml` sets `admin_address`, else empty. |
| `docker.admin` | The docker daemon: full control of the host. | `socket`: the docker socket's path in the container. `host`: the same as a `DOCKER_HOST` value. |

Each one hands the app control of something outside it, so kelso calls every
connection out as a danger on load and on the app's page.

```toml
[connections]
admin = { kind = "kelso.admin" }

[run.main]
image       = "..."
connections = ["admin"]

[run.main.env]
KELSO_SOCKET = "${conn.admin.socket}"
KELSO_API    = "${conn.admin.address}"
```

On Linux the socket is all an app needs. Docker Desktop on macOS cannot carry
a unix socket into a container: run `kelsod --port N` and set
`admin_address = "host.docker.internal:N"` in `config.toml`, and apps that read
`address` reach kelsod over TCP instead.

## `[config]` and `[adv_config]`

Values the operator supplies. `[adv_config]` is the same thing, marked as
noise a normal operator should not have to read. The two share one namespace,
so a name may appear in only one of them.

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `desc` | string | `""` | What this is, in the operator's words rather than the app's. |
| `default` | string | none | With no default, the app will not start until the value is set. |
| `secret` | bool | `false` | Stored encrypted, never returned by the API or shown in the UI. |

`default = "auto"` on a secret means kelso generates one at load and the
operator never sees or sets it — the right answer for a password two
containers need to agree on and nobody else needs.

```toml
[config]
admin_email = { desc = "Login for the web interface" }
timezone    = { desc = "IANA timezone", default = "UTC" }

[adv_config]
mongo_pass  = { secret = true, default = "auto" }
```

Set them with `kelso config <app> --set timezone=America/Denver`, or from the
app's page in the web UI.

### App options

Every app also has these, whether its manifest mentions them or not. They
share the config namespace and always have a default.

| Name | Default | Accepts |
| --- | --- | --- |
| `subdomain` | the app id's last part | A DNS label: letters, digits, `_` and `-`. Routes are published under it. |
| `start_order` | `6` | A whole number from 0 to 9: the group this app starts in. 0 init starts when kelsod starts and stops when it stops; `kelso up` starts 1 to 9 in order, and `kelso down` stops them in reverse. Named groups: 2 support services (databases and the like), 4 routing & connections, 6 applications, 8 lazy applications. The odd numbers are free, to fit something between two of them. |
| `snapshot_max_count` | `0` | A whole number; keep this many snapshots, 0 for all. Not used yet. |

A manifest may declare one of these names in `[config]` or `[adv_config]` to
change its default and description, or to leave the default out and make the
operator set it. The value is still checked the same way, and it cannot be a
secret.

```toml
[adv_config]
subdomain = { default = "recipes" }
```

## `[run.<unit>.routes.<name>]`

A named port the outside world may reach. The route named `main` is published
at the app's bare subdomain; every other name gets `<name>-<subdomain>`.

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `port` | string | **required** | `"8080"` (kelso picks the host port), `"8443:8443"` (pinned), or either with `/udp`. |
| `scheme` | `http` \| `https` | `http` | How a reverse proxy should *dial the container*, not what a browser sees. |
| `private` | bool | `false` | LAN-only: never handed to a route provider. |
| `desc` | string | `""` | Shown beside the URL. |

```toml
[run.main.routes]
main    = { port = "8443", scheme = "https" }
metrics = { port = "9090", private = true, desc = "Prometheus scrape target" }
```

Pin a host port only when something outside kelso already points at it — a TV
that expects `:8096`, or an app that advertises its own port. Otherwise let
kelso allocate, and collisions stop being your problem.

## `[commands.<name>]`

Operations the app declares for itself, runnable from the CLI or as a button
in the web UI. This is how a bundle ships its own maintenance: a backup, a
reindex, a password reset.

The Run button and `kelso cmd` run a command's string in its unit's `shell`,
with the operator's arguments added to the end. For someone in a console, each
unit with commands also gets `/kelso/bin/kelso_cmd`, which runs one the same
way (`kelso_cmd backup --full`) or, with no arguments, lists them. kelso's
consoles put `/kelso/bin` at the end of `PATH` and open with that list.

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `cmd` | string | **required** | Run by the unit's `shell`, with any operator arguments added to the end as they were typed. |
| `run_unit` | identifier | `"main"` | Which container to run it in. Must exist in `[run]`. |
| `desc` | string | `""` | Shown in `kelso cmd <app>` and in the UI. |

```toml
[commands.backup]
cmd      = "mealie-cli backup create"
desc     = "Write a backup into the data volume"

[commands.psql]
cmd      = "psql -U postgres"
run_unit = "database"
desc     = "Open a database shell"
```

## `[cron.<name>]`

Runs one of the app's `[commands]` on a schedule. kelsod checks every five
minutes and runs what is due, one job at a time, so a schedule is best effort
to within a few minutes and a long job delays the ones after it. A job whose
unit is not running is skipped and stays due until it is. If kelsod was down
through several scheduled times, the job runs once, not once per miss; a job
that has never run counts from when its app was loaded. Each run is recorded
on the Activity and Cron pages, with its output and whether it exited 0.
`kelso cron` lists what is next and `kelso cron tick` runs what is due now.

Jobs should generally be short (~1 minute). If you have a long-running job, consider
using kelo's cron to kick it off, but not run it directly.

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `schedule` | string | **required** | Five-field cron: minute, hour, day of month, month, day of week, in the host's local time. Each field is `*` or a comma list of `n`, `a-b`, `*/s`, `a-b/s`. Day of week is 0-7, both 0 and 7 Sunday. |
| `command` | identifier | **required** | A `[commands]` entry. |
| `args` | string | `""` | Added to the command, as an operator's arguments would be. |
| `timeout` | integer | `600` | Seconds before kelso stops waiting and records the run as failed, at most 1800. A job that needs longer belongs in a background process in the app itself. A command started with `compose exec` keeps running in its container until it finishes. |

```toml
[cron.nightly-backup]
schedule = "0 3 * * *"
command  = "backup"
args     = "--keep 7"
```

## Substitution in `env`

`${…}` in `[run.<unit>.env]` is resolved against one flat keyspace, at load
time, and a reference to something that does not exist is an error rather than
an empty string:

- `${<config key>}` — anything from `[config]` or `[adv_config]`, and the app
  options.
- `${routes.<name>}` — the full public URL of a declared route.
- `${klso.domain}`, `${klso.volumes}`, `${klso.cmd}`, `${klso.routes}` — the
  app's own resolved values.

Every unit also gets `KLSO_ID`, `KLSO_VERSION`, and `KLSO_RUN_UNIT` for free.

```toml
[run.main.env]
# Jellyfin advertises this address to clients on the network.
JELLYFIN_PublishedServerUrl = "${routes.main}"
DB_PASSWORD                 = "${mongo_pass}"
```

## Healthchecks

A unit's `healthcheck` is a command docker runs inside the container; exiting
0 means healthy. The bundle says only what to run. Kelso writes it into
compose with the timing: every 5 seconds for the first 2 minutes after a start,
so `kelso up` hears quickly, and every 60 seconds after that. Kelso never runs
the check itself; it reads what docker last recorded.

```toml
[run.db]
image       = "docker.io/mongo:8.0.11"
healthcheck = ["mongosh", "--quiet", "--eval", "db.adminCommand('ping')"]

[run.main]
image       = "nginx:alpine"
healthcheck = "wget -q -O /dev/null http://localhost:8080/ || exit 1"
```

Leave it out when the image already has a healthcheck (`docker image inspect
--format '{{json .Config.Healthcheck}}' <image>` shows it): that one applies
as the image's author wrote it.

## Free-form docker options

`[run.<unit>.compose]` is copied verbatim into that unit's compose service, for
the things kelso does not model:

```toml
[run.main.compose]
mem_limit = "256m"

[run.main.compose.ulimits.nofile]
soft = 10032
hard = 10032
```

Two rules apply.

**Keys kelso generates are refused** — `image`, `volumes`, `ports`, `labels`,
`environment`, `command`, `hostname`, `restart`, `network_mode`,
`healthcheck`. Those have
manifest fields; setting them twice would mean one of them silently losing.

**Keys kelso does not recognise are announced.** There is an allowlist of
options that shape how a container runs without reaching outside it —
`depends_on`, `mem_limit`, `user`, `ulimits`, `read_only` and
friends — and anything outside it produces a warning on load, in `kelso
inspect`, and on the app's card in the web UI:

> Warning: This application sets free-form docker options on main that are not
> guaranteed to be safe. Please review them before continuing

Nothing is refused: the machine belongs to the operator, and `privileged =
true` is a legitimate thing for a bundle to need. But kelso cannot know what an
arbitrary compose key does, so it says so and shows the operator exactly what
was asked for. This also catches typos — compose silently ignores a key it
does not know, so a misspelled `devcies` would otherwise do nothing at all
and say nothing about it. See [demo-danger](../demo-apps/demo-danger.klso.md).

## A bundle in one file

Anything above can live in a `<app_id>.klso.md` markdown file instead of a
folder, with the manifest and any scripts in fenced code blocks tagged with
their path:

````markdown
# My App

Whatever prose you like, including why the manifest looks the way it does.

```toml klso_path="manifest.toml"
[app]
version = "1.0.0"
```

```sh klso_path="start.sh:+x"
#!/bin/sh
exec my-app --config /config
```
````

The `:+x` suffix marks the extracted file executable, which a script you
intend to run as a container command needs.

One file, committable anywhere, readable as documentation by someone who has
never run kelso. See [demo-markdown](../demo-apps/demo-markdown.klso.md).

## Checking your work

```bash
kelso inspect <app>      # what the manifest declares, resolved
kelso load <app>         # parse errors, one list, with the key that caused each
```

`kelso inspect` on an unloaded app reads the manifest it *would* load
from, so it is the fastest way to see whether an edit did what you meant.
