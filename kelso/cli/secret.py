"""`kelso secret`: host secrets, the recovery phrase, and revealing any secret.

App secrets are set with `kelso config <app> NAME=-`; only `reveal` prints one.
"""

import argparse

from tabulate import tabulate

from kelso.cli import phrase
from kelso.cli.kv import read_secret
from kelso.lib import recovery
from kelso.lib.kelso import KelsoCtx
from kelso.lib.rekey import rekey


def register(subparsers) -> None:
  parser = subparsers.add_parser(
    "secret",
    help="Host secrets, the recovery phrase, and revealing a secret",
    formatter_class=argparse.RawDescriptionHelpFormatter,
    description=(
      "Host secrets are values kelso keeps encrypted in its database rather\n"
      "than in config.toml, such as a route provider's password or API key.\n"
      "config.toml refers to one by name from an arg ending in _secret:\n"
      "\n"
      "  [route_provider.web.args]\n"
      '  password_secret = "route_provider.web.password"\n'
      "\n"
      "With no command, lists every secret config.toml refers to and every\n"
      "one stored, and whether each is set."
    ),
  )
  parser.set_defaults(func=run_list)
  sub = parser.add_subparsers(dest="secret_command")

  set_ = sub.add_parser(
    "set",
    help="Set a host secret, asked for on a terminal, otherwise read from stdin",
  )
  set_.add_argument("name", metavar="NAME")
  set_.set_defaults(func=run_set, activity="secret")

  unset = sub.add_parser("unset", help="Remove a host secret")
  unset.add_argument("name", metavar="NAME")
  unset.set_defaults(func=run_unset, activity="secret")

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

  rekey_cmd = sub.add_parser(
    "rekey",
    help="Make a new recovery phrase and re-encrypt every secret under it",
    description="Makes a new recovery phrase, re-encrypts every app secret and "
    "host secret under the key it derives, and shows the phrase. "
    "The old key is kept, to read backups taken under it. Rekeying does not "
    "undo an exposed key: whoever had it could already read every secret, so "
    "change the secrets themselves too.",
  )
  rekey_cmd.add_argument(
    "--phrase",
    action="store_true",
    help="Enter an existing recovery phrase instead of making a new one",
  )
  rekey_cmd.set_defaults(func=run_rekey, activity="rekey")

  phrase_cmd = sub.add_parser(
    "recovery-phrase", help="Show this kelso's recovery phrase"
  )
  phrase_cmd.set_defaults(func=run_recovery_phrase)


def run_list(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  stored = set(ctx.kelso_db.list_secrets())
  refs = ctx.config.secret_references()
  names = sorted({*stored, *refs})
  if not names:
    print(
      "No host secrets yet. A route provider in config.toml names the one "
      'it needs with an arg like password_secret = "route_provider.web.password"; '
      "set it with `kelso secret set <name>`."
    )
    return

  rows = [
    [
      name,
      "set" if name in stored else "missing",
      ", ".join(refs.get(name, [])) or "(nothing in config.toml)",
    ]
    for name in names
  ]
  print(tabulate(rows, headers=["NAME", "STATUS", "USED BY"], tablefmt="simple"))
  if any(name not in stored for name in names):
    print("\nSet a missing one with `kelso secret set <name>`.")


def run_set(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  # Asked for before the lock, which nothing else should wait on while you type.
  value = read_secret(args.name)
  if not value:
    raise ValueError(f"No value given for {args.name!r}; nothing was stored")
  with ctx.kelso_lock("secret set"):
    db = ctx.kelso_db
    verb = "Replaced" if args.name in db.list_secrets() else "Set"
    db.set_secret(args.name, value)
  print(f"{verb} host secret {args.name!r}")


def run_unset(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  with ctx.kelso_lock("secret unset"):
    db = ctx.kelso_db
    if args.name not in db.list_secrets():
      raise ValueError(f"No host secret {args.name!r}; `kelso secret` lists them")
    db.del_secret(args.name)
  print(f"Removed host secret {args.name!r}")


def run_reveal(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  if len(args.names) > 2:
    raise ValueError("Give NAME for a host secret, or APP NAME for an app's")
  if len(args.names) == 1:
    (name,) = args.names
    value = ctx.kelso_db.get_secret(name)
    if value is None:
      raise ValueError(f"No host secret {name!r}; `kelso secret` lists them")
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


def run_rekey(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  if args.phrase:
    entered = input(f"Enter the {recovery.WORDS} words, separated by spaces: ")
    entropy = recovery.entropy_from_phrase(entered.split())
  else:
    entropy = recovery.new_entropy()

  result = rekey(ctx, entropy)
  print(
    f"Re-encrypted {result.values} value(s) across {result.apps} app(s) and "
    f"kelsodb under the new key.\n"
  )
  if not args.phrase:
    phrase.show(recovery.phrase(entropy))
  if result.unreadable:
    raise ValueError(
      f"{len(result.unreadable)} value(s) could not be decrypted with any key on "
      "file and were left as they were. Set them again with `kelso config`."
    )


def run_recovery_phrase(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  keyfile = recovery.read_keyfile(ctx.config.master_keyfile)
  if keyfile.seed is None:
    raise ValueError(
      f"No recovery phrase in {ctx.config.master_keyfile}. Make one with "
      "`kelso secret rekey`."
    )
  print(recovery.format_phrase(recovery.phrase(keyfile.seed)))
