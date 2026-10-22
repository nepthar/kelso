# kproxy

A DNS server and transparent HTTP/HTTPS proxy that decides, per domain,
whether a set of containers may reach the internet. Point a container's DNS at
it and every outbound connection by name goes through it, where it is allowed,
denied, or held until someone decides. It never terminates TLS.

It is a standalone Go program with one dependency (`golang.org/x/net`). Kelso
runs one per app, but nothing in it is Kelso-specific.

## How it works

- **DNS.** Every `A` query gets the proxy's own address (`-ip`). `AAAA` and
  every other type get an empty answer.
- **Proxy.** Connections arrive on port 80 or 443. For HTTP the proxy reads the
  `Host` header; for HTTPS it reads the server name (SNI) from the TLS
  ClientHello. It decides, then dials the real host and pipes the bytes
  through untouched, starting with the ones it already read.
- **Refused outright, in every mode:** connections with no name (no SNI, no
  `Host`) or an IP address for a name. Names with no root domain (single
  labels, unknown TLDs, bare suffixes like `co.uk`) are refused too, unless an
  allow rule with a [target](#targets) matches them.
- **Rejections.** HTTP gets `503` with `Retry-After`; HTTPS gets a TLS
  `access_denied` alert.

The proxy only sees connections made by name. To stop an app connecting
straight to an IP address, put it on a Docker network with `internal: true`
(no route out) and give the proxy a second, normal network for egress. See
[`itest/compose.yml`](itest/compose.yml).

## Modes

| Mode | Allowed by a rule | Denied by a rule | No matching rule |
|---|---|---|---|
| `strict` | proxied | rejected | rejected |
| `snitch` | proxied | rejected | held up to `-hold` (60s) and listed as pending |
| `track` | proxied | rejected | proxied |

In snitch mode, deciding a pending domain releases every connection held for
it. A connection still undecided when its hold runs out is closed without a
verdict, and the domain stays pending.

Until rules are loaded, every connection is held and `/healthz` fails.

## Rules

A rule names a domain and a verdict. It matches that name and every name ending
in `.` plus it, so `google.com` matches `maps.google.com` but not
`notgoogle.com`. Any name works, down to a bare TLD such as `com`. The most
specific matching rule wins:

```
mode snitch
deny nepthar.github.io
allow tacos.nepthar.github.io
```

Here `tacos.nepthar.github.io` and its subdomains are allowed, the rest of
`nepthar.github.io` is denied, and the rest of `github.io` is undecided.

That is also the rules file format (`-rules-file`): one `allow`, `deny` or
`mode` line each, `#` for comments. With a rules file, the proxy loads it at
startup and rewrites it (atomically, keeping its permissions) on every change.
Without one, rules live in memory until something pushes them with
`PUT /rules`.

### Targets

An allow rule can name a host and port to connect to, instead of the name
asked for on its usual port. It is how an app on an internal network reaches
one service on your LAN:

```
allow adguard rpi.den.zyx.pw:3000
allow nas 192.168.1.20:5000
```

The app connects to `http://adguard`. Docker's resolver passes the name to the
proxy, the proxy answers with itself, and the connection is piped to
`rpi.den.zyx.pw:3000`. The app still can't reach that address, or anything
else on the LAN, directly. A bare name like `adguard` gets through only
because its rule has a target: `allow adguard` alone is still refused, so the
proxy's own DNS can't be used to reach arbitrary LAN names.

- The host is a name or an IPv4 address, and the port is required and
  numeric. A name is resolved by the proxy's resolver (in Docker, the host's
  DNS) on every connection, over IPv4.
- Targets follow the same matching as any rule, so `allow example.com
  1.2.3.4:443` pins every subdomain of `example.com` to that address.
- Both schemes go to the one target, so the app must use the scheme its port
  speaks.
- The target sees `Host: adguard`, or SNI `adguard`, and HTTPS certificates
  won't match that name.
- A container with the same name wins, because Docker resolves it first.
- Only HTTP and HTTPS: the name comes from the `Host` header or SNI.

## Running it

### In Docker

```sh
docker build -t kproxy:dev .
```

In the compose file, the proxy gets a fixed address on the internal network
and a healthcheck; apps set `dns:` to that address and wait for the proxy to be
healthy:

