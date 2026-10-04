# Roadmap

## Audience
This roadmap is based on the desire to become a tool that someone technical enough to install linux on an old machine, and can paste in terminal commands would reach for. We want more people to run apps on their own hardware.

Later, it should also be a real option for small businesses: a semi-technical owner with an always-on box on site, running something like Frigate and a homepage for employees.

Kubernetes and similar can be overkill, while raw Docker Compose files alone still leave missing pices of the puzzle.

## Current Roadmap

### App upgrade paths
`kelso update` is the only way an app changes version; `kelso load` refuses to.
Today `update` takes any new version. Wanted: a manifest says which versions it
can upgrade from (`upgrade_from`); without it, any version. Updating to version
6 over data last loaded at version 3, when 6 only upgrades from 4, refuses:
"Upgrade failed while attempting to upgrade to version 6: This only supports
upgrading from versions >= 4. Please upgrade to an intermediate version first."

Going back a version is only ever `restore` from a snapshot, which brings the
matching data with it. `update` refuses a version older than the data's.

Versions compare as lists of numbers: split on any of `-.|/`, compare left to
right, and a missing place counts as zero. `24-45.23|3` is `[24, 45, 23, 3]`,
and `1.2` equals `1.2.0`. A version must start with a number; what follows the
leading numbers, as in `1.0.0-beta`, is not compared.

The version these checks act on is `loaded_version` in the app's config store,
which every load records: it survives `unload`, goes with `rm --purge`, and
comes back with a snapshot on `restore`. `kelso dev` runs a working copy and
skips these checks.

A bundle may carry a `migrations.toml`: manifest-level changes to apply between
versions, ending at the current one. The app migrates its own data; these cover
what kelso holds -- renaming a config value, renaming or removing a volume. The
supported way to drop a volume: version N moves the data off it, N+1 removes it
in `migrations.toml`, and N+2 can drop that migration with `upgrade_from = N+1`,
so every install passed through the version that removed it.

### Alerts
Nothing says when an app crashes, the disk fills, or a snapshot fails; you find
out by opening the dashboard. For v1, one configurable webhook and two levels:
- **notifications**: the activity log, as it happens.
- **problems**: what `doctor` reports, an unhealthy app, a full disk, a failed
  snapshot.

Alerts queue in a logtab spool, so a webhook that is down gets them later.

### Restricted Network Mode
A mode where a sidecar hijacks dns and proxies ALL outgoing http/https requests,
allowing only those that are explicity set up as a `[connection]` in the manifest.

### Off-host snapshots
Snapshots stay on the box. Copying them elsewhere also needs a plan for
`conf/master.key`: no snapshot carries it, so a restore on another machine
cannot read its secrets.

### "Lambda Function" apps
Default state - not running, but can be started triggered on a cron or incoming http request

### First run setup wizard

### Manifest editing and validation from the webui

### Kelso config.toml editing and validation from the webui

### Services & Service Catalog
Allow bundle developers to better focus on their own app by saying "Just give me a postgres instance + login for my app" rather than adding postgres to their manifest manually. This `services` system would enable a bundle to list the services it "provides" and have other services "require" them. This feature will require a lot of thought.

### Rootless docker and podman
Kelso needs rootful docker today, and `kelso init` refuses a rootless daemon.
Both break the same assumption: `lib/lifecycle/rootfs.py` reads and deletes
volume files as the container's root, which under a user namespace maps back to
the invoking user and cannot touch what the app's containers wrote.

### Resource limits
`mem_limit` and `cpus` pass through as raw compose keys, but no app option sets
them, so one runaway app can starve the box.

## Known issues

- **An app whose loaded manifest no longer parses goes quiet.** A loaded app
  keeps the manifest it was loaded with. When a newer kelso stops accepting
  something in it, `ps` shows the app with blank columns, `inspect` says it is
  neither loaded nor in a catalog, `cmd` and `config` print a validation error,
  `kelso cron` silently drops its jobs, and `doctor` reports nothing.
  `kelso update` fails too, since its pre-update snapshot reads that manifest.
  Workaround: `kelso load <app>` from a source that parses.
- **Two kelso roots on one docker daemon see each other's apps.** Compose
  project names and the `kelso.app_id` label are not scoped to a root, so a
  second root reports the first one's containers as needing manual recovery,
  and its `cleanup` offers to remove images the other root still uses. Run one
  kelso root per docker daemon.
- **`kelso system gen-masterkey` orphans every existing secret.** It writes a
  new key and nothing re-encrypts what the old one protected: app secrets,
  route provider credentials, and cached tokens all stop decrypting. Do not run
  it on a root that holds secrets.
- **kelsod being down is invisible.** kelsod records the metrics behind volume
  sizes, resumes apps at boot, and runs cron. With it stopped, `kelso status`
  shows `0.0 B` for every volume kind, `doctor` says nothing, apps stay down
  after a reboot, and cron jobs do not run. Check it with
  `systemctl --user status kelsod`.

## Todo

- **Drop the Textual configuration mode; rethink interactive command-line
  config.** `kelso config --edit` and `kelso dev` open a full-screen Textual
  form on a terminal, which is the heaviest dependency for the least-used path.
  Remove it, and decide what interactive config at the command line should be.
- **Refuse volume names that are volume kinds.** A volume named `data`,
  `bulk`, `logs`, `temp`, `app` or `host` reads ambiguously everywhere a volume
  is shown next to its kind, and the `Data:` line `kelso start` prints picks a
  volume by the name `data`.
- **Screenshots in the README.** The web UI is a large part of using kelso and
  the README never shows it.
- **A routes guide.** Setting up each provider (Nginx Proxy Manager with its
  wildcard certificate, Pangolin, Cloudflare Tunnel), what `kelso_address` is
  for, and assigning, publishing and checking a route. Today the only
  documentation is the comments in the generated `config.toml`.
- **"What kelso does to your containers."** One section listing everything
  kelso adds beyond the manifest: the `/etc/localtime` mount, log rotation
  (10 MB x 3), `restart: on-failure` and kelsod resuming apps at boot, a private
  network per app, the `kelso.*` labels, the `KLSO_*` variables, `/kelso/bin`
  for apps with commands, and the host port range (`port_base` to
  `port_base + 1000`) to open in a firewall.
- **Doctor checks for the quiet failures** above: a loaded manifest that no
  longer parses (naming the fix), and kelsod not listening or its metrics gone
  stale.
- **Release hygiene.** A changelog or GitHub release notes for each tag; a
  platform statement in the README (Linux with systemd, amd64 and arm64, tested
  on Ubuntu Server 26.04 and Raspberry Pi OS); a GitHub description and topics
  that match the README.
