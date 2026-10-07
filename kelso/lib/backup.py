"""Backups: every app's data, configuration and bundle, and kelso's own state,
in one restic repository at `backups/` in the kelso root.

A run backs up each app on its own -- stopped for the moment its data is read,
so what is saved is a point the app could start from -- then kelso's state.
Each backup is a restic snapshot tagged with what it is; an app's bulk
volumes, read without stopping it, are a second snapshot from the same run.

Inside the repository every path is fixed, whatever this machine's layout:

    /kelso/app/<id>/data/<volume>   a data volume
    /kelso/app/<id>/bulk/<volume>   a bulk volume you turned on
    /kelso/app/<id>/app_bundle      the bundle it was loaded from
    /kelso/app/<id>/config.logtab   its config, secrets still encrypted
    /kelso/state/...                config.toml, kelsodb, every app's config,
                                    the local repo, and links.toml: where
                                    each volume root and backups/ pointed
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from logging import getLogger
from pathlib import Path

from kelso.lib.apps import AppID
from kelso.lib.config import BackupKeep
from kelso.lib.cronexpr import CronSchedule
from kelso.lib.kelso import KelsoCtx
from kelso.lib.recovery import backup_password_from, read_keyfile
from kelso.lib.repo import LOCAL_REPO
from kelso.lib.restic import Restic, Snapshot

logger = getLogger("kelso.backup")

# Why a backup was taken. Retention treats each differently: scheduled ones
# thin out by age, the rest are kept by count, and the two kelso takes on its
# own -- before an update, before a restore -- never push yours out.
SCHEDULED = "scheduled"
MANUAL = "manual"
UPDATE = "update"
PRE_RESTORE = "pre-restore"
REASONS = (SCHEDULED, MANUAL, UPDATE, PRE_RESTORE)

DATA = "data"
BULK = "bulk"

# The tag kelso's own state is backed up under, in place of `app`.
STATE = "state"

APP_ROOT = "/kelso/app"
STATE_ROOT = "/kelso/state"


@dataclass(frozen=True)
class Backup:
  """One app's backup from one run: its data snapshot, and bulk if it has one."""

  # None for kelso's own state.
  app: AppID | None
  run: str
  time: str
  reason: str
  version: str
  kelso_id: str
  snapshots: dict[str, str]

  @property
  def id(self) -> str:
    """What a person names it by: the run, which is also when it began."""
    return self.run


@dataclass
class RunResult:
  run: str
  # The repository was created by this run: the moment to say what opens it.
  created: bool = False
  backed_up: list[str] = field(default_factory=list)
  # "<app>: <what went wrong>" for each app this run could not back up.
  failed: list[str] = field(default_factory=list)


def destination_problem(ctx: KelsoCtx) -> str | None:
  """Why backups cannot go where they should right now, or None. Changes
  nothing, so doctor can ask."""
  root = ctx.config.backups_root
  if root.is_symlink() and not root.exists():
    return (
      f"{root} links to {root.readlink()}, which is not there. Mount the disk it "
      f"points into, or fix the link, before backing up."
    )
  if ctx.config.backup.require_mount and shares_kelso_disk(ctx):
    return (
      f"{root} resolves to {root.resolve()}, on the same filesystem as the "
      f"kelso root, and [backup] require_mount is set: the disk it should be on "
      f"is not mounted."
    )
  return None


def shares_kelso_disk(ctx: KelsoCtx) -> bool:
  """Whether backups land on the kelso root's own filesystem."""
  root = ctx.config.backups_root
  where = root if root.exists() else root.parent
  kelso_device = ctx.config.kelso_root.resolve().stat().st_dev
  return where.resolve().stat().st_dev == kelso_device


def destination(ctx: KelsoCtx) -> Path:
  """Where backups go, once it is known to be there. Raises ValueError if not.

  `backups/` is usually a link to another disk. A link to nothing means that
  disk is missing, and writing here then would fill the kelso root instead.
  """
  problem = destination_problem(ctx)
  if problem:
    raise ValueError(problem)
  root = ctx.config.backups_root
  root.mkdir(exist_ok=True)
  return root.resolve()


