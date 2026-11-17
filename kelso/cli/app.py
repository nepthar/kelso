"""`kelso app` -- which app and bundle an id names."""

import argparse
import logging

from kelso.lib.kelso import KelsoCtx

logger = logging.getLogger("kelso.cli")


def register(subparsers) -> None:
  parser = subparsers.add_parser(
    "app", help="Print the app an id resolves to, and its bundle"
  )
  sub = parser.add_subparsers(dest="app_command", required=True)

  which = sub.add_parser(
    "which",
    help="Print the app an id resolves to, and its bundle",
    description=(
      "Prints `<app id> <bundle path>` for the app that APP names, and exits 1 "
      "if it names none or several."
    ),
  )
  which.add_argument(
    "app", metavar="APP", help="App ID, its last part, or <app>@<repo>"
  )
  which.add_argument("--loaded", action="store_true", help="Look only at loaded apps")
  which.set_defaults(func=run_which)


def run_which(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  name, _, repo = args.app.partition("@")
  app = ctx.inventory.app_id(name, prefer_loaded=args.loaded, repo=repo)
  if args.loaded and app not in ctx.inventory.loaded:
    raise ValueError(f"{app} is not loaded")
  entry = ctx.inventory.bundle(app, repo)
  print(app, entry.path if entry else "-")
