"""Connections: what an app asks kelso to reach for it.

A manifest declares connections in `[connections]`, and each tells the manifest
about itself as `${conn.<name>.<property>}`. It never sets the environment
itself: the manifest maps properties to whatever names the app reads.

A mounted connection is a host path at `/run/kelso/conn/<name>`, only in the
units that list or reference it. It hands the app control of something outside
it, so each is a danger callout. A network connection is what a restricted app
may reach through kproxy: it is app-wide, not restricted to a single container.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
  from kelso.lib.config import Config

CONN_KEY_PREFIX = "conn."


@dataclass(frozen=True)
class MountKind:
  name: str
  # What the app gets; also the danger callout.
  desc: str
  host_path: Callable[["Config"], Path]
  # Property -> value, given the config and where the host path is mounted.
  properties: Callable[["Config", str], dict[str, str]]
  property_names: tuple[str, ...]


MOUNT_KINDS = {
  kind.name: kind
  for kind in (
    MountKind(
      "kelso.admin",
      "kelsod's admin API: full control of kelso",
      lambda config: config.conn_root,
      lambda config, mounted: {"socket": f"{mounted}/admin.sock"},
      ("socket",),
    ),
    MountKind(
      "docker.admin",
      "the docker daemon: full control of the host",
      lambda config: Path("/var/run/docker.sock"),
      lambda config, mounted: {"socket": mounted, "host": f"unix://{mounted}"},
      ("socket", "host"),
    ),
  )
}


@dataclass(frozen=True)
class MountConnection:
  kind: MountKind

  @property
  def property_names(self) -> tuple[str, ...]:
    return self.kind.property_names


@dataclass(frozen=True)
class WebConnection:
  """Domains on the internet, each with its subdomains."""

  hosts: tuple[str, ...]
  property_names: ClassVar[tuple[str, ...]] = ()


@dataclass(frozen=True)
class HttpConnection:
  """One address on the operator's own network, reached by the connection's name."""

  # host:port, which may hold `${config}` references.
  address: str
  property_names: ClassVar[tuple[str, ...]] = ("host", "url")

  @staticmethod
  def properties(name: str) -> dict[str, str]:
    return {"host": name, "url": f"http://{name}"}


NetworkConnection = WebConnection | HttpConnection
Connection = MountConnection | NetworkConnection


def network_connections(
  connections: Mapping[str, Connection],
) -> dict[str, NetworkConnection]:
  return {
    name: conn
    for name, conn in connections.items()
    if not isinstance(conn, MountConnection)
  }


def guest_path(name: str) -> str:
  """Where a mounted connection's host path is in each unit given it."""
  # Not under /kelso: a unit with commands has that mounted read-only, and
  # docker cannot make a mount point inside it.
  return f"/run/kelso/conn/{name}"
