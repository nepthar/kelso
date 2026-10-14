# Compose Passthrough Demo

Compose has many options kelso does not model. `[run.<unit>.compose]` copies
whatever you put there verbatim into the service's section of the generated
compose.yml. Keys kelso manages (image, ports, ...) are rejected at parse
time.

This manifest produces a service like:

```yaml
services:
  main:
    image: redis:7-alpine
    # ...
    mem_limit: 256m
    stop_grace_period: 30s
    ulimits:
      nofile:
        soft: 10032
        hard: 10032
```

Its healthcheck is not passthrough: `healthcheck` is a run-unit field, and
kelso writes it into compose with its own timing.

```toml klso_path="manifest.toml"
[app]
version      = "0.1.0"
display_name = "Compose Passthrough Demo"
description  = "Uses [run.<unit>.compose] to set compose options kelso doesn't model"

[run.main]
image       = "redis:7-alpine"
healthcheck = "redis-cli ping || exit 1"

[run.main.compose]
mem_limit         = "256m"
stop_grace_period = "30s"

[run.main.compose.ulimits.nofile]
soft = 10032 # Integers stay integers all the way to compose.yml.
hard = 10032
```
