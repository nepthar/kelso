"""Restic as far as kelso uses it, for the fake docker.

A repository is a plain directory: `config` holds the password, each snapshot
is `snapshots/<id>.json` beside a copy of what it backed up under
`trees/<id>/`, laid out at the paths it was backed up at. Real restic is
exercised by the `docker`-marked tests in test_restic.py.
"""

import json
import secrets
import shutil
from datetime import UTC, datetime
from pathlib import Path

WRONG_PASSWORD = "Fatal: wrong password or no key found\n"


def now() -> datetime:
  """When a snapshot is taken. A test that needs the clock patches this."""
  return datetime.now(UTC)


def _parse_docker(args: list[str]) -> tuple[dict[str, str], dict[str, Path], list[str]]:
  """`run --rm -e K[=V] -v HOST:GUEST:MODE ... IMAGE ARGS` -> env, binds, args."""
  env: dict[str, str] = {}
  binds: dict[str, Path] = {}
  i = 1
  while i < len(args):
    arg = args[i]
    if arg == "--rm":
      i += 1
    elif arg == "-e":
      key, _, value = args[i + 1].partition("=")
      env[key] = value
      i += 2
    elif arg == "-v":
      host, guest, _mode = args[i + 1].split(":")
      binds[guest] = Path(host)
      i += 2
    elif arg == "--entrypoint":
      i += 2
    else:
      # IMAGE -c SCRIPT $0 ARGS: kelso wraps restic in a shell that hands the
      # repository back to its user afterwards. Files here are already the
      # test's own, so only restic's part is played.
      rest = args[i + 1 :]
      if rest[:1] == ["-c"]:
        rest = rest[3:]
      return env, binds, rest
  return env, binds, []


def run(args: list[str], process_env: dict[str, str]) -> tuple[int, str, str]:
  env, binds, restic = _parse_docker(args)
  password = env.get("RESTIC_PASSWORD") or process_env.get("RESTIC_PASSWORD", "")
  repo = binds[env["RESTIC_REPOSITORY"]]
  command, rest = restic[0], restic[1:]
  config = repo / "config"

  if command == "init":
    if config.exists():
      return 1, "", "Fatal: config file already exists\n"
    repo.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({"password": password}))
    (repo / "snapshots").mkdir()
    (repo / "trees").mkdir()
    return 0, "created restic repository\n", ""

  if not config.exists():
    return 10, "", "Fatal: repository does not exist\n"
  if json.loads(config.read_text())["password"] != password:
    return 12, "", WRONG_PASSWORD

  if command == "backup":
    tags, paths, i = [], [], 0
    while i < len(rest):
      if rest[i] in ("--host", "--tag"):
        if rest[i] == "--tag":
          tags.append(rest[i + 1])
        i += 2
      elif rest[i] == "--json":
        i += 1
      else:
        paths.append(rest[i])
        i += 1
    snap_id = secrets.token_hex(32)
    tree = repo / "trees" / snap_id
    for guest in paths:
      source = binds[guest]
      dest = tree / guest.lstrip("/")
      dest.parent.mkdir(parents=True, exist_ok=True)
      if source.is_dir():
        shutil.copytree(source, dest, symlinks=True)
      else:
        shutil.copy2(source, dest)
    meta = {
      "id": snap_id,
      "time": now().isoformat(),
      "tags": tags,
      "paths": sorted(paths),
      "hostname": "fake",
    }
    (repo / "snapshots" / f"{snap_id}.json").write_text(json.dumps(meta))
    summary = {"message_type": "summary", "snapshot_id": snap_id}
    return 0, json.dumps(summary) + "\n", ""

  if command == "snapshots":
    wanted = set()
    if "--tag" in rest:
      wanted = set(rest[rest.index("--tag") + 1].split(","))
    found = [
      json.loads(p.read_text()) for p in sorted((repo / "snapshots").glob("*.json"))
    ]
    found = [s for s in found if wanted <= set(s["tags"])]
    return 0, json.dumps(found), ""

  if command == "restore":
    snap_id, target = rest[0], binds[rest[rest.index("--target") + 1]]
    shutil.copytree(repo / "trees" / snap_id, target, symlinks=True, dirs_exist_ok=True)
    return 0, "", ""

  if command == "dump":
    snap_id, path = rest[0], rest[1]
    found = repo / "trees" / snap_id / path.lstrip("/")
    if not found.is_file():
      return 1, "", f"Fatal: cannot dump file: path {path!r} not found in snapshot\n"
    return 0, found.read_text(), ""

  if command == "forget":
    for snap_id in rest:
      (repo / "snapshots" / f"{snap_id}.json").unlink()
      shutil.rmtree(repo / "trees" / snap_id)
    return 0, "", ""

  if command == "prune":
    return 0, "", ""

  if command == "key" and rest[0] == "passwd":
    guest_file = rest[rest.index("--new-password-file") + 1]
    folder, _, name = guest_file.rpartition("/")
    new = (binds[folder] / name).read_text()
    config.write_text(json.dumps({"password": new}))
    return 0, "", ""

  return 1, "", f"fake restic: unsupported command {restic}\n"
