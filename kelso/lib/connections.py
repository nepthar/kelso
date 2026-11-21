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
  hosts: tuple[str, ...]
  property_names: ClassVar[tuple[str, ...]] = ()


@dataclass(frozen=True)
class HttpConnection:
  # host:port, which may hold `${config}` references.
  address: str
  property_names: ClassVar[tuple[str, ...]] = ("host", "url")

  @staticmethod
  def properties(host: str) -> dict[str, str]:
    return {"host": host, "url": f"http://{host}"}


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
  return f"/run/kelso/conn/{name}"
