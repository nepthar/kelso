# kproxy

A DNS server and transparent proxy that decides what a set of containers may
reach. Point a container's DNS at it and every outbound connection by name goes
through it: HTTP and HTTPS by the name in the traffic, where it is allowed,
denied or held until someone decides, and other TCP and UDP ports to one fixed
destination each. It never terminates TLS.

It is a standalone Go program whose only dependency is `golang.org/x/net`
(which brings in `golang.org/x/sys`). Kelso
runs one per app, but nothing in it is Kelso-specific.

## How it works

- **DNS.** An `A` query for a name the rules would let through gets the
  address it arrived on, which is the proxy's own address on the asker's
  network. Other types for such a name get an empty answer, and every other
  name gets `NXDOMAIN`, so a denied app fails at the lookup.
- **Listeners.** Each port the proxy listens on has one kind, set by the rules:
  - `http` reads the `Host` header and `tls` reads the server name (SNI) from
    the TLS ClientHello, then decide by that name. Many rules share one of these
    ports. Ports 80 (`http`) and 443 (`tls`) always exist, even with no rules.
  - `tcp` and `udp` send everything to their one rule's destination without
    reading it. A `tcp` listener dials as soon as a client connects, so
    protocols where the server speaks first work.

  After deciding, the proxy dials the destination and pipes the bytes through
  untouched, starting with the ones it already read.
- **Refused outright, whatever the rules say:** `http` and `tls` connections
  with no name (no SNI, no `Host`) or an IP address for a name. Names with no
  root domain (single labels, unknown TLDs, bare suffixes like `co.uk`) are
  never let through by the default policy, only by a rule naming them.
- **Rejections.** HTTP gets `503` with `Retry-After`; TLS gets an
  `access_denied` alert.

