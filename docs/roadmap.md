# Roadmap

## Audience
This roadmap is based on the desire to become a tool that someone technical enough to install linux on an old machine, and can paste in terminal commands would reach for. We want more people to run apps on their own hardware.

Later, it should also be a real option for small businesses: a semi-technical owner with an always-on box on site, running something like Frigate and a homepage for employees.

Kubernetes and similar can be overkill, while raw Docker Compose files alone still leave missing pices of the puzzle.

## v1
What has to be true before v1, roughly in order of priority.

### Updating apps
`kelso update <app>` (and the dashboard's update icon) pulls the source's
images, snapshots as `pre-update`, and re-loads; repos are still updated by
hand. Still wanted: rolling back when the new version does not come up healthy,
and a plain `load` that changes the version snapshotting first.

## After v1

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
itself (`{"verb": "loaded", "version": "1.0.1"}`), for the history only. Apps loaded before this have no
`loaded_version`, and upgrade as if from any version. `kelso dev` runs a
working copy whose version is "dev": it skips these checks and records nothing.

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
None right now.

## Quirks we're keeping
Kelso is for making self-hosting easier for most people, most of the time, not
a bulletproof production platform. These behave as designed and are not
planned to change.

- **An app's commands hold its lock while they run.** That is `kelso cmd`, the
  Run button, and cron alike. Stopping, reloading, updating or snapshotting the
  app refuses until the command finishes, rather than cutting a backup or a
  migration off halfway. Every command has a `timeout` (600 seconds unless its
  manifest says otherwise, at most 1800), so the wait is bounded.
- **A timed-out command keeps running in its container.** Kelso stops waiting,
  records the run as failed and releases the lock, but docker cannot stop what
  `compose exec` started; it runs on until it finishes or the app stops.
- **kelsod runs jobs one at a time.** A long command started from the web UI
  delays every job queued after it, for any app. Cron runs on its own thread
  and is not held up.
- **Commands get no terminal.** One that waits for input waits until its
  timeout. For anything interactive, use `kelso shell` or the console.
- **A CLI command that changes something is recorded, but not word for word.**
  Its run log keeps kelso's narration and docker's output, not what it prints
  to stdout, and not its arguments, since `--set` can carry a secret. Docker's
  output goes through kelso on its way to the terminal, so `compose up` shows
  plain lines instead of live progress bars.
