# Free-form Docker Options Demo

The sibling of `demo-compose`. That one stays inside `_COMPOSE_ALLOWED_KEYS`,
so it passes through in silence. This one does not, so kelso warns about it
everywhere the app can be reviewed.

Kelso does not try to say what an arbitrary compose key *does* — that would
mean maintaining a table of every key against every docker version. It says
what it knows, and shows the request:

```
Warning: This application sets free-form docker options on main that are not
guaranteed to be safe. Please review them before continuing:
  cap_add = ["SYS_ADMIN"]
  pid = "host"
  privileged = true
```

`kelso load` prints that and asks before loading (`-y` skips it),
`kelso inspect` lists it under `Danger:`, and the Repos page in the web UI
prints it on the app card beside the manifest asking for it.

Three things worth noticing in the manifest below:

- `mem_limit` is on the allowlist, so it is *not* in the warning. Only the
  keys kelso does not model are.
- `pid = "host"` puts the container in the host's process table: it can see,
  and as root signal, every process on the machine. One line, easy to miss in
  a long manifest, which is why it is listed.
- `privileged = true` is host root: the container can mount the host disk.
  Nothing here refuses it — the box belongs to the operator — but it can no
  longer arrive without being said out loud.

The container itself does nothing but sleep.

```toml klso_path="manifest.toml"
[app]
version      = "0.1.0"
display_name = "Free-form Docker Options Demo"
description  = "Sets [run.<unit>.compose] keys kelso does not model, and gets warned about"

[run.main]
image = "alpine:3"
cmd   = ["sleep", "infinity"]

[run.main.compose]
mem_limit  = "64m"           # Allowlisted: shapes the container, warns about nothing.
privileged = true            # Host root.
cap_add    = ["SYS_ADMIN"]   # Capabilities beyond the container default.
pid        = "host"          # Sees every process on the host.
```
