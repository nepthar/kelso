# Kelso App Case Study - Unifi Network Application

In this case study, we walk through how I built `unifi-network-application.klso` in about half an hour using Linuxserver.io's documentation and sample docker compose file.

By following along, you will see:

- How kelso apps make multi-container software easy to distribute
- How to translate a docker compose file into kelso's world
- How to provision volumes, secrets, and routes

## Goal - Run Unifi's Network Application so you can manage your wifi

The Unifi Network Application is a piece of software that Ubiquiti developed to manage their wifi access points. If you fully buy into their hardware stack, this will run on their hardware. However, they also provide it in a format that can be run on your own hardware (like a raspberry pi, for instance).

Linuxserver.io takes this software, packages it, and distributes it in a container that we can run. However, their instructions require a fair amount of setup and knowledge to actually stand up a robust, "home-production-ready" deployment. The kelso ecosystem solves this for us.

Feel free to skip to the completed kelso app at [apps/unifi-network-application.klso](../apps/unifi-network-application.klso).


## Step 1. Make the bare kelso app
Let's start with a barebones app containing what we already know. Bundles you write yourself go in the `local` repo, so this is `~/kelso/repos/local/unifi-network-application.klso/manifest.toml`. The folder name is the app id:
```toml
[app]
version = "0.1.0"
description = "The unifi network application"

[adv_config]
# I want this on my network as "https://unifi.<my domain>"
subdomain = { default = "unifi" }

[config]
# There will probably be config, not sure what yet

[volumes]
# I imagine we'll have to put the Application's state somewhere.

[run.main]
# There will be SOME container that has to run here.
```

`subdomain` is one of the options every app has. Declaring it here only changes its default, and putting it in `[adv_config]` keeps it out of the way of someone filling in the config.

