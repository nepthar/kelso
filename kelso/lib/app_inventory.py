"""Every app kelso knows of, read once, and what a name means among them.

An Inventory is a snapshot: the apps loaded under var/run/, the apps with kept
config under conf/apps/, and the bundles in each repo. Resolving a name is two
steps: `app_id` picks the app, `bundle` picks what it loads from. It does not
see docker or the kelso db, so an app that left only containers or volumes
behind is not found by name.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from kelso.lib.apps import AppID
from kelso.lib.bundle import scan_bundles
from kelso.lib.util import same_path

if TYPE_CHECKING:
  from kelso.lib.kelso import KelsoCtx


@dataclass(frozen=True)
class CatalogEntry:
  app_id: AppID
  path: Path
  # None for a bundle outside every repo.
  repo: str | None


@dataclass(frozen=True)
class Inventory:
  loaded: frozenset[AppID]
  # Every app with kept config -> the bundle it was last loaded from, if known.
  origins: dict[AppID, Path | None]
  # Every repo's bundles: repos in config.toml order, then by app id.
  catalog: tuple[CatalogEntry, ...]

  def app_id(self, name: str, *, prefer_loaded: bool = False, repo: str = "") -> AppID:
    """The app an id, or the last part of one, names.

    Looks only at `repo`'s apps if given. With `prefer_loaded`, a name that
    matches a loaded app means that one. Raises ValueError if nothing matches,
    or several apps do.
    """
    known = self.loaded | set(self.origins) | {e.app_id for e in self.catalog}
    if repo:
      known = {e.app_id for e in self.catalog if e.repo == repo}
    found: list[AppID] = []
    for ids in (self.loaded, known) if prefer_loaded else (known,):
      found = [i for i in ids if i == name] or sorted(i for i in ids if i.stem == name)
      if found:
        break
    if len(found) == 1:
      return found[0]
    where = f" in repo {repo}" if repo else ""
    if not found:
      raise ValueError(
        f'No app matching "{name}" found{where}. `kelso ps` lists loaded apps, '
        f"and `kelso repo list` the apps in each repo."
      )
    raise ValueError(
      f'Multiple apps matching "{name}" found{where}: {", ".join(found)}. '
      f"Name the one you mean in full."
    )

  def bundle(self, app_id: AppID, repo: str = "") -> CatalogEntry | None:
    """The bundle `app_id` loads from, or None if there is none.

    `repo`'s if given; else the one it was last loaded from, if that is still
    there; else its only one. Raises ValueError if several are left to choose
    from, or `repo` does not carry it.
    """
    entries = [e for e in self.catalog if e.app_id == app_id]
    if repo:
      entries = [e for e in entries if e.repo == repo]
      if not entries:
        raise ValueError(
          f"Repo {repo} does not carry {app_id}; "
          f"`kelso repo list` shows what each repo carries"
        )
    else:
      origin = self.origins.get(app_id)
      if origin is not None and origin.exists():
        return next(
          (e for e in entries if same_path(e.path, origin)),
          CatalogEntry(app_id, origin, None),
        )
    if len(entries) > 1:
      lines = "\n".join(f"  {e.repo}: {e.path}" for e in entries)
      raise ValueError(
        f'Multiple bundles found for "{app_id}":\n{lines}\n'
        f"Name the one you mean as {app_id}@<repo>, or by its path."
      )
    return entries[0] if entries else None

  def contested(self) -> dict[AppID, list[str]]:
    """Ids more than one bundle carries, and which repos those are in."""
    repos: dict[AppID, list[str]] = {}
    for e in self.catalog:
      repos.setdefault(e.app_id, []).append(e.repo)
    return {app_id: names for app_id, names in repos.items() if len(names) > 1}


def no_bundle_message(app_id: AppID) -> str:
  return f"No bundle found for {app_id}; run `kelso load <path>` on its bundle"


def _ids(names) -> list[AppID]:
  """`names` that are valid app ids, as AppIDs."""
  ids = []
  for name in names:
    try:
      ids.append(AppID(name))
    except ValueError:
      continue
  return ids


def build_inventory(ctx: "KelsoCtx") -> Inventory:
  """Read the loaded apps, the apps with kept config, and every repo's bundles."""
  run_root = ctx.config.run_root
  names = [p.name for p in run_root.iterdir()] if run_root.is_dir() else []
  loaded = frozenset(i for i in _ids(names) if ctx.loaded_paths(i).exists())

  origins = {i: ctx.loaded_origin(i) for i in _ids(ctx.config.app_config_ids())}

  catalog = []
  for name, repo in ctx.config.repos.items():
    entries = []
    for raw_id, rel_path in scan_bundles(repo.path):
      try:
        entries.append(CatalogEntry(AppID(raw_id), repo.path / rel_path, name))
      except ValueError:
        continue
    catalog += sorted(entries, key=lambda e: e.app_id)
  return Inventory(loaded, origins, tuple(catalog))
