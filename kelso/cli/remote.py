"""Running kelso on another host over ssh, for `deploy` and `config --host`."""

import json
import subprocess
import sys

from kelso.lib.configflow import ConfigRequest, request_from_view

SSH = ["ssh", "-o", "BatchMode=yes"]


def remote(host: str, command: str, *, tty: bool = False) -> list[str]:
  # sshd's shell runs no profile, so the PATH lacks ~/.local/bin, where kelso's
  # own install (`uv tool install`) puts it.
  return [*SSH, *(["-t"] if tty else []), host, f"PATH=~/.local/bin:$PATH; {command}"]


def remote_request(host: str, app: str) -> ConfigRequest:
  """What `app` on `host` can be asked."""
  asked = subprocess.run(
    remote(host, f"kelso config {app} --json"), capture_output=True, text=True
  )
  if asked.returncode:
    raise ValueError(f"Could not ask {host} what {app} needs: {asked.stderr.strip()}")
  return request_from_view(json.loads(asked.stdout))


def run_remote(host: str, command: str) -> None:
  """Run `command` on `host` in this terminal, exiting with its code if it fails."""
  done = subprocess.run(remote(host, command, tty=sys.stdin.isatty()))
  if done.returncode:
    raise SystemExit(done.returncode)
