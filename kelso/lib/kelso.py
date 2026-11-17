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


@dataclass(frozen=True)
class Found:
  """One app a query could mean: a loaded app, or a bundle in a repo."""

  app_id: AppID
  # The bundle: where a loaded app was loaded from, or the repo entry.
  path: Path | None
  # The repo the bundle is in, if any.
  repo: str | None
  loaded: bool
  # A loaded app bound to its path rather than to a repo.
  by_path: bool = False

  def line(self) -> str:
    return f"{self.app_id} {self.path or '-'}"


@dataclass(frozen=True)
class Which:
  """Every app a query matches, and the one it resolves to, if any."""

  query: str
  resolved: Found | None
  matches: tuple[Found, ...]
  problem: str

  def require(self) -> Found:
    """The resolved app. Raises ValueError saying why there is none."""
    if self.resolved is None:
      raise ValueError(self.problem)
    return self.resolved

  def require_id(self) -> AppID:
    """The app id named, even when several bundles carry that one id.

    Raises ValueError saying why there is none.
    """
    if self.resolved is not None:
      return self.resolved.app_id
    ids = {f.app_id for f in self.matches}
    if len(ids) != 1:
      raise ValueError(self.problem)
    return ids.pop()


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

  def bundle_path(self, app: AppID | str) -> Path:
    """The bundle on disk `app` resolves to, by `which`, or an error."""
    path = self.which(str(app)).require().path
    if path is None or not path.exists():
      raise ValueError(self._no_bundle_message(app))
    return path

  def _no_bundle_message(self, app: AppID | str) -> str:
    """Why no repo offers `app`, naming the source a loaded copy came from."""
    bound = None
    if self.config.app_config_path(app).is_file():
      bound = self.app_store(app).get_meta(BOUND_TO_META)
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
      bundle = self.bundle_path(app)
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

  def which(self, query: str) -> Which:
    """Resolve `<id>`, `<stem>` or `<id>@<repo>` among loaded apps and repo bundles.

    A full id beats a stem. A loaded match wins, then a bundle the app's kept
    config was made for; otherwise the query resolves only if it matches one.
    """
    name, _, repo = query.partition("@")
    catalog = self.app_catalog()
    loaded = self.loaded_app_ids()
    ids = sorted(set(catalog) | loaded)
    matched = _match(ids, name)
    # An app with only state left (containers, config, volumes) still answers
    # to its id, so it can be stopped and removed.
    state_only = (
      [] if matched or repo else _match(sorted(self._observed_app_ids()), name)
    )

    found: list[Found] = []
    bound_found: list[Found] = []
    for app_id in matched:
      entries = [e for e in catalog.get(app_id, ()) if not repo or e.source == repo]
      bound = ""
      if self.config.app_config_path(app_id).is_file():
        bound = self.app_store(app_id).get_meta(BOUND_TO_META) or ""
      origin = self.loaded_origin(app_id)
      # With its config gone, a loaded app's source is unknown; its bundles answer.
      if app_id in loaded and (origin or not catalog.get(app_id)):
        same = [
          e for e in catalog.get(app_id, ()) if origin and same_path(e.path, origin)
        ]
        in_repo = bound.removeprefix("repo ") if bound.startswith("repo ") else None
        if in_repo is None and same:
          in_repo = same[0].source
        if not repo or in_repo == repo:
          by_path = bool(bound) and not bound.startswith("repo ")
          found.append(Found(AppID(app_id), origin, in_repo, True, by_path))
          entries = [e for e in entries if e not in same]
      for e in entries:
        found.append(Found(AppID(app_id), e.path, e.source, False))
        if bound in (f"repo {e.source}", str(e.path)):
          bound_found.append(found[-1])

    found += [Found(AppID(app_id), None, None, False) for app_id in state_only]

    # A loaded app wins, then one whose config was made for that bundle.
    for preferred in ([f for f in found if f.loaded], bound_found, found):
      if preferred:
        if len(preferred) == 1:
          return Which(query, preferred[0], tuple(found), "")
        break
    if found:
      lines = "\n".join(f"  {f.line()}" for f in found)
      problem = (
        f'More than one app matches "{query}":\n{lines}\n'
        f"Name the one you mean in full, as <app>@<repo>, or by its path."
      )
    elif repo:
      carried = sorted({e.source for i in matched for e in catalog.get(i, ())})
      problem = f"Repo {repo!r} does not carry {name}." + (
        f" It is in: {', '.join(carried)}." if carried else " No repo carries it."
      )
    else:
      problem = f'No app found for "{query}"'
    return Which(query, None, tuple(found), problem)

  def resolve_app(self, query_app_id: str) -> AppID:
    return self.which(query_app_id).require_id()

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
    resolved = self.which(str(app_id)).require_id()
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
