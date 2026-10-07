"""Move every encrypted value kelso holds onto the master key a seed derives.

Encrypted values live in two places: each app's config logtab (`config/<name>`
records marked secret) and kelsodb (`system/secrets/*` and `system/tokens/*`).
For each value in effect, rekey appends a new `set` record holding it encrypted
under the new key. History is never rewritten: older records stay as written,
and the key they were written under is still in the key file's history.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cryptography.fernet import InvalidToken

from kelso.lib.crypto import CryptoEngine, FernetCryptoEngine, crypto_from_config
from kelso.lib.kelso import KelsoCtx
from kelso.lib.recovery import read_keyfile, write_seed
from kelso.lib.store import REKEY_FIELD, JsonLogtabStore

logger = logging.getLogger("kelso.rekey")

Rotate = Callable[[str, str], str | None]


@dataclass
class RekeyResult:
  apps: int = 0
  values: int = 0
  # "<file>: <key>" for each value no key on file could decrypt; left as it was.
  unreadable: list[str] = field(default_factory=list)


def _app_record(key: str, value: Any, rotate: Rotate) -> Any:
  if (
    key.startswith("config/")
    and isinstance(value, dict)
    and value.get("secret")
    and isinstance(value.get("value"), str)
  ):
    blob = rotate(key, value["value"])
    return None if blob is None else {**value, "value": blob, REKEY_FIELD: True}
  return None


def _kelsodb_record(key: str, value: Any, rotate: Rotate) -> Any:
  if key.startswith("system/secrets/") and isinstance(value, str):
    return rotate(key, value)
  if (
    key.startswith("system/tokens/")
    and isinstance(value, dict)
    and isinstance(value.get("tok"), str)
  ):
    blob = rotate(key, value["tok"])
    return None if blob is None else {**value, "tok": blob}
  return None


def _append_reencrypted(
  path: Path,
  old: CryptoEngine,
  new: CryptoEngine,
  record: Callable[[str, Any, Rotate], Any],
  result: RekeyResult,
) -> int:
  """Append each encrypted value in effect, moved from `old` to `new`."""
  store = JsonLogtabStore(path)

  def rotate(key: str, blob: str) -> str | None:
    try:
      return new.encrypt(old.decrypt(blob))
    except (InvalidToken, ValueError):
      result.unreadable.append(f"{path.name}: {key}")
      return None

  written = 0
  for key, value in store.scan().items():
    moved = record(key, value, rotate)
    if moved is not None:
      store.write(key, moved)
      written += 1
  return written


def reencrypt_app_store(
  path: Path,
  old: CryptoEngine,
  new: CryptoEngine,
  result: RekeyResult | None = None,
) -> int:
  """Append one app's secrets, decrypted with `old`, encrypted with `new`.

  Each record is marked as a rekey: the plaintext did not change, so nothing
  becomes pending. The caller holds the app's lock.
  """
  result = result if result is not None else RekeyResult()
  if not path.is_file():
    return 0
  return _append_reencrypted(path, old, new, _app_record, result)


def rekey(ctx: KelsoCtx, entropy: bytes) -> RekeyResult:
  """Make `entropy` the seed, then re-encrypt everything under its key.

  Every write is an append, so a rekey that dies part-way is undone by
  deleting the lines it added to each file, the `seed` record last.
  """
  config = ctx.config
  old = crypto_from_config(config)
  with ctx.kelso_lock("rekey"):
    write_seed(config.master_keyfile, entropy)
  new = FernetCryptoEngine(read_keyfile(config.master_keyfile).master_key)

  result = RekeyResult()
  for app in sorted(config.app_config_ids()):
    with ctx.app_lock(app, "rekey"):
      path = config.app_config_path(app)
      result.values += reencrypt_app_store(path, old, new, result)
      result.apps += 1

  with ctx.kelso_lock("rekey"):
    if config.kelsodb_path.is_file():
      result.values += _append_reencrypted(
        config.kelsodb_path, old, new, _kelsodb_record, result
      )

  for item in result.unreadable:
    logger.warning("could not decrypt %s with any key on file; left as it was", item)
  return result
