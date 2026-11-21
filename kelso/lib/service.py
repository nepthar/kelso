import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

UNIT_NAME = "kelsod.service"

UNIT_TEMPLATE = """\
[Unit]
Description=Kelso admin daemon

[Service]
Type=simple
ExecStart="{kelsod}"
Environment="KELSO_ROOT={root}"
Restart=on-failure

[Install]
WantedBy=default.target
"""

ACTIVATE = (
  ("systemctl", "--user", "daemon-reload"),
  ("systemctl", "--user", "enable", UNIT_NAME),
  # restart, not start: rerunning after an upgrade has to pick up the new code.
  ("systemctl", "--user", "restart", UNIT_NAME),
  # Without it the user manager, and kelsod with it, stops at logout.
  ("loginctl", "enable-linger"),
)


def has_systemd() -> bool:
  return Path("/run/systemd/system").is_dir() and shutil.which("systemctl") is not None


def unit_path() -> Path:
  base = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
  return Path(base).expanduser() / "systemd" / "user" / UNIT_NAME


def kelsod_path() -> Path:
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


def _unit_root(text: str) -> str | None:
  prefix = 'Environment="KELSO_ROOT='
  for line in text.splitlines():
    if line.startswith(prefix):
      return line.removeprefix(prefix).removesuffix('"')
  return None


def write_unit(root: Path) -> Path:
  path = unit_path()
  if path.is_file():
    serves = _unit_root(path.read_text())
    if serves is not None and serves != str(root):
      raise RuntimeError(
        f"{path} already runs kelsod for {serves}. Remove it first if this "
        f"machine's kelsod should serve {root} instead."
      )
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(UNIT_TEMPLATE.format(kelsod=kelsod_path(), root=root))
  return path


def stays_up(settle: float = 2.0) -> bool:
  time.sleep(settle)
  result = subprocess.run(
    ("systemctl", "--user", "is-active", UNIT_NAME), capture_output=True, text=True
  )
  return result.stdout.strip() == "active"


def activate() -> None:
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
