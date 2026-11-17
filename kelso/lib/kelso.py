import json
import logging
import os
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cached_property
from pathlib import Path

from filelock import FileLock, Timeout

from kelso.lib.apps import AppID
from kelso.lib.bundle import load_bundle, scan_bundles
from kelso.lib.config import Config
from kelso.lib.crypto import crypto_from_config
from kelso.lib.docker import load_kelso_run_unit_status
from kelso.lib.logtab import LogTab
from kelso.lib.observations import (
  AppObservation,
  RunState,
  app_state,
  collect_observations,
)
from kelso.lib.options import APP_OPTIONS
from kelso.lib.spec import AppSpec
from kelso.lib.store import AppStore, KelsoStore
from kelso.lib.util import same_path

logger = logging.getLogger("kelso")

LOCK_TIMEOUT = 5.0

ACTIVITY_LOG_MAX_BYTES = 10 * 1024 * 1024
ACTIVITY_LOG_HISTORY = 2000  # records
METRICS_LOG_MAX_BYTES = 10 * 1024 * 1024
METRICS_LOG_HISTORY = 50_000  # records: days of readings

# Lives in the app's config store, so `--purge` clears it and nothing else does.
BOUND_TO_META = "bound_to"

LIVE_METRIC_CUTOFF_AGE_SECONDS = 60 * 60 * 2


def lock_timeout() -> float:
  """The acquire timeout, overridable via `KELSO_LOCK_TIMEOUT`."""
  return float(os.environ.get("KELSO_LOCK_TIMEOUT", LOCK_TIMEOUT))


def _lock_now() -> str:
  return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def write_lock_holder(path: Path, by: str) -> None:
  """Overwrite the lockfile with who holds it. Call after acquire; filelock
  truncates on acquire, so this is what a waiter reads on timeout."""
  path.write_text(
    json.dumps(
      {"by": by, "pid": os.getpid(), "at": _lock_now()},
      separators=(",", ":"),
    )
  )


def lock_holder_hint(path: Path) -> str:
  """What the lockfile says, for a timed-out acquire."""
  try:
    record = json.loads(path.read_text())
  except (OSError, json.JSONDecodeError, ValueError):
    return ""
  return (
    f"Held by `kelso {record.get('by', '?')}` "
    f"(pid {record.get('pid', '?')}, since {record.get('at', '?')}).\n"
  )


@dataclass(frozen=True)
class CatalogEntry:
  app_id: str
  path: Path
  source: str


def ambiguity_message(app: AppID | str, entries: tuple[CatalogEntry, ...]) -> str:
  locations = "\n".join(f"  {entry.source}: {entry.path}" for entry in entries)
  return (
    f'More than one repo carries "{app}":\n{locations}\n'
    f"Name the one you mean as {app}@<repo>, or pass its full path."
  )


def _match(ids: list[str], name: str) -> list[str]:
  """The ids `name` names in full, or else those it is the last part of."""
  return [i for i in ids if i == name] or [i for i in ids if AppID(i).stem == name]


def _prefer(*groups: "list[ResolvedApp]") -> "ResolvedApp | None":
  """The sole member of the first non-empty group; None if that group has several."""
  for group in groups:
    if group:
      return group[0] if len(group) == 1 else None
  return None


@dataclass(frozen=True)
class ResolvedApp:
  """One app a query could mean: a loaded app, or a bundle in a repo."""

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


@dataclass(frozen=True)
class LoadedAppPaths:
  app_id: AppID
  run_path: Path

  def exists(self) -> bool:
    return self.manifest_path.is_file()

  @property
  def compose_path(self) -> Path:
    return self.run_path / "compose.yml"

  @property
  def bundle_path_in_run(self) -> Path:
    return self.run_path / "app_bundle"

  @property
  def manifest_path(self) -> Path:
    return self.bundle_path_in_run / "manifest.toml"


