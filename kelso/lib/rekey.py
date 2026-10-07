"""Move every encrypted value kelso holds onto the master key a seed derives.

Encrypted values live in two places: each app's config logtab (`config/<name>`
records marked secret) and kelsodb (`system/secrets/*` and `system/tokens/*`).
For each value in effect, rekey appends a new `set` record holding it encrypted
under the new key. History is never rewritten: older records stay as written,
readable through the retired key kept beside the new one.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cryptography.fernet import InvalidToken

from kelso.lib.crypto import CryptoEngine, FernetCryptoEngine
from kelso.lib.kelso import KelsoCtx
from kelso.lib.observations import REKEYED_AT, changes_since_start
from kelso.lib.recovery import read_keyfile, write_seed
from kelso.lib.store import JsonLogtabStore
from kelso.lib.util import now_ts

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
    return None if blob is None else {**value, "value": blob}
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
  crypto: CryptoEngine,
  record: Callable[[str, Any, Rotate], Any],
  result: RekeyResult,
) -> int:
  """Append each encrypted value in effect, re-encrypted. Returns how many."""
  store = JsonLogtabStore(path)

  def rotate(key: str, blob: str) -> str | None:
    try:
      return crypto.rotate(blob)
    except InvalidToken:
      result.unreadable.append(f"{path.name}: {key}")
      return None

  written = 0
  for key, value in store.scan().items():
    new = record(key, value, rotate)
    if new is not None:
      store.write(key, new)
      written += 1
  return written


def reencrypt_app_store(
  path: Path, crypto: CryptoEngine, result: RekeyResult | None = None
) -> int:
  """Append one app's secrets re-encrypted under `crypto`'s current key.

  The plaintext does not change, so neither does what is pending: an app that
  was current before is marked current again. The caller holds the app's lock.
  """
  result = result if result is not None else RekeyResult()
  if not path.is_file():
    return 0
  was_current = not changes_since_start(path).any
  written = _append_reencrypted(path, crypto, _app_record, result)
  if written and was_current:
    JsonLogtabStore(path).write(REKEYED_AT, now_ts())
  return written


def rekey(ctx: KelsoCtx, entropy: bytes) -> RekeyResult:
  """Make `entropy` the seed, then re-encrypt everything under its key.

  The new key is written first, with the old one retired beside it. From then
  on every reader decrypts both, so a crash part-way leaves a root that works,
  and running rekey again with the same phrase finishes the job.
  """
  config = ctx.config
  with ctx.kelso_lock("rekey"):
    write_seed(config.master_keyfile, entropy)
  keyfile = read_keyfile(config.master_keyfile)
  crypto = FernetCryptoEngine(keyfile.master_key, keyfile.retired)

  result = RekeyResult()
  for app in sorted(config.app_config_ids()):
    with ctx.app_lock(app, "rekey"):
      result.values += reencrypt_app_store(config.app_config_path(app), crypto, result)
      result.apps += 1

  with ctx.kelso_lock("rekey"):
    if config.kelsodb_path.is_file():
      result.values += _append_reencrypted(
        config.kelsodb_path, crypto, _kelsodb_record, result
      )

  for item in result.unreadable:
    logger.warning("could not decrypt %s with any key on file; left as it was", item)
  return result
