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

A small operator can take a few minutes of downtime, even fifteen, for a backup
or an update. What they cannot take is a change they cannot undo. Kelso trades
uptime for stability and reversibility every time.

### Where it sits

Hosted agent-native platforms have the same loop (an agent runs `plan`, then
`up`, and reads an exit code) on someone else's cloud. Kelso is that loop on
your own machine: your data stays home, the
bundle is a file you keep, and pulling the plug is always an option. Home server
GUIs (CasaOS, Umbrel, Unraid's apps) are app stores with a human clicking. Kelso
is not competing with either on their terms.

---

## v2: an agent drives kelso

v2 is done when a person with a fresh machine and an agent can say "make me a
recipe site at recipes.example.com" and end up with a running, published,
backed-up app, without opening a terminal themselves and without the agent
guessing at any step.

### Two workflows: dev and hosting

Kelso is used in two ways, and they stay separate in how we think about,
document and test them, even where they share code.

| | Dev | Hosting |
| --- | --- | --- |
| What it is | Making an app: `kelso new`, `kelso dev`, `kelso deploy` | Running apps: `kelso init`, kelsod, start, backups, routes, the web UI |
| Where it runs | The laptop of whoever is building the app | The always-on box |
| Supported on | macOS with Docker Desktop; Ubuntu | Ubuntu; possibly a current Debian |
| Needs kelsod | No | Yes |

Each customer journey we test belongs to one of the two and is tested on that
workflow's platforms: a dev journey on macOS and on Ubuntu, a hosting journey
on Ubuntu. A journey that crosses over, such as `kelso deploy` from a Mac to an
Ubuntu box, is tested as both. Ubuntu is where we start; other distributions
probably work, but nothing is claimed for them until they are tested.

### 1. The agent contract

What an agent can rely on, on every command. This extends what kelso already
does well (refuse, and name the fix) into something an agent can parse.

- **Docs written for agents first.** `docs/agent/` holds task-shaped
  instructions an agent follows: install kelso, write a bundle, deploy and
  publish an app, update it, recover from each known failure. Each step says
  what to run, what success looks like, and what to do otherwise. A root
  `llms.txt` points at them. Human docs stay, but they are not where an agent is
  sent.
- **`kelso docs agent`** prints those docs from the installed kelso, so an
  agent always reads the playbook for the version it is driving, never a newer
  one from the web.
- **`--json` on every command.** The same data the human output shows, stable
  and documented, so an agent never scrapes a table.
- **Exit codes that mean something.** 0 done; 1 failed while doing it; 2 refused
  before doing anything, with the fix named. An agent knows from the code alone
  whether anything changed.
- **Problems as data.** Every refusal, `doctor` finding and start blocker is
  `{code, owner, problem, evidence, fix}`. `owner` says who has to act: the
  `app` (its code or manifest), the `operator` (config, a secret, a disk), or
  `kelso` (a bug to report). `evidence` is what the finding rests on, such as
  the log tail of a unit that exited. `fix` is the commands that resolve it.
  The current prose messages become the human rendering of the same record.
- **Questions as data.** When a plan cannot go ahead without an answer (a
  required config value, a host volume to bind), the command exits 2 and prints
  the `ConfigRequest` it needs as JSON. The answer is a `ConfigResponse`, given
  with `kelso config <app> --set`, the same one the TUI and the web form fill.
  Plan, answer, plan again converges to ready.
- **Nothing waits on a terminal that isn't there.** A command that would ask
  `[y/N]` checks for a terminal first. Without one it refuses with exit 2,
  naming the `--yes` that skips the question, instead of waiting for an answer
  that never comes.
- **Plan, then apply.** Anything that changes state can print what it will do
  without doing it (`--plan`), as text for a human and JSON for the agent. Most
  verbs already build a plan object first (`RemovalPlan`, `RestorePlan`,
  `CleanupPlan`, `DevPlan`); this exposes them. The human approves the plan, not
  a wall of compose output.
- **A manifest schema an agent can check against.** A published JSON Schema for
  `manifest.toml`, and `kelso check <bundle>` that validates without loading,
  reporting every problem at once.
- **Safe to run again.** An agent whose connection drops does not know whether
  its command ran, so it runs it again, and that has to be harmless. Verbs
  converge: `start` on a running app and `update` to the version already loaded
  do nothing and say `unchanged`. A request for a job that is already queued or
  running returns that job instead of adding a second one. `kelso activity wait
  <id>` reattaches to a job and exits with its result.
- **The CLI is the interface.** An agent drives kelso over a shell, locally or
  through `ssh`. There is no MCP server: an agent can use any interface it has
  an example of, and a shell, `--json` and good docs stay usable by a human too.
- **Agents never handle secrets.** The agent writes the manifest and declares
  each secret. Kelso generates the ones marked `auto`, and the person pastes the
  rest into the web UI. A plan that is waiting on a secret names the page to
  send the person to.

### 2. Custom apps: write, run, publish

The point of kelso for its first user: an agent writes a new app and it goes
live.

- **Work locally, deploy with one command. This comes first.** Kelso runs on
  the laptop as well as on the box, and the flow is:
  1. `kelso new <app> --template <name>` makes the project, an ordinary folder
     under version control.
  2. `kelso dev <app>` runs it on the laptop exactly as the box will: the same
     manifest, runtime image, start script and volumes. The person and their
     agent edit, run and fix here.
  3. `kelso deploy <app> <host>` copies the bundle to the box over `ssh`, loads
     and starts it there, and reports live or failed with the reason.

  `deploy` refuses, with exit code 2, when the laptop's kelso version differs
  from the box's, and names the command that brings them level. `new`, `dev` and
  `deploy` are the dev workflow, so they run on the dev platforms, without
  kelsod.
- **`ssh` is the transport.** Whoever can `ssh` to the
  box can deploy to it; there is no new login, port or certificate. `deploy`
  connects with `BatchMode=yes`, so a password prompt fails fast instead of
  hanging an agent. It runs `kelso version --json` on the box, copies the
  bundle (skipped when the box already has that hash), and runs `kelso load`
  and `kelso start` there with `--json`. The remote commands survive a dropped
  connection: they keep running on the box, and running `deploy` again
  reattaches to the activity instead of starting a second one.
- **`kelso host setup <host>`** turns a fresh Ubuntu machine reachable over
  `ssh` into a kelso box: it installs docker and kelso, runs `kelso init` and
  starts kelsod. When the terminal is interactive, `sudo` asks for the password
  there. When it is not, as when an agent runs it, setup refuses before
  changing anything and says to run the same command in a terminal. If the new
  docker group needs a fresh login, setup ends the connection with "Step 1 of
  2 complete. Run `kelso host setup <host>` again to finish." The last step
  hands the box to the person (see [Onboarding](#6-onboarding)). Running it
  again on a finished box does nothing. `deploy` to a host without kelso
  refuses and names `kelso host setup`.

  This is the path we expect most people to take themselves, with no agent
  involved, so it is the main install path in the docs and is tested from both
  dev platforms.
- **`kelso host` is client-side and knows hosts by name.** `setup` records the
  host in a list on the laptop: its name, how to reach it (`ssh` today) and
  where its credentials are. `deploy`, `host open` and every later command take
  that name, so how a host is reached is a detail of the list, not of each
  command.

  The agent docs call developing without kelso an antipattern: running the app
  with the laptop's own Python or Ruby, a shell's environment variables and a
  local database, then expecting the bundle to work on the box. That run never
  touched the start script, the read-only source, the declared volumes or the
  runtime image, so the box becomes the first real test. Developers who know
  what they are doing can work however they like; an agent follows the docs, so
  the docs give it one way.
- **`kelso new <app> --template <name>`** scaffolds a bundle in `repos/local`
  from a small matrix of language and framework templates. Each is the pattern
  kelso-ui already uses: a pinned runtime image, the app's source as an `app`
  volume, and a start script. No image to build or host. A plain language
  template (`python`, `node`, `ruby`) runs a start command the author names, so
  it also covers any framework the matrix leaves out.
  - First: `static` (files served by a static file server), `python`,
    `fastapi`, `rails`.
  - Then: `flask`, `django`, `node`, `express`, `nextjs`, `vite-react`, `ruby`
    (with Sinatra), `go`.
  - Not PHP, in any form.
- **Dependencies install on start.** The start script installs what the
  lockfile names (`pip install`, `bundle install`, `npm ci`) into a `temp`
  volume, then starts the app. The first start is slow and later ones reuse the
  cache. The docs and the templates say plainly what that means: `temp` is not
  backed up, so after a restore the app downloads its dependencies again, and
  needs the network and the package registries to do it. An author who wants
  the dependencies on hand ahead of time has two choices. Publish an image with
  them baked in and name it in the manifest, or install them into a `data`
  volume, which every backup includes.
- **Every template is a canary.** A live test creates an app from each template,
  starts it and fetches its health URL, so a template that stops working is
  found before a user finds it.
- **`kelso check` reads the source too.** Beyond the manifest schema, it checks
  what agents get wrong: a start script that is missing or not executable, a
  route port nothing listens on, a server bound to `127.0.0.1`.
- **A database is a connection.** A manifest says it needs a `postgres`
  connection. Kelso fills it by adding a postgres run unit to the app,
  provisioned with generated credentials, and offers the URL as
  `${conn.<name>.url}`. The database is part of the app, so its backups and
  restores stay whole.
- **The dev loop an agent can close.** `kelso dev` already runs a working copy
  with live source. It gains `--json` status and a "healthy at URL" signal, so an
  agent edits, sees it come up or fail, reads the logs, and edits again.
- **Unchanged means nothing to do.** Loading records a hash of the bundle (its
  manifest and files) and the image digests it resolved. Loading or updating to
  the same hash is a no-op that says `unchanged`, and the hash is how an
  activity names exactly what it ran.
- **Cron an agent can test.** `kelso cron run <app> <job> --wait` runs a job now
  and exits with its result, and `kelso cron runs <app>` lists past runs with
  their output, so an agent can check a schedule without waiting for it.
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
- **Repos stay plain git.** A repo is still a folder on GitHub. No registry, no
  accounts, no review queue.

### 4. Updates that roll back

The guarantee: **an update either works, or the whole app is put back exactly as
it was before the update, from its update backup.** There is no separate
update state or migration engine; backups are the mechanism, so they have to
be bulletproof.

- **A health gate decides "works".** After loading the new version, `update`
  starts the app and waits for every unit to be healthy (or, with no
  healthcheck, still running after the settle time). Anything else, including a
  failed load, restores the update backup and starts the app again.
- **Backups that can always be restored:**
  - Record the image digests the app ran, and have `cleanup` keep any image a
    kept backup names. Today `cleanup` removes images no *loaded* app uses,
    which can leave a rollback with nothing to roll back to.
  - Restore takes the app back to the recorded digests, not to whatever a tag
    points at now.
- **Live-tested, not just unit-tested.** Backup and restore of real root-owned
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
4. **A first-boot web page that hands the box over.** Its job is to leave the
   person holding the two things only they should have: the admin password, and
   a copy of the recovery phrase kept somewhere other than the box. It cannot
   keep them from whoever set the box up, since the phrase lives on the box;
   once the box is handed over, the person removes keys they no longer trust.
   - The page is served by kelsod, not kelso-ui, and only while setup is
     unfinished: kelsod adds the `/setup` routes at startup when the box is not
     set up, and they answer 404 from the moment setup finishes. It is a form
     or two of plain HTML with almost no CSS. kelso-ui stays an ordinary app
     with one mode.
   - kelsod listens on its unix socket, so the page is reachable only from the
     box. The page needs a one-time token (kelso stores its hash; it expires
     in a day). Nothing on the LAN can reach it, so there is no race to finish
     setup first and no password crossing the network in the clear.
   - `kelso host setup` ends by forwarding a local port to kelsod's socket over
     the same `ssh` connection and opening
     `http://localhost:<port>/setup#<token>` in the laptop's browser. The token
     sits after the `#`, which browsers never send, so it stays out of every
     log. Setup waits until the page is done; Ctrl-C leaves it for later, and
     running `kelso host setup <host>` again picks up there.
   - The page asks for an admin password, then shows the recovery phrase once
     and asks for proof it was saved (three of its words typed back), then
     offers Cloudflare and where bulk storage lives. Finishing starts kelso-ui
     with that password and burns the token.
   - Until setup is finished, `kelso problems` reports `setup_incomplete` with
     the command to finish it, and `kelso publish` refuses, so a UI with no
     password never reaches the internet.
   - Installed with the install script or cloud-init, with no laptop involved,
     the same token link is printed for the person to open through
     `ssh -L`. A box set up with no `ssh` at all (a VM image) has kelsod also
     listen on the LAN until setup finishes and prints the link with that
     address on its console; that one case crosses the LAN in the clear, and
     the page says so.

   `kelso host open <host>` later reaches the UI the same way: a forwarded
   port and a browser.

What not to do:

- **A container with kelso inside (Docker-in-Docker with nesting).** It needs a
  privileged container, which is root on the host anyway, and it breaks what
  kelso is built on: volumes that are ordinary host paths, backups that read
  them as root, and kelsod starting apps at boot. A Proxmox LXC with nesting
  enabled is different. It is a normal Linux with systemd, so the install script
  and cloud-init file cover it, and the docs should say so.
- **A custom Ubuntu ISO.** A distribution to maintain, for nothing the
  cloud-init file does not already give.

Someday: **hosted kelso**. An agent decides its person needs an app, creates a
kelso host for them, and deploys to it, with no hardware and no `ssh`. It is
not v2 work, but v2 is built so that it needs no rework:

- **One host, one operator, reached over `ssh`.** A hosted kelso is one VM per
  customer running the same kelso, not a multi-tenant kelsod, and the customer
  reaches it over `ssh` like any other host. Everything in the dev workflow then
  works against it unchanged.
- **Creating a host is a separate service.** A small API that creates a VM with
  kelso on it, installs the caller's `ssh` key, and returns a host entry for
  the laptop's list. `kelso host create` calls it; `deploy` then works as it
  does for a box at home.
- **The setup link is the handoff.** The same one-time link is what the agent
  gives its person to claim the box: set a password, save the recovery phrase.
  For a hosted box it has to be served over HTTPS, since the person may have no
  `ssh` of their own.
- **An agent with no shell at all** (a chat app rather than a coding agent)
  cannot run kelso. It could get an [access token](#one-ui-for-several-hosts)
  for kelsod's API over HTTPS, the same way a kelso-ui does, and later
  [scoped credentials](#scoped-credentials). No MCP.

### 7. Hearing about problems

An agent cannot fix what nothing reports. Today you find out by opening the
dashboard.

- **`kelso problems --json`.** One feed of everything wrong now: `doctor`
  findings, unhealthy apps, a full disk, a failed backup or update, kelsod not
  running. Each item uses the same `{code, problem, fix}` shape, so an agent can
  be pointed at the box and told "keep it healthy".
- **A webhook.** One configurable URL, sent each new problem and, optionally,
  the activity log as it happens. Undelivered messages queue in a logtab spool,
  so a webhook that is down gets them later.

---

## Later

Not in v2, but kept because each has a reason.

### Version rules for updates
A manifest says which versions it can upgrade from (`upgrade_from`); without it,
any version. Updating to version 6 over data last loaded at version 3, when 6
only upgrades from 4, refuses: "Upgrade failed while attempting to upgrade to
version 6: This only supports upgrading from versions >= 4. Please upgrade to an
intermediate version first."

Going back a version is only ever `restore` from a backup, which brings the
matching data with it. `update` refuses a version older than the data's.

Versions compare as lists of numbers: split on any of `-.|/`, compare left to
right, and a missing place counts as zero. `24-45.23|3` is `[24, 45, 23, 3]`,
and `1.2` equals `1.2.0`. A version must start with a number; what follows the
leading numbers, as in `1.0.0-beta`, is not compared.

The version these checks act on is `loaded_version` in the app's config store,
which every load records: it survives `unload`, goes with `rm --purge`, and
comes back with a backup on `restore`. `kelso dev` runs a working copy and
skips these checks.

A bundle may carry a `migrations.toml`: manifest-level changes to apply between
versions, ending at the current one. The app migrates its own data; these cover
what kelso holds -- renaming a config value, renaming or removing a volume. The
supported way to drop a volume: version N moves the data off it, N+1 removes it
in `migrations.toml`, and N+2 can drop that migration with `upgrade_from = N+1`,
so every install passed through the version that removed it.

### Off-host backups
Backups go to `backups/` in the kelso root, usually a link to another disk or a
share. The next step is a bucket: restic speaks S3, Backblaze B2 and SFTP with
the same code, so it is a `[backup]` setting for the repository address and
its credentials as kelso secrets, and a choice of whether the bucket replaces
the local repository or `restic copy` mirrors one into the other each night.

### Restricted network mode, finished
`network_mode = "restricted"` runs an app behind kproxy, reaching only its `web`
and `http` connections. What is left:

- **A `tcp` connection kind.** kproxy already relays a TCP port to one address.
- **Units get a connection by referencing it,** instead of listing it in
  `connections = [...]`.

### Services
Let a bundle say "give me a postgres database and a login" and be given one
shared server, instead of carrying its own postgres. v2's `postgres` connection
covers the common case with a database inside each app. A shared server needs a
way to back up and restore one app's database without the others, which a copy
of a volume cannot do.

### One UI for several hosts
A person with two or three boxes runs one kelso-ui for all of them. It acts on
one host at a time: a host switcher, and pages that are the same as today's.
Over that sits an "All hosts" overview that asks every host for its apps and
shows them with a host column. A host that does not answer shows as
unreachable with when it was last seen; it does not break the page.

- **kelso-ui talks to other hosts through kelsod's API, over TLS with a token**,
  not over `ssh`. `ssh` stays the transport for setting up a host and deploying
  to it, which are a person's or an agent's acts. A UI that runs all the time
  holds a credential for the API it uses and nothing more: an `ssh` key, however
  restricted, is a login to the machine.
- **The host list lives in kelso-ui**, in a `data` volume, so it is backed up
  with it.
- **Each box has a stable ID**, derived from the recovery phrase like its other
  keys, so a box restored from its phrase is the same host. kelsod returns it,
  with a display name, from `/version`, and wherever more than one host is
  visible an app is `host/app`.
- **kelso-ui uses nothing but kelsod's API.** Anything it reads or does on its
  own box without kelsod -- the store, compose files, docker -- moves into the
  API first. This and the box ID are worth doing before the rest, while they
  are small.
- **Hosts on different kelso versions** are a later problem. Until then every
  host runs the same version.

**Access tokens.** kelsod issues tokens to anything that asks and that the
person approves; it does not know or care that the asker is kelso-ui.

1. The asker sends `POST /access-request` with an ID of its own and a line a
   person can read ("kelso-ui on rpi.lab.my-domain.com"). kelsod returns an
   `access_request_id` that only the asker knows, and a short code that both
   sides show.
2. The person approves the request on that box (`kelso access approve`, or the
   box's own UI), checking that the codes match.
3. The asker polls with its `access_request_id`. After approval the answer is
   the token, once. Its credential is then `<asker id>.<token>`.

Requests expire unanswered in a few minutes. kelsod keeps tokens hashed, and
`kelso access list` and `kelso access revoke` show and remove them. kelsod
serves TLS with its own certificate; the asker records its fingerprint on the
first request, and the code both sides show is derived from it, so matching
codes also mean nobody sat in between. kelsod listens on TCP only once the
person turns it on.

### Scoped credentials
Today an agent gets whatever its `ssh` login gets, and an access token gets all
of kelsod's API. A token scoped to verbs (deploy and read logs, but not
restore, purge or read secrets) lets a person hand an agent, or a UI, less than
the whole machine. Scopes would be chosen when the request is made and shown
when it is approved.

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

## Todo

- **Refuse volume names that are volume kinds.** A volume named `data`,
  `bulk`, `logs`, `temp`, `app` or `host` reads ambiguously everywhere a volume
  is shown next to its kind, and the `Data:` line `kelso start` prints picks a
  volume by the name `data`.
- **A loaded manifest viewer on the app page.** The web UI's app page shows a
  brief "manifest" section of a few key-value pairs. There should be a way to
  pop open a view of the whole loaded manifest, as kelso loaded it.
- **A routes guide.** Leading with Cloudflare Tunnel, then Nginx Proxy Manager
  (with its wildcard certificate) and Pangolin: what `route_address` is for, and
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
