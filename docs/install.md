# Installing kelso

Kelso needs `git`, `docker` with the compose plugin, and `uv`. Docker must run
as root, with your user in the `docker` group: rootless docker and podman are
not supported yet. `kelso init` checks all of this and refuses to run until it
holds.

## Prerequisites on a fresh Ubuntu Server
```bash
sudo apt-get update && sudo apt-get install -y git curl
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
curl -LsSf https://astral.sh/uv/install.sh | sh
```
Then log out and back in, so your shell picks up the `docker` group and uv's
`PATH`.

## Install kelso
```bash
uv tool install "git+https://github.com/nepthar/kelso@v1.0.1"
kelso init --yes
sudo reboot
```
The install carries two commands: `kelso`, the CLI, and `kelsod`, the daemon
that starts apps at boot, runs their cron jobs, records metrics, and serves the
admin API the web UI talks to.

`init --yes` sets kelso up in `~/kelso`, fetches its repo of apps, and runs
`kelsod` as a systemd user service. Plain `kelso init` asks where to put the
root instead.

The last thing `init` shows is a twelve-word recovery phrase. Every key kelso
encrypts your apps' secrets with is derived from it, and it is the only way to
read them, or a backup of them, on another machine. Save it in a password
manager. `kelso system recovery-phrase` shows it again.

The reboot matters even though you just logged back in. kelsod runs under
systemd's per-user manager, which keeps the groups it started with; if that
manager started before you joined the `docker` group, kelsod cannot reach
docker until it restarts. A reboot fixes that and shows that kelsod comes back
on its own.

Without systemd, `kelso init` skips the service: run `kelsod` in a terminal
when you want apps resumed, cron run, or the web UI. `kelso system service`
installs the service for a root that already exists.

## Try it
`kelso init` sets up one repo, `staples`: the apps kelso maintains. Kelso's demo
apps are small ones that each show off a feature; add them, then start one and
run its commands:
```bash
kelso repo add github://nepthar/kelso/main/demo-apps --name demos
kelso start demo-cron
kelso cmd demo-cron
kelso cmd demo-cron stamp hello
kelso cmd demo-cron show
kelso logs demo-cron
kelso stop demo-cron
```
Each demo is one readable file: `repos/demos/demo-apps/demo-cron.klso.md` is the
one above. `kelso repo list` shows every app you can start, and when you are
done exploring, `kelso unload demo-cron` and `kelso repo remove demos` tidy up.

For the web UI, choose its admin password and start it; it prints the address
to open: `kelso start kelso-ui --set admin_pass=<password>`.

## Upgrading kelso
Install the release you want by its tag, then restart kelsod on it:
```bash
uv tool install --force "git+https://github.com/nepthar/kelso@<tag>"
kelso system service
```
The second command rewrites kelsod's systemd unit for the new install and
restarts it. Apps keep running throughout, except those in start group 0, which
stop and start with kelsod. Loaded apps keep the version they were loaded at;
`kelso update <app>` moves one to what its repo holds now.

## Uninstalling kelso
```bash
kelso down
systemctl --user disable --now kelsod.service
rm ~/.config/systemd/user/kelsod.service
uv tool uninstall kelso
sudo rm -rf ~/kelso
```
`kelso down` stops every app. The last command deletes all of kelso's data,
app volumes included; `sudo`, because containers write volume files as root.
Skip it to keep the root, which stays a folder of ordinary compose projects.
Docker images kelso pulled stay until you remove them (`docker image prune -a`).

## Volume storage locations

App volumes live in `<kelso_root>/volumes/<kind>/<app>/<volume>` where `<kind>`
is one of `data`, `temp`, `bulk` and `logs`. You probably want to change where
some of these volumes are stored and you can do so with symlinks.

For example:
```
mv ~/kelso/volumes/bulk/* /mnt/nas/kelso-bulk/
rmdir ~/kelso/volumes/bulk
ln -s /mnt/nas/kelso-bulk ~/kelso/volumes/bulk
```

**Note: On a share that may not be mounted, link to a directory *inside* the share,
never to the mount point itself.** Kelso checks for dangling links and this is its
only signal that the volume has not been mounted yet.

If kelso finds dangling links to volume roots, it will refuse to start apps
rather than re-populate with empty folders.

## Backups

Every night at 3am, kelsod backs up each app's data volumes, configuration and
loaded copy, and kelso's own state, into `backups/` in the kelso root. What is
included is in [volumes](volumes.md). `kelso backup` shows where they go and
how the last run went; `kelso backup run` takes one now; `kelso restore <app>
<backup>` puts an app back.

Like a volume root, `backups/` is meant to be linked to another disk:

```
mv ~/kelso/backups /mnt/nas/kelso-backups
ln -s /mnt/nas/kelso-backups ~/kelso/backups
```

A link to nothing is refused, so an unmounted share is never filled from the
local disk. To insist that backups are on a mounted disk, set it in
`config.toml`:

```toml
[backup]
require_mount = true
```

Backups are encrypted, and your recovery phrase is the only way to read them on
another machine. Keep it somewhere other than this machine.

## Recovering a machine

To bring kelso back on a new machine, or a rebuilt one, from its backups:

1. Install kelso, then `kelso init --with-phrase`. It asks for your recovery
   phrase instead of making a new one, so it can read the backups.
2. Point volume roots that should live on another disk at it, as above. This
   machine's disks need not match the old one's. Restore shows where each
   volume root and `backups/` pointed when the backup was taken, next to where
   they point here, and asks before going on if a volume root differs.
3. `kelso restore <backups directory>`, for example
   `kelso restore /mnt/nas/kelso-backups`. It restores kelso's configuration
   and state, then every app, and points `backups/` at that directory so the
   next backup adds to it.
4. Check `config.toml`'s host volumes and `kelso_address` for this machine;
   `kelso system doctor` reports paths that are not there. Then `kelso up`.

Restore refuses a kelso that already holds apps. `kelso system purge` deletes
every app, its data and its config, leaving config.toml, the phrase, repos,
`backups/` and the volume roots as they are; restore one app at a time instead
with `kelso restore <app> <backup>`.
