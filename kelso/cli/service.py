"""`kelso service` -- kelsod as a systemd user service."""

import argparse
from pathlib import Path

from kelso.lib import service
from kelso.lib.kelso import KelsoCtx
from kelso.lib.util import Conn

NO_SYSTEMD = (
  "This machine does not run systemd, so kelsod was not installed as a service. "
  "Run `kelsod` in a terminal when you want the web UI."
)


def register(subparsers) -> None:
  parser = subparsers.add_parser("service", help="Run kelsod as a systemd service")
  sub = parser.add_subparsers(dest="service_command", required=True)

  install = sub.add_parser(
    "install", help="Write kelsod's systemd user unit, then start it now and at boot"
  )
  install.set_defaults(func=_install)


def install_service(config_path: Path, conn: Conn) -> None:
  """Write and start the unit. Raises RuntimeError naming what is left to do."""
  if not service.has_systemd():
    raise RuntimeError(NO_SYSTEMD)
  unit = service.write_unit(config_path)
  conn.out(f"Wrote {unit}")
  service.activate()
  conn.out(f"kelsod is running as {service.UNIT_NAME}, and will start at boot.")
  conn.out(f"  Logs: journalctl --user -u {service.UNIT_NAME}")


def _install(_args: argparse.Namespace, ctx: KelsoCtx, conn: Conn) -> None:
  install_service(ctx.config.config_path, conn)
