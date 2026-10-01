"""`kelso repo` -- the sources the catalog is built from."""

import argparse
import logging
from datetime import datetime
from pathlib import Path

from tabulate import tabulate

from kelso.lib import repo as repo_lib
from kelso.lib.apps import read_app_actions
from kelso.lib.config import load_config_file
from kelso.lib.kelso import CatalogEntry, KelsoCtx
from kelso.lib.repo import USAGE
from kelso.lib.util import fmt_size

logger = logging.getLogger("kelso.cli")


def register(subparsers) -> None:
  parser = subparsers.add_parser("repo", help="Manage the repos apps come from")
  sub = parser.add_subparsers(dest="repo_command", required=True)

  add = sub.add_parser("add", help="Add a repo of apps", description=USAGE)
  add.add_argument("location", help="A github:// url, or a directory on this machine")
  add.add_argument(
    "--name", default="", help="Name it something other than the default"
  )
  add.set_defaults(func=_add)

  update = sub.add_parser("update", help="Bring mirrored repos up to the remote")
  update.add_argument("name", nargs="?", default="", help="One repo, or all of them")
  update.set_defaults(func=_update)

  remove = sub.add_parser("remove", help="Drop a repo and its mirrored copy")
  remove.add_argument("name", help="Repo to remove")
  remove.set_defaults(func=_remove)

  listing = sub.add_parser("list", help="Show configured repos and their apps")
  listing.set_defaults(func=_list)


def _add(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  result = repo_lib.add(ctx, args.location, name=args.name)
  print(f"Added repo {result.repo.name} -> {result.repo.describe()}")
  if result.mirrored is not None:
    done = result.mirrored
    print(
      f"Mirrored {len(done.bundles)} apps at {done.sha[:8]} "
      f"({fmt_size(done.total_bytes)})"
    )
  _report_contested(ctx)


def _update(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  results = repo_lib.update(ctx, args.name)
  if not results:
    print("No mirrored repos to update.")
    return
  for result in results:
    if result.unchanged:
      print(f"{result.name}: already at {result.sha[:8]}")
    else:
      print(
        f"{result.name}: {result.sha[:8]} "
        f"({len(result.bundles)} apps, {fmt_size(result.total_bytes)})"
      )
  _report_contested(ctx)


def _remove(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  result = repo_lib.remove(ctx, args.name)
  if result.bound:
    logger.warning(
      f"These apps were loaded from {result.name}: {', '.join(result.bound)}.\n"
      f"They keep running -- what is loaded under var/run/ is already a copy -- but "
      f"kelso will no longer see updates for them."
    )
  print(f"Removed repo {result.name}")


def _list(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  with ctx.kelso_lock("repo list"):
    catalog = ctx.app_catalog()
    loaded = ctx.loaded_app_ids()
    # One read of the activity log for every app, rather than one per row.
    actions = read_app_actions(ctx)
    origins = {app_id: ctx.loaded_origin(app_id) for app_id in loaded}

    blocks = []
    for name, repo in ctx.config.repos.items():
      entries = [
        entry
        for app_id in sorted(catalog)
        for entry in catalog[app_id]
        if entry.source == name
      ]
      state = ctx.kelso_db.get_repo_state(name) if repo.mirrored else None
      at = f" {state['sha'][:8]}" if state else ""
      header = f"Repo: {name} {len(entries)} apps {repo.describe()}{at}"
      block = f"{header}\n{'=' * len(header)}"
      if entries:
        rows = [
          (e.app_id, _status(e, loaded, origins, actions), _relative(e.path, repo.path))
          for e in entries
        ]
        block += "\n" + tabulate(
          rows, headers=["APP_ID", "STATUS", "PATH"], tablefmt="simple"
        )
      blocks.append(block)
    print("\n\n".join(blocks))


def _relative(path: Path, root: Path) -> str:
  return str(path.relative_to(root)) if path.is_relative_to(root) else str(path)


def _status(
  entry: CatalogEntry,
  loaded: set[str],
  origins: dict[str, Path | None],
  actions: dict[str, tuple[datetime, str]],
) -> str:
  """The last thing kelso did with this bundle, or how it stands if nothing yet."""
  if entry.app_id not in loaded:
    return "-"

  origin = origins.get(entry.app_id)
  if origin is not None and origin != entry.path:
    return "-"

  action = actions.get(entry.app_id)
  return action[1] if action else "loaded"


def _report_contested(ctx: KelsoCtx) -> None:
  # `ctx.config` predates the change.
  fresh = KelsoCtx(load_config_file(ctx.config.config_path))
  for line in repo_lib.contested_lines(fresh):
    logger.warning(line)