```yaml
proxy:
  image: kproxy:dev
  user: "1000:1000"   # owns the admin socket; binding 53/80/443 still works
  command: ["-ip", "10.88.0.2", "-admin", "/data/admin.sock", "-rules-file", "/data/rules.txt"]
  volumes: ["./kproxy-data:/data"]
  healthcheck:
    test: ["CMD", "/kproxy", "healthcheck", "-admin", "/data/admin.sock"]
app:
  dns: 10.88.0.2
  depends_on: { proxy: { condition: service_healthy } }
```

Give the internal network an `ip_range` that excludes the proxy's address, or
Docker may hand it to another container.

### Standalone

```sh
go build -o kproxy . && printf 'mode snitch\nallow google.com\n' > rules.txt
./kproxy -ip 127.0.0.1 -dns 127.0.0.1:15353 -http 127.0.0.1:8080 -https 127.0.0.1:8443 \
  -admin ./admin.sock -admin-http 127.0.0.1:18099 -rules-file ./rules.txt
```

Then send traffic through it with curl (`--connect-to ::127.0.0.1:8443`), or a
throwaway Chrome profile that maps every name to the proxy:

```sh
open -na "Google Chrome" --args --user-data-dir="$PWD/chrome-profile" --disable-quic \
  --host-resolver-rules="MAP *:443 127.0.0.1:8443, MAP *:80 127.0.0.1:8080"
```

macOS won't let an unprivileged program bind 80/443 on localhost, hence the
port mapping. Unix socket paths are limited to about 104 characters there.

## Flags

| Flag | Default | |
|---|---|---|
| `-ip` | (required) | IPv4 address returned for every `A` query |
| `-dns` | `:53` | DNS listen address, UDP and TCP |
| `-http`, `-https` | `:80`, `:443` | proxy listen addresses |
| `-admin` | `/run/kproxy/admin.sock` | admin unix socket |
| `-admin-http` | | also serve the admin API and UI on this TCP address |
| `-rules-file` | | load rules from and save them to this file |
| `-mode` | `strict` | mode for a rules file that doesn't name one |
| `-hold` | `60s` | how long snitch mode holds an undecided connection |
| `-max-conns` | `1024` | most client connections open at once |

`kproxy healthcheck [-admin PATH]` exits 0 once the proxy on that socket has
rules loaded.

## Admin API

Served on the unix socket, and on `-admin-http` if set. There is no
authentication: anyone who can reach it can change the rules. Over TCP, it
only accepts requests addressed to an IP or `localhost`, and writes must be
`Content-Type: application/json`, which keeps other websites in your browser
out.

| | |
|---|---|
| `GET /` | web UI |
| `GET /healthz` | `200` once rules are loaded, else `503` |
| `GET /pending` | undecided hosts, grouped by root domain |
| `POST /decide` | `{"domain": "github.com", "verdict": "allow"}`; no target |
| `GET /rules` | mode and rules; `?format=text` for the rules file |
| `PUT /rules` | replace everything: `{"mode": "snitch", "rules": [{"domain": "adguard", "verdict": "allow", "target": "rpi.den.zyx.pw:3000"}]}` |
| `DELETE /rules/{name}` | remove one rule; that name goes back to undecided |
| `GET /stats` | per root domain, HTTP and HTTPS counted separately |

```sh
curl --unix-socket admin.sock http://k/pending
```

The web UI shows pending hosts as a tree under their root domain, with
allow/deny on every row, plus the rules and traffic. Roots are taken from the
ICANN part of the Public Suffix List, so `googleapis.com` and `github.io` are
roots; rows whose subdomains belong to many different owners (the list's
private part) are tagged "shared".

## Resources

Measured on arm64 Linux: about 8 MB idle, and about 22 KB and 6 file
descriptors (2 sockets, 2 kernel pipes) per open connection, so roughly 30 MB
at the default `-max-conns`. On Linux the bytes are spliced between sockets
rather than copied through the program. Once one side of a connection has
finished, the other gets 30 seconds before both are closed. The pending list
keeps at most 1,000 hosts; more are still held, just not listed. The stats
keep at most 10,000 domains and count the rest under `other`. Setting
`GOMEMLIMIT` makes Go give memory back sooner after a spike.

## Testing

```sh
go test -race ./...                          # unit tests
go test -run '^$' -bench . -benchtime 3000x  # per-connection cost; run on Linux
./itest/run.sh                               # a real compose stack; needs Docker and internet
```

## Not supported

IPv6, ports other than 80 and 443, protocols other than HTTP and TLS, TLS
termination, and different rules for different containers behind one proxy.
