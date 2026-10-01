import argparse

from tabulate import tabulate

from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle.volumes import volumes_on_disk
from kelso.lib.util import fmt_size, path_size


def register(subparsers) -> None:
  parser = subparsers.add_parser("volumes", help="List all volumes with their sizes")
  parser.set_defaults(func=run)


def run(_args: argparse.Namespace, ctx: KelsoCtx) -> None:
  with ctx.kelso_lock("volumes"):
    rows = sorted(
      (v.app_id, v.name, v.kind, v.use, fmt_size(path_size(v.path)))
      for v in volumes_on_disk(ctx)
    )
    print(
      tabulate(
        rows, headers=["app_id", "volume", "type", "use", "size"], tablefmt="simple"
      )
    )
