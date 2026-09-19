"""What each connection kind wires into a run unit, and the values it publishes.

A manifest attaches connections by name and reads what they publish as
`${connections.<name>.<field>}`; how a kind is delivered -- a mount today,
networks or a proxy later -- is decided here and nowhere else.
"""

from dataclasses import dataclass
from pathlib import Path

from kelso.lib.config import Config
from kelso.lib.manifest import CONNECTION_FIELDS
from kelso.lib.spec import AppConnection

# Where connection mounts land inside a container, one directory per connection.
GUEST_ROOT = "/run/kelso"


@dataclass(frozen=True)
class Wiring:
  # Host directories that must exist before the container starts; docker would
  # create a missing one as root.
  host_dirs: tuple[Path, ...]
  # Compose `volumes` entries.
  mounts: tuple[str, ...]
  # field -> value, for `${connections.<name>.<field>}`.
  values: dict[str, str]


def wire(connection: AppConnection, config: Config) -> Wiring:
  match connection.kind:
    case "kelso_admin":
      guest = f"{GUEST_ROOT}/{connection.name}"
      socket = config.admin_socket_path
      wiring = Wiring(
        host_dirs=(socket.parent,),
        mounts=(f"{socket.parent}:{guest}",),
        values={"socket": f"{guest}/{socket.name}"},
      )
    case other:
      raise ValueError(f"connection {connection.name}: unknown kind {other!r}")
  assert set(wiring.values) == set(CONNECTION_FIELDS[connection.kind])
  return wiring


def grants(connection: AppConnection) -> str:
  """What the connection lets the app do, in the operator's terms."""
  match connection.kind:
    case "kelso_admin":
      return "control of kelso: it can start, stop, uninstall and restore any app"
    case other:
      raise ValueError(f"connection {connection.name}: unknown kind {other!r}")
