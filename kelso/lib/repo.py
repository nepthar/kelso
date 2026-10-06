"""Application repositories: the directories the catalog scans for bundles.

A `local` repo is a directory the operator keeps; a `github` repo is one kelso
mirrors into `repos/<name>/`. Nothing below `Repo.path` knows which kind it
has. `local` is built in at `repos/local`.

This module owns the repo model and the verbs over it. A mirror is a shallow,
sparse git checkout of one folder; the git commands are `kelso.lib.git`.
"""

import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from kelso.lib import git
from kelso.lib.bundle import scan_bundles
from kelso.lib.util import (
  fmt_size,
  now_ts,
  validate_github_segment,
  validate_identifier,
)

logger = logging.getLogger("kelso.repo")

LOCAL_REPO = "local"

GITHUB_SCHEME = "github://"

_REF_RE = re.compile(r"[A-Za-z0-9._-]+")

RepoKind = Literal["local", "github"]

USAGE = (
  f"{GITHUB_SCHEME}<user>/<repo>/<ref>[/<path>]\n"
  f"  e.g. {GITHUB_SCHEME}nepthar/kelso/main/apps\n"
  f"  <ref> is a branch, tag, or commit sha; <path> is the folder of apps\n"
  f"  inside the repository, and defaults to its root."
)


@dataclass(frozen=True)
class GithubFolder:
  """The folder inside a GitHub repository that a repo mirrors."""

  user: str
  repo: str
  ref: str
  path: tuple[str, ...]

  @property
  def url(self) -> str:
    return "/".join((f"{GITHUB_SCHEME}{self.user}", self.repo, self.ref, *self.path))

  @property
  def clone_url(self) -> str:
    return f"https://github.com/{self.user}/{self.repo}.git"

  @property
  def repo_path(self) -> str:
    """The folder as git addresses it; empty means the repository root."""
    return "/".join(self.path)

  def describe(self, sha: str) -> str:
    where = f" {self.repo_path}" if self.path else ""
    return f"{self.user}/{self.repo}@{sha[:8]}{where}"


@dataclass(frozen=True)
class Repo:
  """One configured source of bundles."""

  name: str
  path: Path
  kind: RepoKind
  remote: GithubFolder | None = None
  # Where a mirrored repo's git checkout lives; `path` is its folder inside.
  checkout: Path | None = None

  @property
  def mirrored(self) -> bool:
    return self.remote is not None

  def describe(self) -> str:
    return self.remote.url if self.remote else str(self.path)


def parse_github_url(raw: str) -> GithubFolder:
  """Parse a `github://user/repo/ref[/path]` repo URL."""
  if not raw.startswith(GITHUB_SCHEME):
    raise ValueError(f"Unsupported repo url {raw!r}; expected\n  {USAGE}")

  parts = raw[len(GITHUB_SCHEME) :].split("/")
  if len(parts) < 3:
    raise ValueError(f"Malformed repo url {raw!r}; expected\n  {USAGE}")

  user, repo, ref, *path = parts
  user = validate_github_segment(user, "user")
  repo = validate_github_segment(repo, "repo")
  if not ref:
    raise ValueError(f"Malformed repo url {raw!r}: empty ref")
  # The ref is handed to `git fetch`, where a leading dash would be an option.
  if not _REF_RE.fullmatch(ref) or ref.startswith("-") or ".." in ref:
    raise ValueError(
      f"Malformed repo url {raw!r}: {ref!r} is not a branch, tag, or commit sha"
    )
  for segment in path:
    check_segment(segment, raw)

  return GithubFolder(user=user, repo=repo, ref=ref, path=tuple(path))


def check_segment(segment: str, context: str) -> None:
  """Refuse a path segment that could escape the folder it is listed under."""
  if segment in ("", ".", ".."):
    raise ValueError(f"Malformed path segment {segment!r} in {context}")
  if any(c < " " or c == "\x7f" for c in segment):
    raise ValueError(f"Unprintable character in path segment of {context}")


def name_from_url(raw: str) -> str:
  """The repo name a url implies: the GitHub repository's own name."""
  name = parse_github_url(raw).repo
  try:
    validate_identifier(name)
  except ValueError as e:
    raise ValueError(
      f"{raw} implies the repo name {name!r}, which kelso cannot use: {e}\n"
      f"Pass --name to choose another."
    ) from e
  return name


# --- mirroring -------------------------------------------------------------

MAX_BUNDLES = 128
MAX_REPO_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class MirrorResult:
  name: str
  sha: str
  previous_sha: str | None
  bundles: tuple[str, ...]
  total_bytes: int

  @property
  def unchanged(self) -> bool:
    return self.sha == self.previous_sha


