"""Putting an app back as one of its backups holds it."""

import shlex
import shutil
from dataclasses import dataclass
from logging import getLogger
from pathlib import Path

from kelso.lib.apps import AppID, record_app_action
from kelso.lib.backup import (
  APP_ROOT,
  BULK,
  DATA,
  PRE_RESTORE,
  Backup,
  backup_app,
  backups,
  refuse_same_second,
  run_id,
)
from kelso.lib.crypto import FernetCryptoEngine
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle.load import materialize
from kelso.lib.lifecycle.rootfs import run_as_root
from kelso.lib.recovery import master_key_at
from kelso.lib.restic import Restic
from kelso.lib.run_layout import AppRunData
from kelso.lib.spec import AppSpec

logger = getLogger("kelso.lifecycle.restore")


@dataclass(frozen=True)
class RestorePlan:
  """What `kelso backup restore` will overwrite, and with which backup."""

  app_id: AppID
  backup: Backup
  run_path: Path
  config_path: Path
  # No pre-restore backup is taken when undoing the last restore, or every undo
  # would make another and chase its own tail.
  is_latest_pre_restore: bool = False


def find_backup(app: AppID, backup_id: str, restic: Restic) -> Backup:
  """One app's backup by id. Raises ValueError naming the ones there are."""
  available = backups(restic, app)
  for backup in available:
    if backup.id == backup_id:
      return backup
  detail = "\n".join(f"  {b.id}  {b.reason}" for b in reversed(available))
  raise ValueError(
    f"No backup {backup_id} of {app}. Available:\n{detail or '  (none)'}"
  )


def restore_plan(
  app: AppID, backup_id: str, ctx: KelsoCtx, restic: Restic
) -> RestorePlan:
  """Work out what restoring would overwrite, without overwriting it."""
  backup = find_backup(app, backup_id, restic)

  # Clobbering files and data volumes out from under live containers is how a
  # restore becomes a corrupt half-state.
  try:
    running_count = ctx.run_state(app).running_count
  except ValueError:
    running_count = 0
  if running_count:
    raise ValueError(
      f"App {app} has {running_count} running Kelso-labeled container(s); "
      f"run `kelso stop {app}` first"
    )

  pre_restores = [b for b in backups(restic, app) if b.reason == PRE_RESTORE]
  return RestorePlan(
    app_id=app,
    backup=backup,
    run_path=ctx.loaded_paths(app).run_path,
    config_path=ctx.config.app_config_path(app),
    is_latest_pre_restore=bool(pre_restores) and pre_restores[-1].id == backup.id,
  )


def _remove_as_root(path: Path) -> None:
  """Restic restores as root, so what it wrote can only be removed as root."""
  if path.exists():
    run_as_root(
      f"remove {path}", f"rm -rf -- {shlex.quote(str(path.resolve()))}", [path.parent]
    )


def _replace_volumes(app: AppID, kind: str, restored: Path, ctx: KelsoCtx) -> None:
  """Put each volume of `kind` the backup holds over the live one."""
  if not restored.is_dir():
    return
  names = sorted(entry.name for entry in restored.iterdir() if entry.is_dir())
  if not names:
    return
  live_root = ctx.config.volume_roots[kind] / app
  live_root.mkdir(parents=True, exist_ok=True)

  # Containers write as root, so neither the removal nor the copy can be done
  # by this process. One `sh -c` in one container keeps it to a single step, so
  # a failure to start that container fails before anything has been deleted.
  # Targets are named under the *resolved* root: they have to match the bind
  # mount, and a target that is itself a symlink should be replaced, not
  # followed.
  root = live_root.resolve()
  targets = " ".join(shlex.quote(str(root / name)) for name in names)
  sources = " ".join(shlex.quote(str(restored.resolve() / name)) for name in names)
  script = f"set -e; rm -rf -- {targets}; cp -a -- {sources} {shlex.quote(str(root))}"
  run_as_root(f"restore the {kind} volumes of {app}", script, [live_root, restored])


def restore(
  plan: RestorePlan, ctx: KelsoCtx, restic: Restic, *, backup_first: bool = True
) -> AppRunData | None:
  """Replace an app's data, config and loaded copy with a backup's.

  The caller holds the app and kelso locks. Returns the run data of the app as
  restored, or None when the backup was of an app that was not loaded.
  """
  app = plan.app_id
  backup = plan.backup
  scratch = ctx.config.temp_root / "restore" / str(app)
  _remove_as_root(scratch)
  try:
    for part in (DATA, BULK):
      if part in backup.snapshots:
        restic.restore(backup.snapshots[part], scratch, what=f"read {app}'s backup")
    tree = scratch / APP_ROOT.lstrip("/") / str(app)
    return _restore_from(plan, tree, ctx, restic, backup_first=backup_first)
  finally:
    _remove_as_root(scratch)


def _restore_from(
  plan: RestorePlan, tree: Path, ctx: KelsoCtx, restic: Restic, *, backup_first: bool
) -> AppRunData | None:
  app = plan.app_id
  if not (tree / "config.logtab").is_file():
    raise ValueError(f"Backup {plan.backup.id} of {app} holds no config; not restored")

  # Parse the backup's loaded copy before touching anything; a broken one fails
  # here with the current state intact.
  bundle = tree / "app_bundle"
  spec = AppSpec.from_file(bundle / "manifest.toml", app) if bundle.is_dir() else None

  has_state = plan.run_path.exists() or plan.config_path.exists()
  if backup_first and has_state and not plan.is_latest_pre_restore:
    try:
      run = run_id()
      refuse_same_second(restic, run, app)
      pre = backup_app(app, ctx, restic, reason=PRE_RESTORE, run=run)
    except Exception as e:
      raise ValueError(
        f"Backing up {app} before restoring failed; the restore was not started. "
        f"Fix the problem below, or pass --no-backup to skip.\n{e}"
      ) from e
    logger.info("backed up %s before restoring, as %s", app, pre.id)

  # Volumes first: the step most likely to fail outright, and failing here
  # leaves the loaded copy and config untouched.
  _replace_volumes(app, DATA, tree / DATA, ctx)
  _replace_volumes(app, BULK, tree / BULK, ctx)

  plan.config_path.parent.mkdir(parents=True, exist_ok=True)
  shutil.copyfile(tree / "config.logtab", plan.config_path)
  # Taken before a rekey, its secrets are under the key current then: append
  # them again under today's.
  then = master_key_at(ctx.config.master_keyfile, plan.backup.time)
  if then and then != ctx.config.master_key:
    ctx.app_store(app).rekey_secrets(FernetCryptoEngine(then))

  if plan.run_path.exists():
    shutil.rmtree(plan.run_path)
  if spec is None:
    record_app_action("restored", app, ctx, backup=plan.backup.id)
    logger.info(
      "restored %s from backup %s; it was not loaded then", app, plan.backup.id
    )
    return None

  plan.run_path.mkdir(parents=True, mode=0o700)
  shutil.copytree(bundle, plan.run_path / "app_bundle")
  try:
    run_data, _ = materialize(spec, ctx)
  except Exception as e:
    # The volumes and loaded copy are already the backup's; only the generated
    # half is missing, which is what re-loading rebuilds.
    record_app_action("restore-failed", app, ctx)
    raise ValueError(
      f"App {app} was restored from backup {plan.backup.id}, but its compose.yml "
      f"and routes could not be rebuilt; fix the problem below and run "
      f"`kelso load {app}`.\n{e}"
    ) from e

  record_app_action("restored", app, ctx, backup=plan.backup.id)
  logger.info("restored %s from backup %s", app, plan.backup.id)
  return run_data