The proxy only sees connections made by name, so it needs the network layout
in [In Docker](#in-docker) to stop an app connecting straight to an IP
address.

## Default policy

Rules say what to do with the names they match. On `http` and `tls`
listeners, the default policy says what to do with everything else:

| Default policy | A name no rule matches is… |
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

Each line of the rules file (`-rules-file`) is one rule, and says what the
proxy does under the hood: which listener, which name, and where it goes.

```
# rules.txt for kproxy. Lists allowed domains. See github.com/nepthar/kelso/kproxy for more information
default deny

http  adguard                rpi.lab.my-domain.com:3000
http  *.mylab.com
http  admin.mylab.com        deny
https api.weather.com
https *.googleapis.com
https ads.googleapis.com     deny

tls :6443 x.someapi.com      x.someapi.com:443
tcp :9669 redis              redis.lab.my-domain.com:9669
udp :2343 video-stream       cam0.lab.my-domain.com:2343
```

The long form is `<kind> :<port> <name> [<dest>|deny]`:

| Kind | Name | Destination |
|---|---|---|
| `http`, `tls` | a name, or `*.name` for the name and everything below it | optional: `HOST:PORT`, or `*:PORT` for the name asked for; defaults to `*:<port>` |
| `tcp`, `udp` | a label, for the stats and DNS | required: `HOST:PORT` |

`http NAME [...]` is short for `http :80 NAME [...]`, and `https NAME [deny]`
is short for `tls :443 NAME *:443`. `https` takes no destination, so the
line that sends a TLS name somewhere else is always the long one (see
[Forward Targets](#forward-targets)).

- **Matching.** On one listener, an exact name beats any wildcard, and a longer
  wildcard beats a shorter one. Above, `ads.googleapis.com` is denied, the rest
  of `googleapis.com` and below is allowed, and `api.weather.com` is allowed
  but `weather.com` and `x.api.weather.com` are up to the default policy.
  Rules on different listeners never interact. Any name works, down to
  `*.com`.
- **`deny`** only exists on `http` and `tls` listeners, where it narrows a
  wildcard or answers a name for good under `ask`. A port with no rule already
  refuses connections, and `tcp` and `udp` have no name to deny.
- **DNS** answers a name if any listener would let it through: an allow
  matches it, or no rule does and the default isn't `deny`. A `tcp` or `udp`
  rule's name always resolves; above, `redis` does and `redsi` doesn't.
- **Lines** are split on spaces, and everything from `#` to the end of a line
  is a comment.

The proxy refuses to start, and `PUT /rules` refuses the change, if two rules
can't both be true:

- two kinds on one port: `kproxy can't add a tcp listener on port 443 because
  it already has a tls listener`. TCP and UDP ports are separate, so
  `tcp :53` and `udp :53` can coexist, but `http`, `tls` and `tcp` all use TCP
  ports.
- a second rule on a `tcp` or `udp` port, which has no name to tell them apart.
- the same name twice on one listener.
- a port the proxy already uses for DNS or `-ingress`.

It logs a warning, without refusing, for a `tls` rule that sends a name to a
different host, and for a listener with only denies under `default deny`.

With a rules file, the proxy loads it at startup and rewrites it (atomically,
keeping its permissions) on every change, starting with a comment that says
what the file is. Without one, rules live in memory until something pushes
them with `PUT /rules`. Changing the rules opens and closes listeners to match.

### Forward Targets

A destination other than `*` sends a name somewhere other than where DNS
would. It is how an app on an internal network reaches one service on your
LAN:

```
http adguard rpi.lab.my-domain.com:3000
tcp :1883 mqtt 192.168.0.32:1883
```

The app connects to `http://adguard`. Docker's resolver passes the name to the
proxy, the proxy answers with itself, and the connection is piped to
`rpi.lab.my-domain.com:3000`. The app still can't reach that address, or
anything else on the LAN, directly.

- The host is a name or an IPv4 address, and the port is required and
  numeric. A name is resolved by the proxy's resolver (in Docker, the host's
  DNS) on every connection, over IPv4, or for UDP once per client.
- An `http` target sees `Host: adguard`. Servers that route by host name may
  not answer to it.
- A `tls` target sees SNI `adguard`, and its certificate won't match that
  name, so the client's certificate check fails unless it skips it. The proxy
  warns about these.
- A container with the same name wins, because Docker resolves it first.
- `tcp` and `udp` targets get whatever the client sends. Servers that tell
  clients other addresses to connect to, like Redis Cluster or Kafka, don't
  work, because the client can't reach those addresses.

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
mkdir -p ~/kproxy-test && go build -o ~/kproxy-test/kproxy . && printf 'default ask\nhttps *.google.com\nhttp *.google.com\n' > ~/kproxy-test/rules.txt
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

Click **allow** on `*.example.com` in the admin page within 30 seconds and curl
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

Google itself loads, but pages look broken until you allow `*.gstatic.com`,
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
| `-http`, `-https` | `:80`, `:443` | listen addresses for the `http :80` and `tls :443` listeners; rules still call them 80 and 443 |
| `-bind` | every address | address every other listener binds to |
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
| `GET /pending` | undecided hosts, grouped by listener and root domain |
| `POST /decide` | `{"kind": "tls", "port": 443, "name": "*.github.com", "verdict": "allow"}`, on an existing `http` or `tls` listener; no destination |
| `GET /rules` | default policy and rules; `?format=text` for the rules file |
| `PUT /rules` | replace everything: `{"default": "ask", "rules": [{"kind": "http", "port": 80, "name": "adguard", "verdict": "allow", "dest": "rpi.lab.my-domain.com:3000"}]}`; rules use the long form, with no shorthands |
| `DELETE /rules/{kind}/{port}/{name}` | remove one rule, such as `/rules/tls/443/*.github.com`; that name goes back to undecided |
| `GET /stats` | per root domain (or rule name, for `tcp` and `udp`), and per listener |

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

IPv6, QUIC (HTTP/3 over UDP 443, which clients fall back from to TCP), TLS
termination, listening on one address only for some rules, and different
rules for different containers behind one proxy.