def repository(ctx: KelsoCtx) -> Restic:
  """The backup repository, opened with the password the phrase derives."""
  seed = read_keyfile(ctx.config.master_keyfile).seed
  if seed is None:
    raise ValueError(
      "There is no recovery phrase, so there is no key to encrypt backups with. "
      "Make one with `kelso system rekey`."
    )
  return Restic(
    destination(ctx),
    backup_password_from(seed),
    ctx.config.temp_root / "restic-cache",
  )


def backup_due(ctx: KelsoCtx, *, since: datetime, now: datetime) -> bool:
  """Whether the [backup] schedule has come round since the last scheduled run,
  or since `since` when there has not been one. Local time, as cron is."""
  last = ctx.kelso_db.last_backup_run(SCHEDULED)
  anchor = since
  if last is not None:
    anchor = datetime.fromisoformat(last["time"]).astimezone().replace(tzinfo=None)
  schedule = CronSchedule.parse(ctx.config.backup.schedule)
  return schedule.next_after(anchor) <= now


def now() -> datetime:
  """The one clock backups read. A test that needs time to pass patches this."""
  return datetime.now(UTC)


def run_id() -> str:
  """When a run began, to the second: what a backup is named by."""
  return now().strftime("%Y%m%d-%H%M%S")


def refuse_same_second(restic: Restic, run: str, app: AppID | None = None) -> None:
  """Raise if a backup named `run` exists already. Two in one second is not
  something that happens on purpose, so the second does nothing."""
  if not restic.exists():
    return
  tags = {"run": run, **({"app": str(app)} if app is not None else {})}
  if restic.snapshots(tags):
    raise ValueError(
      f"There is already a backup {run}, taken within this second; no action taken."
    )


# --- what an app's backup holds ------------------------------------------------


def backed_up_apps(ctx: KelsoCtx) -> list[AppID]:
  """Every app kelso holds anything for: loaded, or unloaded with data kept."""
  ids = set(ctx.config.app_config_ids())
  data_root = ctx.config.volume_roots[DATA]
  if data_root.is_dir():
    ids |= {entry.name for entry in data_root.iterdir() if entry.is_dir()}
  return [AppID(raw) for raw in sorted(ids)]


def _volume_dirs(ctx: KelsoCtx, app: AppID, kind: str) -> dict[str, Path]:
  root = ctx.config.volume_roots[kind] / app
  if not root.is_dir():
    return {}
  return {entry.name: entry for entry in sorted(root.iterdir()) if entry.is_dir()}


def app_sources(ctx: KelsoCtx, app: AppID) -> dict[Path, str]:
  """What an app's data snapshot holds: host path to path in the backup."""
  base = f"{APP_ROOT}/{app}"
  sources = {
    path: f"{base}/{DATA}/{name}" for name, path in _volume_dirs(ctx, app, DATA).items()
  }
  bundle = ctx.loaded_paths(app).bundle_path_in_run
  if bundle.is_dir():
    sources[bundle] = f"{base}/app_bundle"
  config = ctx.config.app_config_path(app)
  if config.is_file():
    sources[config] = f"{base}/config.logtab"
  return sources


def bulk_sources(ctx: KelsoCtx, app: AppID) -> dict[Path, str]:
  """The bulk volumes the operator turned on, as host path to backup path."""
  if not ctx.config.app_config_path(app).is_file():
    return {}
  wanted = ctx.app_store(app).backed_up_bulk()
  return {
    path: f"{APP_ROOT}/{app}/{BULK}/{name}"
    for name, path in _volume_dirs(ctx, app, BULK).items()
    if name in wanted
  }


LINKS_FILE = f"{STATE_ROOT}/links.toml"


def root_links(ctx: KelsoCtx) -> dict[str, str]:
  """Where each volume root and backups/ points, and any other link at the top
  of the kelso root: path under the root to its link target, or "" for a plain
  directory there."""
  root = ctx.config.kelso_root
  named = [f"volumes/{kind}" for kind in ctx.config.volume_roots]
  named.append("backups")
  named += sorted(
    entry.name
    for entry in root.iterdir()
    if entry.is_symlink() and entry.name not in named
  )
  return {
    name: str((root / name).readlink()) if (root / name).is_symlink() else ""
    for name in named
    if (root / name).exists() or (root / name).is_symlink()
  }


