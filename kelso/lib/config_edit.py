"""Editing config.toml in place.

Edits go through tomlkit, which round-trips the operator's own comments,
ordering and whitespace. Two rules hold for every edit here: the kelso lock is
held, and nothing is committed until the new text parses through
`load_config_file`.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import tomlkit
from tomlkit import TOMLDocument

from kelso.lib.config import _expand_path, load_config_file
from kelso.lib.repo import LOCAL_REPO
from kelso.lib.util import validate_identifier

logger = logging.getLogger("kelso.config_edit")

if TYPE_CHECKING:
  from kelso.lib.kelso import KelsoCtx


@contextmanager
def edit_config(ctx: KelsoCtx) -> Iterator[TOMLDocument]:
  """Yield config.toml as a tomlkit document; write it back if it validates."""
  path = ctx.config.config_path
  document = tomlkit.parse(path.read_text())
  yield document
  _commit(path, tomlkit.dumps(document).rstrip("\n") + "\n")


def _commit(path: Path, text: str) -> None:
  """Replace `path` with `text`, but only once it loads as a kelso config."""
  incoming = path.with_name(f".{path.name}.incoming")
  incoming.write_text(text)
  try:
    load_config_file(incoming)
  except (ValueError, RuntimeError) as e:
    incoming.unlink(missing_ok=True)
    raise ValueError(f"Refusing to write {path}: the result is not valid.\n{e}") from e
  os.replace(incoming, path)


def _table(**items):
  """A new table that ends in a blank line, so the next header is not pressed
  against it; tomlkit adds none when a table goes in ahead of another."""
  table = tomlkit.table()
  for key, value in items.items():
    table[key] = value
  table.add(tomlkit.nl())
  return table


def _host_volumes(document: TOMLDocument):
  """The `[host_volume]` table, created on first use."""
  if "host_volume" not in document:
    document["host_volume"] = tomlkit.table(is_super_table=True)
  return document["host_volume"]


def _entry(path: str, *, readonly: bool, require_mount: bool):
  flags = {"readonly": readonly, "require_mount": require_mount}
  return _table(path=path, **{name: True for name, on in flags.items() if on})


def _check_path(ctx: KelsoCtx, raw: str, *, require_mount: bool) -> None:
  """Refuse a path that is not there."""
  resolved = _expand_path(raw, ctx.config.config_path.parent, ctx.config.kelso_root)
  if not resolved.exists():
    raise ValueError(
      f"No such directory: {resolved}\n"
      f"Create it first, or point the host volume somewhere that exists."
    )
  if not resolved.is_dir():
    raise ValueError(f"Host volume path is not a directory: {resolved}")
  if require_mount and not resolved.is_mount():
    # Not an error: a share being down is what require_mount catches at start
    # time, and kelso has to stay configurable while it is down.
    logger.warning(
      "%s is not a mount point right now; require_mount will refuse to start "
      "an app bound to it until the share is mounted",
      resolved,
    )


def add_host_volume(
  ctx: KelsoCtx,
  tag: str,
  path: str,
  *,
  readonly: bool = False,
  require_mount: bool = False,
) -> None:
  validate_identifier(tag)
  if tag in ctx.config.host_volumes:
    raise ValueError(
      f"Host volume {tag!r} already exists ({ctx.config.host_volumes[tag].path}). "
      f"Change it with `kelso system host-volume --set {tag}=<path>`."
    )
  _check_path(ctx, path, require_mount=require_mount)
  with edit_config(ctx) as document:
    _host_volumes(document)[tag] = _entry(
      path, readonly=readonly, require_mount=require_mount
    )


def set_host_volume(
  ctx: KelsoCtx,
  tag: str,
  path: str,
  *,
  readonly: bool = False,
  require_mount: bool = False,
) -> None:
  """Replace an existing entry. Flags are the new whole truth, not a patch:
  omitting --readonly clears it, the same as it would on a fresh add."""
  if tag not in ctx.config.host_volumes:
    known = ", ".join(sorted(ctx.config.host_volumes)) or "(none)"
    raise ValueError(
      f"No host volume {tag!r}; known tags: {known}. "
      f"Add it with `kelso system host-volume --add {tag}=<path>`."
    )
  _check_path(ctx, path, require_mount=require_mount)
  with edit_config(ctx) as document:
    _host_volumes(document)[tag] = _entry(
      path, readonly=readonly, require_mount=require_mount
    )


def remove_host_volume(ctx: KelsoCtx, tag: str) -> None:
  """Drop a `[host_volume]` entry."""
  if tag not in ctx.config.host_volumes:
    known = ", ".join(sorted(ctx.config.host_volumes)) or "(none)"
    raise ValueError(f"No host volume {tag!r}; known tags: {known}")
  with edit_config(ctx) as document:
    del _host_volumes(document)[tag]


def _repos(document: TOMLDocument):
  """The `[repo]` super table, created on first use."""
  if "repo" not in document:
    document["repo"] = tomlkit.table(is_super_table=True)
  return document["repo"]


def add_repo(ctx: KelsoCtx, name: str, *, path: str = "", url: str = "") -> None:
  """Write a `[repo.<name>]` table. Exactly one of path or url.

  Existence is checked against the document, not `ctx.config`, which may
  predate an earlier add.
  """
  validate_identifier(name)
  if bool(path) == bool(url):
    raise ValueError("A repo needs exactly one of a local path or a github:// url")
  if name == LOCAL_REPO:
    raise ValueError(
      f"{LOCAL_REPO!r} is the built-in repo at {ctx.config.repos_root / LOCAL_REPO}; "
      f"give this one another name."
    )
  with edit_config(ctx) as document:
    repos = _repos(document)
    if name in repos:
      raise ValueError(
        f"Repo {name!r} already exists in {ctx.config.config_path}. Add this one "
        f"under a different name, or remove that one with "
        f"`kelso repo remove {name}`."
      )
    repos[name] = _table(path=path) if path else _table(url=url)


def remove_repo(ctx: KelsoCtx, name: str) -> None:
  """Drop a `[repo.<name>]` table. The mirrored directory is the caller's."""
  with edit_config(ctx) as document:
    repos = document.get("repo") or {}
    if name not in repos:
      raise ValueError(f"Repo {name!r} is not in {ctx.config.config_path}")
    del repos[name]


