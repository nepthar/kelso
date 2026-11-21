import sys
from getpass import getpass


def parse_kv(raw: str, flag: str) -> tuple[str, str]:
  if "=" not in raw:
    raise ValueError(f"{flag} expects KEY=VALUE, got {raw!r}")
  key, _, value = raw.partition("=")
  if not key or not value:
    raise ValueError(f"{flag} expects KEY=VALUE, got {raw!r}")
  return key, value


def read_secret(name: str) -> str:
  if sys.stdin.isatty():
    return getpass(f"Value for {name} (hidden): ").strip()
  return sys.stdin.read().rstrip("\r\n")
