#!/usr/bin/env bash
# Runs kproxy in a real compose stack and checks what passes. Needs docker and
# internet access; builds kproxy:dev from the parent directory.
set -uo pipefail
cd "$(dirname "$0")"

fails=0
expect() { # expect <description> <want> <got>
  if [[ "$3" == *"$2"* ]]; then echo "ok   $1"; else echo "FAIL $1: want '$2', got '$3'"; fails=$((fails + 1)); fi
}
c() { docker compose exec -T client "$@" 2>&1; }
admin() { docker compose exec -T --user 0 client curl -sS --unix-socket /run/kproxy/admin.sock "$@" 2>&1; }
push() {
  docker compose run --rm --no-deps -T --user 0 --entrypoint curl client -sS \
    --unix-socket /run/kproxy/admin.sock -X PUT http://k/rules -w '%{http_code}' -d "$1" 2>/dev/null
}
health() { docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q proxy)"; }
code() { c curl -sS -m 10 -o /dev/null -w '%{http_code}' "$@"; }
rules='{"mode":"snitch","rules":[{"domain":"example.com","verdict":"allow"},{"domain":"example.org","verdict":"deny"}]}'

docker build -q -t kproxy:dev .. >/dev/null || exit 1
docker compose down -v -t 1 >/dev/null 2>&1
trap 'docker compose down -v -t 1 >/dev/null 2>&1' EXIT
docker compose up -d proxy internal-web lan-web >/dev/null 2>&1

sleep 3
expect "unhealthy before rules" "starting" "$(health)"
expect "push rules" "204" "$(push "$rules")"
sleep 2
expect "healthy after rules" "healthy" "$(health)"
docker compose up -d client >/dev/null 2>&1 || { echo "client did not start"; exit 1; }

expect "A answered with proxy" "172.30.99.2" "$(c nslookup www.example.com)"
expect "internal name via docker" "172.30.99.1" "$(c nslookup internal-web)"
expect "AAAA empty" "0" "$(c nslookup -type=AAAA example.com | grep -c '^Address: ')"
expect "MX empty" "0" "$(c nslookup -type=MX example.com | grep -c 'mail exchanger')"

expect "https allowed, subdomain" "200" "$(code https://www.example.com/)"
expect "http allowed" "200" "$(code http://example.com/)"
expect "https denied" "access denied" "$(c curl -sS -m 10 https://example.org/)"
expect "http denied" "503" "$(code http://example.org/)"
expect "raw ip unreachable" "Could not connect" "$(c curl -sS -m 5 https://1.1.1.1/)"
expect "no SNI denied" "access denied" "$(c curl -sSk -m 5 https://172.30.99.2/)"
expect "no Host denied" "503" "$(c sh -c 'printf "GET / HTTP/1.0\r\n\r\n" | nc -w 3 172.30.99.2 80')"
expect "other port refused" "Could not connect" "$(c curl -sS -m 5 https://example.com:8443/)"
expect "forward reaches LAN by name" "200" "$(code http://lanweb/passwd)"
expect "forwarded LAN ip unreachable" "Could not connect" "$(c curl -sS -m 5 http://172.30.98.10:8080/passwd)"
expect "forward listed in rules" '"lanweb":"172.30.98.10:8080"' "$(admin http://k/rules)"

held=$(mktemp)
c curl -sS -m 10 -o /dev/null -w '%{http_code}' https://github.com/ >"$held" &
sleep 1
expect "unknown is pending" '"domain":"github.com"' "$(admin http://k/pending)"
admin -X POST http://k/decide -d '{"domain":"github.com","verdict":"allow"}' >/dev/null
wait
expect "held conn released on approve" "200" "$(cat "$held")"
expect "held conn closed on timeout" "000" "$(code https://gitlab.com/)"
expect "still pending after timeout" '"domain":"gitlab.com"' "$(admin http://k/pending)"
expect "stats split by scheme" '"example.com":{"http":' "$(admin http://k/stats)"

docker compose restart -t 1 proxy >/dev/null 2>&1
sleep 2
expect "unhealthy after restart" "starting" "$(health)"
expect "github approval gone until re-push" "000" "$(code https://github.com/)"
push "$rules" >/dev/null
expect "allowed after re-push" "200" "$(code https://www.example.com/)"

echo "$fails failed"
exit $((fails > 0))
