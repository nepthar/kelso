"""`kelso secret`: secrets, which no other command prints."""

import argparse

from kelso.lib.kelso import KelsoCtx


def register(subparsers) -> None:
  parser = subparsers.add_parser("secret", help="Reveal an app's or this host's secret")
  sub = parser.add_subparsers(dest="secret_command", required=True)

  reveal = sub.add_parser(
    "reveal",
    help="Print a secret in plaintext",
    description=(
      "With APP, prints that app's secret config value NAME; without, the host "
      "secret NAME that config.toml refers to."
    ),
  )
  reveal.add_argument("names", nargs="+", metavar="[APP] NAME")
  reveal.set_defaults(func=run_reveal)


def run_reveal(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  if len(args.names) > 2:
    raise ValueError("Give NAME for a host secret, or APP NAME for an app's")
  if len(args.names) == 1:
    (name,) = args.names
    value = ctx.kelso_db.get_secret(name)
    if value is None:
      raise ValueError(f"No host secret {name!r}; `kelso system secret` lists them")
    print(value)
    return

  raw, name = args.names
  app = ctx.resolve_app_id(raw)
  with ctx.locked(f"secret reveal {app}", app):
    secret, value = ctx.app_store(app).get_config(name)
  if value is None:
    raise ValueError(f"{app} has no {name} set; `kelso config {app}` lists its config")
  if not secret:
    raise ValueError(f"{name} is not a secret; `kelso config {app} {name}` prints it")
  print(value)
