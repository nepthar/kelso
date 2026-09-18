"""`kelso service install` and the unit `kelso init` writes, against a fake systemd."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from kelso.lib import service

FAKE_SYSTEMD = """#!/bin/sh
echo "$(basename "$0") $*" >> "$FAKE_SYSTEMD_LOG"
if [ "$(basename "$0") $*" = "$FAKE_SYSTEMD_FAIL" ]; then
  echo "Failed to connect to bus" >&2
  exit 1
fi
"""


@pytest.fixture
def fake_systemd(tmp_path, monkeypatch) -> Path:
  """systemctl and loginctl that log their arguments; returns the log."""
  bin_dir = tmp_path / "systemd-bin"
  bin_dir.mkdir()
  for name in ("systemctl", "loginctl"):
    script = bin_dir / name
    script.write_text(FAKE_SYSTEMD)
    script.chmod(0o755)
  log = tmp_path / "systemd.log"
  monkeypatch.setenv("FAKE_SYSTEMD_LOG", str(log))
  monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
  monkeypatch.setattr("kelso.lib.service.has_systemd", lambda: True)
  return log


def _calls(log: Path) -> list[str]:
  return log.read_text().splitlines() if log.exists() else []


def test_install_writes_the_unit_and_starts_it(kelso_env, fake_systemd):
  result = kelso_env.run("service", "install")
  assert result.returncode == 0, result.stderr

  unit = service.unit_path().read_text()
  assert f'Environment="KELSO_CONFIG={kelso_env.config}"' in unit
  assert "Type=simple" in unit
  assert "Restart=on-failure" in unit
  exec_start = next(line for line in unit.splitlines() if line.startswith("ExecStart="))
  assert exec_start.endswith('/kelsod"')

  assert _calls(fake_systemd) == [
    "systemctl --user daemon-reload",
    "systemctl --user enable kelsod.service",
    "systemctl --user restart kelsod.service",
    "loginctl enable-linger",
  ]


def test_install_can_be_run_again(kelso_env, fake_systemd):
  assert kelso_env.run("service", "install").returncode == 0
  again = kelso_env.run("service", "install")
  assert again.returncode == 0, again.stderr


def test_install_refuses_a_unit_serving_another_config(kelso_env, fake_systemd):
  unit = service.unit_path()
  unit.parent.mkdir(parents=True)
  unit.write_text(service.unit_text(Path("/bin/kelsod"), Path("/elsewhere.toml")))

  refused = kelso_env.run("service", "install")
  assert refused.returncode == 1
  assert "/elsewhere.toml" in refused.stderr
  assert "/elsewhere.toml" in unit.read_text()
  assert _calls(fake_systemd) == []


def test_install_names_what_is_left_when_systemctl_fails(
  kelso_env, fake_systemd, monkeypatch
):
  monkeypatch.setenv("FAKE_SYSTEMD_FAIL", "systemctl --user enable kelsod.service")

  failed = kelso_env.run("service", "install")
  assert failed.returncode == 1
  assert "Failed to connect to bus" in failed.stderr
  assert "  systemctl --user enable kelsod.service" in failed.stderr
  assert "  loginctl enable-linger" in failed.stderr
  assert "daemon-reload\n" not in failed.stderr
  assert service.unit_path().is_file()


def test_install_refuses_without_systemd(kelso_env):
  refused = kelso_env.run("service", "install")
  assert refused.returncode == 1
  assert "does not run systemd" in refused.stderr
  assert not service.unit_path().exists()


def test_init_installs_the_service(kelso_env, fake_systemd, tmp_path):
  root = tmp_path / "fresh"
  result = kelso_env.run("--root", str(root), "init", "--no-mirror", input="\n")
  assert result.returncode == 0, result.stderr

  assert f"KELSO_CONFIG={root.resolve() / 'config.toml'}" in (
    service.unit_path().read_text()
  )
  assert "systemctl --user restart kelsod.service" in _calls(fake_systemd)


def test_init_without_systemd_says_to_run_kelsod_by_hand(kelso_env, tmp_path):
  root = tmp_path / "fresh"
  result = kelso_env.run("--root", str(root), "init", "--no-mirror", input="\n")
  assert result.returncode == 0, result.stderr
  assert "Run `kelsod` in a terminal" in result.stdout
  assert not service.unit_path().exists()


def test_init_still_succeeds_when_systemd_refuses(
  kelso_env, fake_systemd, monkeypatch, tmp_path
):
  monkeypatch.setenv("FAKE_SYSTEMD_FAIL", "systemctl --user daemon-reload")
  root = tmp_path / "fresh"

  result = kelso_env.run("--root", str(root), "init", "--no-mirror", input="\n")
  assert result.returncode == 0, result.stderr
  assert "finish with:" in result.stderr
  assert (root / "config.toml").is_file()
