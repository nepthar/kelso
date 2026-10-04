# Nginx Proxy Manager

A reverse proxy with a web UI and letsencrypt cert handling.
Runs with `network_mode = "host"` so it can bind ports 80/443 directly; two
data volumes hold its config and certs.

```toml klso_path="manifest.toml"
[app]
version      = "2.16.0"
display_name = "Nginx Proxy Manager"
description  = "Reverse proxy, w/ ssl certs managed by letsencrypt"
network_mode = "host"

[adv_config]
# Routing: up before the apps it routes to.
start_order = { default = "4" }

[volumes]
data    = { kind = "data" }
letsenc = { kind = "data" }

[run.main]
image   = "jc21/nginx-proxy-manager:2.16.0"
volumes = { data = "/data", letsenc = "/etc/letsencrypt" }
# The image ships this script; it is what the project's own docs use.
healthcheck = ["/usr/bin/check-health"]
```
