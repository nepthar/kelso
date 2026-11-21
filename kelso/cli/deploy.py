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
from kelso.cli.kv import parse_kv
from kelso.cli.load import confirm_compose_warnings
from kelso.lib.bundle import KLSO_MD_SUFFIX, KLSO_SUFFIX, app_id_from_path, load_bundle
from kelso.lib.config import REPOS_DIR
from kelso.lib.configflow import ConfigRequest, request_from_view
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
  parser.add_argument(
    "--set",
    action="append",
    default=[],
    dest="sets",
    metavar="NAME=VALUE",
    help=(
      "Answer something the app needs on the box: a config name, or "
      "volume.<name> for a host volume there (repeatable)"
    ),
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

  found = subprocess.run(_remote(host, "kelso root"), capture_output=True, text=True)
  if found.returncode:
    raise ValueError(
      f"Could not run kelso on {host}: {found.stderr.strip()}\n"
      f"Nothing was copied. Check that `ssh {host} '~/.local/bin/kelso root'` works."
    )
  root = found.stdout.strip()
  dest = f"{root}/{REPOS_DIR}/{LOCAL_REPO}/{source.name}"

  loaded = subprocess.run(
    _remote(host, f"kelso app which --loaded {app}"), capture_output=True, text=True
  )
  if loaded.returncode not in (0, 1):
    raise ValueError(f"Could not ask {host} about {app}: {loaded.stderr.strip()}")
  was = loaded.stdout.strip().partition(" ")[2]
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

  answers = _answers(host, app, args.sets)
  if answers:
    _check(
      subprocess.run(
        _remote(host, f"kelso config {app} --response"),
        input=json.dumps({"values": answers}),
        text=True,
      )
    )
  _check(subprocess.run(_remote(host, command)))


def _answers(host: str, app: str, sets: list[str]) -> dict[str, str]:
  """What to set on the box before starting: `--set`, plus whatever the app
  still needs, asked here. Raises ValueError when there is no one to ask."""
  asked = subprocess.run(
    _remote(host, f"kelso config {app} --json"), capture_output=True, text=True
  )
  if asked.returncode:
    raise ValueError(f"Could not ask {host} what {app} needs: {asked.stderr.strip()}")
  request = request_from_view(json.loads(asked.stdout))
  answers = dict(parse_kv(raw, "--set") for raw in sets)
  errors = request.validate(answers)
  if errors:
    raise ValueError("; ".join(errors) + f"\n{app} was copied but not started.")
  missing = [name for name in request.missing() if name not in answers]
  if not missing:
    return answers
  if sys.stdin.isatty() and sys.stdout.isatty():
    return {**collect(request, "pass --set NAME=VALUE").values, **answers}
  raise ValueError(_needs(host, app, request, missing))


def _needs(host: str, app: str, request: ConfigRequest, missing: list[str]) -> str:
  lines = [f"{app} was copied to {host} but not started. It needs:"]
  for name in missing:
    entry = request.field(name)
    assert entry is not None
    one_of = f" One of: {', '.join(entry.choices)}." if entry.choices else ""
    secret = " (secret)" if entry.secret else ""
    lines.append(f"  {name}{secret}: {entry.desc}{one_of}")
  lines.append(
    f"Run this again with `--set NAME=VALUE` for each, or have a person fill "
    f"them in on {host} with `kelso config {app} --edit`."
  )
  return "\n".join(lines)


def _remote(host: str, command: str) -> list[str]:
  # sshd's shell runs no profile, so the PATH lacks ~/.local/bin, where kelso's
  # own install (`uv tool install`) puts it.
  return [*SSH, host, f"PATH=~/.local/bin:$PATH; {command}"]


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
