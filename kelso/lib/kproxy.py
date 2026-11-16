"""What kelso needs to run an app behind kproxy: `restrict`, `ask` and `watch`.

An app behind kproxy has its units sit on an internal network with no route out. kproxy
is the one container on both that network and an ordinary one: it answers the
units' DNS, lets them reach only what `[connections]` lists, and relays each
of the app's routes in to its unit. The kproxy program itself lives in the
repository's `kproxy/` directory; this module only generates what runs it.
"""

import hashlib
import ipaddress
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

KPROXY_SERVICE = "kproxy"
KPROXY_GUEST_DIR = "/run/kproxy"
KPROXY_RUN_DIR = "kproxy"
# What kproxy saw, written when it stops: `{"stats": ..., "pending": ...}`.
STATS_FILE = "stats.json"
INTERNAL_NETWORK = "internal"
EGRESS_NETWORK = "egress"

# config.toml's `subnet_pool` when it sets none. Each app behind kproxy gets one
# /24 from the pool, recorded in kelsodb like a port.
DEFAULT_SUBNET_POOL = "10.250.0.0/16"

# kproxy's own ports inside its container: DNS, HTTP and HTTPS. Ingress
# listeners are numbered from INGRESS_PORT_BASE, clear of all three.
INGRESS_PORT_BASE = 20000

# Set to rules_digest() when compose runs, so a change of rules -- a forward
# whose config changed -- recreates kproxy instead of leaving it on the old ones.
RULES_ENV = "KELSO_KPROXY_RULES"

_LABEL = re.compile(r"[a-z0-9_-]+")


# Docker 27 added the gateway mode option that keeps a kproxy app's network
# from having an address on the host; an older docker ignores it silently.
MIN_DOCKER = 27


def docker_too_old(major: int) -> str | None:
  """Why apps behind kproxy can't run on this docker, or None if they can."""
  if major >= MIN_DOCKER:
    return None
  return (
    f"docker {major} is older than {MIN_DOCKER}, which apps behind kproxy need to "
    "keep their network apart from the host. Upgrade docker."
  )


def endpoint_name(value: str) -> str:
  """Normalize a domain name, which a rule matches with its subdomains.

  Raises ValueError for anything kproxy would refuse as a rule name.
  """
  name = value.strip().lower().removesuffix(".")
  try:
    ipaddress.ip_address(name)
    is_ip = True
  except ValueError:
    is_ip = False
  if not name or is_ip or not all(_LABEL.fullmatch(label) for label in name.split(".")):
    raise ValueError(f"{value!r} is not a domain name")
  return name


def forward_target(value: str) -> str:
  """Normalize an http connection's address: host:port, the host a name or IPv4 address.

  Raises ValueError for anything kproxy would refuse as a rule target.
  """
  host, sep, port = value.strip().rpartition(":")
  bad = ValueError(
    f"address {value!r} must be host:port with a numeric port, "
    "like 192.168.1.3:3000 or nas.lan:80"
  )
  if not sep or not port.isdigit() or not 1 <= int(port) <= 65535:
    raise bad
  try:
    ip = ipaddress.ip_address(host)
  except ValueError:
    ip = None
  if ip is not None:
    if ip.version != 4:
      raise ValueError(f"address {value!r}: only IPv4 addresses are supported")
    return f"{ip}:{int(port)}"
  try:
    return f"{endpoint_name(host)}:{int(port)}"
  except ValueError:
    raise bad from None


def subnets(pool: str) -> Iterable[ipaddress.IPv4Network]:
  """The /24s in `pool`. Raises ValueError unless it is an IPv4 network that holds one."""
  net = ipaddress.ip_network(pool)
  if net.version != 4 or net.prefixlen > 24:
    raise ValueError(f"{pool!r} must be an IPv4 network of /24 or larger")
  return net.subnets(new_prefix=24)


def proxy_address(subnet: str) -> str:
  """kproxy's fixed address on an app's internal network: the subnet's .2."""
  return str(ipaddress.ip_network(subnet)[2])


def dynamic_range(subnet: str) -> str:
  """The upper half of the subnet, which docker hands out; kproxy's .2 is below it."""
  return str(list(ipaddress.ip_network(subnet).subnets(prefixlen_diff=1))[1])


