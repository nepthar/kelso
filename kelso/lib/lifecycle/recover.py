"""Bringing a whole kelso back from its backups, and clearing the way for it.

The path back after losing a machine is three steps, with room between them to
point volume roots at the disks this machine has:

    kelso init --with-phrase      the same recovery phrase, so the same keys
    (link volumes/<kind>, backups/ where they should go)
    kelso restore <backups dir>   kelso's state, then every app

Restore only goes onto a root that holds no app; `purge` makes one that does
into one that does not.
"""

import shlex
import shutil
import tomllib
from dataclasses import dataclass, field
from logging import getLogger
from pathlib import Path

from kelso.lib.apps import AppID
from kelso.lib.backup import (
  LINKS_FILE,
  STATE_ROOT,
  backup_password_from,
  backups,
  root_links,
  state_backups,
)
from kelso.lib.config import load_config_file
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle.restore import restore, restore_plan
from kelso.lib.lifecycle.rm import PURGE, removal_plan, rm
from kelso.lib.lifecycle.rootfs import run_as_root
from kelso.lib.lifecycle.run import stop
from kelso.lib.recovery import read_keyfile
from kelso.lib.repo import LOCAL_REPO
from kelso.lib.restic import WRONG_PASSWORD, Restic, ResticError

logger = getLogger("kelso.lifecycle.recover")


def held_apps(ctx: KelsoCtx) -> list[AppID]:
  """Every app this root holds something of: config, a loaded copy, or data in
  any volume root. Containers are not asked: on a docker daemon two roots
  share, some of them are another root's."""
  config = ctx.config
  ids = set(config.app_config_ids())
  if config.run_root.is_dir():
    ids |= {entry.name for entry in config.run_root.iterdir() if entry.is_dir()}
  for root in config.volume_roots.values():
    if root.is_dir():
      ids |= {entry.name for entry in root.iterdir() if entry.is_dir()}
  return [AppID(raw) for raw in sorted(ids)]


# --- purge -------------------------------------------------------------------


def purge(ctx: KelsoCtx) -> list[AppID]:
  """Stop and purge every app: its loaded copy, config, secrets, volumes and
  routes. Leaves config.toml, the key file, repos, backups/ and the volume
  roots themselves -- links included -- as they are. Returns what it purged.
  """
  purged = []
  for app in held_apps(ctx):
    with ctx.locked(f"purge {app}", app):
      try:
        running = ctx.run_state(app).running_count
      except ValueError:
        running = 0
      if running:
        stop(app, ctx)
      rm(removal_plan(app, ctx, mode=PURGE), ctx)
    purged.append(app)
  scratch = ctx.config.temp_root / "restore"
  if scratch.exists():
    run_as_root(
      "remove what earlier restores left",
      f"rm -rf -- {shlex.quote(str(scratch.resolve()))}",
      [scratch.parent],
    )
  return purged


# --- restoring everything -------------------------------------------------------


@dataclass
class RecoverResult:
  kelso_id: str
  restored: list[str] = field(default_factory=list)
  # "<app>: <what went wrong>" for each app that did not come back.
  failed: list[str] = field(default_factory=list)
  # Whether backups/ now points at where the backups were restored from.
  linked: bool = False
  # Mirrored repos fetched again, and "<repo>: <error>" for those that were not.
  mirrored: list[str] = field(default_factory=list)
  unmirrored: list[str] = field(default_factory=list)


def open_backups(ctx: KelsoCtx, path: Path) -> Restic:
  """The repository at `path`, opened with this root's recovery phrase."""
  seed = read_keyfile(ctx.config.master_keyfile).seed
  if seed is None:
    raise ValueError(
      "This kelso has no recovery phrase. Make it with "
      "`kelso init --with-phrase`, using the phrase the backups were made under."
    )
  if not (path / "config").is_file():
    raise ValueError(f"There is no backup repository at {path}")
  restic = Restic(
    path.resolve(), backup_password_from(seed), ctx.config.temp_root / "restic-cache"
  )
  try:
    restic.snapshots()
  except ResticError as e:
    if e.returncode == WRONG_PASSWORD:
      raise ValueError(
        f"The backups at {path} were made under a different recovery phrase. "
        f"Run `kelso system rekey --phrase` with the phrase they were made "
        f"under, then restore again."
      ) from e
    raise
  return restic


@dataclass(frozen=True)
class LinkChange:
  """One path in the kelso root, as the backup recorded it and as it is here.
  An empty target is a plain directory."""

  name: str
  then: str
  now: str


@dataclass(frozen=True)
class Layout:
  """The kelso root's links when the newest state backup was taken."""

  kelso_root: str
  links: dict[str, str]
  taken: str

  def compared(self, ctx: KelsoCtx) -> list[LinkChange]:
    here = root_links(ctx)
    names = [*self.links, *(n for n in here if n not in self.links)]
    return [LinkChange(n, self.links.get(n, ""), here.get(n, "")) for n in names]

  def differs(self, ctx: KelsoCtx) -> bool:
    """Whether a volume root here points somewhere else than it did. backups/
    is left out: restore links it itself."""
    return any(
      change.then != change.now
      for change in self.compared(ctx)
      if change.name != "backups"
    )