## Step 2. General overview
You can find the instructions here: [https://github.com/linuxserver/docker-unifi-network-application](https://github.com/linuxserver/docker-unifi-network-application). As of this writing (summer 2026), the instructions contain a few key parts:

### **The Image**: `lscr.io/linuxserver/unifi-network-application:latest`
Looks like it works for both amd64 and arm. Great.

### **Mongodb**
The setup instructions mention that this image expects a mongodb instance, properly configured, and of a supported version:
> Starting with version 8.1 of Unifi Network Application, mongodb 3.6 through 7.0 are supported. Starting with version 9.0 of Unifi Network Application, mongodb 8.0 is also supported.

> Make sure you pin your database image version and do not use latest, as mongodb does not support automatic upgrades between major versions.

> MongoDB >4.4 on X86_64 Hardware needs a CPU with AVX support. Some lower end Intel CPU models like Celeron and Pentium (before Tiger-Lake) more Details: Advanced Vector Extensions - Wikipedia don't support AVX, but you can still use MongoDB 4.4.

It looks like we're going to have two "run units" in this kelso app, and probably two separate volumes, one for each. The instructions suggest using the "official mongodb image".

**Thought**: maybe we should pin versions since it seems like there's some limitations on which versions of the app work with which versions of mongodb. Let's use latest for the application for now, and pick the most recent mongodb 8 release.

```toml
# manifest.toml additions:

[volumes]
# provision two "data"-class volumes, designed to store important app state.
app_config = { kind = "data" }
db_data    = { kind = "data" }

[run.main]
image = "lscr.io/linuxserver/unifi-network-application:latest"
volumes = { app_config = ... } # Not sure where it needs to mount yet.

[run.unifi-db]
image = "docker.io/mongo:8.3.7"
```

Run units reach each other by name, so the application will find its database at the hostname `unifi-db`.

## Step 3. Mongodb requirements

### Mongodb init script
The setup instructions say
> If you are using the official mongodb container, you can create your user using an init-mongo.sh file with the following contents (do not modify; copy/paste as is):
```sh
#!/bin/bash

if which mongosh > /dev/null 2>&1; then
  mongo_init_bin='mongosh'
else
  mongo_init_bin='mongo'
fi
"${mongo_init_bin}" <<EOF
use ${MONGO_AUTHSOURCE}
db.auth("${MONGO_INITDB_ROOT_USERNAME}", "${MONGO_INITDB_ROOT_PASSWORD}")
db.createUser({
  user: "${MONGO_USER}",
  pwd: "${MONGO_PASS}",
  roles: [
    "clusterMonitor",
    { db: "${MONGO_DBNAME}", role: "dbOwner" },
    { db: "${MONGO_DBNAME}_stat", role: "dbOwner" },
    { db: "${MONGO_DBNAME}_audit", role: "dbOwner" },
    { db: "${MONGO_DBNAME}_restore", role: "dbOwner" }
  ]
})
EOF
```

Hm, that seems important to get right. Let's turn that into a file, `init-mongo.sh` as they suggest, and distribute it with our bundle: `unifi-network-application.klso/init-mongo.sh`, with those contents pasted in directly. The instructions say to mount it read-only at `/docker-entrypoint-initdb.d/init-mongo.sh`.

That makes a new type of volume - an `app` volume. `app` volumes are files that come with the bundle itself. `src` names the file or folder inside the bundle (it defaults to the volume's name), and `app` volumes are always mounted read-only, so there is no `:ro` to remember.

Here are the updated sections:

```toml
[volumes]
db_data     = { kind = "data" }
app_config  = { kind = "data" }
init_script = { kind = "app", src = "init-mongo.sh" }

[run.unifi-db]
image = "docker.io/mongo:8.3.7"
volumes = { db_data = "?", init_script = "/docker-entrypoint-initdb.d/init-mongo.sh" }
```

### Database environment
Looking a bit further down the page, we get a sample docker `compose.yml` snippet for mongodb:

```yaml
  unifi-db:
    image: docker.io/mongo:<version tag>
    container_name: unifi-db
    environment:
      - MONGO_INITDB_ROOT_USERNAME=root
      - MONGO_INITDB_ROOT_PASSWORD=
      - MONGO_USER=unifi
      - MONGO_PASS=
      - MONGO_DBNAME=unifi
      - MONGO_AUTHSOURCE=admin
    volumes:
      - /path/to/data:/data/db
      - /path/to/init-mongo.sh:/docker-entrypoint-initdb.d/init-mongo.sh:ro
    restart: unless-stopped
```

Takeaways:
- The database volume should be mounted at /data/db
- Two passwords need to exist: one for the `root` user, which the mongo image creates on first start and `init-mongo.sh` logs in as, and one for the `unifi` user that the script creates.

Neither password is something a person ever needs to type. So instead of making the operator choose them, we declare them as secrets with `default = "auto"`: kelso generates each one when the app is first loaded, stores it encrypted, and hands it to every container that references it.

Updates to `manifest.toml`

```toml
[config]
# Generated on first load and stored encrypted; nobody has to choose them.
mongo_pass      = { desc = "MongoDB Password", secret = true, default = "auto" }
mongo_root_pass = { desc = "MongoDB root password", secret = true, default = "auto" }

[run.unifi-db]
image = "docker.io/mongo:8.3.7"
volumes = { db_data = "/data/db", init_script = "/docker-entrypoint-initdb.d/init-mongo.sh" }

[run.unifi-db.env]
# The INITDB vars are read by the mongo image itself: they create the root user
# and turn on auth. init-mongo.sh then logs in as root to create MONGO_USER.
MONGO_INITDB_ROOT_USERNAME = "root"
MONGO_INITDB_ROOT_PASSWORD = "${mongo_root_pass}"
MONGO_USER = "unifi"
MONGO_PASS = "${mongo_pass}" # pass in the secret we configured here
MONGO_DBNAME = "unifi"
MONGO_AUTHSOURCE = "admin"
```

## Step 4. The full compose file

Moving towards the end of the instructions, they provide a full-ish docker `compose.yml` file along with a command to run it via `docker`.

```yaml
---
services:
  unifi-network-application:
    image: lscr.io/linuxserver/unifi-network-application:latest
    container_name: unifi-network-application
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=Etc/UTC
      - MONGO_USER=unifi
      - MONGO_PASS=
      - MONGO_HOST=unifi-db
      - MONGO_PORT=27017
      - MONGO_DBNAME=unifi
      - MONGO_AUTHSOURCE=admin
      - MEM_LIMIT=1024 #optional
      - MEM_STARTUP=1024 #optional
      - MONGO_TLS= #optional
    volumes:
      - /path/to/unifi-network-application/data:/config
    ports:
      - 8443:8443
      - 3478:3478/udp
      - 10001:10001/udp
      - 8080:8080
      - 1900:1900/udp #optional
      - 8843:8843 #optional
      - 8880:8880 #optional
      - 6789:6789 #optional
      - 5514:5514/udp #optional
    restart: unless-stopped
```

Lower down, they also provide information on what all of the ports are for. This is helpful for naming our `routes`. We pick the ones we need and skip ones that aren't relevant to our deployment.

Three choices are worth explaining:

- **Pinned host ports.** Access points find the controller on fixed ports (8080 for device communication, 3478/udp for STUN, 10001/udp for discovery), so we pin them with `"host:container"` rather than letting kelso pick a port.
- **Every route is `private`.** A private route is reachable on this machine's address but never handed to a route provider, so nothing is published to the internet by accident. If you want the admin page at `https://admin-unifi.<your domain>`, publish just that one with `kelso config unifi-network-application --route admin=<provider>`.
- **Logs get their own volume.** Linuxserver keeps everything under `/config`, logs included. Mounting `/config/logs` as a `logs` volume keeps them out of snapshots, which only capture `data` volumes.

Using that information, we can complete our `manifest`:
```toml
[app]
version      = "1.0.0"
display_name = "Unifi Network Application"

[adv_config]
subdomain = { default = "unifi" }

[config]
mongo_pass      = { desc = "MongoDB Password", secret = true, default = "auto" }
mongo_root_pass = { desc = "MongoDB root password", secret = true, default = "auto" }

[volumes]
db_data     = { kind = "data", desc = "MongoDB data" }
app_data    = { kind = "data", desc = "Unifi Network Application data" }
app_logs    = { kind = "logs", desc = "Unifi Network Application logs" }
init_script = { kind = "app", src = "init-mongo.sh", desc = "First run initialization script for mongodb" }

[run.unifi-db]
image = "docker.io/mongo:8.3.7"
volumes = { db_data = "/data/db", init_script = "/docker-entrypoint-initdb.d/init-mongo.sh" }

[run.unifi-db.env]
MONGO_INITDB_ROOT_USERNAME = "root"
MONGO_INITDB_ROOT_PASSWORD = "${mongo_root_pass}"
MONGO_USER = "unifi"
MONGO_PASS = "${mongo_pass}"
MONGO_DBNAME = "unifi"
MONGO_AUTHSOURCE = "admin"

[run.main]
image = "lscr.io/linuxserver/unifi-network-application:latest"
volumes = { app_data = "/config/data", app_logs = "/config/logs" }

[run.main.routes]
admin        = { port = "8443:8443", private = true, scheme = "https" }
stun         = { port = "3478:3478/udp", private = true }
ap_discovery = { port = "10001:10001/udp", private = true }
device_comm  = { port = "8080:8080", private = true }
discover_l2  = { port = "1900:1900/udp", private = true }

[run.main.env]
PUID = "1000"
PGID = "1000"
TZ = "Etc/UTC"
MONGO_USER = "unifi"
MONGO_PASS = "${mongo_pass}"
MONGO_HOST = "unifi-db"
MONGO_PORT = "27017"
MONGO_DBNAME = "unifi"
MONGO_AUTHSOURCE = "admin"
MEM_LIMIT = "1024"
MEM_STARTUP = "1024"
MONGO_TLS = ""
```

## Step 5. Problems with raspberry Pi

When we try to run this on a raspberry pi with `kelso start unifi-network-application`, we notice it doesn't seem to be working. Checking the logs with `kelso logs unifi-network-application`, we find that the database uses CPU extensions that the raspberry pi's arm chip doesn't support. No problem, we can walk back the version of mongo until we find one that works.

Between each attempt we want a fresh database, because `init-mongo.sh` only runs against an empty one. `kelso rm --data` empties every volume while keeping the app loaded, with its configuration, secrets and routes intact, so we only set those up once:

```bash
kelso stop unifi-network-application
kelso rm --data unifi-network-application
kelso load unifi-network-application   # picks up the edited manifest
kelso start unifi-network-application
```

It turns out that version 8.0.11 is both >= 8 and can run on the raspberry pi, so we pin it there. Since we pinned the db, let's pin the unifi-network-application to a version we can confirm works for us as well. While we're here, healthchecks let `kelso up` (and the status shown everywhere) know when each container is actually ready, rather than merely running:

```toml
[run.unifi-db]
image = "docker.io/mongo:8.0.11"
volumes = { db_data = "/data/db", init_script = "/docker-entrypoint-initdb.d/init-mongo.sh" }
# `ping` needs no credentials.
healthcheck = ["mongosh", "--quiet", "--eval", "db.adminCommand('ping')"]

...

[run.main]
image = "lscr.io/linuxserver/unifi-network-application:10.0.162"
volumes = { app_data = "/config/data", app_logs = "/config/logs" }
healthcheck = "curl -fsk https://localhost:8443/status || exit 1"
```

Finally, the bundle's `version` should say what it runs. Kelso compares it when you `kelso update`, so we set it to the application's version, `10.0.162`.

## Step 6. Publish!
Now that we've got a working bundle we can share, we can push the folder it lives in to GitHub, and anyone can `kelso repo add github://<user>/<repo>/<branch>/<folder>` to run it.
