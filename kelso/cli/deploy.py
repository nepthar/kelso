import argparse
import json
import logging
import shlex
import subprocess
import sys
from pathlib import Path

from kelso.cli.confirm import Refused
from kelso.cli.load import confirm_compose_warnings
from kelso.cli.remote import SSH, remote, remote_request, run_remote
from kelso.lib.bundle import KLSO_MD_SUFFIX, KLSO_SUFFIX, app_id_from_path, load_bundle
from kelso.lib.config import REPOS_DIR
from kelso.lib.configflow import ConfigRequest
from kelso.lib.kelso import KelsoCtx
from kelso.lib.repo import LOCAL_REPO

logger = logging.getLogger("kelso.cli")


def register(subparsers) -> None:
  parser = subparsers.add_parser(
    "deploy",
    help="Copy an app bundle to another host and load or update it there",
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
    help="Update without backing up first, when the host's backup disk is not there",
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
    raise Refused(
      f"Could not run kelso on {host}: {found.stderr.strip()}\n"
      f"Nothing was copied. Check that `ssh {host} '~/.local/bin/kelso root'` works."
    )
  root = found.stdout.strip()
  dest = f"{root}/{REPOS_DIR}/{LOCAL_REPO}/{source.name}"

  asked = subprocess.run(
    remote(host, f"kelso status {app} --json"), capture_output=True, text=True
  )
  if asked.returncode not in (0, 1):
    raise Refused(f"Could not ask {host} about {app}: {asked.stderr.strip()}")
  there = json.loads(asked.stdout) if asked.returncode == 0 else {}
  was = there.get("bundle") if there.get("loaded") else None
  if not was:
    command = f"kelso start {app}@{LOCAL_REPO} --allow-compose --wait"
  elif was == dest:
    command = f"kelso update {app} --allow-compose --start"
    if args.no_backup:
      command += " --no-backup"
  else:
    raise Refused(
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
  request = remote_request(host, app)
  missing = request.missing()
  if not missing:
    return
  if not sys.stdin.isatty():
    raise ValueError(_needs(host, app, request, missing))
  run_remote(host, f"kelso config {app} --edit")


def _needs(host: str, app: str, request: ConfigRequest, missing: list[str]) -> str:
  lines = [f"{app} was copied to {host} but not started. It needs:"]
  for name in missing:
    entry = request.field(name)
    assert entry is not None
    one_of = f" One of: {', '.join(entry.choices)}." if entry.choices else ""
    secret = " (secret)" if entry.secret else ""
    lines.append(f"  {name}{secret}: {entry.desc}{one_of}")
  lines.append(
    f"Set each with `kelso config {app} NAME=VALUE --host {host}` (NAME=- for a "
    f"secret), or fill them in with `kelso config {app} --edit --host {host}`; "
    f"then deploy again."
  )
  return "\n".join(lines)


def _files(source: Path) -> list[str]:
  spec = load_bundle(source).app_spec()
  if not source.is_dir():
    return []
  files = ["manifest.toml"]
  for name, volume in spec.volumes.items():
    if volume.kind != "app":
      continue
    src = volume.src or name
    if not (source / src).exists():
      raise Refused(
        f"[volumes.{name}] names {src}, which is not in {source}. "
        f"Nothing was copied; add it or fix the volume's src."
      )
    files.append(src)
  return files


def _check(done: subprocess.CompletedProcess) -> None:
  if done.returncode:
    raise SystemExit(done.returncode)
