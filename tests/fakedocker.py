"""The docker the test suite talks to, and the guard that stands in for real one.

Two ways in, one behaviour:

- In-process. `FakeSubprocess` replaces the `subprocess` module that
  `kelso.lib.docker` and `kelso.lib.lifecycle.rootfs` use, so a docker call is
  a function call. This is what almost every test gets: starting a Python
  interpreter per docker call was most of the suite's run time, and all it
  verified was that `Popen` works. Everything kelso does around the process --
  arguments, streaming into a sink, error tails, JSON parsing -- still runs.
- As an executable. `kelso_env` also installs `bin/docker`, which calls `main`
  here, for the few tests that start kelso as a real child process (the lock
  tests), where no monkeypatch can reach.

Either way a call is appended to the log as JSON and containers persist in the
state file, so a test reads both the same way regardless of which ran.
"""

import io
import json
import os
import subprocess
import sys
from pathlib import Path

DOCKER = "docker"


class FakeDocker:
  """docker's behaviour, as far as kelso relies on it. `call` is one invocation."""

  def __init__(self, state: Path, log: Path):
    self.state = state
    self.log = log

  def _containers(self) -> list[dict]:
    return json.loads(self.state.read_text()) if self.state.exists() else []

  def call(self, args: list[str], cwd: Path, stdout=None, stderr=None):
    """Run `docker *args` in `cwd`. Returns (returncode, stdout, stderr) text,
    except for `docker run`, which returns the real `sh` run's CompletedProcess."""
    # Where the `app` volume links pointed at the moment of the call. `kelso dev`
    # swaps them for the duration of one docker command and puts them back, so
    # this is the only way a test can observe the swap from outside.
    app_volumes = cwd / "volumes" / "app"
    app_links = (
      {p.name: os.readlink(p) for p in sorted(app_volumes.iterdir())}
      if app_volumes.is_dir()
      else {}
    )
    with self.log.open("a") as f:
      # Resolved, as a child's `os.getcwd()` would report it.
      f.write(
        json.dumps({"args": args, "cwd": os.path.realpath(cwd), "app_links": app_links})
        + "\n"
      )

    containers = self._containers()
    out: list[str] = []
    if args[:3] == ["compose", "up", "-d"]:
      app_id = cwd.name
      containers = [c for c in containers if c["app_id"] != app_id]
      containers.append(
        {
          "app_id": app_id,
          "run_unit": "main",
          "id": "fake-container",
          "state": "running",
        }
      )
      self.state.write_text(json.dumps(containers))
    elif args[:2] == ["compose", "down"]:
      app_id = cwd.name
      containers = [c for c in containers if c["app_id"] != app_id]
      if containers:
        self.state.write_text(json.dumps(containers))
      else:
        self.state.unlink(missing_ok=True)
    elif args[:2] == ["compose", "logs"]:
      for container in containers:
        if container["app_id"] == cwd.name:
          out.append(f"{container['run_unit']}-1  | hello from {container['run_unit']}")
    elif args[0] == "run":
      # `docker run --rm -v HOST:HOST ... IMAGE sh -c SCRIPT`, kelso's stand-in
      # for sudo (kelso/lib/lifecycle/rootfs.py). Every bind maps a host path
      # to itself, so running the script right here is faithful to what the
      # container would do -- the only thing the real one adds is root, which a
      # test has no way to want. `sh` is cheap to start, and the script's work
      # (tar, cp, rm) is what the test is about.
      if args[-3:-1] != ["sh", "-c"]:
        return 1, "", "fake docker: unexpected `docker run` shape: " + " ".join(args)
      return subprocess.run(["sh", "-c", args[-1]], stdout=stdout, stderr=stderr)
    elif args[:2] == ["ps", "-a"]:
      for c in containers:
        out.append(
          json.dumps(
            {
              "ID": c["id"],
              "Names": f"{c['app_id']}-{c['run_unit']}-1",
              "State": c["state"],
              "Labels": f"kelso.app_id={c['app_id']},kelso.run_unit={c['run_unit']}",
            }
          )
        )
    elif args[0] == "stats":
      for c in containers:
        if c.get("state") != "running":
          continue
        out.append(
          json.dumps(
            {
              "ID": c["id"],
              "Name": f"{c['app_id']}-{c['run_unit']}-1",
              "CPUPerc": c.get("cpu_perc", "0.00%"),
              "MemPerc": c.get("mem_perc", "0.00%"),
            }
          )
        )
    return 0, "".join(line + "\n" for line in out), ""


