import base64
import hashlib
from typing import Protocol

from cryptography.fernet import Fernet

from kelso.lib.config import Config


class CryptoEngine(Protocol):
  def encrypt(self, plaintext: str) -> str: ...

  def decrypt(self, ciphertext: str) -> str: ...


def crypto_from_config(config: Config) -> CryptoEngine:
  if config.master_key:
    return FernetCryptoEngine(config.master_key)
  return MissingKeyEngine(str(config.master_keyfile))


class MissingKeyEngine:
  """A root with no seed: refuses every use rather than store plaintext."""

  def __init__(self, keyfile: str):
    self._keyfile = keyfile

  def _refuse(self, _: str) -> str:
    raise ValueError(
      f"No recovery phrase in {self._keyfile}, so kelso has no key to encrypt "
      f"or decrypt secrets with. Make one with `kelso system rekey`."
    )

  encrypt = decrypt = _refuse


class NoopCryptoEngine:
  """Stores values as they are. For tests that do not exercise encryption."""

  def encrypt(self, plaintext: str) -> str:
    return plaintext

  def decrypt(self, ciphertext: str) -> str:
    return ciphertext


def _fernet(master_key: str) -> Fernet:
  digest = hashlib.sha256(master_key.encode()).digest()
  return Fernet(base64.urlsafe_b64encode(digest))


class FernetCryptoEngine:
  def __init__(self, master_key: str):
    self._fernet = _fernet(master_key)

  def encrypt(self, plaintext: str) -> str:
    return self._fernet.encrypt(plaintext.encode()).decode()

  def decrypt(self, ciphertext: str) -> str:
    return self._fernet.decrypt(ciphertext.encode()).decode()
