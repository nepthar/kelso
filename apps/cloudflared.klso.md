# Cloudflared

The Cloudflare Tunnel connector. It dials out to Cloudflare and serves the
hostnames the `cloudflare_tunnel` route provider publishes, so apps become
reachable without opening a port on your router.

Create the tunnel in the Cloudflare dashboard (Zero Trust → Networks →
Tunnels), copy its token, and set it here:

```
kelso install cloudflared
kelso config cloudflared --set tunnel_token=<token>
kelso start cloudflared
```

The token is what this container needs. The route provider separately needs an
API token, a tunnel id and an account id; see `[route_provider.<tag>]` in
`config.toml`.

```toml klso_path="manifest.toml"
[app]
version      = "1.0.0"
display_name = "Cloudflared"
description  = "Cloudflare Tunnel connector for the cloudflare_tunnel route provider"
source       = "github:nepthar/kelso/main/apps/cloudflared.klso.md"

[config]
tunnel_token = { desc = "Tunnel token from Zero Trust > Networks > Tunnels", secret = true }

[run.main]
image = "docker.io/cloudflare/cloudflared:2026.9.1"
cmd   = ["tunnel", "--no-autoupdate", "run"]
env   = { TUNNEL_TOKEN = "${tunnel_token}" }
```