class KelsoCtx:
  def __init__(self, config: Config):
    self.config = config
    # FileLock will not create the parent. One FileLock per path so a nested
    # acquire in this process is reentrant rather than self-deadlocking.
    self.config.lock_root.mkdir(parents=True, exist_ok=True)
    self._kelso_lock = FileLock(self.config.kelso_lockfile_path)
    self._app_locks: dict[str, FileLock] = {}

    self.activity_log = LogTab(
      config.activity_log,
      auto_compact_size_bytes=ACTIVITY_LOG_MAX_BYTES,
      auto_compact_history=ACTIVITY_LOG_HISTORY,
    )

    # Only kelsod's job thread writes this, so compacting in place is safe.
    self.metrics_log = LogTab(
      config.metrics_log,
      auto_compact_size_bytes=METRICS_LOG_MAX_BYTES,
      auto_compact_history=METRICS_LOG_HISTORY,
    )

  def _app_filelock(self, app: AppID | str) -> FileLock:
    key = str(app)
    lock = self._app_locks.get(key)
    if lock is None:
      lock = FileLock(self.config.app_lockfile_path(key))
      self._app_locks[key] = lock
    return lock

  @contextmanager
  def _acquire(self, lock: FileLock, path: Path, by: str, what: str) -> Iterator[None]:
    timeout = lock_timeout()
    try:
      with lock.acquire(timeout=timeout):
        write_lock_holder(path, by)
        yield
    except Timeout:
      raise ValueError(
        f"Another process has locked {what}. Giving up after "
        f"{timeout:g} seconds.\n"
        f"{lock_holder_hint(path)}"
        f"If no other kelso is running, remove {path}"
      ) from None

  @contextmanager
  def kelso_lock(self, by: str) -> Iterator[None]:
    """Hold the kelso-wide lock. Reentrant. Library code assumes this is held
    whenever it mutates kelso-wide state (routes, kelsodb, config.toml)."""
    with self._acquire(self._kelso_lock, self.config.kelso_lockfile_path, by, "kelso"):
      yield

  @contextmanager
  def app_lock(self, app: AppID | str, by: str) -> Iterator[None]:
    """Hold one app's lock. Reentrant. Acquire before the kelso lock."""
    path = self.config.app_lockfile_path(app)
    with self._acquire(self._app_filelock(app), path, by, f"app {app}"):
      yield

  @contextmanager
  def locked(self, by: str, app: AppID | str | None = None) -> Iterator[None]:
    """App lock (if an app) then kelso lock. The usual lifecycle nest."""
    if app is None:
      with self.kelso_lock(by):
        yield
      return
    with self.app_lock(app, by):
      with self.kelso_lock(by):
        yield

  @cached_property
  def kelso_db(self) -> KelsoStore:
    return KelsoStore.from_config(self.config)

  def run_path(self, app: AppID | str) -> Path:
    app_id = str(app)
    return self.config.run_root / app_id

  def loaded_paths(self, app: AppID | str) -> LoadedAppPaths:
    app_id = AppID(app)
    return LoadedAppPaths(app_id, self.config.app_run_path(app_id))

  def catalog_bundle(self, app: AppID | str) -> Path:
    """The repo bundle `app` loads from: the one its kept config was made for,
    or its only one. Raises ValueError if there is none, or several."""
    bundles = self._bundles(str(app), self.app_catalog())
    if not bundles:
      raise ValueError(self.no_bundle_message(app))
    pick = _prefer([b for b in bundles if b.bound], bundles)
    path = AppResolution(str(app), pick, tuple(bundles)).require().path
    assert path is not None
    return path

  def _bound_to(self, app: AppID | str) -> str:
    if not self.config.app_config_path(app).is_file():
      return ""
    return self.app_store(app).get_meta(BOUND_TO_META) or ""

  def no_bundle_message(self, app: AppID | str) -> str:
    """Why no repo offers `app`, naming the source a loaded copy came from."""
    bound = self._bound_to(app)
    if not bound and self.is_loaded(app):
      return (
        f"{app} is loaded, but its config no longer says where from; load it "
        f"as {app}@<repo> or by its path."
      )
    if not bound:
      return f'No app found for "{app}"'
    if not bound.startswith("repo "):
      return f"{app} was loaded from {bound}, not a repo; run `kelso load {bound}`."
    name = bound.removeprefix("repo ")
    repo = self.config.repos.get(name)
    if repo is None:
      problem = "is not in config.toml; add it back with `kelso repo add`"
    elif not repo.path.is_dir() and repo.mirrored:
      problem = f"is not mirrored; run `kelso repo update {name}`"
    elif not repo.path.is_dir():
      problem = f"is missing: {repo.path} is not a directory"
    else:
      problem = "no longer carries it"
    return f"{app} came from repo {name}, which {problem}."

  def is_loaded(self, app: AppID | str) -> bool:
    return self.loaded_paths(app).exists()

  def app_option(self, app: AppID | str, name: str) -> str:
    """An app option: as configured, else the manifest's default, else kelso's."""
    _, value = self.app_store(app).get_config(name)
    if value is None:
      try:
        spec = self.loaded_spec(app)
      except ValueError:
        # `up` orders every app by this; `start` is what refuses a broken one.
        spec = None
      value = spec.config[name].default if spec else None
    return value or APP_OPTIONS[name].default(AppID(app))

  def loaded_spec(self, app: AppID | str) -> "AppSpec | None":
    """The loaded app's spec, or None when it is not loaded.

    Raises ValueError naming the fix when the loaded manifest no longer parses.
    """
    paths = self.loaded_paths(app)
    if not paths.manifest_path.is_file():
      return None
    try:
      return AppSpec.from_file(paths.manifest_path, paths.app_id)
    except ValueError as e:
      raise ValueError(
        f"{app}'s loaded manifest no longer parses; `kelso load {app}` from a "
        f"bundle that does. {e}"
      ) from e

  def require_loaded_spec(self, app: AppID | str) -> "AppSpec":
    """The loaded app's spec. Raises ValueError naming the fix if there is none."""
    spec = self.loaded_spec(app)
    if spec is None:
      raise ValueError(f"App {app} is not loaded; run `kelso load {app}` first")
    return spec

  def app_state(self, app: AppID | str) -> str:
    """Where one app stands, from the filesystem alone."""
    app_id = str(app)
    return app_state(
      run_dir_exists=self.loaded_paths(app_id).run_path.is_dir(),
      config_exists=self.config.app_config_path(app_id).is_file(),
      volumes_exist=any(
        (root / app_id).is_dir() for root in self.config.volume_roots.values()
      ),
    )

  def bundle_spec(self, app: AppID | str) -> "AppSpec | None":
    """The catalog bundle's spec, or None when there is no single bundle."""
    try:
      bundle = self.catalog_bundle(app)
    except ValueError:
      return None
    try:
      return load_bundle(bundle).app_spec()
    except ValueError:
      return None

  def app_store(self, app: AppID | str) -> AppStore:
    """The app's own config store at ``conf/apps/<app_id>.logtab``."""
    return AppStore.from_path(
      self.config.app_config_path(app), crypto_from_config(self.config)
    )

  def app_catalog(self) -> dict[str, tuple[CatalogEntry, ...]]:
    """Every bundle in every app source, keyed by app id, in source order."""
    found: dict[str, list[CatalogEntry]] = {}
    for name, repo in self.config.repos.items():
      for app_id, rel_path in scan_bundles(repo.path):
        found.setdefault(app_id, []).append(
          CatalogEntry(app_id, repo.path / rel_path, name)
        )
    return {app_id: tuple(entries) for app_id, entries in found.items()}

  def loaded_origin(self, app: AppID | str) -> Path | None:
    """The bundle a loaded app was loaded from, as `load` recorded it."""
    if not self.config.app_config_path(app).is_file():
      return None
    origin = self.app_store(app).get_meta("origin")
    return Path(origin) if origin else None

  def manifest_stale(self, app: AppID | str) -> bool:
    """Whether the source bundle's manifest no longer matches the loaded copy."""
    paths = self.loaded_paths(app)
    origin = self.loaded_origin(app)
    if origin is None or not paths.manifest_path.is_file():
      return False
    source = origin / "manifest.toml"
    if not source.is_file():
      return False
    return source.read_bytes() != paths.manifest_path.read_bytes()

  def resolved_bundles(self) -> dict[str, Path]:
    """Map app_id -> its bundle, for ids exactly one repo carries.

    Contested ids are absent rather than resolved to an arbitrary repo.
    """
    return {
      app_id: entries[0].path
      for app_id, entries in self.app_catalog().items()
      if len(entries) == 1
    }

  def contested_app_ids(self) -> dict[str, tuple[str, ...]]:
    """Ids more than one repo carries, and which repos those are."""
    return {
      app_id: tuple(entry.source for entry in entries)
      for app_id, entries in self.app_catalog().items()
      if len(entries) > 1
    }

  def loaded_app_ids(self) -> set[str]:
    """Every app id with a loaded copy under var/run/."""
    run_root = self.config.run_root
    if not run_root.is_dir():
      return set()
    found: set[str] = set()
    for entry in run_root.iterdir():
      try:
        paths = LoadedAppPaths(AppID(entry.name), entry)
      except ValueError:
        continue
      if paths.exists():
        found.add(entry.name)
    return found

  def loaded_app(self, app: AppID | str) -> ResolvedApp:
    """A loaded app, the bundle it was loaded from, and the repo it is bound to."""
    bound = self._bound_to(app)
    repo = bound.removeprefix("repo ") if bound.startswith("repo ") else None
    return ResolvedApp(AppID(app), self.loaded_origin(app), repo, True)

  def _bundles(
    self, app_id: str, catalog: dict[str, tuple[CatalogEntry, ...]], repo: str = ""
  ) -> list[ResolvedApp]:
    """`app_id`'s bundles in repos, or in `repo` alone."""
    bound = self._bound_to(app_id)
    return [
      ResolvedApp(
        AppID(app_id),
        e.path,
        e.source,
        False,
        bound in (f"repo {e.source}", str(e.path)),
      )
      for e in catalog.get(app_id, ())
      if not repo or e.source == repo
    ]

  def resolve_app(self, query: str) -> AppResolution:
    """Resolve `<id>`, `<stem>` or `<id>@<repo>` among loaded apps and repo bundles.

    A full id beats a stem. A loaded app wins, unless a repo is named; then the
    bundle the app's kept config was made for; otherwise it resolves only if
    one matches.
    """
    name, _, repo = query.partition("@")
    catalog = self.app_catalog()
    loaded_ids = set() if repo else self.loaded_app_ids()
    matched = _match(sorted(set(catalog) | loaded_ids), name)
    loaded = [self.loaded_app(i) for i in matched if i in loaded_ids]
    bundles = [
      b
      for i in matched
      for b in self._bundles(i, catalog, repo)
      if not any(m.path and same_path(m.path, b.path) for m in loaded)
    ]
    if not matched and not repo:
      # An app with only state left (containers, config, volumes) still answers
      # to its id, so it can be stopped and removed.
      bundles = [
        ResolvedApp(AppID(i), None, None, False)
        for i in _match(sorted(self._observed_app_ids()), name)
      ]
    matches = [*loaded, *bundles]
    resolved = _prefer(loaded, [b for b in bundles if b.bound], matches)
    return AppResolution(query, resolved, tuple(matches))

  def resolve_app_id(self, query: str) -> AppID:
    """The app id `query` names, even when several bundles carry that one id.

    Raises ValueError saying why there is none.
    """
    found = self.resolve_app(query)
    if found.resolved is None and len({m.app_id for m in found.matches}) == 1:
      return found.matches[0].app_id
    return found.require().app_id

  def _observed_app_ids(self) -> set[str]:
    """Every app_id that has left a trace anywhere."""
    ids = set(self.app_catalog())
    if self.config.run_root.is_dir():
      ids |= {path.name for path in self.config.run_root.iterdir() if path.is_dir()}
    ids |= set(load_kelso_run_unit_status())
    ids |= set(self.kelso_db.app_ids())
    ids |= self.config.app_config_ids()
    return ids

  def run_state(self, app_id: AppID | str) -> RunState:
    """ "Light" run-state of an app"""
    resolved = self.resolve_app_id(str(app_id))
    paths = self.loaded_paths(resolved)
    return RunState(
      app_id=resolved,
      run_path=paths.run_path,
      run_dir_exists=paths.run_path.is_dir(),
      compose_exists=paths.compose_path.is_file(),
      containers=load_kelso_run_unit_status().get(resolved, ()),
    )

  def observations(self) -> tuple[AppObservation, ...]:
    collected = collect_observations(self)
    return tuple(collected[key] for key in sorted(collected))

  def record_gauge(self, name: str, reading: int | float) -> None:
    """Record a gauge reading at the current time."""
    key = "gauge/" + name
    self.metrics_log.write(key, str(reading))

  def read_gauges(self, prefix: str) -> dict[str, LogTab.Entry]:
    """Fetch the current values of all current gauges with the given prefix"""
    prefix = "gauge/" + prefix
    abs_cutoff = int(datetime.now(UTC).timestamp()) - LIVE_METRIC_CUTOFF_AGE_SECONDS
    all_found = self.metrics_log.scan(prefix)
    return {
      key: entry for key, entry in all_found.items() if entry.unix_seconds >= abs_cutoff
    }

  def history_gauges(self, prefix: str, since: int) -> dict[str, list[LogTab.Entry]]:
    """Gauge history matching `prefix`, from `since` (unix seconds) onward."""
    prefix = "gauge/" + prefix
    all_found = self.metrics_log.history(prefix)
    by_key: dict[str, list[LogTab.Entry]] = defaultdict(list)
    for key, entry in all_found:
      if entry.unix_seconds >= since:
        by_key[key].append(entry)
    return dict(by_key)
