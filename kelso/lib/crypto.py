import base64
import hashlib
from collections.abc import Iterable
from typing import Protocol

from cryptography.fernet import Fernet, MultiFernet

from kelso.lib.config import Config


class CryptoEngine(Protocol):
  def encrypt(self, plaintext: str) -> str: ...

  def decrypt(self, ciphertext: str) -> str: ...

  def rotate(self, ciphertext: str) -> str: ...


def crypto_from_config(config: Config) -> CryptoEngine:
  if config.master_key:
    return FernetCryptoEngine(config.master_key, config.retired_master_keys)
  return NoopCryptoEngine()


class NoopCryptoEngine:
  def encrypt(self, plaintext: str) -> str:
    return plaintext

  def decrypt(self, ciphertext: str) -> str:
    return ciphertext

  def rotate(self, ciphertext: str) -> str:
    return ciphertext


def _fernet(master_key: str) -> Fernet:
  digest = hashlib.sha256(master_key.encode()).digest()
  return Fernet(base64.urlsafe_b64encode(digest))


class FernetCryptoEngine:
  """Encrypts under `master_key`; decrypts under it or any `retired` key."""

  def __init__(self, master_key: str, retired: Iterable[str] = ()):
    keys = [master_key, *(k for k in retired if k and k != master_key)]
    self._fernet = MultiFernet([_fernet(k) for k in keys])

  def encrypt(self, plaintext: str) -> str:
    return self._fernet.encrypt(plaintext.encode()).decode()

  def decrypt(self, ciphertext: str) -> str:
    return self._fernet.decrypt(ciphertext.encode()).decode()

  def rotate(self, ciphertext: str) -> str:
    """The same plaintext, encrypted under the current key."""
    return self._fernet.rotate(ciphertext.encode()).decode()