def _route_providers(document: TOMLDocument):
  """The `[route_provider]` super table, created on first use."""
  if "route_provider" not in document:
    document["route_provider"] = tomlkit.table(is_super_table=True)
  return document["route_provider"]


def set_route_provider(
  ctx: KelsoCtx, tag: str, *, kind: str, domain: str, args: dict[str, str]
) -> None:
  """Write a `[route_provider.<tag>]` block, replacing one already there."""
  validate_identifier(tag)
  with edit_config(ctx) as document:
    providers = _route_providers(document)
    if args:
      table = tomlkit.table()
      table["kind"] = kind
      table["domain"] = domain
      # The args table is written last, so it carries the blank line.
      table["args"] = _table(**args)
    else:
      table = _table(kind=kind, domain=domain)
    providers[tag] = table


def remove_route_provider(ctx: KelsoCtx, tag: str) -> None:
  """Drop a `[route_provider.<tag>]` block."""
  with edit_config(ctx) as document:
    providers = document.get("route_provider") or {}
    if tag not in providers:
      raise ValueError(f"Route provider {tag!r} is not in {ctx.config.config_path}")
    del providers[tag]


def set_kelso_address(ctx: KelsoCtx, address: str) -> None:
  """Set the top-level `kelso_address` every route provider points traffic at."""
  if not address:
    raise ValueError("kelso_address cannot be empty")
  with edit_config(ctx) as document:
    document["kelso_address"] = address
