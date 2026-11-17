"""Every app kelso knows of, read once, and the apps a name could mean.

An Inventory is a snapshot: the apps loaded under var/run/, the apps with kept
config under conf/apps/, and the bundles in each repo. It lists candidates for
a name; choosing among them is `KelsoCtx.resolve_app`. It does not see docker
or the kelso db, so an app that left only containers or volumes behind is not
found by name.
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
  source: str


@dataclass(frozen=True)
class ResolvedApp:
  """One app a name could mean: a loaded app, a bundle in a repo, or kept config."""

  app_id: AppID
  # The bundle: where a loaded app was loaded from, or the repo entry.
  path: Path | None
  # The repo the bundle is in, if any.
  repo: str | None
  loaded: bool
  # A repo bundle the app's kept config was made for.
  bound: bool = False

  def line(self) -> str:
    return f"{self.app_id} {self.path or '-'}"


def _match(ids: set[AppID], name: str) -> list[AppID]:
  """The ids `name` names in full, or else those it is the last part of."""
  ordered = sorted(ids)
  return [i for i in ordered if i == name] or [i for i in ordered if i.stem == name]


@dataclass(frozen=True)
class Inventory:
  loaded: frozenset[AppID]
  # Every app with kept config -> the bundle that config was made for, if known.
  configured: dict[AppID, Path | None]
  # Repo name -> its bundles, sorted by app id; repos in config.toml order.
  catalog: dict[str, list[CatalogEntry]]

  def bound_to(self, app_id: AppID) -> Path | None:
    """The bundle `app_id`'s kept config, secrets and data were made for: the
    one it was last loaded from. Loading it from any other bundle is refused,
    and among several bundles carrying the id this one is chosen."""
    return self.configured.get(app_id)

  def entries(self, app_id: AppID) -> list[CatalogEntry]:
    """`app_id`'s bundles across every repo, in repo order."""
    return [
      e for entries in self.catalog.values() for e in entries if e.app_id == app_id
    ]

  def contested(self) -> dict[AppID, list[str]]:
    """Ids more than one repo carries, and which repos those are."""
    repos: dict[AppID, list[str]] = {}
    for entries in self.catalog.values():
      for e in entries:
        repos.setdefault(e.app_id, []).append(e.source)
    return {app_id: names for app_id, names in repos.items() if len(names) > 1}

  def bundles(self, app_id: AppID, repo: str = "") -> list[ResolvedApp]:
    """`app_id`'s bundles in repos, or in `repo` alone."""
    bound = self.bound_to(app_id)
    return [
      ResolvedApp(
        app_id, e.path, e.source, False, bound is not None and same_path(bound, e.path)
      )
      for e in self.entries(app_id)
      if not repo or e.source == repo
    ]

  def _loaded_app(self, app_id: AppID) -> ResolvedApp:
    origin = self.bound_to(app_id)
    repo = next(
      (e.source for e in self.entries(app_id) if origin and same_path(e.path, origin)),
      None,
    )
    return ResolvedApp(app_id, origin, repo, True)

  def candidates(self, query: str) -> list[ResolvedApp]:
    """Every app `<id>`, `<stem>` or `<id>@<repo>` could mean.

    A full id beats a stem. With a repo named, only that repo's bundles; else
    loaded apps, then bundles (less a loaded app's own), then ids with only
    kept config.
    """
    name, _, repo = query.partition("@")
    catalog_ids = {e.app_id for entries in self.catalog.values() for e in entries}
    if repo:
      return [b for i in _match(catalog_ids, name) for b in self.bundles(i, repo)]
    matched = _match(catalog_ids | self.loaded | set(self.configured), name)
    loaded = [self._loaded_app(i) for i in matched if i in self.loaded]
    bundles = [
      b
      for i in matched
      for b in self.bundles(i)
      if not any(m.path and same_path(m.path, b.path) for m in loaded)
    ]
    kept = [
      ResolvedApp(i, None, None, False)
      for i in matched
      if i not in self.loaded and i not in catalog_ids
    ]
    return [*loaded, *bundles, *kept]


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

  configured = {i: ctx.loaded_origin(i) for i in _ids(ctx.config.app_config_ids())}

  catalog = {}
  for name, repo in ctx.config.repos.items():
    entries = []
    for raw_id, rel_path in scan_bundles(repo.path):
      for app_id in _ids([raw_id]):
        entries.append(CatalogEntry(app_id, repo.path / rel_path, name))
    catalog[name] = sorted(entries, key=lambda e: e.app_id)
  return Inventory(loaded, configured, catalog)
