"""`kelso deploy`: copy a bundle to another box over ssh and load or update it there.

Everything that changes the app happens on the box, through its own `kelso`.
"""

import argparse
import json
import logging
import shlex
import subprocess
import sys
from pathlib import Path

from kelso.cli.configform import collect
from kelso.cli.load import confirm_compose_warnings
from kelso.cli.remote import SSH, remote, remote_request, send_response
from kelso.lib.bundle import KLSO_MD_SUFFIX, KLSO_SUFFIX, app_id_from_path, load_bundle
from kelso.lib.config import REPOS_DIR
from kelso.lib.configflow import EMPTY_CONFIG_RESPONSE, ConfigRequest
from kelso.lib.kelso import KelsoCtx
from kelso.lib.repo import LOCAL_REPO

logger = logging.getLogger("kelso.cli")


def register(subparsers) -> None:
  parser = subparsers.add_parser(
    "deploy",
    help="Copy an app bundle to another box and load or update it there",
  )
  parser.add_argument(
    "bundle",
    metavar="PATH",
    help=f"Path to a {KLSO_SUFFIX} folder or {KLSO_MD_SUFFIX} file",
  )
  parser.add_argument("host", metavar="HOST", help="A host you can `ssh` to")
  parser.add_argument(
    "--no-backup",
    action="store_true",
    help="Update without backing up first, when the box's backup disk is not there",
  )
  parser.add_argument(
    "--allow-compose",
    action="store_true",
    help=(
      "Go ahead although the manifest sets compose keys kelso does not model; "
      "the operator should review them first"
    ),
  )
  parser.set_defaults(func=run)


def run(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  source = Path(args.bundle).expanduser().resolve()
  app = app_id_from_path(source)
  host = args.host
  files = _files(source)
  if not args.allow_compose and not confirm_compose_warnings(app, source):
    print("Nothing deployed.")
    return

  found = subprocess.run(remote(host, "kelso root"), capture_output=True, text=True)
  if found.returncode:
    raise ValueError(
      f"Could not run kelso on {host}: {found.stderr.strip()}\n"
      f"Nothing was copied. Check that `ssh {host} '~/.local/bin/kelso root'` works."
    )
  root = found.stdout.strip()
  dest = f"{root}/{REPOS_DIR}/{LOCAL_REPO}/{source.name}"

  asked = subprocess.run(
    remote(host, f"kelso status {app} --json"), capture_output=True, text=True
  )
  if asked.returncode not in (0, 1):
    raise ValueError(f"Could not ask {host} about {app}: {asked.stderr.strip()}")
  there = json.loads(asked.stdout) if asked.returncode == 0 else {}
  was = there.get("bundle") if there.get("loaded") else None
  if not was:
    command = f"kelso start {app}@{LOCAL_REPO} --allow-compose"
  elif was == dest:
    command = f"kelso update {app} --allow-compose"
    if args.no_backup:
      command += " --no-backup"
    command += f" && kelso start {app}"
  else:
    raise ValueError(
      f"{host} already has {app} loaded from {was}. Nothing was copied.\n"
      f"If this is the same app and you want to deploy your version, remove it "
      f"on {host} first with `kelso rm --purge {app}`, which deletes its data "
      f"and config.\n"
      f"If this is a different app, give yours another app id."
    )

  logger.info(f"Copying {source.name} to {host}:{dest}")
  rsync = ["rsync", "-a", "--delete", "-e", shlex.join(SSH)]
  if source.is_dir():
    copy = subprocess.run([*rsync, "--relative", *files, f"{host}:{dest}/"], cwd=source)
  else:
    copy = subprocess.run([*rsync, str(source), f"{host}:{dest}"])
  _check(copy)

  _ask_for_missing(host, app)
  _check(subprocess.run(remote(host, command)))


def _ask_for_missing(host: str, app: str) -> None:
  """Ask here for what the app still needs on `host`, and send it there.

  Raises ValueError when there is no one to ask.
  """
  request = remote_request(host, app)
  missing = request.missing()
  if not missing:
    return
  if not sys.stdin.isatty():
    raise ValueError(_needs(host, app, request, missing))
  response = collect(request, f"run `kelso config {app} --edit --host {host}`")
  if response != EMPTY_CONFIG_RESPONSE:
    send_response(host, app, response)


def _needs(host: str, app: str, request: ConfigRequest, missing: list[str]) -> str:
  lines = [f"{app} was copied to {host} but not started. It needs:"]
  for name in missing:
    entry = request.field(name)
    assert entry is not None
    one_of = f" One of: {', '.join(entry.choices)}." if entry.choices else ""
    secret = " (secret)" if entry.secret else ""
    lines.append(f"  {name}{secret}: {entry.desc}{one_of}")
  lines.append(
    f"Fill them in with `kelso config {app} --edit --host {host}`, or set each "
    f"with `ssh {host} kelso config {app} NAME=VALUE` (NAME=- for a secret), "
    f"then deploy again."
  )
  return "\n".join(lines)


def _files(source: Path) -> list[str]:
  """What the app runs from: its manifest and the bundle paths its `app` volumes name.

  Raises ValueError if the manifest does not parse or a named path is missing.
  """
  spec = load_bundle(source).app_spec()
  if not source.is_dir():
    return []
  files = ["manifest.toml"]
  for name, volume in spec.volumes.items():
    if volume.kind != "app":
      continue
    src = volume.src or name
    if not (source / src).exists():
      raise ValueError(
        f"[volumes.{name}] names {src}, which is not in {source}. "
        f"Nothing was copied; add it or fix the volume's src."
      )
    files.append(src)
  return files


def _check(done: subprocess.CompletedProcess) -> None:
  """Exit with a failed step's code; it has already said why on stderr."""
  if done.returncode:
    raise SystemExit(done.returncode)
