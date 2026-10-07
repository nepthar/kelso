"""The recovery phrase: twelve words every key kelso uses is derived from.

The phrase encodes 128 random bits (BIP39's English wordlist and checksum). It
is the one thing to keep off the machine: from it, a fresh install derives the
same master key, so a backup of this root can be restored anywhere.

`conf/master.key` keeps the phrase's entropy as `seed`, and the master key is
derived from it. A rekey appends a new `seed`; the file's history is the record
of every key this root has had, so something written under an older one -- a
snapshot's secrets -- is read with the seed that was current when it was
written (`master_key_at`).
"""

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

_WORDLIST = Mnemonic("english")


def new_entropy() -> bytes:
  return secrets.token_bytes(ENTROPY_BYTES)


def phrase(entropy: bytes) -> list[str]:
  return _WORDLIST.to_mnemonic(entropy).split()


def entropy_from_phrase(words: Iterable[str]) -> bytes:
  """The entropy twelve words encode. Raises ValueError on a word not in the
  list, the wrong count, or a failed checksum (a word mistyped as another)."""
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
  """A 32-byte key for one purpose, named by `label`."""
  return HKDF(
    algorithm=hashes.SHA256(), length=32, salt=None, info=label.encode()
  ).derive(entropy)


def master_key_from(entropy: bytes) -> str:
  """The master key string the crypto engine takes, for this entropy."""
  return derive(entropy, SECRETS_LABEL).hex()


@dataclass(frozen=True)
class KeyFile:
  """What `conf/master.key` holds."""

  master_key: str
  seed: bytes | None


def read_keyfile(path: Path, at: str | None = None) -> KeyFile:
  """The key file now, or as it stood at timestamp `at`."""
  if not path.is_file():
    return KeyFile("", None)
  table = LogTab(path).load(at=at)
  seed_entry = table.get(SEED_KEY)
  seed = bytes.fromhex(seed_entry.value) if seed_entry else None
  master_key = master_key_from(seed) if seed is not None else ""
  return KeyFile(master_key, seed)


def master_key_at(path: Path, at: str) -> str:
  """The master key that was current at timestamp `at`; empty if none was."""
  return read_keyfile(path, at=at).master_key


def write_seed(path: Path, entropy: bytes, *, title: str = "Kelso Master Key") -> None:
  """Make `entropy` the master key's seed. Older seeds stay in the history."""
  LogTab(path, title=title).write(SEED_KEY, entropy.hex())
  path.chmod(0o600)


def format_phrase(words: list[str]) -> str:
  """Twelve numbered words in three columns, read down."""
  rows = WORDS // 3
  lines = []
  for r in range(rows):
    cells = [f"{c * rows + r + 1:>2}. {words[c * rows + r]:<10}" for c in range(3)]
    lines.append("    " + "  ".join(cells).rstrip())
  return "\n".join(lines)
