"""Restic, run in a pinned container: the engine under kelso's backups.

It runs in a throwaway container because the files it reads are root-owned.
What it backs up is bound at fixed paths inside the container rather than
where it lives on this machine, so every backup records the same layout --
`/kelso/app/<id>/data/<volume>` -- however the volume roots are linked. The
password never appears on a command line: docker passes `RESTIC_PASSWORD`
through from this process's environment.
"""

import json
import os
import socket
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from logging import getLogger
from pathlib import Path

from kelso.lib.docker import DOCKER
from kelso.lib.util import fmt_size

logger = getLogger("kelso.restic")

# Pinned by digest: a backup format is not something to take updates to blind.
# The digest is the multi-architecture index, so it serves amd64 and arm64.
RESTIC_IMAGE = (
  "restic/restic:0.19.1"
  "@sha256:136600b6ff6843d61d355f7f71f460a166429f35de6fd11b568fece3c9a4d510"
)

REPO = "/repo"
CACHE = "/cache"
TARGET = "/restore"

# Restic runs as root, to read volume files containers wrote as root. What it
# writes into the repository and its cache would be root's too, so after every
# command the same container gives them to the user kelso runs as. The exit
# status stays restic's; a chown that fails (a share that squashes root, say)
# does not fail the command it follows.
OWNED_BY_KELSO = (
  'restic "$@"; status=$?; '
  f'chown -R "$KELSO_UID:$KELSO_GID" {REPO} {CACHE} 2>/dev/null; '
  "exit $status"
)


# Restic's exit status when the password opens no key in the repository.
WRONG_PASSWORD = 12


class ResticError(RuntimeError):
  def __init__(self, message: str, returncode: int | None = None) -> None:
    super().__init__(message)
    self.returncode = returncode


@dataclass(frozen=True)
class Snapshot:
  id: str
  # When it was taken, as kelso writes timestamps: UTC, to the second.
  time: str
  tags: dict[str, str]
  paths: tuple[str, ...]
  # What it backed up, in bytes, before dedup and compression.
  size: int = 0


def _moment(raw: str) -> datetime:
  """Restic's nanosecond, local-offset time, to the microsecond, in UTC."""
  head, _, rest = raw.partition(".")
  if not rest:
    return datetime.fromisoformat(head.replace("Z", "+00:00")).astimezone(UTC)
  digits = rest[: len(rest) - len(rest.lstrip("0123456789"))]
  offset = rest[len(digits) :]
  offset = "+00:00" if offset in ("", "Z") else offset
  return datetime.fromisoformat(
    f"{head}.{digits[:6].ljust(6, '0')}{offset}"
  ).astimezone(UTC)


def _timestamp(raw: str) -> str:
  """Restic's nanosecond, local-offset time as kelso's UTC seconds."""
  moment = _moment(raw).replace(microsecond=0)
  return moment.isoformat().replace("+00:00", "Z")


def _summary(message: dict) -> str:
  """What one backup read and added: `12 files, 3.4 MB new of 1.2 GB, 4.1s`."""
  files = sum(
    int(message.get(key, 0))
    for key in ("files_new", "files_changed", "files_unmodified")
  )
  added = fmt_size(float(message.get("data_added", 0)))
  total = fmt_size(float(message.get("total_bytes_processed", 0)))
  seconds = float(message.get("total_duration", 0))
  noun = "file" if files == 1 else "files"
  return f"{files} {noun}, {added} new of {total}, {seconds:.1f}s"


def _tags(raw: Iterable[str]) -> dict[str, str]:
  return dict(tag.partition("=")[::2] for tag in raw)


