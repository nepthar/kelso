import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from mnemonic import Mnemonic

from kelso.lib.logtab import LogTab

WORDS = 12
ENTROPY_BYTES = 16

SEED_KEY = "seed"

# The label for the key that encrypts secrets. A new use gets its own label,
# never this one, so a key for one thing cannot decrypt another.
SECRETS_LABEL = "kelso/secrets/v1"
# The backup repository's password, so the phrase alone opens a backup.
BACKUP_LABEL = "kelso/backup/v1"

_WORDLIST = Mnemonic("english")


def new_entropy() -> bytes:
  return secrets.token_bytes(ENTROPY_BYTES)


def phrase(entropy: bytes) -> list[str]:
  return _WORDLIST.to_mnemonic(entropy).split()


def entropy_from_phrase(words: Iterable[str]) -> bytes:
  cleaned = [w.strip().lower() for w in words if w.strip()]
  if len(cleaned) != WORDS:
    raise ValueError(f"A recovery phrase is {WORDS} words; got {len(cleaned)}")
  unknown = [w for w in cleaned if w not in _WORDLIST.wordlist]
  if unknown:
    raise ValueError(f"Not a recovery phrase word: {', '.join(unknown)}")
  joined = " ".join(cleaned)
  if not _WORDLIST.check(joined):
    raise ValueError(
      "Those words fail the phrase's checksum; one of them is wrong or out of order"
    )
  return bytes(_WORDLIST.to_entropy(joined))


def derive(entropy: bytes, label: str) -> bytes:
  return HKDF(
    algorithm=hashes.SHA256(), length=32, salt=None, info=label.encode()
  ).derive(entropy)


def backup_password_from(entropy: bytes) -> str:
  return derive(entropy, BACKUP_LABEL).hex()


def master_key_from(entropy: bytes) -> str:
  return derive(entropy, SECRETS_LABEL).hex()


@dataclass(frozen=True)
class KeyFile:
  master_key: str
  seed: bytes | None


def read_keyfile(path: Path, at: str | None = None) -> KeyFile:
  if not path.is_file():
    return KeyFile("", None)
  table = LogTab(path).load(at=at)
  seed_entry = table.get(SEED_KEY)
  seed = bytes.fromhex(seed_entry.value) if seed_entry else None
  master_key = master_key_from(seed) if seed is not None else ""
  return KeyFile(master_key, seed)


def master_key_at(path: Path, at: str) -> str:
  return read_keyfile(path, at=at).master_key


def write_seed(path: Path, entropy: bytes, *, title: str = "Kelso Master Key") -> None:
  LogTab(path, title=title).write(SEED_KEY, entropy.hex())
  path.chmod(0o600)


def format_phrase(words: list[str]) -> str:
  rows = WORDS // 3
  lines = []
  for r in range(rows):
    cells = [f"{c * rows + r + 1:>2}. {words[c * rows + r]:<10}" for c in range(3)]
    lines.append("    " + "  ".join(cells).rstrip())
  return "\n".join(lines)