def rules_text(
  hosts: Iterable[str],
  forwards: Mapping[str, str],
  ingress: Iterable[tuple[str, int, str, int, str]] = (),
  *,
  default: str = "deny",
  denied: Iterable[str] = (),
) -> str:
  """The rules file for an app: what it may reach, how routes get in, and the
  `default` policy (deny, ask or allow) for everything else.

  Each of `hosts` is allowed with its subdomains over HTTP and HTTPS. Each of
  `forwards` is a name over HTTP sent to its host:port. `ingress` is (route, host
  port, unit, container port, proto) per route; the i-th gets `ingress_port(i)`.
  Each of `denied` is refused with its subdomains, even if `hosts` lists it.
  Raises ValueError for a forward that isn't host:port, naming it.
  """
  http: dict[str, str] = {}
  https: dict[str, str] = {}
  refused = {endpoint_name(host) for host in denied}
  for host in hosts:
    if endpoint_name(host) in refused:
      continue
    pattern = "*." + endpoint_name(host)
    http.setdefault(pattern, "")
    https[pattern] = ""
  for host in refused:
    http["*." + host] = https["*." + host] = " deny"
  for name, target in forwards.items():
    try:
      http[endpoint_name(name)] = " " + forward_target(target)
    except ValueError as e:
      raise ValueError(f"connection {name}: {e}") from None
  lines = [f"http {name}{dest}" for name, dest in sorted(http.items())]
  lines += [f"https {name}{verdict}" for name, verdict in sorted(https.items())]
  lines += [
    f"ingress {proto} :{ingress_port(i)} {name} {unit}:{container_port}"
    for i, (name, _, unit, container_port, proto) in enumerate(ingress)
  ]
  return "\n".join([f"default {default}", *lines]) + "\n"


def rules_json(rules: str) -> dict[str, Any]:
  """`rules_text`'s output in the long form kproxy's `PUT /rules` takes."""
  out: dict[str, Any] = {"rules": []}
  for line in rules.splitlines():
    words = line.split()
    match words:
      case ["default", policy]:
        out["default"] = policy
      case ["http" | "https" as kind, name, *rest]:
        rule = {
          "kind": "http" if kind == "http" else "tls",
          "port": 80 if kind == "http" else 443,
          "name": name,
          "verdict": "deny" if rest == ["deny"] else "allow",
        }
        if rest and rest != ["deny"]:
          rule["dest"] = rest[0]
        out["rules"].append(rule)
      case ["ingress", proto, port, name, dest]:
        out["rules"].append(
          {
            "kind": proto,
            "port": int(port.removeprefix(":")),
            "ingress": True,
            "name": name,
            "verdict": "allow",
            "dest": dest,
          }
        )
  return out


def ingress_port(i: int) -> int:
  """The port kproxy listens on for the i-th route `rules_text` was given."""
  return INGRESS_PORT_BASE + i


def rules_digest(rules: str) -> str:
  """Put in kproxy's environment, so new rules recreate the container."""
  return hashlib.sha256(rules.encode()).hexdigest()[:12]


def source_dir() -> Path | None:
  """kproxy's source: shipped inside an installed kelso, or beside a checkout."""
  here = Path(__file__).resolve().parents[1]
  for path in (here / "_kproxy", here.parent / "kproxy"):
    if (path / "Dockerfile").is_file():
      return path
  return None


def image_tag(source: Path) -> str:
  """An image tag that changes whenever anything the image is built from does."""
  digest = hashlib.sha256()
  for path in sorted(source.iterdir()):
    if path.name in ("Dockerfile", "go.mod", "go.sum", "ui.html") or (
      path.suffix == ".go" and not path.name.endswith("_test.go")
    ):
      digest.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
  return f"kelso-kproxy:{digest.hexdigest()[:12]}"


def compose_networks(subnet: str) -> dict[str, Any]:
  return {
    INTERNAL_NETWORK: {
      "internal": True,
      # Without this the host has an address on the bridge, and units reach
      # anything the host itself listens on (sshd, kelsod) through it.
      "driver_opts": {"com.docker.network.bridge.gateway_mode_ipv4": "isolated"},
      "ipam": {"config": [{"subnet": subnet, "ip_range": dynamic_range(subnet)}]},
    },
    EGRESS_NETWORK: {},
  }


def compose_service(*, source: Path, subnet: str, ports: list[str]) -> dict[str, Any]:
  """The kproxy service. `ports` publishes each route's host port on the
  `ingress_port` its rule listens on."""
  command = ["-admin", f"{KPROXY_GUEST_DIR}/admin.sock"]
  command += ["-rules-file", f"{KPROXY_GUEST_DIR}/rules.txt"]
  command += ["-stats-file", f"{KPROXY_GUEST_DIR}/{STATS_FILE}"]
  service: dict[str, Any] = {
    "image": image_tag(source),
    "build": {"context": str(source)},
    # The image exists nowhere but here: build it rather than try to pull it.
    "pull_policy": "never",
    "command": command,
    "networks": {
      INTERNAL_NETWORK: {"ipv4_address": proxy_address(subnet)},
      EGRESS_NETWORK: {},
    },
    "volumes": [
      {
        "type": "bind",
        "source": f"./{KPROXY_RUN_DIR}",
        "target": KPROXY_GUEST_DIR,
        "bind": {"create_host_path": False},
      }
    ],
    "healthcheck": {
      "test": [
        "CMD",
        "/kproxy",
        "healthcheck",
        "-admin",
        f"{KPROXY_GUEST_DIR}/admin.sock",
      ],
      "interval": "2s",
      "retries": 30,
    },
    "environment": {"RULES": f"${{{RULES_ENV}}}"},
    "restart": "on-failure",
  }
  if ports:
    service["ports"] = ports
  return service