def links_toml(ctx: KelsoCtx) -> str:
  """`root_links` as the file a state backup carries."""
  lines = [
    "# Where the kelso root's links pointed when this backup was taken.",
    '# "" is a plain directory in the root.',
    f'kelso_root = "{ctx.config.kelso_root}"',
    "",
    "[links]",
  ]
  lines += [f'"{name}" = "{target}"' for name, target in root_links(ctx).items()]
  return "\n".join(lines) + "\n"


def state_sources(ctx: KelsoCtx) -> dict[Path, str]:
  config = ctx.config
  sources = {
    config.config_path: f"{STATE_ROOT}/config.toml",
    config.kelsodb_path: f"{STATE_ROOT}/kelsodb.logtab",
    config.app_config_root: f"{STATE_ROOT}/apps",
    config.repos[LOCAL_REPO].path: f"{STATE_ROOT}/repos/{LOCAL_REPO}",
  }
  return {host: guest for host, guest in sources.items() if host.exists()}


# --- taking backups --------------------------------------------------------------


def _version(ctx: KelsoCtx, app: AppID) -> str:
  spec = ctx.loaded_spec(app)
  if spec is not None:
    return spec.version
  if ctx.config.app_config_path(app).is_file():
    return str(ctx.app_store(app).get_meta("loaded_version") or "")
  return ""


def backup_app(
  app: AppID, ctx: KelsoCtx, restic: Restic, *, reason: str, run: str
) -> Backup:
  """Back up one app, stopping it for the moment its data is read.

  Takes the app's lock itself. A running app is started again even when the
  backup fails.
  """
  # Imported here: lifecycle imports this module for update and restore.
  from kelso.lib.lifecycle.run import start, stop

  tags = {
    "kelso_id": ctx.kelso_db.kelso_id(),
    "app": str(app),
    "version": _version(ctx, app),
    "reason": reason,
    "run": run,
  }
  by = f"back up {app}"
  snapshots: dict[str, str] = {}
  with ctx.app_lock(app, by):
    running = False
    if ctx.is_loaded(app):
      with ctx.kelso_lock(by):
        running = bool(ctx.run_state(app).running_count)
        if running:
          stop(app, ctx)
    try:
      snapshots[DATA] = restic.backup(
        app_sources(ctx, app), {**tags, "part": DATA}, what=f"back up {app}"
      )
    finally:
      if running:
        with ctx.kelso_lock(by):
          start(app, ctx.config.app_run_path(app), ctx)

    # Written once and read while the app runs: nothing is gained by stopping.
    bulk = bulk_sources(ctx, app)
    if bulk:
      snapshots[BULK] = restic.backup(
        bulk, {**tags, "part": BULK}, what=f"back up {app}'s bulk volumes"
      )
  return Backup(
    app=app,
    run=run,
    time=now().isoformat(timespec="seconds").replace("+00:00", "Z"),
    reason=reason,
    version=tags["version"],
    kelso_id=tags["kelso_id"],
    snapshots=snapshots,
  )


def backup_state(ctx: KelsoCtx, restic: Restic, *, reason: str, run: str) -> str:
  tags = {
    "kelso_id": ctx.kelso_db.kelso_id(),
    STATE: "kelso",
    "reason": reason,
    "run": run,
  }
  scratch = ctx.config.temp_root / "backup-state"
  scratch.mkdir(parents=True, exist_ok=True)
  links = scratch / "links.toml"
  with ctx.kelso_lock("back up kelso's state"):
    links.write_text(links_toml(ctx))
    try:
      sources = {**state_sources(ctx), links: LINKS_FILE}
      return restic.backup(sources, tags, what="back up kelso's own state")
    finally:
      links.unlink(missing_ok=True)


