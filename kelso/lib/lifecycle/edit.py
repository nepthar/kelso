"""Editing a bundle's manifest where it lives, in a local repo, and committing it.

A bundle is editable only when every edit can be one clean commit: its repo has
a git repository of its own, on a branch, with nothing staged and no merge or
rebase under way, and the file itself has no changes git has not recorded.
"""

import difflib
import hashlib
import os
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from kelso.lib import git
from kelso.lib.apps import AppID
from kelso.lib.bundle import KLSO_MD_SUFFIX, BundleMdFile, md_bundle_files
from kelso.lib.kelso import CatalogEntry, KelsoCtx
from kelso.lib.lifecycle.load import catalog_target
from kelso.lib.repo import Repo
from kelso.lib.spec import AppSpec


class ManifestMoved(ValueError):
  """The file changed on disk after the editor read it."""


@dataclass(frozen=True)
class RepoGit:
  """A local repo's own git repository, ready to take a commit."""

  checkout: Path
  # Real paths `git status` lists, untracked ones included.
  dirty: frozenset[Path]


@dataclass(frozen=True)
class EditableManifest:
  app_id: AppID
  repo: str
  # The real file, links resolved: manifest.toml, or the whole .klso.md.
  path: Path
  text: str
  checkout: Path

  @property
  def base(self) -> str:
    return text_hash(self.text)


@dataclass(frozen=True)
class EditResult:
  """`diff` is empty, and `commit` None, when nothing changed."""

  diff: str
  commit: str | None


def text_hash(text: str) -> str:
  return hashlib.sha256(text.encode()).hexdigest()


def repo_git(repo: Repo) -> RepoGit:
  """`repo`'s git repository, or ValueError saying why nothing in it is editable."""
  if repo.mirrored:
    raise ValueError(
      f"{repo.name} is a mirror of {repo.describe()}. Edit it there, then "
      f"`kelso repo update {repo.name}`."
    )
  checkout = repo.path.resolve()
  if not (checkout / ".git").exists():
    raise ValueError(
      f"{checkout} has no git repository of its own, so kelso will not edit "
      f"apps in it. Run `git init` there and commit what is in it."
    )
  doing = git.operation(checkout)
  if doing:
    raise ValueError(f"{checkout} is in the middle of a {doing}; finish it first.")
  if not git.on_branch(checkout):
    raise ValueError(f"{checkout} is not on a branch; check one out first.")
  changes = git.status(checkout)
  if any(code[0] not in " ?" for code, _ in changes):
    raise ValueError(f"{checkout} has staged changes; commit or unstage them first.")
  return RepoGit(checkout, frozenset(checkout / path for _, path in changes))


def editable_manifest(ctx: KelsoCtx, target: str) -> EditableManifest:
  """What `<app>` or `<app>@<repo>` edits. Raises ValueError if it is not editable."""
  name, _, repo = target.partition("@")
  found = catalog_target(ctx, name, repo or None)
  if found.bundle is None or found.repo is None:
    raise ValueError(f"No repo carries {found.app_id}, so there is nothing to edit")
  entry = CatalogEntry(found.app_id, found.bundle, found.repo)
  return editable_entry(entry, repo_git(ctx.config.repos[entry.source]))


def editable_entry(entry: CatalogEntry, repo: RepoGit) -> EditableManifest:
  """The file an edit of `entry` replaces, or ValueError if it may not be."""
  is_md = entry.path.name.endswith(KLSO_MD_SUFFIX)
  real = (entry.path if is_md else entry.path / "manifest.toml").resolve()
  if not real.is_relative_to(repo.checkout):
    raise ValueError(f"{real} is outside {repo.checkout}, so kelso cannot commit it.")
  if real in repo.dirty:
    raise ValueError(
      f"{real} has changes git has not recorded; commit or discard them first."
    )
  return EditableManifest(
    entry.app_id, entry.source, real, real.read_text(), repo.checkout
  )


def check_manifest_text(manifest: EditableManifest, text: str) -> None:
  """Everything `load` would check, short of loading. Raises ValueError."""
  if manifest.path.name.endswith(KLSO_MD_SUFFIX):
    files = md_bundle_files(manifest.path.name, text)
    BundleMdFile(manifest.path, manifest.app_id, files).app_spec()
  else:
    AppSpec.from_bytes(text.encode(), manifest.app_id, manifest.path)


def edit_manifest(
  ctx: KelsoCtx, target: str, text: str, base: str, message: str = ""
) -> EditResult:
  """Check `text`, put it in place of what `base` hashed, and commit it.

  Raises ManifestMoved if the file no longer hashes to `base`.
  """
  text = text.replace("\r\n", "\n")
  app = editable_manifest(ctx, target).app_id
  with ctx.app_lock(app, f"edit {app}"):
    current = editable_manifest(ctx, target)
    if current.base != base:
      raise ManifestMoved(
        f"{current.path} changed after it was opened for editing. Start again "
        f"from what is there now."
      )
    if text == current.text:
      return EditResult("", None)
    check_manifest_text(current, text)
    _replace(current.path, text)

    shown = current.path.relative_to(current.checkout)
    diff = "".join(
      difflib.unified_diff(
        current.text.splitlines(keepends=True),
        text.splitlines(keepends=True),
        f"a/{shown}",
        f"b/{shown}",
      )
    )
    message = message.strip() or f"Edited {app} on {date.today().isoformat()}"
    try:
      sha = git.commit(current.checkout, current.path, message)
    except RuntimeError as e:
      raise RuntimeError(f"Saved {current.path}, but did not commit it: {e}") from e
    return EditResult(diff, sha)


def _replace(path: Path, text: str) -> None:
  incoming = path.with_name(f".{path.name}.incoming")
  incoming.write_text(text)
  shutil.copymode(path, incoming)
  os.replace(incoming, path)
