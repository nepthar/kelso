import os
import shutil
import socket
import subprocess
from pathlib import Path

GIT = "git"


def git(*args: str, cwd: Path | None = None, strip: bool = True) -> str:
  try:
    result = subprocess.run(
      [GIT, *args],
      cwd=cwd,
      capture_output=True,
      text=True,
      # Never stop to ask for credentials: there is no one to answer.
      env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
  except FileNotFoundError:
    raise RuntimeError("git is not installed; install it and try again") from None
  if result.returncode != 0:
    raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
  return result.stdout.strip() if strip else result.stdout


def head(checkout: Path) -> str | None:
  if not (checkout / ".git").is_dir():
    return None
  try:
    return git("rev-parse", "--verify", "-q", "HEAD", cwd=checkout)
  except RuntimeError:
    return None


def checkout_folder(checkout: Path, url: str, ref: str, folder: str) -> str:
  if not (checkout / ".git").is_dir():
    # Not a checkout, so nothing here is git's: start over.
    shutil.rmtree(checkout, ignore_errors=True)
    checkout.mkdir(parents=True)
    git("init", "-q", cwd=checkout)
    git("remote", "add", "origin", url, cwd=checkout)
  else:
    git("remote", "set-url", "origin", url, cwd=checkout)

  git("fetch", "-q", "--depth", "1", "--filter=blob:none", "origin", ref, cwd=checkout)
  if folder:
    git("sparse-checkout", "set", "--", folder, cwd=checkout)
  else:
    git("sparse-checkout", "disable", cwd=checkout)
  git("checkout", "-q", "--force", "--detach", "FETCH_HEAD", cwd=checkout)
  return git("rev-parse", "HEAD", cwd=checkout)


def toplevel(path: Path) -> Path | None:
  try:
    return Path(git("rev-parse", "--show-toplevel", cwd=path)).resolve()
  except (RuntimeError, OSError):
    return None


def _identity(cwd: Path) -> list[str]:
  try:
    if git("config", "user.email", cwd=cwd):
      return []
  except RuntimeError:
    pass
  return ["-c", "user.name=kelso", "-c", f"user.email=kelso@{socket.gethostname()}"]


def adopt(path: Path) -> bool:
  if toplevel(path) is not None:
    return False
  git("init", "-q", cwd=path)
  git("add", "-A", cwd=path)
  if git("status", "--porcelain", cwd=path):
    git(*_identity(path), "commit", "-q", "-m", "Added as a kelso repo", cwd=path)
  return True


def status(checkout: Path) -> list[tuple[str, str]]:
  raw = git(
    "status", "--porcelain=v1", "-z", "--untracked-files=all", cwd=checkout, strip=False
  )
  out = []
  fields = iter(raw.split("\0"))
  for field in fields:
    if not field:
      continue
    code, path = field[:2], field[3:]
    out.append((code, path))
    if code[0] in "RC":
      next(fields, None)
  return out


def uncommitted(checkout: Path) -> int:
  return len(status(checkout))
