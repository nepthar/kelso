"""Move every encrypted value kelso holds onto the master key a seed derives.

Encrypted values live in two places: each app's config logtab (`config/<name>`
records marked secret) and kelsodb (`system/secrets/*` and `system/tokens/*`).
Each record is re-encrypted where it sits, history included, so nothing on file
is left readable only under the key being replaced.
"""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cryptography.fernet import InvalidToken

from kelso.lib.crypto import CryptoEngine, FernetCryptoEngine
from kelso.lib.kelso import KelsoCtx
from kelso.lib.logtab import LogTab
from kelso.lib.recovery import read_keyfile, write_seed

logger = logging.getLogger("kelso.rekey")


@dataclass
class RekeyResult:
  apps: int = 0
  values: int = 0
  # "<file>: <key>" for each value no key on file could decrypt; left as it was.
  unreadable: list[str] = field(default_factory=list)


def _rotator(
  crypto: CryptoEngine, path: Path, result: RekeyResult, *, plaintext: bool
) -> Callable[[str, str], str]:
  """Re-encrypt one ciphertext, or encrypt it if the root never had a key."""

  def rotate(key: str, blob: str) -> str:
    try:
      return crypto.rotate(blob)
    except InvalidToken:
      if plaintext:
        return crypto.encrypt(blob)
      result.unreadable.append(f"{path.name}: {key}")
      return blob

  return rotate


def _json_transform(
  path: Path,
  result: RekeyResult,
  crypto: CryptoEngine,
  plaintext: bool,
  rewrite: Callable[[str, Any, Callable[[str, str], str]], Any],
) -> int:
  rotate = _rotator(crypto, path, result, plaintext=plaintext)

  def transform(key: str, raw: str) -> str:
    try:
      value = json.loads(raw)
    except ValueError:
      return raw
    new = rewrite(key, value, rotate)
    return raw if new is value else json.dumps(new, separators=(",", ":"))

  return LogTab(path).rewrite(transform)


def _app_record(key: str, value: Any, rotate: Callable[[str, str], str]) -> Any:
  if (
    key.startswith("config/")
    and isinstance(value, dict)
    and value.get("secret")
    and isinstance(value.get("value"), str)
  ):
    return {**value, "value": rotate(key, value["value"])}
  return value


def _kelsodb_record(key: str, value: Any, rotate: Callable[[str, str], str]) -> Any:
  if key.startswith("system/secrets/") and isinstance(value, str):
    return rotate(key, value)
  if (
    key.startswith("system/tokens/")
    and isinstance(value, dict)
    and isinstance(value.get("tok"), str)
  ):
    return {**value, "tok": rotate(key, value["tok"])}
  return value


def reencrypt_app_store(
  path: Path, crypto: CryptoEngine, result: RekeyResult | None = None
) -> int:
  """Re-encrypt one app config logtab's secrets under `crypto`'s current key.

  The caller holds the app's lock.
  """
  result = result if result is not None else RekeyResult()
  if not path.is_file():
    return 0
  return _json_transform(path, result, crypto, False, _app_record)


def rekey(ctx: KelsoCtx, entropy: bytes) -> RekeyResult:
  """Make `entropy` the seed, then re-encrypt everything under its key.

  The new key is written first, with the old one retired beside it. From then
  on every reader decrypts both, so a crash part-way leaves a root that works,
  and running rekey again with the same phrase finishes the job.
  """
  config = ctx.config
  had_key = bool(config.master_key)
  with ctx.kelso_lock("rekey"):
    write_seed(config.master_keyfile, entropy)
  keyfile = read_keyfile(config.master_keyfile)
  crypto = FernetCryptoEngine(keyfile.master_key, keyfile.retired)
  plaintext = not had_key

  result = RekeyResult()
  for app in sorted(config.app_config_ids()):
    with ctx.app_lock(app, "rekey"):
      path = config.app_config_path(app)
      result.values += _json_transform(path, result, crypto, plaintext, _app_record)
      result.apps += 1

  with ctx.kelso_lock("rekey"):
    if config.kelsodb_path.is_file():
      result.values += _json_transform(
        config.kelsodb_path, result, crypto, plaintext, _kelsodb_record
      )

  for item in result.unreadable:
    logger.warning("could not decrypt %s with any key on file; left as it was", item)
  return result
