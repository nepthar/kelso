import argparse
import sys
from getpass import getpass

from tabulate import tabulate

from kelso.cli import activity, decrypt, doctor, phrase, service, volumes
from kelso.cli.kv import parse_kv
from kelso.lib import recovery
from kelso.lib.config_edit import add_host_volume, remove_host_volume, set_host_volume
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle.recover import held_apps, purge
from kelso.lib.rekey import rekey


def register(subparsers) -> None:
  parser = subparsers.add_parser(
    "system", help="Host setup, secrets, and diagnostics for kelso itself"
  )
  sub = parser.add_subparsers(dest="system_command", required=True)

  secret = sub.add_parser(
    "secret",
    help="List or set the encrypted secrets config.toml refers to",
    formatter_class=argparse.RawDescriptionHelpFormatter,
    description=(
      "System secrets are values kelso keeps encrypted in its database rather\n"
      "than in config.toml, such as a route provider's password or API key.\n"
      "config.toml refers to one by name from an arg ending in _secret:\n"
      "\n"
      "  [route_provider.web.args]\n"
      '  password_secret = "route_provider.web.password"\n'
      "\n"
      "With no arguments, lists every secret config.toml refers to and every\n"
      "one stored, and whether each is set."
    ),
    epilog=(
      "examples:\n"
      "  kelso system secret                               list them\n"
      "  kelso system secret route_provider.web.password   asks for the value,\n"
      "                                                    without echoing it\n"
      "  kelso system secret route_provider.web.password < npm-password.txt\n"
      "                                                    reads it from stdin\n"
      "  kelso system secret --unset route_provider.web.password"
    ),
  )
  secret.add_argument(
    "name",
    nargs="?",
    metavar="NAME",
    help="Secret to set: asked for on a terminal, otherwise read from stdin",
  )
  secret.add_argument(
    "--stdin",
    dest="stdin_key",
    metavar="NAME",
    help="Same as giving NAME",
  )
  secret.add_argument(
    "--set",
    action="append",
    default=[],
    dest="sets",
    metavar="NAME=VALUE",
    help="Set a secret inline (repeatable; the value lands in your shell history)",
  )
  secret.add_argument(
    "--unset",
    action="append",
    default=[],
    dest="unsets",
    metavar="NAME",
    help="Remove a secret (repeatable)",
  )
  secret.set_defaults(
    func=run,
    activity=lambda a: (
      "secret" if a.name or a.sets or a.unsets or a.stdin_key else None
    ),
  )

  rekey = sub.add_parser(
    "rekey",
    help="Make a new recovery phrase and re-encrypt every secret under it",
    description="Makes a new recovery phrase, re-encrypts every app secret and "
    "system secret under the key it derives, and shows the phrase. "
    "The old key is kept, to read backups taken under it. Rekeying does not "
    "undo an exposed key: whoever had it could already read every secret, so "
    "change the secrets themselves too.",
  )
  rekey.add_argument(
    "--phrase",
    action="store_true",
    help="Enter an existing recovery phrase instead of making a new one",
  )
  rekey.set_defaults(func=run_rekey, activity="rekey")

  purge_cmd = sub.add_parser(
    "purge",
    help="Delete every app, its data and config, so kelso can be restored",
    description="Stops and purges every app: its loaded copy, config, secrets, "
    "volumes and routes, as `kelso rm --purge` does for one. Leaves config.toml, "
    "the recovery phrase, repos, backups/ and the volume roots -- links "
    "included -- as they are, so `kelso restore <backups directory>` can follow.",
  )
  purge_cmd.add_argument("-y", "--yes", action="store_true", help="Do not ask first")
  purge_cmd.set_defaults(func=run_purge, activity="purge")

  phrase_cmd = sub.add_parser(
    "recovery-phrase", help="Show this kelso's recovery phrase"
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
  if args.name and args.stdin_key and args.name != args.stdin_key:
    raise ValueError(
      f"Two names given ({args.name!r} and --stdin {args.stdin_key!r}); set one at a time"
    )
  sets = [parse_kv(raw, "--set") for raw in args.sets]
  # Asked for before the lock, which nothing else should wait on while you type.
  name = args.name or args.stdin_key
  if name:
    sets.insert(0, (name, _read_secret(name)))
  for set_name, value in sets:
    if not value:
      raise ValueError(f"No value given for {set_name!r}; nothing was stored")

  with ctx.kelso_lock("system secret"):
    db = ctx.kelso_db
    stored = set(db.list_secrets())
    for set_name, value in sets:
      db.set_secret(set_name, value)
      verb = "Replaced" if set_name in stored else "Set"
      print(f"{verb} system secret {set_name!r}")
      stored.add(set_name)

    for unset_name in args.unsets:
      if unset_name not in stored:
        raise ValueError(
          f"No system secret {unset_name!r}; `kelso system secret` lists them"
        )
      db.del_secret(unset_name)
      stored.discard(unset_name)
      print(f"Removed system secret {unset_name!r}")

    if not sets and not args.unsets:
      _list_secrets(sorted(stored), ctx)


def _read_secret(name: str) -> str:
  """The value, asked for without echo on a terminal, else all of stdin."""
  if sys.stdin.isatty():
    return getpass(f"Value for {name} (hidden): ").strip()
  return sys.stdin.read().rstrip("\r\n")


def _list_secrets(stored: list[str], ctx: KelsoCtx) -> None:
  refs = ctx.config.secret_references()
  names = sorted({*stored, *refs})
  if not names:
    print(
      "No system secrets yet. A route provider in config.toml names the one "
      'it needs with an arg like password_secret = "route_provider.web.password"; '
      "set it with `kelso system secret <name>`."
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
    print("\nSet a missing one with `kelso system secret <name>`.")


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


def run_purge(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  apps = held_apps(ctx)
  if not apps:
    print("No apps to purge; kelso is ready to restore into.")
    return
  if not args.yes:
    last = ctx.kelso_db.last_backup_run()
    print(f"This permanently deletes {len(apps)} app(s), with their data and config:")
    for app in apps:
      print(f"  {app}")
    print(
      f"The last backup ran {last['time']}." if last else "Kelso has never backed up."
    )
    try:
      answer = input("Type purge to delete them: ")
    except EOFError:
      answer = ""
    if answer.strip() != "purge":
      print("Nothing purged.")
      return
  for app in purge(ctx):
    print(f"Purged {app}")
  print("Kelso holds no apps now; `kelso restore <backups directory>` can follow.")


def run_recovery_phrase(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  keyfile = recovery.read_keyfile(ctx.config.master_keyfile)
  if keyfile.seed is None:
    raise ValueError(
      f"No recovery phrase in {ctx.config.master_keyfile}. Make one with "
      "`kelso system rekey`."
    )
  print(recovery.format_phrase(recovery.phrase(keyfile.seed)))
