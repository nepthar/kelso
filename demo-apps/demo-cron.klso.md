# Cron Demo

For exercising `[commands]` and `[cron]`. The container does nothing but stay
up; everything happens in `demo.sh`, which each command runs with a different
first argument. The commands write to and read from `stamps.log` on a data
volume, so you can see what ran and when.

Run any of them by hand with `kelso cmd demo-cron <command> [args]`, or the
Run button on the app's page. The cron jobs below run the same commands on a
schedule once the app is started: watch them land on the Cron page, then use
`kelso cron` to see what is next and `kelso cron tick` to run whatever is due.

| Command | What it does |
| --- | --- |
| `stamp [note]` | Appends a timestamp and the note to the log |
| `show [n]` | Prints the last `n` lines of the log (default 20) |
| `clear` | Empties the log |
| `fail [status]` | Exits with `status` (default 3), to show a failed run |
| `slow [seconds]` | Sleeps, to show a run that times out |

The cron jobs: a stamp every five minutes, a `show` on the hour, a failure
every half hour, and every two hours a `slow` that outlasts its 60-second
timeout.

```toml klso_path="manifest.toml"
[app]
version      = "0.1.0"
display_name = "Cron Demo"
description  = "Commands and cron jobs that stamp, read, fail, and time out"
author       = "Kelso Server"
url          = "https://github.com/nepthar/kelso"

[volumes]
script = { kind = "app", src = "demo.sh" }
stamps = { kind = "data", desc = "stamps.log, written by the stamp command" }

[run.main]
image   = "alpine:latest"
# Idles until stopped; the TERM trap lets `kelso stop` finish within a second.
cmd     = ["/bin/sh", "-c", "trap 'exit 0' TERM; while :; do sleep 1; done"]
volumes = { script = "/demo/demo.sh", stamps = "/data" }

[commands]
stamp = { cmd = "/demo/demo.sh stamp", desc = "Append a timestamp, and any note given, to the log" }
show  = { cmd = "/demo/demo.sh show", desc = "Print the last lines of the log (default 20)" }
clear = { cmd = "/demo/demo.sh clear", desc = "Empty the log" }
fail  = { cmd = "/demo/demo.sh fail", desc = "Exit with a non-zero status (default 3)" }
slow  = { cmd = "/demo/demo.sh slow", desc = "Sleep for a while (default 90 seconds)" }

[cron.stamp-every-five]
schedule = "*/5 * * * *"
command  = "stamp"
args     = "from cron"

[cron.show-hourly]
schedule = "0 * * * *"
command  = "show"
args     = "5"

[cron.fail-half-hourly]
schedule = "*/30 * * * *"
command  = "fail"

[cron.too-slow]
schedule = "15 */2 * * *"
command  = "slow"
args     = "120"
timeout  = 60
```

`demo.sh`, mounted at `/demo/demo.sh`:

```sh klso_path="demo.sh:+x"
#!/bin/sh
# demo-cron's commands: `demo.sh <command> [args]`.
LOG=/data/stamps.log

case "$1" in
  stamp)
    shift
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) ${*:-by hand}" >> "$LOG"
    tail -n 1 "$LOG"
    ;;
  show)
    if [ -s "$LOG" ]; then tail -n "${2:-20}" "$LOG"; else echo "No stamps yet."; fi
    ;;
  clear)
    : > "$LOG"
    echo "Cleared."
    ;;
  fail)
    echo "Failing on purpose with status ${2:-3}."
    exit "${2:-3}"
    ;;
  slow)
    echo "Sleeping ${2:-90} seconds."
    sleep "${2:-90}"
    echo "Done."
    ;;
  *)
    echo "Unknown command: $1" >&2
    exit 64
    ;;
esac
```
