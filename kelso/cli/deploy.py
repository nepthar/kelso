"""`kelso deploy`: copy a bundle to another box over ssh and load or update it there.

Everything that changes the app happens on the box, through its own `kelso`.
"""

import argparse
import logging
import shlex
import subprocess
from pathlib import Path

from kelso.cli.load import confirm_compose_warnings
from kelso.lib.bundle import KLSO_MD_SUFFIX, KLSO_SUFFIX, app_id_from_path, load_bundle
from kelso.lib.config import REPOS_DIR
from kelso.lib.kelso import KelsoCtx
from kelso.lib.repo import LOCAL_REPO

logger = logging.getLogger("kelso.cli")

SSH = ["ssh", "-o", "BatchMode=yes"]


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
  parser.set_defaults(func=run)


def run(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  source = Path(args.bundle).expanduser().resolve()
  app = app_id_from_path(source)
  host = args.host
  files = _files(source)
  if not confirm_compose_warnings(app, source):
    print("Nothing deployed.")
    return

  found = subprocess.run([*SSH, host, "kelso root"], capture_output=True, text=True)
  if found.returncode:
    raise ValueError(
      f"Could not run kelso on {host}: {found.stderr.strip()}\n"
      f"Nothing was copied. Check that `ssh {host} kelso root` works."
    )
  root = found.stdout.strip()
  dest = f"{root}/{REPOS_DIR}/{LOCAL_REPO}/{source.name}"

  loaded = subprocess.run(
    [*SSH, host, f"kelso app source {app}"], capture_output=True, text=True
  )
  if loaded.returncode:
    raise ValueError(f"Could not ask {host} about {app}: {loaded.stderr.strip()}")
  was = loaded.stdout.strip()
  if not was:
    command = f"kelso load {app}@{LOCAL_REPO} --yes && kelso start {app}"
  elif was == f"repo {LOCAL_REPO}":
    command = f"kelso update {app} --yes"
    if args.no_backup:
      command += " --no-backup"
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

  _check(subprocess.run([*SSH, host, command]))


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
