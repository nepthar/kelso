# Roadmap

## Where kelso is going

Kelso is the standard way to tell an LLM "build me this app and put it on the
internet", and have it happen on hardware you own.

The person says what they want. An agent (Claude Code, Codex, Cursor, anything
that can run a shell) writes a bundle, deploys it with kelso, and reads back
what happened. Kelso's job is to be the part the agent cannot be: a small,
reviewable format for what an app is, and a runtime that owns the state, says
exactly what went wrong and how to fix it, and can always put things back.

Two things follow from that, and they shape every item below.

- **An agent drives; a human approves.** Every command, error and document is
  written so an agent can act on it without guessing. The human reads a short
  manifest and a plan, and says yes.
- **Apps are made and shared, not shopped for.** There is no app store. A bundle
  is a file you can paste in a chat, commit anywhere, or point someone at with a
  URL. Making one should take an agent minutes, and running someone else's
  should take one command and a review.

### Who it is for

Today: someone who can install Linux on an old machine and paste a command, or
who has an agent that can. That includes people who would never write a compose
file but can describe the app they want.

Later: a small business with an always-on box on site, running something like
Frigate and an internal homepage, with a semi-technical owner and an agent doing
the work.

Not: anyone who needs failover, multi-node scheduling, or more than a few dozen
concurrent users. Kubernetes exists for that.

### Where it sits

