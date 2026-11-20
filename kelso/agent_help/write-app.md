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
   version; `latest` is whatever the box pulled last.

2. **Check it without running it:** `kelso inspect <path>` prints what the
   manifest resolves to, or every parse error with the key that caused it.

3. **Run it:** `kelso dev <path>` loads the bundle and runs it in the
   foreground until interrupted, printing its routes. It holds the terminal, so
   run it in the background and stop it with an interrupt signal when done.
   Fetch the route's URL to see that the app answers.

4. **Deploy it:** `kelso deploy <path> <host> --yes` copies the bundle into
   the box's `repos/local`, then loads and starts it there, or updates it if
   that box already runs it from the same place.

## When deploy stops partway

- **The app needs config or a host volume.** The first deploy loads the app
  and `start` refuses, naming what is missing. Finish on the box:
  `ssh <host> 'kelso start <app> --set key=value --bind volume=tag'`. A host
  volume tag must exist in that box's `config.toml` first.
- **The box has the app from somewhere else.** Deploy refuses and copies
  nothing. The message says where it was loaded from.

## What a clean local run does not prove

Docker Desktop on a Mac hides file ownership problems that Linux shows. Never
mount one volume inside another's path. Check the app on a Linux box before
calling it done: `kelso ps` there should say `running`, and `healthy` if the
app has a healthcheck.
