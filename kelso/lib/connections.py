"""Connections: what an app asks kelso to reach for it, by kind.

A manifest declares connections in `[connections]` and attaches them to run
units. A kind decides how it reaches a unit -- by mounting a host path at
`/run/kelso/conn/<name>`, or for a network kind through kproxy -- and what it
tells the manifest, as `${conn.<name>.<property>}`. It never sets the
environment itself: the manifest maps properties to whatever names the app
reads.

A kind with a host path hands the app control of something outside it, so each
is a danger callout. A network kind is what a restricted app may reach.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
  from kelso.lib.config import Config

CONN_KEY_PREFIX = "conn."


@dataclass(frozen=True)
class ConnectionKind:
  name: str
  # What the app gets; also the danger callout.
  desc: str
  # None for a network kind, which mounts nothing.
  host_path: Callable[["Config"], Path] | None
  # Property -> value, given the config and where the host path is mounted,
  # or for a network kind the connection's name.
  properties: Callable[["Config", str], dict[str, str]]
  property_names: tuple[str, ...]

  @property
  def network(self) -> bool:
    return self.host_path is None


def guest_path(name: str) -> str:
  """Where a connection's host path is mounted in each unit it is attached to."""
  # Not under /kelso: a unit with commands has that mounted read-only, and
  # docker cannot make a mount point inside it.
  return f"/run/kelso/conn/{name}"


CONNECTION_KINDS = {
  kind.name: kind
  for kind in (
    ConnectionKind(
      "kelso.admin",
      "kelsod's admin API: full control of kelso",
      lambda config: config.conn_root,
      lambda config, mounted: {"socket": f"{mounted}/admin.sock"},
      ("socket",),
    ),
    ConnectionKind(
      "docker.admin",
      "the docker daemon: full control of the host",
      lambda config: Path("/var/run/docker.sock"),
      lambda config, mounted: {"socket": mounted, "host": f"unix://{mounted}"},
      ("socket", "host"),
    ),
    ConnectionKind(
      "web",
      "the domains it lists, over HTTP and HTTPS",
      None,
      lambda config, name: {},
      (),
    ),
    ConnectionKind(
      "forward",
      "one address on your network, by this connection's name",
      None,
      lambda config, name: {"host": name, "url": f"http://{name}"},
      ("host", "url"),
    ),
  )
}