Hosted agent-native platforms such as [Ample](https://ample.computer/) have the
same loop (an agent runs `plan`, then `up`, and reads an exit code) on someone
else's cloud. Kelso is that loop on your own machine: your data stays home, the
bundle is a file you keep, and pulling the plug is always an option. Home server
GUIs (CasaOS, Umbrel, Unraid's apps) are app stores with a human clicking. Kelso
is not competing with either on their terms.

---

## v2: an agent drives kelso

v2 is done when a person with a fresh machine and an agent can say "make me a
recipe site at recipes.example.com" and end up with a running, published,
snapshotted app, without opening a terminal themselves and without the agent
guessing at any step.

### 1. The agent contract

What an agent can rely on, on every command. This extends what kelso already
does well (refuse, and name the fix) into something an agent can parse.

- **Docs written for agents first.** `docs/agent/` holds task-shaped
  instructions an agent follows: install kelso, write a bundle, deploy and
  publish an app, update it, recover from each known failure. Each step says
  what to run, what success looks like, and what to do otherwise. A root
  `llms.txt` points at them. Human docs stay, but they are not where an agent is
  sent.
- **`--json` on every command.** The same data the human output shows, stable
  and documented, so an agent never scrapes a table.
- **Exit codes that mean something.** 0 done; 1 failed while doing it; 2 refused
  before doing anything, with the fix named. An agent knows from the code alone
  whether anything changed.
- **Problems as data.** Every refusal, `doctor` finding and start blocker is
  `{code, problem, fix}`, where `fix` is the commands that resolve it. The
  current prose messages become the human rendering of the same record.
- **Plan, then apply.** Anything that changes state can print what it will do
  without doing it (`--plan`), as text for a human and JSON for the agent. Most
  verbs already build a plan object first (`RemovalPlan`, `RestorePlan`,
  `CleanupPlan`, `DevPlan`); this exposes them. The human approves the plan, not
  a wall of compose output.
- **A manifest schema an agent can check against.** A published JSON Schema for
  `manifest.toml`, and `kelso check <bundle>` that validates without loading,
  reporting every problem at once.
- **The CLI is the interface.** An agent drives kelso over a shell, locally or
  through `ssh`. There is no MCP server in v2: a shell, `--json` and good docs
  give an agent everything MCP would, and stay usable by a human. If a client
  ever requires MCP, it is a thin wrapper over the same verbs, not a second
  interface.

### 2. Custom apps: write, run, publish

The point of kelso for its first user: an agent writes a new app and it goes
live.

- **`kelso new <app>`** scaffolds a bundle in `repos/local` from a small set of
  runtime templates: static site, Python, Node. Each is the pattern kelso-ui
  already uses: a pinned runtime image, the app's source as an `app` volume, and
  a start script. No image to build or host.
- **Build from the bundle when a runtime is not enough.** A run unit may point at
  a Dockerfile in the bundle instead of an image. The built image is tagged with
  the bundle's version, so snapshots and rollback still name an exact image.
- **The dev loop an agent can close.** `kelso dev` already runs a working copy
  with live source. It gains `--json` status and a "healthy at URL" signal, so an
  agent edits, sees it come up or fail, reads the logs, and edits again.
- **Live on the internet in one step.** `kelso publish <app>` assigns the main
  route to the default provider and starts it. With Cloudflare Tunnel (below)
  that needs no DNS, port forwarding or certificates.

### 3. Sharing

- **Run a bundle from a URL.** `kelso start https://…/app.klso.md` fetches it,
  shows the danger callouts and the plan, and asks. A single-file bundle becomes
  something you can send in a message.
- **Record where it came from.** A bundle loaded from a URL or repo records its
  source and commit, so `update` knows where to look and `inspect` can say who
  you are trusting.
- **`kelso share <app>`** writes a bundle back out as a single `.klso.md`, its
  manifest and files plus the prose an agent wrote about it, ready to commit or
  send. Secrets and config values never go with it.
- **Repos stay plain git.** A repo is still a folder on GitHub. No registry, no
  accounts, no review queue.

### 4. Updates that roll back

The guarantee: **an update either works, or the whole app is put back exactly as
it was before the update, from its pre-update snapshot.** There is no separate
update state or migration engine; snapshots are the mechanism, so they have to
be bulletproof.

- **A health gate decides "works".** After loading the new version, `update`
  starts the app and waits for every unit to be healthy (or, with no
  healthcheck, still running after the settle time). Anything else, including a
  failed load, restores the pre-update snapshot and starts the app again.
- **Snapshots that can always be restored:**
  - Refuse to start a snapshot without the disk space for it.
  - Verify the archive after writing it (it lists, and its files and checksums
    match) before the update touches anything.
  - Record the image digests the app ran, and have `cleanup` keep any image a
    kept snapshot names. Today `cleanup` removes images no *loaded* app uses,
    which can leave a rollback with nothing to roll back to.
  - Restore takes the app back to the recorded digests, not to whatever a tag
    points at now.
- **Live-tested, not just unit-tested.** Snapshot and restore of real root-owned
  data is on the release checklist (see [testing](testing.md)), and the rollback
  path is exercised on purpose with a bundle whose new version fails its
  healthcheck.
- **Version rules on top, later.** `upgrade_from` and `migrations.toml`
  (described under [Later](#later)) refuse an unsupported jump before anything
  is touched. They add refusals, not state.

### 5. Cloudflare Tunnel as the default route

Publishing an app is the hardest step for most people: DNS, certificates, port
forwarding and a reverse proxy. A Cloudflare Tunnel removes all four, the setup
is simpler than any of them, and Cloudflare keeps adding useful pieces to it.

- **One token to set it up.** `kelso route setup cloudflare` (and the same in the
  first-boot page) takes an API token, creates a remotely managed tunnel through
  the API, starts the `cloudflared` app in start group 4, and sets it as the
  default route provider. Today the operator creates the tunnel in the dashboard
  and copies three values into config.
- **Access in front of private apps.** A route can ask for Cloudflare Access, so
  an app meant for the household is published behind a login without the app
  knowing about it.
- **The others stay.** Nginx Proxy Manager and Pangolin keep working for people
  who want everything self-hosted. The routes guide (in the todo list) leads with
  Cloudflare.

### 6. Onboarding

The aim: nobody types into a terminal to install kelso. Either they flash an
image and open a page, or their agent does the install over `ssh`.

What makes sense, in order:

1. **One install script that finishes the job.** `curl … | sh` installs docker,
   uv and kelso, runs `kelso init`, and starts kelsod, with no "log out and back
   in" and no reboot. (The reboot exists only because systemd's user manager
   keeps the groups it started with; restarting `user@<uid>` or starting kelsod
   with the docker group explicitly should remove it. Verify on Ubuntu and
   Raspberry Pi OS.) This is the step an agent runs over `ssh`, and every option
   below is built from it.
2. **A cloud-init file.** The same install as `user-data`. It covers Ubuntu
   Server's autoinstall, any VPS provider's "user data" box, Proxmox and other
   hypervisors, and Raspberry Pi images that read cloud-init. Pasting text into a
   box is the closest thing to a no-terminal install that kelso can ship without
   building an OS.
3. **A VM image.** Built from the cloud-init file in CI, for amd64 and arm64:
   import it into Proxmox, TrueNAS, Unraid, UTM or VirtualBox and boot. It is
   also the easiest way to try kelso without committing a machine.
4. **A first-boot web page.** On a fresh install, kelso-ui serves a setup page at
   `http://kelso.local`: set the admin password, connect Cloudflare, pick where
   bulk storage lives. This is the existing "first run setup wizard" item, and it
   is what makes options 2 and 3 terminal-free.

What not to do:

- **A container with kelso inside (Docker-in-Docker with nesting).** It needs a
  privileged container, which is root on the host anyway, and it breaks what
  kelso is built on: volumes that are ordinary host paths, snapshots that read
  them as root, and kelsod starting apps at boot. A Proxmox LXC with nesting
  enabled is different. It is a normal Linux with systemd, so the install script
  and cloud-init file cover it, and the docs should say so.
- **A custom Ubuntu ISO.** A distribution to maintain, for nothing the
  cloud-init file does not already give.

Later, and only if people ask: **a hosted option**, a VPS with kelso
preinstalled and the first-boot page as its only management surface. It is the
one way to need no hardware at all, and it is also the furthest from "your
hardware". Worth revisiting once the rest works.

### 7. Hearing about problems

An agent cannot fix what nothing reports. Today you find out by opening the
dashboard.

- **`kelso problems --json`.** One feed of everything wrong now: `doctor`
  findings, unhealthy apps, a full disk, a failed snapshot or update, kelsod not
  running. Each item uses the same `{code, problem, fix}` shape, so an agent can
  be pointed at the box and told "keep it healthy".
- **A webhook.** One configurable URL, sent each new problem and, optionally,
  the activity log as it happens. Undelivered messages queue in a logtab spool,
  so a webhook that is down gets them later.
- **The quiet failures become loud.** Everything in [Known issues](#known-issues)
  that today fails silently is a problem in this feed first.

### Before v2: v1.x

Fix what an agent would trip over first, since every one of these is a place
where it cannot tell what went wrong:

- The known issues below, starting with the two that fail silently.
- `load` naming the real cause when an app's repo no longer carries it (today
  it says `No app found`).

---

## Later

Not in v2, but kept because each has a reason.

### Version rules for updates
A manifest says which versions it can upgrade from (`upgrade_from`); without it,
any version. Updating to version 6 over data last loaded at version 3, when 6
only upgrades from 4, refuses: "Upgrade failed while attempting to upgrade to
version 6: This only supports upgrading from versions >= 4. Please upgrade to an
intermediate version first."

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

### Off-host snapshots
Snapshots stay on the box. Copying them elsewhere also needs a plan for
`conf/master.key`: no snapshot carries it, so a restore on another machine
cannot read its secrets.

### Restricted network mode
A mode where a sidecar takes over DNS and proxies all outgoing HTTP and HTTPS,
allowing only what the manifest declares as a `[connection]`. This matters more
once agents write apps: it bounds what a generated app can reach.

### Services
Let a bundle say "give me a postgres database and a login" instead of carrying
its own postgres. Bundles list services they provide, and others require them.
This needs a lot of thought, and v2's runtime templates may cover the common
case first.

### Sleeping apps
Not running by default, started by a cron job or an incoming request.

### Resource limits
`mem_limit` and `cpus` pass through as raw compose keys, but no app option sets
them, so one runaway app can starve the box. More pressing once agents are
writing apps.

### Rootless docker and podman
Kelso needs rootful docker today, and `kelso init` refuses a rootless daemon.
Both break the same assumption: `lib/lifecycle/rootfs.py` reads and deletes
volume files as the container's root, which under a user namespace maps back to
the invoking user and cannot touch what the app's containers wrote.

---

## Known issues

- **An app whose loaded manifest no longer parses goes quiet.** A loaded app
  keeps the manifest it was loaded with. When a newer kelso stops accepting
  something in it, `ps` shows the app with blank columns, `inspect` says it is
  neither loaded nor in a catalog, `cmd` and `config` print a validation error,
  `kelso cron` silently drops its jobs, and `kelso update` fails, since its
  pre-update snapshot reads that manifest. `kelso system doctor` reports it.
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
  shows `0.0 B` for every volume kind, apps stay down after a reboot, and cron
  jobs do not run. `kelso system doctor` reports it.
- **`load` says `No app found` when an app's repo no longer carries it.** If the
  repo a loaded app came from is gone or not mirrored, `kelso load <app>` falls
  back to the loaded copy, finds no source, and reports the app as missing.
  Check `kelso system doctor` for a repo that is not a directory.

## Todo

- **Drop the Textual configuration mode; rethink interactive command-line
  config.** `kelso config --edit` and `kelso dev` open a full-screen Textual
  form on a terminal, which is the heaviest dependency for the least-used path.
  Remove it, and decide what interactive config at the command line should be.
- **Refuse volume names that are volume kinds.** A volume named `data`,
  `bulk`, `logs`, `temp`, `app` or `host` reads ambiguously everywhere a volume
  is shown next to its kind, and the `Data:` line `kelso start` prints picks a
  volume by the name `data`.
- **A loaded manifest viewer on the app page.** The web UI's app page shows a
  brief "manifest" section of a few key-value pairs. There should be a way to
  pop open a view of the whole loaded manifest, as kelso loaded it.
- **A routes guide.** Leading with Cloudflare Tunnel, then Nginx Proxy Manager
  (with its wildcard certificate) and Pangolin: what `kelso_address` is for, and
  assigning, publishing and checking a route. Today the only documentation is
  the comments in the generated `config.toml`.
- **"What kelso does to your containers."** One section listing everything
  kelso adds beyond the manifest: the `/etc/localtime` mount, log rotation
  (10 MB x 3), `restart: on-failure` and kelsod resuming apps at boot, a private
  network per app, the `kelso.*` labels, the `KLSO_*` variables, `/kelso/bin`
  for apps with commands, and the host port range (`port_base` to
  `port_base + 1000`) to open in a firewall.
- **Release hygiene.** A changelog or GitHub release notes for each tag; a
  platform statement in the README (Linux with systemd, amd64 and arm64, tested
  on Ubuntu Server 26.04 and Raspberry Pi OS); a GitHub description and topics
  that match the README.
