# Write an app, run it here, deploy it to a box

1. **Write the bundle** in `$(kelso root)/repos/local/`. Its file name is its
   app id: `com.example.notes.klso.md` is the app `com.example.notes`. The
   smallest bundle is one file:

   ````markdown
   # Notes

   ```toml klso_path="manifest.toml"
   [app]
   version      = "0.1.0"
   display_name = "Notes"
   description  = "A place for notes"

   [volumes]
   data = { kind = "data" }

   [run.main]
   image   = "ghcr.io/example/notes:1.4.2"
   volumes = { data = "/data" }

   [run.main.routes]
   main = { port = "8080" }
   ```
   ````

   `kelso agent-help manifest` is the full reference. Pin image tags to a
   version; `latest` is whatever the box pulled last. A route is HTTP or HTTPS;
   anything else (UDP, discovery, a raw TCP protocol) goes under
   `[run.main.ports]`.

2. **Check it without running it:** `kelso inspect <path>` prints what the
   manifest resolves to, or every parse error with the key that caused it.

3. **Run it:** `kelso dev <path>` loads the bundle and runs it in the
   foreground, printing its routes. If the app needs config, pass
   `--set NAME=VALUE` for each; with no terminal, dev refuses and names what
   is missing. It holds the terminal, so run it in the background and stop it
   with `kill <pid>`: that stops the containers and cleans up, as Ctrl-C does.
   Fetch the route's URL to see that the app answers.

4. **Deploy it:** `kelso deploy <path> <host>` copies the bundle into
   the box's `repos/local`, then loads and starts it there, or updates it if
   that box already runs it from the same place.

## When deploy stops partway

- **The app needs config or a host volume.** Deploy copies the bundle, then
  stops and lists what the app needs on that box, with the choices for each.
  Run it again with `--set NAME=VALUE` for each: a config name, or
  `volume.<name>` for a host volume. A host volume tag must exist in that
  box's `config.toml` first. A secret is for a person to set:
  `ssh <host>` then `kelso config <app> --edit`.
- **The box has the app from somewhere else.** Deploy refuses and copies
  nothing. The message says where it was loaded from.

## What a clean local run does not prove

Docker Desktop on a Mac hides file ownership problems that Linux shows. A
container runs as whatever user its image sets; kelso does not change that.
Kelso makes each volume folder writable by any user, so an image that runs as
a non-root user can still write to its volumes, and files owned by root in a
volume on the box are normal for an image that runs as root. Never mount one
volume inside another's path. Check the app on a Linux box before
calling it done: `kelso ps` there should say `running`, and `healthy` if the
app has a healthcheck.