def mirror(repo: Repo, ctx) -> MirrorResult:
  """Bring `repos/<name>` to what the remote holds now, as a sparse git checkout.

  Raises ValueError for a local repo, a folder the remote does not have, or one
  over the size limits -- whose copy is then removed.
  """
  if repo.remote is None or repo.checkout is None:
    raise ValueError(
      f"Repo {repo.name!r} is a local directory; there is nothing to update"
    )

  previous = git.head(repo.checkout)
  logger.info("Fetching the latest from %s", repo.remote.url)
  try:
    sha = git.checkout_folder(
      repo.checkout, repo.remote.clone_url, repo.remote.ref, repo.remote.repo_path
    )
  except RuntimeError as e:
    raise ValueError(f"Could not update {repo.name} from {repo.remote.url}: {e}") from e
  if not repo.path.is_dir():
    raise ValueError(
      f"{repo.remote.url}: there is no folder {repo.remote.repo_path!r} at {sha[:8]}"
    )

  bundles = tuple(sorted(app_id for app_id, _ in scan_bundles(repo.path)))
  total = sum(
    f.stat().st_size
    for f in repo.path.rglob("*")
    if f.is_file() and ".git" not in f.relative_to(repo.checkout).parts
  )
  if len(bundles) > MAX_BUNDLES or total > MAX_REPO_BYTES:
    shutil.rmtree(repo.checkout)
    ctx.kelso_db.del_repo_state(repo.name)
    raise ValueError(
      f"{repo.describe()} holds {len(bundles)} apps in {fmt_size(total)}, over "
      f"kelso's limit of {MAX_BUNDLES} apps or {fmt_size(MAX_REPO_BYTES)} for one "
      f"repo. Its copy was removed."
    )

  ctx.kelso_db.set_repo_state(repo.name, sha=sha, at=now_ts())
  return MirrorResult(repo.name, sha, previous, bundles, total)


# --- the repo verbs --------------------------------------------------------


@dataclass(frozen=True)
class AddResult:
  repo: Repo
  mirrored: MirrorResult | None


@dataclass(frozen=True)
class RemoveResult:
  name: str
  bound: tuple[str, ...]


def add(ctx, location: str, *, name: str = "") -> AddResult:
  """Add a `github://` url or a local directory, mirroring a url immediately.

  A local repo needs an explicit `name`; a url takes the repository's own.
  """
  from kelso.lib.config import load_config_file
  from kelso.lib.config_edit import add_repo
  from kelso.lib.kelso import KelsoCtx

  remote = location.startswith(GITHUB_SCHEME)
  name = name or (name_from_url(location) if remote else "")
  if not name:
    raise ValueError(f"Repo {location} needs a name of its own; pass --name")

  with ctx.locked(f"repo add {name}"):
    add_repo(ctx, name, url=location) if remote else add_repo(ctx, name, path=location)
    # `ctx.config` predates the entry just written.
    fresh = KelsoCtx(load_config_file(ctx.config.config_path))
    repo = fresh.config.repos[name]
    if remote:
      return AddResult(repo, mirror(repo, fresh))
    if repo.path.is_dir() and git.adopt(repo.path.resolve()):
      logger.info("put %s under git, so its apps can be edited", repo.path)
    return AddResult(repo, None)


def update(ctx, name: str = "") -> tuple[MirrorResult, ...]:
  """Mirror one repo, or every mirrored one. Raises if `name` is local."""
  wanted = [get(ctx, name)] if name else list(ctx.config.repos.values())
  mirrored = [repo for repo in wanted if repo.mirrored]
  if name and not mirrored:
    raise ValueError(f"Repo {name!r} is a local directory; there is nothing to update")
  with ctx.locked("repo update"):
    return tuple(mirror(repo, ctx) for repo in mirrored)


def remove(ctx, name: str) -> RemoveResult:
  """Drop a repo, and the mirrored copy if it had one."""
  from kelso.lib.config_edit import remove_repo

  repo = get(ctx, name)
  if repo.name == LOCAL_REPO:
    raise ValueError(f"{LOCAL_REPO} is built in and cannot be removed")

  bound = bound_apps(ctx, repo.name)
  with ctx.locked(f"repo remove {name}"):
    remove_repo(ctx, repo.name)
    if repo.checkout is not None:
      shutil.rmtree(repo.checkout, ignore_errors=True)
      ctx.kelso_db.del_repo_state(repo.name)
  return RemoveResult(repo.name, bound)


def get(ctx, name: str) -> Repo:
  repo = ctx.config.repos.get(name)
  if repo is None:
    known = ", ".join(sorted(ctx.config.repos))
    raise ValueError(f"No repo {name!r}; configured repos: {known}.")
  return repo


def bound_apps(ctx, name: str) -> tuple[str, ...]:
  """Every loaded app recorded as coming from this repo."""
  from kelso.lib.lifecycle import bound_to

  return tuple(
    sorted(
      app_id
      for app_id in ctx.config.app_config_ids()
      if bound_to(app_id, ctx) == f"repo {name}"
    )
  )


def contested_lines(ctx) -> list[str]:
  """One line per app id that more than one repo carries."""
  return [
    f"{app_id} is in {len(repos)} repos ({', '.join(sorted(repos))}); "
    f"load it as {app_id}@<repo>."
    for app_id, repos in sorted(ctx.contested_app_ids().items())
  ]
