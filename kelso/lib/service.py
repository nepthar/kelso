"""kelsod as a systemd user unit: writing the unit, and asking systemd to run it.

systemd only. Anywhere else, kelsod is something you run in a terminal.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

UNIT_NAME = "kelsod.service"
CONFIG_ENV = "KELSO_CONFIG"

ACTIVATE = (
  ("systemctl", "--user", "daemon-reload"),
  ("systemctl", "--user", "enable", UNIT_NAME),
  # restart, not start: rerunning after an upgrade has to pick up the new code.
  ("systemctl", "--user", "restart", UNIT_NAME),
  # Without it the user manager, and kelsod with it, stops at logout.
  ("loginctl", "enable-linger"),
)


def has_systemd() -> bool:
  """Whether this machine was booted with systemd -- the test `sd_booted` makes."""
  return Path("/run/systemd/system").is_dir() and shutil.which("systemctl") is not None


def unit_path() -> Path:
  base = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
  return Path(base).expanduser() / "systemd" / "user" / UNIT_NAME


def kelsod_path() -> Path:
  """The kelsod installed alongside the kelso that is running."""
  sibling = Path(sys.executable).parent / "kelsod"
  if sibling.is_file():
    return sibling
  found = shutil.which("kelsod")
  if found:
    return Path(found)
  raise RuntimeError(
    f"kelsod is not installed next to kelso ({sibling}) or on PATH; "
    f"reinstall kelso with `uv tool install`"
  )


def unit_text(kelsod: Path, config_path: Path) -> str:
  return (
    "[Unit]\n"
    "Description=Kelso admin daemon\n"
    "\n"
    "[Service]\n"
    "Type=simple\n"
    f'ExecStart="{kelsod}"\n'
    f'Environment="{CONFIG_ENV}={config_path}"\n'
    "Restart=on-failure\n"
    "\n"
    "[Install]\n"
    "WantedBy=default.target\n"
  )


def _unit_config(text: str) -> str | None:
  """The config path an existing unit runs kelsod against."""
  prefix = f'Environment="{CONFIG_ENV}='
  for line in text.splitlines():
    if line.startswith(prefix):
      return line.removeprefix(prefix).removesuffix('"')
  return None


def write_unit(config_path: Path) -> Path:
  """Write (or rewrite) the unit for `config_path`.

  Raises RuntimeError if the unit already serves a different kelso config.
  """
  path = unit_path()
  if path.is_file():
    serves = _unit_config(path.read_text())
    if serves is not None and serves != str(config_path):
      raise RuntimeError(
        f"{path} already runs kelsod for {serves}. Remove it first if this "
        f"machine's kelsod should serve {config_path} instead."
      )
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(unit_text(kelsod_path(), config_path))
  return path


def activate() -> None:
  """Start kelsod now and at every boot.

  Raises RuntimeError naming the command that failed and every one left to run.
  """
  for i, command in enumerate(ACTIVATE):
    try:
      result = subprocess.run(command, capture_output=True, text=True)
      error = result.stderr.strip() if result.returncode else None
    except OSError as e:
      error = str(e)
    if error is not None:
      left = "\n".join(f"  {' '.join(c)}" for c in ACTIVATE[i:])
      raise RuntimeError(
        f"`{' '.join(command)}` failed: {error}\n"
        f"The unit is written; finish with:\n{left}"
      )
