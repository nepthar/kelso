"""Running kelso on another host over ssh, for `deploy` and `config --host`."""

import json
import subprocess

from kelso.lib.configflow import ConfigRequest, ConfigResponse, request_from_view

SSH = ["ssh", "-o", "BatchMode=yes"]


def remote(host: str, command: str) -> list[str]:
  # sshd's shell runs no profile, so the PATH lacks ~/.local/bin, where kelso's
  # own install (`uv tool install`) puts it.
  return [*SSH, host, f"PATH=~/.local/bin:$PATH; {command}"]


def remote_request(host: str, app: str) -> ConfigRequest:
  """What `app` on `host` can be asked."""
  asked = subprocess.run(
    remote(host, f"kelso config {app} --json"), capture_output=True, text=True
  )
  if asked.returncode:
    raise ValueError(f"Could not ask {host} what {app} needs: {asked.stderr.strip()}")
  return request_from_view(json.loads(asked.stdout))


def send_response(host: str, app: str, response: ConfigResponse) -> None:
  """Apply `response` on `host`, which prints what it set and what is still needed."""
  done = subprocess.run(
    remote(host, f"kelso config {app} --json-response"),
    input=json.dumps({"values": response.values}),
    text=True,
  )
  if done.returncode:
    raise SystemExit(done.returncode)
