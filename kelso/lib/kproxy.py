"""What kelso needs to run a `network_mode = "restricted"` app behind kproxy.

A restricted app's units sit on an internal network with no route out. kproxy
is the one container on both that network and an ordinary one: it answers the
units' DNS, lets them reach only the domains in `[endpoints]`, and relays each
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
INTERNAL_NETWORK = "internal"
EGRESS_NETWORK = "egress"

# Each restricted app gets one /24 from here, recorded in kelsodb like a port.
SUBNET_POOL = ipaddress.ip_network("10.250.0.0/16")

# kproxy's own ports inside its container: DNS, HTTP and HTTPS. Ingress
# listeners are numbered from INGRESS_PORT_BASE, clear of all three.
INGRESS_PORT_BASE = 20000

RULES_HEADER = (
  "# rules.txt for kproxy. Lists allowed domains. "
  "See github.com/nepthar/kelso/kproxy for more information"
)

_LABEL = re.compile(r"^[a-z0-9_-]+$")


def endpoint_name(value: str) -> str:
  """Normalize an [endpoints] entry: a domain name, matched with its subdomains.

  Raises ValueError for anything kproxy would refuse as a rule name.
  """
  name = value.strip().lower().removesuffix(".")
  try:
    ipaddress.ip_address(name)
    is_ip = True
  except ValueError:
    is_ip = False
  if not name or is_ip or not all(_LABEL.match(label) for label in name.split(".")):
    raise ValueError(f"{value!r} is not a domain name")
  return name


def subnets() -> Iterable[ipaddress.IPv4Network]:
  return SUBNET_POOL.subnets(new_prefix=24)


def proxy_address(subnet: str) -> str:
  """kproxy's fixed address on an app's internal network: the subnet's .2."""
  return str(ipaddress.ip_network(subnet)[2])


def dynamic_range(subnet: str) -> str:
  """The upper half of the subnet, which docker hands out; kproxy's .2 is below it."""
  return str(list(ipaddress.ip_network(subnet).subnets(prefixlen_diff=1))[1])


def rules_text(endpoints: Mapping[str, Iterable[str]]) -> str:
  """The rules file for an app: everything in [endpoints] allowed, the rest denied."""
  names = sorted({endpoint_name(n) for group in endpoints.values() for n in group})
  return (
    "\n".join([RULES_HEADER, "default deny", *(f"allow {n}" for n in names)]) + "\n"
  )


def source_dir() -> Path | None:
  """The kproxy source next to this kelso, if this kelso runs from a checkout."""
  path = Path(__file__).resolve().parents[2] / "kproxy"
  return path if (path / "Dockerfile").is_file() else None


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
      "ipam": {"config": [{"subnet": subnet, "ip_range": dynamic_range(subnet)}]},
    },
    EGRESS_NETWORK: {},
  }


def compose_service(
  *,
  source: Path,
  subnet: str,
  ingress: Iterable[tuple[int, str, int, str]],
) -> dict[str, Any]:
  """The kproxy service. `ingress` is (host port, unit, container port, proto) per
  route: kproxy publishes each host port and relays it to the unit."""
  command = ["-admin", f"{KPROXY_GUEST_DIR}/admin.sock"]
  command += ["-rules-file", f"{KPROXY_GUEST_DIR}/rules.txt"]
  ports = []
  for i, (host_port, unit, container_port, proto) in enumerate(ingress):
    listen = INGRESS_PORT_BASE + i
    suffix = "/udp" if proto == "udp" else ""
    command += ["-ingress", f"{listen}={unit}:{container_port}{suffix}"]
    ports.append(f"{host_port}:{listen}{suffix}")
  service: dict[str, Any] = {
    "image": image_tag(source),
    "build": {"context": str(source)},
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
    "restart": "on-failure",
  }
  if ports:
    service["ports"] = ports
  return service
