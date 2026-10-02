# Roadmap

## Audience
This roadmap is based on the desire to become a tool that someone technical enough to install linux on an old machine, and can paste in terminal commands would reach for. We want more people to run apps on their own hardware.

Later, it should also be a real option for small businesses: a semi-technical owner with an always-on box on site, running something like Frigate and a homepage for employees.

Kubernetes and similar can be overkill, while raw Docker Compose files alone still leave missing pices of the puzzle.

## v1
What has to be true before v1, roughly in order of priority.

### Upgrading kelso keeps apps loading
Upgrading den and harbor from #31 to #38 took moving files by hand and editing
manifests that no longer parsed. Once people run kelso, `uv tool upgrade kelso`
has to leave their apps working, or say exactly what to do:
- A manifest format version, so a format change is a known event rather than a
  parse error.
- `doctor` reports a loaded app whose saved manifest no longer parses, with
  `kelso load <app>` as the fix. Today it only shows as "unknown" on the
  volumes page.
- A release note for each on-disk or manifest format change.

### Updating apps
Nothing pulls a newer image: `load` re-reads the bundle, and compose only pulls
an image it does not have. Wanted: `kelso update <app>` (pull, recreate, roll
back if it does not come up healthy), and the UI showing that an update exists.
A load that changes an app's version snapshots it first, as restore already
does, unless passed `--no-snapshot`.

### App upgrade paths
A manifest says which versions it can upgrade from (`upgrade_from`); without it,
any version. Loading version 6 over data last loaded at version 3, when 6 only
upgrades from 4, refuses: "Upgrade failed while attempting to upgrade to version
6: This only supports upgrading from versions >= 4. Please upgrade to an
intermediate version first."

Going back a version is only ever `restore` from a snapshot, which brings the
matching data with it. `load` refuses a version older than the data's.

Versions compare as lists of numbers: split on any of `-.|/`, compare left to
right, and a missing place counts as zero. `24-45.23|3` is `[24, 45, 23, 3]`,
and `1.2` equals `1.2.0`. A version must start with a number; what follows the
leading numbers, as in `1.0.0-beta`, is not compared.

`load` records the version as `loaded_version` in the app's config store, the
record upgrades act on: it survives `unload`, goes with `rm --purge`, and comes
back with a snapshot on `restore`. The activity log carries it too, on the load
itself (`loaded - 1.0.1`), for the history only. Apps loaded before this have no
`loaded_version`, and upgrade as if from any version. `kelso dev` runs a
working copy whose version is "dev": it skips these checks and records nothing.

A bundle may carry a `migrations.toml`: manifest-level changes to apply between
versions, ending at the current one. The app migrates its own data; these cover
what kelso holds -- renaming a config value, renaming or removing a volume. The
supported way to drop a volume: version N moves the data off it, N+1 removes it
in `migrations.toml`, and N+2 can drop that migration with `upgrade_from = N+1`,
so every install passed through the version that removed it.

### Use and surface docker health checks
`up` waits for healthy containers, but health shows nowhere else. The data is
already in `KelsoRunUnitStatus.health`; `ps`, `status`, and the UI should show it.

### One-command installer
`kelso init` installs `kelsod` as a systemd user service. Installing kelso itself
still takes `uv` and a separate `kelso init`.

### Cron jobs
`[commands]` and `kelso cmd` exist. The manifest accepts a `cron` table, but
nothing runs it yet.

### Alerts
Nothing says when an app crashes, the disk fills, or a snapshot fails; you find
out by opening the dashboard. For v1, one configurable webhook and two levels:
- **notifications**: the activity log, as it happens.
- **problems**: what `doctor` reports, an unhealthy app, a full disk, a failed
  snapshot.

Alerts queue in a logtab spool, so a webhook that is down gets them later.

## After v1

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

### Resource limits
`mem_limit` and `cpus` pass through as raw compose keys, but no app option sets
them, so one runaway app can starve the box.

## Known issues
- **Rootless docker breaks snapshot and restore silently.** `lib/lifecycle/
  rootfs.py` assumes the container's root can read root-owned volume files;
  under userns that maps back to the invoking user. Nothing checks; `init`
  should refuse.
- **A `cmd` job holds the app lock for the command's whole run.** Kelso-wide
  ops can proceed; the same app cannot be loaded, started, or stopped until it
  exits. Fine for the batch-style commands the UI is for; a long-runner still
  wedges that app. The runner also allocates no TTY, so a command that waits
  on stdin hangs rather than prompting.
- **Only daemon jobs file activity output.** A CLI invocation prints to the
  operator's terminal and records only its status line in `activity.logtab`,
  so the UI's Activity page shows what kelsod ran, not what the operator
  typed. The mechanism to close this is in place — `Job.call(args, ctx,
  echo=stream)` writes the run log and the terminal from one stream — and the
  plan is to migrate CLI verbs onto their Job classes, verb by verb.
- **Stopping an app warns about unset config variables.** `compose down` runs
  without the config env, so compose prints `"__KELSO_CONFIG__<name>" variable
  is not set`. Harmless, and alarming when the name is a password.
- **Route registration logs a blank host.** It prints `-> http://:8096`; the
  proxy entry itself has the right address.
