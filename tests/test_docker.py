import os
import shutil
import socket
import subprocess
import sys

import pytest

from kelso.lib.logtab import LogTab


def _free_port() -> int:
  with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    return sock.getsockname()[1]


# --- the suite must never touch the real docker daemon ---------------------
#
# These guard the guard: `block_real_docker` in conftest.py shadows the real
# binary for every unmarked test. Without it, anything calling docker reads --
# or disturbs -- whatever the developer has running, which silently couples
# results to the machine (a real bug: test_curated_examples_materialize once
# failed because an example bundle was genuinely running).


def test_docker_on_path_is_the_guard():
  found = shutil.which("docker")
  assert found is not None and "docker-guard" in found, found


def test_invoking_docker_fails_loudly(expect_docker_calls):
  result = subprocess.run(["docker", "ps", "-a"], capture_output=True, text=True)
  assert result.returncode != 0
  assert "must not shell out to the real docker" in result.stderr


def test_kelso_env_prefers_the_working_fake(kelso_env):
  assert shutil.which("docker") == str(kelso_env.root / "bin" / "docker")


def test_a_refused_call_is_recorded_even_though_kelso_swallows_it(
  expect_docker_calls,
):
  """The reason the guard logs instead of only exiting non-zero.

  `load_kelso_run_unit_status` passes check=False, so a refusal comes back as
  an empty mapping -- identical to a successful call on a machine with nothing
  running. Asserting on the return value therefore proves nothing. The log is
  what distinguishes "we were blocked" from "there was nothing to see".
  """
  from kelso.lib.docker import load_kelso_run_unit_status

  assert load_kelso_run_unit_status() == {}  # the swallowed failure
  assert "docker ps -a" in expect_docker_calls.read_text()  # the real evidence


def test_streamed_output_goes_to_the_sink_not_stdout(kelso_env, capsys):
  """`sink_output` is how a job captures compose output no terminal will see."""
  import io

  from kelso.lib.docker import docker_run_command, sink_output

  kelso_env.set_containers(
    [{"app_id": "demo", "run_unit": "main", "id": "abc", "state": "running"}]
  )
  sink = io.StringIO()
  with sink_output(sink):
    # `ps` is the one thing the fake docker prints to stdout; json_output=False
    # so it takes the streamed path the sink intercepts.
    docker_run_command(["ps", "-a"], json_output=False, check=False)

  assert "abc" in sink.getvalue()
  # Nothing leaked to the terminal the operator does not have.
  assert capsys.readouterr().out == ""


def test_a_streamed_failure_hands_the_error_a_tail(kelso_env, monkeypatch):
  """With a sink set, `see the docker output above` points at nothing, so the
  error carries the captured tail instead.

  The one test in the suite where docker is a real child process: everywhere
  else `use_fake_docker` answers in-process. This keeps kelso's own streaming
  -- the pipe read in chunks into the sink, and the exit code after -- honest
  against a process that actually writes and exits.
  """
  import io

  import kelso.lib.docker
  from kelso.lib.docker import DockerError, docker_run_command, sink_output

  monkeypatch.setattr(kelso.lib.docker, "subprocess", subprocess)
  bin_dir = kelso_env.root / "bin"
  (bin_dir / "docker").write_text(
    "#!/bin/sh\necho 'starting up'\necho 'boom: something broke' >&2\nexit 1\n"
  )
  (bin_dir / "docker").chmod(0o755)

  sink = io.StringIO()
  with sink_output(sink), pytest.raises(DockerError) as excinfo:
    docker_run_command(["compose", "up"], json_output=False, check=True)
  # Both streams, merged, reached the sink as they were written...
  assert sink.getvalue() == "starting up\nboom: something broke\n"
  # ...and the failure carries them, since no terminal saw them.
  assert "boom: something broke" in str(excinfo.value)
  assert excinfo.value.returncode == 1


@pytest.mark.docker
def test_real_docker_up_and_down(tmp_path):
  if shutil.which("docker") is None:
    pytest.skip("docker is not installed")
  if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
    pytest.skip("docker daemon is not available")

  root = tmp_path / "kelso"
  app = root / "repos" / "local" / "docker-smoke.klso"
  app.mkdir(parents=True)
  (root / "conf" / "apps").mkdir(parents=True)
  (root / "volumes").mkdir()
  port = _free_port()
  (app / "manifest.toml").write_text(
    f"""\
[app]
version = "0.1.0"

[run.main]
image = "nginx:alpine"

[run.main.routes]
main = {{ port = "{port}:80" }}
"""
  )
  LogTab(root / "conf" / "master.key").write("master_key", "0" * 64)
  config = root / "config.toml"
  config.write_text(
    """\
repos_root = "repos"
port_base = 41000
"""
  )
  env = {**os.environ, "KELSO_CONFIG": str(config)}

  def kelso(*args):
    return subprocess.run(
      [sys.executable, "-m", "kelso.cli", *args],
      env=env,
      capture_output=True,
      text=True,
    )

  try:
    started = kelso("start", "docker-smoke")
    assert started.returncode == 0, started.stderr

    containers = subprocess.run(
      [
        "docker",
        "ps",
        "-q",
        "--filter",
        "label=kelso.app_id=docker-smoke",
      ],
      capture_output=True,
      text=True,
      check=True,
    ).stdout.splitlines()
    assert len(containers) == 1
    published = subprocess.run(
      ["docker", "port", containers[0], "80/tcp"],
      capture_output=True,
      text=True,
      check=True,
    ).stdout
    assert str(port) in published
  finally:
    kelso("stop", "docker-smoke")
