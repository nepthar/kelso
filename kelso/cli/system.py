import argparse
import sys

from tabulate import tabulate

from kelso.cli import activity, decrypt, doctor, phrase, service, volumes
from kelso.cli.kv import parse_kv
from kelso.lib import recovery
from kelso.lib.config_edit import add_host_volume, remove_host_volume, set_host_volume
from kelso.lib.kelso import KelsoCtx
from kelso.lib.rekey import rekey


def register(subparsers) -> None:
  parser = subparsers.add_parser(
    "system", help="Host setup, secrets, and diagnostics for kelso itself"
  )
  sub = parser.add_subparsers(dest="system_command", required=True)

  secret = sub.add_parser("secret", help="List or set encrypted system config")
  secret.add_argument(
    "--set",
    action="append",
    default=[],
    dest="sets",
    metavar="KEY=VALUE",
    help="Set a system config value (repeatable)",
  )
  secret.add_argument(
    "--unset",
    action="append",
    default=[],
    dest="unsets",
    metavar="KEY",
    help="Remove a system config (repeatable)",
  )
  secret.add_argument(
    "--stdin",
    dest="stdin_key",
    metavar="KEY",
    help="Set KEY from stdin (for secrets)",
  )
  secret.set_defaults(
    func=run,
    activity=lambda a: "secret" if a.sets or a.unsets or a.stdin_key else None,
  )

  rekey = sub.add_parser(
    "rekey",
    help="Make a new recovery phrase and re-encrypt every secret under it",
    description="Shows a new recovery phrase, checks you saved it, then "
    "re-encrypts every app secret and system secret under the key it derives. "
    "The old key is kept, to read snapshots taken under it. Rekeying does not "
    "undo an exposed key: whoever had it could already read every secret, so "
    "change the secrets themselves too.",
  )
  rekey.add_argument(
    "--phrase",
    action="store_true",
    help="Enter an existing recovery phrase instead of making a new one",
  )
  rekey.set_defaults(func=run_rekey, activity="rekey")

  phrase_cmd = sub.add_parser(
    "recovery-phrase", help="Show this kelso's recovery phrase"
  )
  phrase_cmd.add_argument(
    "--confirm",
    action="store_true",
    help="Check you saved it, instead of showing it",
  )
  phrase_cmd.set_defaults(func=run_recovery_phrase)

  hv = sub.add_parser(
    "host-volume",
    help="List, add, change or remove [host_volume] entries in config.toml",
  )
  hv.add_argument("--add", metavar="TAG=PATH", help="Declare a new host volume")
  hv.add_argument(
    "--set", dest="set_", metavar="TAG=PATH", help="Replace an existing one"
  )
  hv.add_argument("--rm", metavar="TAG", help="Remove one")
  hv.add_argument(
    "--readonly",
    action="store_true",
    help="With --add/--set: apps may only mount it read-only",
  )
  hv.add_argument(
    "--require-mount",
    action="store_true",
    help="With --add/--set: refuse to start unless the path is a mount point",
  )
  hv.set_defaults(
    func=run_host_volume,
    activity=lambda a: "host-volume" if a.add or a.set_ or a.rm else None,
  )

  for command in (activity, decrypt, doctor, service, volumes):
    command.register(sub)


def run(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  with ctx.kelso_lock("system secret"):
    db = ctx.kelso_db
    changed = False

    if args.stdin_key is not None:
      value = sys.stdin.read().rstrip("\n")
      if not value:
        raise ValueError("empty value")
      db.set_secret(args.stdin_key, value)
      print(f"Set system config {args.stdin_key!r}")
      changed = True

    for raw in args.sets:
      name, value = parse_kv(raw, "--set")
      db.set_secret(name, value)
      print(f"Set system config {name!r}")
      changed = True

    for name in args.unsets:
      db.del_secret(name)
      print(f"Unset system config {name!r}")
      changed = True

    if changed:
      return

    for name in db.list_secrets():
      print(name)


def run_host_volume(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  chosen = [flag for flag in (args.add, args.set_, args.rm) if flag]
  if len(chosen) > 1:
    raise ValueError("Use one of --add, --set or --rm at a time")

  with ctx.kelso_lock("host-volume"):
    if args.add:
      tag, path = parse_kv(args.add, "--add")
      add_host_volume(
        ctx,
        tag,
        path,
        readonly=args.readonly,
        require_mount=args.require_mount,
      )
      print(f"Added host volume {tag} -> {path}")
      return

    if args.set_:
      tag, path = parse_kv(args.set_, "--set")
      set_host_volume(
        ctx,
        tag,
        path,
        readonly=args.readonly,
        require_mount=args.require_mount,
      )
      print(f"Set host volume {tag} -> {path}")
      return

    if args.rm:
      remove_host_volume(ctx, args.rm)
      print(f"Removed host volume {args.rm}")
      return

    rows = [
      (
        tag,
        str(volume.path),
        "yes" if volume.readonly else "",
        "yes" if volume.require_mount else "",
      )
      for tag, volume in sorted(ctx.config.host_volumes.items())
    ]
    print(
      tabulate(rows, headers=["tag", "path", "readonly", "require_mount"])
      if rows
      else "No host volumes declared."
    )


def run_rekey(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  if args.phrase:
    entered = input(f"Enter the {recovery.WORDS} words, separated by spaces: ")
    entropy = recovery.entropy_from_phrase(entered.split())
    confirmed = True
  else:
    entropy = recovery.new_entropy()
    words = recovery.phrase(entropy)
    phrase.show(words)
    if not phrase.check(words):
      raise ValueError("The phrase was not confirmed, so nothing was changed")
    confirmed = True

  result = rekey(ctx, entropy)
  if confirmed:
    recovery.mark_confirmed(ctx.config.master_keyfile)
  print(
    f"Re-encrypted {result.values} value(s) across {result.apps} app(s) and "
    f"kelsodb under the new key."
  )
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
      "`kelso system rekey`."
    )
  words = recovery.phrase(keyfile.seed)
  if not args.confirm:
    print(recovery.format_phrase(words))
    return
  if not phrase.check(words):
    raise ValueError("That does not match the recovery phrase on file")
  with ctx.kelso_lock("recovery-phrase"):
    recovery.mark_confirmed(ctx.config.master_keyfile)
