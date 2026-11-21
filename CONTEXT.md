# Kelso Server

Kelso Server runs apps on hardware you own: an app says what it needs, and kelso
provides it.

## Language

### Places

**Kelso host**:
A machine that runs kelso apps for real, with kelsod keeping them up across
reboots. Where an operator manages apps.
_Avoid_: box, server, node

**Workstation**:
The machine where you write kelso app bundles and deploy them from. It has its
own kelso root, so `kelso dev` runs an app the way a kelso host would.
_Avoid_: laptop, dev machine, client

### Apps

**Kelso app bundle** ("bundle"):
A `.klso` folder or `.klso.md` file: what an author writes, publishes, and
loads from.
_Avoid_: package, stack, project

**Kelso app** ("app"):
What is loaded and running from a bundle, named by its app id. Commands act on
apps.
_Avoid_: stack, service, container

**App id**:
The reverse-domain name of an app, such as `net.klso.immich`; a unique suffix
such as `immich` stands for it.

**Loaded app**:
An app on a kelso host's working set: running, or meant to be ready to run.
What `kelso ps` and the web UI list, as opposed to every app the host's repos
offer.
_Avoid_: installed app

**Load**:
Freeze a bundle into the kelso host's run directory so the app can start;
loading again picks up changes to the bundle.
_Avoid_: install, deploy

**Unload**:
Take an app off the working set, keeping its data and config.
_Avoid_: uninstall

**Remove**:
Unload an app and delete its temp and logs. An app that is removed always
leaves the working set; emptying the temp of an app that stays loaded is
cleanup.
_Avoid_: delete, uninstall

**Reset**:
Remove an app and empty every volume kelso holds for it, keeping its config, so
it next starts as it did when first loaded. Host volumes are the operator's and
are never touched.
_Avoid_: wipe, clear data

**Purge**:
Reset an app and delete its config, secrets and routes as well: nothing kelso
holds for it remains. Config only ever goes with the data, never alone.

**App resolution**:
Which app a name typed on the command line stands for: a loaded app, an app in
a repo, or none because several match.

**Start group**:
The order, 0 to 9, in which apps start; group 0 (kelso-ui) stays up when every
other app is stopped.

**Up / down**:
Move a kelso host's running apps toward a start group, one direction only:
`up N` starts groups 1 to N and never stops anything, `down N` stops groups
above N and never starts anything.

### Configuring apps

**App config**:
Everything an operator sets on one app, each under one name: its own config
values (no dots), and `route.<name>`, `volume.<name>`, `backup.<name>` and
`network.mode`.
_Avoid_: settings, options, env

**Secret**:
A config value kelso keeps encrypted and never accepts on a command line; it
is typed at a prompt or read from stdin, and shown only by revealing it. A
*host secret* is one `config.toml`
refers to, such as a route provider's password.
_Avoid_: password, credential