class Restic:
  """One restic repository at a local path."""

  def __init__(
    self,
    repo: Path,
    password: str,
    cache: Path,
    owner: tuple[int, int] | None = None,
  ) -> None:
    """`owner` is the (uid, gid) the repository and cache are left owned by:
    the user kelso and kelsod run as, unless a test says otherwise."""
    self.repo = repo
    self._password = password
    self._cache = cache
    self.owner = owner or (os.getuid(), os.getgid())

  def run(
    self,
    args: list[str],
    binds: Mapping[Path, str] | None = None,
    *,
    what: str,
    writable: bool = False,
    say: bool = False,
  ) -> str:
    """Run `restic <args>`; return its stdout.

    `binds` maps host paths to where they appear in the container, read-only
    unless `writable`. The repository and the cache are always writable. With
    `say`, the terminal is told `what` first: for the steps that take a while.
    """
    if say:
      logger.info("restic: %s", what)
    self._cache.mkdir(parents=True, exist_ok=True)
    mounts = [(self.repo.resolve(), REPO, "rw"), (self._cache.resolve(), CACHE, "rw")]
    for host, guest in (binds or {}).items():
      mounts.append((host.resolve(), guest, "rw" if writable else "ro"))
    for host, _, _ in mounts:
      if ":" in str(host):
        raise ValueError(f"Kelso cannot use a path containing a colon: {host}")

    cmd = [
      DOCKER,
      "run",
      "--rm",
      "-e",
      "RESTIC_PASSWORD",
      "-e",
      f"RESTIC_REPOSITORY={REPO}",
      "-e",
      f"RESTIC_CACHE_DIR={CACHE}",
      "-e",
      f"KELSO_UID={self.owner[0]}",
      "-e",
      f"KELSO_GID={self.owner[1]}",
      *(
        arg for host, guest, mode in mounts for arg in ("-v", f"{host}:{guest}:{mode}")
      ),
      "--entrypoint",
      "sh",
      RESTIC_IMAGE,
      "-c",
      OWNED_BY_KELSO,
      "restic",
      *args,
    ]
    logger.debug("restic: %s", " ".join(args))
    env = {**os.environ, "RESTIC_PASSWORD": self._password}
    result = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if result.returncode != 0:
      detail = result.stderr.strip()
      raise ResticError(
        f"Unable to {what}: restic exited {result.returncode}"
        + (f"\n{detail}" if detail else ""),
        result.returncode,
      )
    return result.stdout

  def exists(self) -> bool:
    return (self.repo / "config").is_file()

  def init(self) -> bool:
    """Create the repository unless it exists. True when it was created."""
    if self.exists():
      return False
    self.repo.mkdir(parents=True, exist_ok=True)
    self.run(["init"], what=f"create a backup repository at {self.repo}", say=True)
    return True

  def backup(
    self, sources: Mapping[Path, str], tags: Mapping[str, str], *, what: str
  ) -> str:
    """Back up each host path at its guest path; returns the snapshot's id."""
    args = ["backup", "--json", "--host", socket.gethostname()]
    for key, value in tags.items():
      args += ["--tag", f"{key}={value}"]
    out = self.run([*args, *sources.values()], sources, what=what, say=True)
    for line in out.splitlines():
      message = json.loads(line)
      if message.get("message_type") == "summary":
        logger.info("restic: %s", _summary(message))
        return str(message["snapshot_id"])
    raise ResticError(f"Unable to {what}: restic reported no snapshot")

  def snapshots(self, tags: Mapping[str, str] | None = None) -> list[Snapshot]:
    """Every snapshot carrying all of `tags`, oldest first."""
    args = ["snapshots", "--json"]
    if tags:
      args += ["--tag", ",".join(f"{k}={v}" for k, v in tags.items())]
    found = [
      Snapshot(
        id=raw["id"],
        time=_timestamp(raw["time"]),
        tags=_tags(raw.get("tags") or ()),
        paths=tuple(raw.get("paths") or ()),
        size=int((raw.get("summary") or {}).get("total_bytes_processed", 0)),
      )
      for raw in json.loads(self.run(args, what="list backups") or "[]")
    ]
    return sorted(found, key=lambda s: s.time)

  def restore(self, snapshot_id: str, target: Path, *, what: str) -> None:
    """Restore a snapshot under `target`.

    A file backed up at `/kelso/app/x/config.logtab` lands at
    `<target>/kelso/app/x/config.logtab`.
    """
    target.mkdir(parents=True, exist_ok=True)
    self.run(
      ["restore", snapshot_id, "--target", TARGET],
      {target: TARGET},
      what=what,
      writable=True,
      say=True,
    )

  def dump(self, snapshot_id: str, path: str, *, what: str) -> str:
    """One file from a snapshot, by the path it was backed up at."""
    return self.run(["dump", snapshot_id, path], what=what)

  def forget(self, snapshot_ids: Iterable[str], *, what: str) -> None:
    ids = list(snapshot_ids)
    if ids:
      self.run(["forget", *ids], what=what, say=True)

  def prune(self) -> None:
    self.run(["prune"], what="free the space of forgotten backups", say=True)

  def change_password(self, new_password: str, scratch: Path) -> None:
    """Make `new_password` the repository's password in place of this one."""
    scratch.mkdir(parents=True, exist_ok=True)
    secret = scratch / "new-password"
    fd = os.open(secret, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
      os.write(fd, new_password.encode())
    finally:
      os.close(fd)
    try:
      self.run(
        ["key", "passwd", "--new-password-file", f"{TARGET}/new-password"],
        {scratch: TARGET},
        what="change the backup repository's password",
        say=True,
      )
    finally:
      secret.unlink(missing_ok=True)
    self._password = new_password