class GuardDocker:
  """Refuses every call, and records it first.

  Exiting non-zero is not enough on its own: kelso calls docker with
  check=False in places (see `load_kelso_run_unit_status`), which turns a
  refusal into an empty result that looks exactly like "no containers are
  running". The log is what makes an accidental call visible no matter how the
  caller handles the failure.
  """

  def __init__(self, log: Path):
    self.log = log

  def call(self, args: list[str], cwd: Path, stdout=None, stderr=None):
    invocation = "docker " + " ".join(args)
    with self.log.open("a") as f:
      f.write(invocation + "\n")
    return (
      1,
      "",
      f"tests must not shell out to the real docker daemon, but one invoked: {invocation}\n",
    )


class _Finished:
  """What `Popen` hands back, for a process that has already run: its merged
  output waiting in the pipe."""

  def __init__(self, args, returncode: int, output: str):
    self.args = args
    self.returncode: int | None = None
    self._code = returncode
    self.stdout = io.StringIO(output)

  def wait(self, timeout=None) -> int:
    self.returncode = self._code
    return self._code

  def poll(self) -> int:
    return self.wait()


class FakeSubprocess:
  """Stands in for the `subprocess` module where kelso starts docker.

  Handles the call shapes `kelso.lib.docker` and `rootfs` use. Anything that
  is not docker goes to the real module untouched.
  """

  PIPE = subprocess.PIPE
  STDOUT = subprocess.STDOUT
  DEVNULL = subprocess.DEVNULL
  CompletedProcess = subprocess.CompletedProcess

  def __init__(self, docker):
    self.docker = docker

  @staticmethod
  def _is_docker(argv) -> bool:
    return bool(argv) and argv[0] == DOCKER

  def run(
    self,
    argv,
    *,
    cwd=None,
    capture_output=False,
    text=False,
    env=None,
    stdout=None,
    stderr=None,
    **kwargs,
  ):
    if not self._is_docker(argv):
      return subprocess.run(
        argv,
        cwd=cwd,
        capture_output=capture_output,
        text=text,
        env=env,
        stdout=stdout,
        stderr=stderr,
        **kwargs,
      )
    where = Path(cwd) if cwd else Path.cwd()
    result = self.docker.call(list(argv[1:]), where, stdout=stdout, stderr=stderr)
    if isinstance(result, subprocess.CompletedProcess):
      return result
    code, out, err = result
    if capture_output or stdout == subprocess.PIPE or stderr == subprocess.PIPE:
      if not text:
        out, err = out.encode(), err.encode()
      return subprocess.CompletedProcess(argv, code, out, err)
    # Not captured: a real child would have written to kelso's own stdout and
    # stderr file descriptors, not to whatever `sys.stdout` is rebound to.
    if out:
      os.write(1, out.encode())
    if err:
      os.write(2, err.encode())
    return subprocess.CompletedProcess(argv, code)

  def Popen(
    self, argv, *, cwd=None, stdout=None, stderr=None, text=False, env=None, **kwargs
  ):
    if not self._is_docker(argv):
      return subprocess.Popen(
        argv, cwd=cwd, stdout=stdout, stderr=stderr, text=text, env=env, **kwargs
      )
    # kelso's only Popen shape: stdout piped, stderr merged into it, text.
    assert stdout == subprocess.PIPE and stderr == subprocess.STDOUT and text, (
      "fake docker: unexpected Popen shape"
    )
    code, out, err = self.docker.call(list(argv[1:]), Path(cwd) if cwd else Path.cwd())
    return _Finished(argv, code, out + err)


def main() -> None:
  """`bin/docker` for tests that run kelso as a child process."""
  docker = FakeDocker(
    Path(os.environ["FAKE_DOCKER_STATE"]), Path(os.environ["FAKE_DOCKER_LOG"])
  )
  result = docker.call(sys.argv[1:], Path.cwd())
  if isinstance(result, subprocess.CompletedProcess):
    sys.exit(result.returncode)
  code, out, err = result
  sys.stdout.write(out)
  sys.stderr.write(err)
  sys.exit(code)
