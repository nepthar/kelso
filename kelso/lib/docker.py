import json
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from logging import getLogger
from pathlib import Path
from typing import Any, TextIO

from kelso.lib.spec import KELSO_APP_ID_LABEL, KELSO_RUN_UNIT_LABEL

logger = getLogger("kelso.docker")

DOCKER = "docker"

# Where streamed output goes when the caller has no terminal. Unset, the child
# inherits kelso's stdio.
_output_sink: ContextVar[TextIO | None] = ContextVar("docker_output_sink", default=None)

# How much captured output a DockerError carries; the full text is in the sink.
_ERROR_TAIL = 1500


@contextmanager
def sink_output(sink: TextIO) -> Iterator[None]:
  """Capture streamed docker output into `sink` for the duration."""
  token = _output_sink.set(sink)
  try:
    yield
  finally:
    _output_sink.reset(token)


@dataclass(frozen=True)
class KelsoRunUnitStatus:
  app_id: str
  run_unit: str
  container_id: str
  name: str
  state: str
  # docker's own summary, e.g. "Up 2 minutes (healthy)" or "Exited (0) 3s ago".
  status: str = ""

  @property
  def finished(self) -> bool:
    """Exited cleanly: a one-shot that did what it was started for."""
    return self.state.lower() == "exited" and self.status.startswith("Exited (0)")

  @property
  def health(self) -> str:
    """ "healthy", "unhealthy" or "starting" from a healthcheck; "" with none."""
    if "(health: starting)" in self.status:
      return "starting"
    if "(unhealthy)" in self.status:
      return "unhealthy"
    if "(healthy)" in self.status:
      return "healthy"
    return ""


@dataclass(frozen=True)
class DockerReturn:
  """Result of a ``docker`` invocation."""

  returncode: int
  data: list[dict[str, Any]]


def _parse_docker_label_string(raw: str) -> dict[str, str]:
  labels: dict[str, str] = {}
  for part in raw.split(","):
    key, sep, value = part.partition("=")
    if sep:
      labels[key] = value
  return labels


def load_kelso_run_unit_status() -> dict[str, tuple[KelsoRunUnitStatus, ...]]:
  """Return every container that carries a Kelso app ID label."""
  result = docker_run_command(
    [
      "ps",
      "-a",
      "--filter",
      f"label={KELSO_APP_ID_LABEL}",
    ],
    check=False,
  )

  statuses: dict[str, list[KelsoRunUnitStatus]] = {}
  for container in result.data:
    labels = _parse_docker_label_string(container.get("Labels") or "")
    app_id = labels.get(KELSO_APP_ID_LABEL)
    if not app_id:
      continue
    run_unit = labels.get(KELSO_RUN_UNIT_LABEL, "")

    app_units = statuses.setdefault(app_id, [])
    app_units.append(
      KelsoRunUnitStatus(
        app_id=app_id,
        run_unit=run_unit,
        container_id=container.get("ID", ""),
        name=container.get("Names", ""),
        state=container.get("State", ""),
        status=container.get("Status", ""),
      )
    )
  return {app_id: tuple(app_units) for app_id, app_units in statuses.items()}


@dataclass(frozen=True)
class DockerImage:
  id: str
  # Every repo:tag naming it; empty for a dangling image.
  refs: tuple[str, ...]
  size: int


_SIZE_UNITS = {"B": 1, "kB": 10**3, "KB": 10**3, "MB": 10**6, "GB": 10**9, "TB": 10**12}


def _size_bytes(human: str) -> int:
  """docker's decimal `Size` column, e.g. "187MB", as bytes."""
  number = human.rstrip("kKMGTB")
  return int(float(number or 0) * _SIZE_UNITS.get(human[len(number) :], 1))


def list_images() -> list[DockerImage]:
  """Every local image, one entry per image id."""
  refs: dict[str, list[str]] = {}
  sizes: dict[str, int] = {}
  for row in docker_run_command(["image", "ls"]).data:
    image_id = row.get("ID", "")
    repo, tag = row.get("Repository", ""), row.get("Tag", "")
    refs.setdefault(image_id, [])
    if "<none>" not in (repo, tag):
      refs[image_id].append(f"{repo}:{tag}")
    sizes[image_id] = _size_bytes(row.get("Size", ""))
  return [DockerImage(i, tuple(refs[i]), sizes[i]) for i in refs]


def container_images() -> set[str]:
  """The image each container, kelso's or not, was created from, as docker shows it."""
  return {row.get("Image", "") for row in docker_run_command(["ps", "-a"]).data}


