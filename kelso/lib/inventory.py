"""Every app kelso knows of, read once, and what a name for one resolves to.

An Inventory is a snapshot: the apps loaded under var/run/, the apps with kept
config under conf/apps/, and the bundles in each repo. Resolving a name is a
pure function of it. It does not see docker or the kelso db, so an app that
left only containers or volumes behind is not found by name.
"""

from dataclasses import dataclass
from pathlib import Path

from kelso.lib.apps import AppID
from kelso.lib.util import same_path


@dataclass(frozen=True)
class CatalogEntry:
  app_id: str
  path: Path
  source: str


@dataclass(frozen=True)
class Configured:
  """Where an app's kept config records it was loaded from."""

  bound_to: str | None
  origin: Path | None


@dataclass(frozen=True)
class ResolvedApp:
  """One app a query could mean: a loaded app, a bundle in a repo, or kept config."""

  app_id: AppID
  # The bundle: where a loaded app was loaded from, or the repo entry.
  path: Path | None
  # The repo the bundle is in; for a loaded app, the repo it is bound to.
  repo: str | None
  loaded: bool
  # A repo bundle the app's kept config was made for.
  bound: bool = False

  def line(self) -> str:
    return f"{self.app_id} {self.path or '-'}"


@dataclass(frozen=True)
class AppResolution:
  """Every app a query matches, and the one it resolves to, if any."""

  query: str
  resolved: ResolvedApp | None
  matches: tuple[ResolvedApp, ...]

  def require(self) -> ResolvedApp:
    """The resolved app. Raises ValueError saying why there is none."""
    if self.resolved is not None:
      return self.resolved
    name, _, repo = self.query.partition("@")
    if self.matches:
      lines = "\n".join(f"  {m.line()}" for m in self.matches)
      raise ValueError(
        f'More than one app matches "{self.query}":\n{lines}\n'
        f"Name the one you mean in full, as <app>@<repo>, or by its path."
      )
    if repo:
      raise ValueError(f"Repo {repo!r} does not carry {name}.")
    raise ValueError(f'No app found for "{self.query}"')

  def require_id(self) -> AppID:
    """The app id named, even when several bundles carry that one id."""
    if self.resolved is None and len({m.app_id for m in self.matches}) == 1:
      return self.matches[0].app_id
    return self.require().app_id


def _match(ids: set[str], name: str) -> list[str]:
  """The ids `name` names in full, or else those it is the last part of."""
  ids = sorted(ids)
  return [i for i in ids if i == name] or [i for i in ids if AppID(i).stem == name]


def _prefer(*groups: list[ResolvedApp]) -> ResolvedApp | None:
  """The sole member of the first non-empty group; None if that group has several."""
  for group in groups:
    if group:
      return group[0] if len(group) == 1 else None
  return None


@dataclass(frozen=True)
class Inventory:
  loaded: frozenset[str]
  configured: dict[str, Configured]
  # Every bundle in every repo, keyed by app id, in repo order.
  catalog: dict[str, tuple[CatalogEntry, ...]]

  def bound_to(self, app_id: str) -> str | None:
    kept = self.configured.get(app_id)
    return kept.bound_to if kept else None

  def contested(self) -> dict[str, tuple[str, ...]]:
    """Ids more than one repo carries, and which repos those are."""
    return {
      app_id: tuple(e.source for e in entries)
      for app_id, entries in self.catalog.items()
      if len(entries) > 1
    }

  def sole_bundles(self) -> dict[str, Path]:
    """Each id exactly one repo carries, and its bundle."""
    return {
      app_id: entries[0].path
      for app_id, entries in self.catalog.items()
      if len(entries) == 1
    }

  def bundles(self, app_id: str, repo: str = "") -> list[ResolvedApp]:
    """`app_id`'s bundles in repos, or in `repo` alone."""
    bound = self.bound_to(app_id)
    return [
      ResolvedApp(
        AppID(app_id),
        e.path,
        e.source,
        False,
        bound in (f"repo {e.source}", str(e.path)),
      )
      for e in self.catalog.get(app_id, ())
      if not repo or e.source == repo
    ]

  def bundle(self, app_id: str) -> ResolvedApp:
    """The bundle `app_id` loads from: the one its kept config was made for, or
    its only one. Raises ValueError if there are several."""
    bundles = self.bundles(app_id)
    pick = _prefer([b for b in bundles if b.bound], bundles)
    return AppResolution(app_id, pick, tuple(bundles)).require()

  def _loaded_app(self, app_id: str) -> ResolvedApp:
    kept = self.configured.get(app_id)
    bound = (kept.bound_to if kept else None) or ""
    repo = bound.removeprefix("repo ") if bound.startswith("repo ") else None
    return ResolvedApp(AppID(app_id), kept.origin if kept else None, repo, True)

  def resolve(self, query: str) -> AppResolution:
    """Resolve `<id>`, `<stem>` or `<id>@<repo>`.

    A full id beats a stem. A loaded app wins, unless a repo is named; then the
    bundle the app's kept config was made for; otherwise the query resolves
    only if it matches one app.
    """
    name, _, repo = query.partition("@")
    ids = set(self.catalog)
    if not repo:
      ids |= self.loaded | set(self.configured)
    matched = _match(ids, name)
    loaded = [self._loaded_app(i) for i in matched if i in self.loaded and not repo]
    bundles = [
      b
      for i in matched
      for b in self.bundles(i, repo)
      if not any(m.path and same_path(m.path, b.path) for m in loaded)
    ]
    kept = [
      ResolvedApp(AppID(i), None, None, False)
      for i in matched
      if i not in self.loaded and i not in self.catalog
    ]
    matches = [*loaded, *bundles, *kept]
    resolved = _prefer(loaded, [b for b in bundles if b.bound], matches)
    return AppResolution(query, resolved, tuple(matches))
