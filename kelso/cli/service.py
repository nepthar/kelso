"""`kelso system service` -- kelsod as a systemd user service."""

import argparse
from pathlib import Path

from kelso.lib import service
from kelso.lib.kelso import KelsoCtx

NO_SYSTEMD = (
  "This machine does not run systemd, so kelsod was not installed as a service. "
  "Run `kelsod` in a terminal if you need the admin socket and daemon."
)


def register(subparsers) -> None:
  parser = subparsers.add_parser(
    "service", help="Write kelsod's systemd user unit, then start it now and at boot"
  )
  parser.set_defaults(func=_install)


def install_service(root: Path) -> None:
  """Write and start the unit. Raises RuntimeError naming what is left to do."""
  if not service.has_systemd():
    raise RuntimeError(NO_SYSTEMD)
  unit = service.write_unit(root)
  print(f"Wrote {unit}")
  service.activate()
  print(f"kelsod is running as {service.UNIT_NAME}, and will start at boot.")
  print(f"  Logs: journalctl --user-unit {service.UNIT_NAME}")


def _install(_args: argparse.Namespace, ctx: KelsoCtx) -> None:
  install_service(ctx.config.kelso_root)
