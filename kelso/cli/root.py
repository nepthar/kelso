import argparse

from kelso.lib.kelso import KelsoCtx


def register(subparsers) -> None:
  parser = subparsers.add_parser("root", help="Print the kelso root path")
  parser.set_defaults(func=run)


def run(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  print(ctx.config.kelso_root)
