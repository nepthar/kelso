"""Move every encrypted value kelso holds onto the master key a new seed derives.

Each store re-appends its own secrets: the app stores and kelsodb, as a
context opened on the new key hands them out, each reading with the old key.
History is never rewritten. Older records stay as written, and the key they
were written under is still in the key file's history.
"""

import logging
from dataclasses import dataclass, field

from kelso.lib.config import load_config_file
from kelso.lib.crypto import crypto_from_config
from kelso.lib.kelso import KelsoCtx
from kelso.lib.recovery import write_seed

logger = logging.getLogger("kelso.rekey")


@dataclass
class RekeyResult:
  apps: int = 0
  values: int = 0
  # "<app or kelsodb>: <key>" for each value the old key could not decrypt;
  # left as it was.
  unreadable: list[str] = field(default_factory=list)


def rekey(ctx: KelsoCtx, entropy: bytes) -> RekeyResult:
  """Make `entropy` the seed, then re-encrypt everything under its key.

  Every write is an append, so a rekey that dies part-way is undone by
  deleting the lines it added to each file, the `seed` record last.
  """
  old = crypto_from_config(ctx.config)
  with ctx.kelso_lock("rekey"):
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
