"""Move every encrypted value kelso holds onto the master key a new seed derives.

Each store re-appends its own secrets: the app stores and kelsodb, as a
context opened on the new key hands them out, each reading with the old key.
History is never rewritten. Older records stay as written, and the key they
were written under is still in the key file's history.
"""

import logging
from dataclasses import dataclass, field

from kelso.lib.backup import repository
from kelso.lib.config import load_config_file
from kelso.lib.crypto import crypto_from_config
from kelso.lib.kelso import KelsoCtx
from kelso.lib.recovery import backup_password_from, write_seed

logger = logging.getLogger("kelso.rekey")


@dataclass
class RekeyResult:
  apps: int = 0
  values: int = 0
  # "<app or kelsodb>: <key>" for each value the old key could not decrypt;
  # left as it was.
  unreadable: list[str] = field(default_factory=list)


def _move_backups(ctx: KelsoCtx, entropy: bytes) -> None:
  """Give the backup repository the password the new phrase derives."""
  root = ctx.config.backups_root
  if not root.is_symlink() and not (root / "config").is_file():
    return  # No repository yet: the first backup makes it with the new one.
  try:
    restic = repository(ctx)
  except ValueError as e:
    raise ValueError(
      f"Nothing changed: the backup repository's password comes from the "
      f"recovery phrase, and it cannot be updated now. {e}"
    ) from e
  if restic.exists():
    restic.change_password(
      backup_password_from(entropy), ctx.config.temp_root / "rekey"
    )


def rekey(ctx: KelsoCtx, entropy: bytes) -> RekeyResult:
  """Make `entropy` the seed, then re-encrypt everything under its key.

  Every write is an append, so a rekey that dies part-way is undone by
  deleting the lines it added to each file, the `seed` record last.
  """
  old = crypto_from_config(ctx.config)
  with ctx.kelso_lock("rekey"):
    # First, so a backup disk that is not there stops the rekey before
    # anything changed: its password comes from the phrase too.
    _move_backups(ctx, entropy)
    write_seed(ctx.config.master_keyfile, entropy)
  # Its stores encrypt with the key the new seed derives.
  rekeyed = KelsoCtx(load_config_file(ctx.config.config_path))

  result = RekeyResult()
  for app in sorted(ctx.config.app_config_ids()):
    with ctx.app_lock(app, "rekey"):
      written, unreadable = rekeyed.app_store(app).rekey_secrets(old)
    result.apps += 1
    result.values += written
    result.unreadable += [f"{app}: {name}" for name in unreadable]

  with ctx.kelso_lock("rekey"):
    written, unreadable = rekeyed.kelso_db.rekey_secrets(old)
  result.values += written
  result.unreadable += [f"kelsodb: {name}" for name in unreadable]

  for item in result.unreadable:
    logger.warning("could not decrypt %s with the old key; left as it was", item)
  return result
