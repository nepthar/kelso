# Nginx Proxy Manager

A reverse proxy with a web UI and letsencrypt cert handling.
Runs with `network_mode = "host"` so it can bind ports 80/443 directly; two
data volumes hold its config and certs.

```toml klso_path="manifest.toml"
[app]
version      = "1.0.0"
display_name = "Nginx Proxy Manager"
description  = "Reverse proxy, w/ ssl certs managed by letsencrypt. Pinned to :latest"
network_mode = "host"

[adv_config]
# Routing: up before the apps it routes to.
start_order = { default = "4" }

[volumes]
data    = { kind = "data" }
letsenc = { kind = "data" }

[run.main]
image   = "jc21/nginx-proxy-manager:latest"
volumes = { data = "/data", letsenc = "/etc/letsencrypt" }
```
