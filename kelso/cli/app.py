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
      "if it names none or several. A loaded app wins over bundles that are "
      "not loaded. Other matches are listed on stderr."
    ),
  )
  which.add_argument(
    "app", metavar="APP", help="App ID, its last part, or <app>@<repo>"
  )
  which.add_argument(
    "--loaded", action="store_true", help="Resolve only to a loaded app"
  )
  which.set_defaults(func=run_which)


def run_which(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  found = ctx.resolve_app(args.app)
  resolved = found.resolved
  if args.loaded and resolved is not None and not resolved.loaded:
    resolved = None
  for match in found.matches:
    if match != resolved:
      logger.info("%s%s", match.line(), " (loaded)" if match.loaded else "")
  if resolved is None:
    raise SystemExit(1)
  print(resolved.line())