def remove_image(image: DockerImage) -> None:
  """Delete an image by every name it has. Raises DockerError if docker refuses."""
  cmd = ["image", "rm", *(image.refs or (image.id,))]
  result = subprocess.run([DOCKER, *cmd], capture_output=True, text=True)
  if result.returncode != 0:
    raise DockerError(cmd, result.returncode, result.stderr)


def pull_image(image: str) -> None:
  """`docker pull`, streaming its progress. Raises DockerError if it fails."""
  docker_run_command(["pull", image], json_output=False)


class DockerError(RuntimeError):
  """A docker invocation exited non-zero."""

  def __init__(self, cmd: list[str], returncode: int, stderr: str = "") -> None:
    self.cmd = cmd
    self.returncode = returncode
    self.stderr = stderr
    # A streamed command already put its own diagnostics on the terminal, so
    # point at those rather than reporting a bare exit code with no detail.
    detail = stderr.strip() or "see the docker output above"
    super().__init__(f"docker {' '.join(cmd)} failed ({returncode}): {detail}")


def _parse_json_output(stdout: str) -> list[dict[str, Any]]:
  """Parse docker JSON output, tolerating both a single array and NDJSON lines."""
  text = stdout.strip()
  if not text:
    return []
  try:
    parsed = json.loads(text)
    return parsed if isinstance(parsed, list) else [parsed]
  except json.JSONDecodeError:
    # Fall back to newline-delimited JSON (one object per line).
    return [json.loads(line) for line in text.splitlines() if line.strip()]


class DockerTimeout(RuntimeError):
  """A docker invocation ran past its timeout and kelso stopped waiting."""


def _timed_out(cmd: list[str], timeout: float | None) -> DockerTimeout:
  # Killing the client does not stop what `compose exec` started in the
  # container; that runs on until it finishes or the container stops.
  return DockerTimeout(
    f"docker {' '.join(cmd)} was still running after {timeout:g} seconds; "
    f"kelso stopped waiting for it"
  )


def docker_run_command(
  cmd: list[str],
  *,
  cwd: Path | None = None,
  json_output: bool = True,
  check: bool = True,
  env: dict[str, str] | None = None,
  timeout: float | None = None,
) -> DockerReturn:
  """Run `docker <cmd>`: captured and parsed with `json_output`, else streamed.

  Raises DockerTimeout once `timeout` seconds pass, after killing the client.
  """
  full = [DOCKER, *cmd]
  if json_output:
    full += ["--format", "json"]

  run_env = {**os.environ, **env} if env else None
  logger.debug("running: %s (cwd=%s)", " ".join(full), cwd)

  sink = _output_sink.get() if not json_output else None
  if sink is not None:
    # Merge stderr so the sink reads the way a terminal would have.
    proc = subprocess.Popen(
      full,
      cwd=cwd,
      stdout=subprocess.PIPE,
      stderr=subprocess.STDOUT,
      text=True,
      env=run_env,
    )
    stdout = proc.stdout
    assert stdout is not None
    timed_out = threading.Event()

    def stop() -> None:
      timed_out.set()
      proc.kill()

    timer = threading.Timer(timeout, stop) if timeout else None
    if timer is not None:
      timer.start()
    chunks: list[str] = []
    try:
      # By line: `read(n)` waits for n characters, holding back everything a
      # slow pull prints until enough has piled up.
      for line in iter(stdout.readline, ""):
        sink.write(line)
        sink.flush()
        chunks.append(line)
    finally:
      if timer is not None:
        timer.cancel()
      stdout.close()
      proc.wait()
    if timed_out.is_set():
      raise _timed_out(cmd, timeout)
    text = "".join(chunks)
    if check and proc.returncode != 0:
      raise DockerError(cmd, proc.returncode, text[-_ERROR_TAIL:])
    return DockerReturn(returncode=proc.returncode or 0, data=[])

  if not json_output:
    # The child is about to write to the same stdout kelso has been buffering
    # into. Without this, a piped `kelso dev` prints its receipt *after* the
    # compose output it was meant to introduce.
    sys.stdout.flush()
  try:
    result = subprocess.run(
      full,
      cwd=cwd,
      capture_output=json_output,
      text=True,
      env=run_env,
      timeout=timeout,
    )
  except subprocess.TimeoutExpired:
    raise _timed_out(cmd, timeout) from None

  if check and result.returncode != 0:
    raise DockerError(cmd, result.returncode, result.stderr if json_output else "")

  data = _parse_json_output(result.stdout) if json_output else []
  return DockerReturn(returncode=result.returncode, data=data)
