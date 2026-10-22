# kproxy

A DNS server and transparent HTTP/HTTPS proxy that decides, per domain,
whether a set of containers may reach the internet. Point a container's DNS at
it and every outbound connection by name goes through it, where it is allowed,
denied, or held until someone decides. It never terminates TLS.

It is a standalone Go program whose only dependency is `golang.org/x/net`
(which brings in `golang.org/x/sys`). Kelso
runs one per app, but nothing in it is Kelso-specific.

## How it works

- **DNS.** Every `A` query gets the address it arrived on, which is the
  proxy's own address on the asker's network. `AAAA` and every other type get
  an empty answer.
- **Proxy.** Connections arrive on port 80 or 443. For HTTP the proxy reads the
  `Host` header; for HTTPS it reads the server name (SNI) from the TLS
  ClientHello. It decides, then dials the real host and pipes the bytes
  through untouched, starting with the ones it already read.
- **Refused outright, whatever the rules say:** connections with no name (no
  SNI, no `Host`) or an IP address for a name. Names with no root domain
  (single labels, unknown TLDs, bare suffixes like `co.uk`) are refused too,
  unless an allow rule with a [forward target](#forward-targets) matches them.
- **Rejections.** HTTP gets `503` with `Retry-After`; HTTPS gets a TLS
  `access_denied` alert.

The proxy only sees connections made by name, so it needs the network layout
in [In Docker](#in-docker) to stop an app connecting straight to an IP
address.

## Default policy

Rules say what to do with the domains they name. The default policy says what
to do with everything else:

| Default policy | A host no rule matches is… |
|---|---|
| `deny` | rejected |
| `allow` | proxied, and counted in the stats like everything else |
| `ask` | held up to `-hold` (60s) and listed as pending until someone decides |

`ask` works like [Little Snitch](https://www.obdev.at/products/littlesnitch/):
the app's connection waits while you allow or deny the domain, and deciding it
releases every connection held for it. A connection still undecided when its
hold runs out is closed without a verdict, and the domain stays pending.
`allow` is useful for finding out what an app talks to before writing its
rules.

Until rules are loaded, every connection is held and `/healthz` fails.

## Rules

A rule names a domain and a verdict. It matches that name and every name ending
in `.` plus it, so `google.com` matches `maps.google.com` but not
`notgoogle.com`. Any name works, down to a bare TLD such as `com`. The most
specific matching rule wins:

```
default ask
deny nepthar.github.io
allow tacos.nepthar.github.io
```

Here `tacos.nepthar.github.io` and its subdomains are allowed, the rest of
`nepthar.github.io` is denied, and the rest of `github.io` is up to the
default policy: held and asked about.

That is also the rules file format (`-rules-file`): one `default`, `allow` or
`deny` line each, and lines starting with `#` are ignored. With a rules file,
the proxy loads it at startup and rewrites it (atomically, keeping its
permissions) on every change, starting with a comment that says what the file
is. Without one, rules live in memory until something pushes them with
`PUT /rules`.

### Forward Targets

An allow rule can name a host and port to connect to, instead of the name
asked for on its usual port. It is how an app on an internal network reaches
one service on your LAN:

```
allow adguard rpi.lab.my-domain.com:3000
allow nas 192.168.1.20:5000
```

The app connects to `http://adguard`. Docker's resolver passes the name to the
proxy, the proxy answers with itself, and the connection is piped to
`rpi.lab.my-domain.com:3000`. The app still can't reach that address, or anything
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

## Ingress

Containers on an `internal: true` network can't publish ports, so nothing
outside can reach them directly. `-ingress LISTEN=HOST:PORT` relays the other
way: kproxy listens on `LISTEN` and pipes each connection to `HOST:PORT`, with
no rules checked. Add `/udp` for a UDP port:

```
-ingress 20001=main:8080 -ingress 20002=main:10001/udp
```

Publish `LISTEN` on the kproxy container, and the app is reachable through it.
`HOST` is resolved on every connection, so a compose service name works.

The app sees kproxy's address as every client's. For UDP, kproxy keeps a
socket towards the target for each client address and drops it after two
minutes without a reply, at most 1,024 at once.

## Running it

kproxy needs Go 1.27.3 or newer, which fixes security bugs in the TLS and
HTTP code it uses. `go.mod` says so, so an older `go` command fetches that
version itself, and the Dockerfile builds with it.

### In Docker

```sh
docker build -t kproxy:dev .
```

The proxy container is the only one that straddles both networks. Apps sit on
an `internal: true` network, which has no route out, so a connection straight
to an IP address fails as unreachable. The proxy is on that network too,
and also on an ordinary network that can reach the internet. The only way out
is by name, through the proxy:

```
[ app ]  [ app ]  [ db ]        internal network (internal: true): no route out
   │        │
   └───┬────┘                   DNS, HTTP and HTTPS, by name only
       ▼
  [ kproxy ]                    on both networks
       │
       ▼
ordinary network ──► internet and LAN
```

Containers on the internal network still reach each other directly by
service name (`db` above); Docker's own DNS answers those before anything
reaches the proxy.

In the compose file, the proxy gets a fixed address on the internal network
and a healthcheck; apps set `dns:` to that address and wait for the proxy to be
healthy. The proxy itself isn't told its address. It only has to be fixed
because Docker's `dns:` setting takes an IP address, not a name, and fixing an
address means giving the network its own subnet. Every internal network needs
a different one, since Docker refuses overlapping subnets.
[`itest/compose.yml`](itest/compose.yml) is a complete example.

```yaml
proxy:
  image: kproxy:dev
  user: "1000:1000"   # owns the admin socket; binding 53/80/443 still works
  command: ["-admin", "/data/admin.sock", "-rules-file", "/data/rules.txt"]
  networks:
    internal: { ipv4_address: 10.88.0.2 }
    egress: {}
  volumes: ["./kproxy-data:/data"]
  healthcheck:
    test: ["CMD", "/kproxy", "healthcheck", "-admin", "/data/admin.sock"]
app:
  dns: 10.88.0.2
  networks: [internal]
  depends_on: { proxy: { condition: service_healthy } }
```

with the networks defined as:

```yaml
networks:
  internal:
    internal: true
    ipam:
      config:
        - subnet: 10.88.0.0/24
          ip_range: 10.88.0.128/25
  egress: {}
```

The `ip_range` keeps the addresses Docker hands out away from the proxy's, or
Docker may give it to another container.

### Standalone, with a real browser

This runs the proxy as an ordinary program on a Mac and points a throwaway
Chrome window at it, so you can browse and approve domains as they show up.
Nothing on the machine changes: everything listens on localhost, on high
ports, and Chrome uses its own profile.

**1. Build and write a rules file.** From this folder, into a scratch folder
outside the repo:

```sh
mkdir -p ~/kproxy-test && go build -o ~/kproxy-test/kproxy . && printf 'default ask\nallow google.com\n' > ~/kproxy-test/rules.txt
```

**2. Terminal 1: run the proxy.**

```sh
cd ~/kproxy-test && ./kproxy -dns 127.0.0.1:15353 -http 127.0.0.1:8080 -https 127.0.0.1:8443 -admin ./admin.sock -admin-http 127.0.0.1:18099 -rules-file ./rules.txt -hold 30s
```

The DNS server isn't used here, but it has to listen somewhere, and the
default port 53 needs root.

**3. Open the admin page** in your normal browser, not the one from step 5:

```sh
open http://127.0.0.1:18099/
```

It refreshes every few seconds: pending hosts as a tree under their root
domain, the rules, and traffic per domain.

**4. Terminal 2: check it with curl.** `--connect-to` sends curl to the proxy
while keeping the real name in the TLS handshake and `Host` header, which is
what the proxy reads.

```sh
curl -sS -o /dev/null -w '%{http_code}\n' --connect-to ::127.0.0.1:8443 https://www.google.com/
```

That prints `200`. This one hangs, because `example.com` has no rule and is
held:

```sh
curl -sS -o /dev/null -w '%{http_code}\n' --connect-to ::127.0.0.1:8443 https://example.com/
```

Click **allow** on `example.com` in the admin page within 30 seconds and curl
finishes with `200`. Click **deny** instead and it fails with `tlsv1 alert
access denied`. Do nothing and the connection closes after 30 seconds, with
`example.com` still pending. Plain HTTP goes to port 8080 the same way
(`--connect-to ::127.0.0.1:8080 http://...`), and a deny there gets a `503`.

**5. Terminal 3: browse with an isolated Chrome.**

```sh
open -na "Google Chrome" --args --user-data-dir="$HOME/kproxy-test/chrome-profile" --no-first-run --disable-quic --host-resolver-rules="MAP *:443 127.0.0.1:8443, MAP *:80 127.0.0.1:8080" https://www.google.com/
```

- `--user-data-dir` starts a separate Chrome with its own profile, so your
  usual windows, logins and history are untouched.
- `--host-resolver-rules` resolves every name to the proxy, keeping HTTPS and
  HTTP on their own ports, as the proxy's DNS would inside Docker.
- `--disable-quic` stops Chrome trying HTTP/3, which runs over UDP; the proxy
  only handles TCP.

Google itself loads, but pages look broken until you allow `gstatic.com`,
where Google keeps its images and scripts. Pending also fills up with
Chrome's own background traffic: `update.googleapis.com`,
`content-autofill.googleapis.com`, `gvt1.com` and so on. Allow or deny them
in the admin page and watch the held requests go through or fail. Every
decision is written to `~/kproxy-test/rules.txt`, so it survives a restart of
the proxy.

**6. Clean up.** Quit the isolated Chrome window (it's its own app instance),
stop the proxy with Ctrl-C, then:

```sh
rm -rf ~/kproxy-test
```

macOS won't let an unprivileged program bind ports 80 and 443 on localhost,
which is why the proxy uses 8080 and 8443 and Chrome remaps to them. Unix
socket paths are limited to about 104 characters there, so keep `-admin`
short.

## Flags

| Flag | Default | |
|---|---|---|
| `-dns` | `:53` | DNS listen address, UDP and TCP |
| `-http`, `-https` | `:80`, `:443` | proxy listen addresses |
| `-admin` | `/run/kproxy/admin.sock` | admin unix socket |
| `-admin-http` | | also serve the admin API and UI on this TCP address |
| `-rules-file` | | load rules from and save them to this file |
| `-default` | `deny` | default policy for a rules file that doesn't set one |
| `-hold` | `60s` | how long the `ask` policy holds a connection |
| `-max-conns` | `1024` | most client connections open at once |
| `-ingress` | | `LISTEN=HOST:PORT[/udp]`, repeatable; see [Ingress](#ingress) |

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
| `GET /rules` | default policy and rules; `?format=text` for the rules file |
| `PUT /rules` | replace everything: `{"default": "ask", "rules": [{"domain": "adguard", "verdict": "allow", "target": "rpi.lab.my-domain.com:3000"}]}` |
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