def run_backups(
  ctx: KelsoCtx, *, reason: str, apps: Iterable[AppID] | None = None
) -> RunResult:
  """Back up `apps` (every app, and kelso's state, when None), then forget
  what the keep policy no longer wants.

  An app that fails is recorded and skipped; the rest still run.
  """
  restic = repository(ctx)
  run = run_id()
  refuse_same_second(restic, run)
  result = RunResult(run=run, created=restic.init())
  everything = apps is None
  for app in backed_up_apps(ctx) if apps is None else list(apps):
    try:
      backup_app(app, ctx, restic, reason=reason, run=result.run)
      result.backed_up.append(str(app))
    except Exception as e:
      logger.error("could not back up %s: %s", app, e)
      result.failed.append(f"{app}: {e}")
  if everything:
    try:
      backup_state(ctx, restic, reason=reason, run=result.run)
    except Exception as e:
      logger.error("could not back up kelso's state: %s", e)
      result.failed.append(f"kelso's state: {e}")
  forget_expired(ctx, restic)
  ctx.kelso_db.record_backup_run(
    {
      "run": result.run,
      "time": now().isoformat(timespec="seconds").replace("+00:00", "Z"),
      "reason": reason,
      "everything": everything,
      "backed_up": result.backed_up,
      "failed": result.failed,
    }
  )
  return result


# --- listing and retention ----------------------------------------------------


def backups(restic: Restic, app: AppID | None = None) -> list[Backup]:
  """Every app backup in the repository, or one app's, oldest first."""
  found: dict[tuple[str, str], Backup] = {}
  tags = {"app": str(app)} if app is not None else None
  for snap in restic.snapshots(tags):
    if "app" not in snap.tags:
      continue
    key = (snap.tags["app"], snap.tags.get("run", snap.id))
    backup = found.get(key)
    if backup is None:
      backup = found[key] = Backup(
        app=AppID(snap.tags["app"]),
        run=snap.tags.get("run", snap.id[:8]),
        time=snap.time,
        reason=snap.tags.get("reason", ""),
        version=snap.tags.get("version", ""),
        kelso_id=snap.tags.get("kelso_id", ""),
        snapshots={},
      )
    backup.snapshots[snap.tags.get("part", DATA)] = snap.id
  return sorted(found.values(), key=lambda b: b.run)


def state_backups(restic: Restic) -> list[Snapshot]:
  return [s for s in restic.snapshots({STATE: "kelso"})]


def expired(backups_: list[Backup], keep: BackupKeep) -> list[Backup]:
  """The backups of one app or of kelso's state that `keep` no longer wants.

  Scheduled ones are kept as restic's `--keep-daily` and friends would: the
  newest of each of the last `daily` days that have one, and likewise for
  weeks and months. The rest are kept by count, each reason on its own.
  """
  newest_first = sorted(backups_, key=lambda b: b.run, reverse=True)
  kept: set[str] = set()

  scheduled = [b for b in newest_first if b.reason == SCHEDULED]
  for count, period in (
    (keep.daily, "%Y-%m-%d"),
    (keep.weekly, "%G-%V"),
    (keep.monthly, "%Y-%m"),
  ):
    seen: list[str] = []
    for backup in scheduled:
      bucket = datetime.fromisoformat(backup.time).strftime(period)
      if bucket in seen:
        continue
      if len(seen) == count:
        break
      seen.append(bucket)
      kept.add(backup.run)

  for reason, count in ((MANUAL, keep.manual), (UPDATE, 1), (PRE_RESTORE, 1)):
    of_reason = [b for b in newest_first if b.reason == reason]
    kept |= {b.run for b in of_reason[:count]}

  return [b for b in backups_ if b.run not in kept]


def forget_expired(ctx: KelsoCtx, restic: Restic, *, prune: bool = True) -> int:
  """Forget every backup the keep policy no longer wants.

  With `prune`, also free the space they held. Pruning rewrites part of the
  repository, so the backups taken around an update or a restore skip it and
  leave it to the next scheduled run.
  """
  keep = ctx.config.backup.keep
  by_owner: dict[str, list[Backup]] = {}
  for backup in backups(restic):
    by_owner.setdefault(str(backup.app), []).append(backup)
  for snap in state_backups(restic):
    by_owner.setdefault("", []).append(
      Backup(
        app=None,
        run=snap.tags.get("run", snap.id),
        time=snap.time,
        reason=snap.tags.get("reason", ""),
        version="",
        kelso_id=snap.tags.get("kelso_id", ""),
        snapshots={STATE: snap.id},
      )
    )

  doomed = [
    snap_id
    for owned in by_owner.values()
    for backup in expired(owned, keep)
    for snap_id in backup.snapshots.values()
  ]
  if doomed:
    restic.forget(doomed, what="forget expired backups")
    if prune:
      restic.prune()
  return len(doomed)
