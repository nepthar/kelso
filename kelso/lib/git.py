"""The git commands behind a mirrored repo: one folder, shallow and sparse."""

import os
import shutil
import subprocess
from pathlib import Path

GIT = "git"


def git(*args: str, cwd: Path | None = None) -> str:
  """Run `git *args` in `cwd` and return its stdout. Raises RuntimeError on failure."""
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
  return result.stdout.strip()


def head(checkout: Path) -> str | None:
  """The commit checked out, or None before the first checkout."""
  if not (checkout / ".git").is_dir():
    return None
  try:
    return git("rev-parse", "--verify", "-q", "HEAD", cwd=checkout)
  except RuntimeError:
    return None


def checkout_folder(checkout: Path, url: str, ref: str, folder: str) -> str:
  """Bring `checkout` to the tip of `ref` at `url`, with only `folder` on disk.

  Fetches one commit, and only the blobs under `folder`. Returns its sha.
  """
  if not (checkout / ".git").is_dir():
    # Anything else here is an older kelso's mirror; it holds nothing git needs.
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
