# Kelso Server

**Self host, distribute, enjoy!**

Kelso is a self-hosting management system where apps are packages that describe *what* they need rather than *how* they get it. This, combined with a small amount of one time configuration allows for easy distribution, secrets, management, and snapshotting of self-hosted apps.

## How does it work? 
Kelso provides an "infrastructure as code" platform with just enough abstraction for self hosting. Each app is packaged as a kelso app bundle that 1) defines a `manifest.toml` which fully describes the app's containers and what they need and 2) optionally contains any helper scripts or files. A bundle is either a `<app_id>.klso` folder or, for small apps, a single `<app_id>.klso.md` markdown file with the same files embedded in code blocks (see [demo-markdown](demo-apps/demo-markdown.klso.md)). Here's a simplified example:

unifi-network-application.klso/manifest.toml:
```toml
[app]
description = "Unifi Network Application from linuxserver.io"

[config]
# A secret that the user never has to set, generated on load and stored encrypted.
mongo_pass = { secret = true, default = "auto" }

[volumes]
db_data    = { kind = "data" }
app_config = { kind = "data" }

[run.unifi-db]
image   = "docker.io/mongo:8.0.11"
volumes = { db_data = "/data/db" }
env     = { MONGO_PASS = "${mongo_pass}", ... }

[run.main]
image   = "lscr.io/linuxserver/unifi-network-application:10.4.57"
volumes = { app_config = "/config" }
env     = { MONGO_HOST = "unifi-db", MONGO_PASS = "${mongo_pass}", ... }

[run.main.routes]
main = { port = "8443", scheme = "https" }
```

This describes everything that the Unify Network Admin app needs to run on Kelso. You then bring it up with:
```
$ kelso load unifi-network-application
$ kelso start unifi-network-application
```

Under the hood, Kelso is using the manifest + your configuration to create a docker compose project.

**There is no lock-in by design.** If you remove the `kelso` binary from your system, you'll still have an organized, functional folder tree of docker compose projects that you can directly interact with.

Manifests are small enough to be digested in a few seconds. For a full, functioning example, see my [case study](docs/case_study.md) on the Unifi Network Application where we build the manifest from scratch in a few minutes.

## Getting Started:
Kelso needs `git`, `docker` with the compose plugin, and `uv`. Docker must run
as root, with your user in the `docker` group: rootless docker and podman are
not supported yet. `kelso init` checks all of this and refuses to run until it
holds.

### Prerequisites on a fresh Ubuntu Server
```bash
sudo apt-get update && sudo apt-get install -y git curl
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
curl -LsSf https://astral.sh/uv/install.sh | sh
```
Then log out and back in, so the `docker` group and uv's `PATH` take effect.

### Install kelso
```bash
uv tool install "git+https://github.com/nepthar/kelso@v1.0.0"
kelso init --yes
sudo reboot # Ensure all settings are picked up by kelsod
```
`init --yes` sets kelso up in `~/kelso`, fetches its repo of apps, and starts
`kelsod` as a systemd user service that comes back at boot. If you run `kelso init` you can set other options like install location.

### Try it
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

That install carries both commands: `kelso`, the CLI, and `kelsod`, the
admin API the web UI talks to.
On a machine running systemd, `kelso init` also runs `kelsod` as a systemd user
service; `kelso system service` does the same for a root that already exists.
Elsewhere, run `kelsod` in a terminal if you need the admin socket and daemon.

### Upgrading kelso
Install the release you want by its tag, then restart kelsod on it:
```bash
uv tool install --force "git+https://github.com/nepthar/kelso@<tag>"
kelso system service
```
The second command rewrites kelsod's systemd unit for the new install and
restarts it. Apps keep running throughout, except those in start group 0, which
stop and start with kelsod. Loaded apps keep the version they were loaded at;
`kelso update <app>` moves one to what its repo holds now.

### Uninstalling kelso
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

### Volume Storage Locations

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

## Why kelso?

- **Configure your system layout once, load any app**
Kelso places each app's data where you tell it. Apps describe "what" they need instead of "how" it's wired up.

- **Distributing apps you run yourself is hard today.**
Kelso makes it trivial to create and use app repositories. It's just a folder pushed to github, and the apps in it run on any properly configured install of kelso.

- **Running apps with docker compose by hand is time consuming.**
Once you start using docker compose to run your own apps, you end up managing each compose file individually. It's difficult to version control a folder of them properly. Each new app (except for super simple ones) has to be hand-configured and wired in to your system. Oh, and I also HATE `.env` files and docker volumes. **Kelso provides a simple mechanism to store/inspect secrets, application data, and logs. You configure it once, it wires every app automatically**

- **Other solutions exist, but require you to be a sysadmin.**
Kelso is simple to reason about. It is mostly just a bunch of folders and text files.

- **Snapshotting containers SHOULD be trivial in 2026, but is not.**
Since kelso is designed for a single machine you own, it assumes that a few seconds of downtime is an acceptable price for a snapshot you can actually trust. `kelso snapshot take <app>` stops the app, archives its volumes and run state together, and starts it again if it was running — so what you get back is a coherent point in time rather than a copy of files that were being written to. Restoring is the same trade in reverse.

There are GUI options like Portainer and Dockge that help manage containers and stacks, but they basically wrap the problems above in a shiny UI rather than solve them.

## Why NOT kelso?
You may not want to use kelso if:

- You regularly run complicated, redundant container deployments that failover, have more than ~25 concurrent users, or have serious compute requirements.

- You are self-hosting to learn how to use specific technologies like Kubernetes

## See Also


- The anatomy of a kelso app [manifest](docs/manifest.md)
- A [case study](docs/case_study.md): building one from scratch
- Our current [roadmap](docs/roadmap.md)
- How the [test suite](docs/testing.md) is put together


## Why "Kelso"?
[Kelso](https://www.nps.gov/moja/learn/historyculture/kelso-depot.htm) was a small, unremarkable but important train stop in the Mojave desert. Like this app, it served a purpose with little to no fanfare.

## Philosophy & Bigger Picture
I want to enable more people to **run software like it's 1997**. Back in 1997, you bought a copy of Microsoft Word and MSFT had NO IDEA what crazy manifestos you were writing with it, because it was your copy running on hardware you controlled. You could pull the plug. You didn't lose access to your documents if you stopped paying a subscription. "I'm altering the deal, pray I don't alter it any further" was a fun line from Star Wars, not the *implication* of the latest "update" from Adobe Creative Cloud.

Kelso is part of the P2 project, a batteries-included framework providing an OS-like experience for selfhosted, peer to peer apps