def recorded_layout(ctx: KelsoCtx, path: Path) -> Layout | None:
  """What the newest state backup at `path` says the root's links were, or
  None for a backup that predates the record."""
  restic = open_backups(ctx, path)
  states = state_backups(restic)
  if not states:
    raise ValueError(f"The backups at {path} hold no backup of kelso's state")
  try:
    raw = restic.dump(states[-1].id, LINKS_FILE, what="read the backup's layout")
  except ResticError:
    return None
  parsed = tomllib.loads(raw)
  return Layout(
    kelso_root=str(parsed.get("kelso_root", "")),
    links={str(k): str(v) for k, v in parsed.get("links", {}).items()},
    taken=states[-1].time,
  )


def _copy_state(tree: Path, ctx: KelsoCtx) -> KelsoCtx:
  """Put kelso's state from a restored backup in place; return a context on
  the config.toml it brought back."""
  config = ctx.config
  shutil.copyfile(tree / "config.toml", config.config_path)
  # Paths come from the config.toml just restored, not the one init wrote.
  ctx = KelsoCtx(load_config_file(config.config_path))
  config = ctx.config

  if (tree / "kelsodb.logtab").is_file():
    shutil.copyfile(tree / "kelsodb.logtab", config.kelsodb_path)
  apps = tree / "apps"
  if apps.is_dir():
    config.app_config_root.mkdir(parents=True, exist_ok=True)
    for logtab in apps.iterdir():
      shutil.copyfile(logtab, config.app_config_root / logtab.name)
  for saved, target in (
    (tree / "repos" / LOCAL_REPO, config.repos[LOCAL_REPO].path),
    (tree / "scripts", config.scripts_root),
  ):
    if saved.is_dir():
      if target.exists():
        shutil.rmtree(target)
      shutil.copytree(saved, target, symlinks=True)
  return ctx


def _link_backups(ctx: KelsoCtx, source: Path) -> bool:
  """Point backups/ at where the backups came from, so the next one adds to
  them. Only over an empty directory: anything else was put there on purpose."""
  root = ctx.config.backups_root
  if root.resolve() == source.resolve():
    return True
  if root.is_symlink() or (root.is_dir() and any(root.iterdir())):
    return False
  if root.is_dir():
    root.rmdir()
  root.symlink_to(source.resolve())
  return True


def recover(ctx: KelsoCtx, path: Path) -> RecoverResult:
  """Restore kelso's state and then every app from the backups at `path`.

  Refuses a root that holds any app. Apps come back stopped.
  """
  held = held_apps(ctx)
  if held:
    raise ValueError(
      f"This kelso already holds {len(held)} app(s) ({', '.join(held)}), and "
      f"restoring everything would overwrite them. To restore one app, use "
      f"`kelso restore <app> <backup>`. To clear this kelso for a full "
      f"restore, run `kelso system purge`."
    )
  restic = open_backups(ctx, path)
  states = state_backups(restic)
  if not states:
    raise ValueError(f"The backups at {path} hold no backup of kelso's state")

  scratch = ctx.config.temp_root / "restore-state"
  try:
    _remove_as_root(scratch)
    restic.restore(states[-1].id, scratch, what="read kelso's state from its backup")
    with ctx.kelso_lock("restore kelso"):
      ctx = _copy_state(scratch / STATE_ROOT.lstrip("/"), ctx)
  finally:
    _remove_as_root(scratch)

  result = RecoverResult(kelso_id=states[-1].tags.get("kelso_id", ""))
  result.linked = _link_backups(ctx, path)
  _mirror_repos(ctx, result)

  newest = {}
  for backup in backups(restic):
    newest[backup.app] = backup
  for app, backup in sorted(newest.items()):
    try:
      plan = restore_plan(app, backup.id, ctx, restic)
      with ctx.locked(f"restore {app}", app):
        restore(plan, ctx, restic, backup_first=False)
      result.restored.append(str(app))
    except Exception as e:
      logger.error("could not restore %s: %s", app, e)
      result.failed.append(f"{app}: {e}")

  return result


def _mirror_repos(ctx: KelsoCtx, result: RecoverResult) -> None:
  """Fetch each mirrored repo the restored config.toml names.

  A backup holds the local repo, not mirrors: those are git's to fetch again,
  and what `init` fetched may be another branch than the restored config says.
  Best effort, as at init: a repo that cannot be fetched now is still configured.
  """
  from kelso.lib import repo as repo_lib

  for repo in ctx.config.repos.values():
    if not repo.mirrored:
      continue
    try:
      repo_lib.mirror(repo, ctx)
      result.mirrored.append(repo.name)
    except Exception as e:
      logger.warning("could not fetch repo %s: %s", repo.name, e)
      result.unmirrored.append(f"{repo.name}: {e}")


def _remove_as_root(path: Path) -> None:
  if path.exists():
    run_as_root(
      f"remove {path}", f"rm -rf -- {shlex.quote(str(path.resolve()))}", [path.parent]
    )
