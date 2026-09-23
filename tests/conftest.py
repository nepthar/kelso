import io
import json
import os
import shutil
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

import kelso.lib.docker
import kelso.lib.lifecycle.rootfs
from kelso.cli.main import run as cli_run
from kelso.lib.apps import AppID
from kelso.lib.bundle import scan_bundles
from kelso.lib.config import VAR_DIRS
from kelso.lib.logtab import LogTab
from kelso.lib.spec import AppSpec
from kelso.lib.store import JsonLogtabStore

from .fakedocker import FakeDocker, FakeSubprocess, GuardDocker

# The contention tests wait this out in full; 5s each is more than the rest of
# the suite costs. Anything that asserts on the wait should read it from here.
LOCK_TIMEOUT = 0.25


def spec_of(tmp_path: Path, manifest: str, app_id: str = "demo") -> AppSpec:
  """Build an `AppSpec` from manifest TOML, via the real parse-and-validate path."""
  bundle = tmp_path / f"{app_id}.klso"
  bundle.mkdir()
  (bundle / "manifest.toml").write_text(manifest)
  return AppSpec.from_file(bundle / "manifest.toml", AppID(app_id))


# `pytester` runs a throwaway pytest inside a test, which is how
# tests/test_docker.py proves the docker guard actually fails a stray call.
pytest_plugins = ["pytester"]

FIXTURES = Path(__file__).parent / "fixtures" / "apps"

CONFIG = """\
repos_root = "repos"
port_base = 41000
default_route_provider = "web"

[route_provider.web]
kind = "noop"
domain = "kelso.localhost"

[host_volume.media]
path = "external-data"

[host_volume.other]
path = "other-data"
"""

# `bin/docker` for tests that start kelso as a real child process, where the
# in-process fake cannot reach. Same behaviour: it is tests/fakedocker.py.
FAKE_DOCKER = f"""#!{sys.executable}
import sys
sys.path.insert(0, {str(Path(__file__).parent)!r})
from fakedocker import main
main()
"""


@dataclass(frozen=True)
class Result:
  """What a kelso command produced. Shaped like `CompletedProcess`."""

  returncode: int
  stdout: str
  stderr: str


@dataclass(frozen=True)
class KelsoEnv:
  root: Path
  config: Path
  docker_state: Path
  docker_log: Path

  @property
  def local_repo(self) -> Path:
    return self.root / "repos" / "local"

  @property
  def run_root(self) -> Path:
    return self.root / "var" / "run"

  @property
  def conf_root(self) -> Path:
    return self.root / "conf"

  @property
  def master_keyfile(self) -> Path:
    return self.conf_root / "master.key"

  def app_logtab(self, app_id: str) -> Path:
    return self.conf_root / "apps" / f"{app_id}.logtab"

  @property
  def volumes_root(self) -> Path:
    return self.root / "volumes"

  @property
  def db_path(self) -> Path:
    return self.conf_root / "kelsodb.logtab"

  @property
  def kelso_lockfile_path(self) -> Path:
    return self.root / "var" / "lock" / "kelso.lock"

  def app_lockfile_path(self, app_id: str) -> Path:
    return self.root / "var" / "lock" / f"{app_id}.lock"

  def read_db(self) -> dict[str, Any]:
    """Reconstruct the kelso DB as a nested dict from its flat logtab keys.

    The store persists flat ``section/.../key -> value`` entries (see
    :class:`kelso.lib.store.JsonConfigStore`). This rebuilds the nested shape
    tests assert against, e.g. ``db["routes"][app_id][route]`` and
    ``db["system"]["secrets"][name]``.
    """
    db: dict[str, Any] = {}
    for key, value in JsonLogtabStore(self.db_path).scan().items():
      parts = key.split("/")
      if parts[0] == "apps":
        app_id, section, rest = parts[1], parts[2], "/".join(parts[3:])
        app = db.setdefault("apps", {}).setdefault(app_id, {})
        # metadata is flattened onto the app; other sections (config, binds)
        # keep their sub-mapping.
        if section == "metadata":
          app[rest] = value
        else:
          app.setdefault(section, {})[rest] = value
      else:
        # routes/<app>/<name>, system/secrets/<name>, …
        section, rest = parts[1], "/".join(parts[2:])
        db.setdefault(parts[0], {}).setdefault(section, {})[rest] = value
    return db

  def seed_db(self, entries: dict[str, Any]) -> None:
    """Write raw flat ``key -> value`` entries directly into the DB logtab."""
    store = JsonLogtabStore(self.db_path)
    for key, value in entries.items():
      store.write(key, value)

  def run(self, *args: str, input: str | None = None) -> Result:
    """Run a kelso command in this process.

    Spawning an interpreter per command cost ~0.12s and bought nothing: the
    environment is already isolated by `kelso_env`, and `cli_main.run` is the
    same entry point `main` uses. Both streams are captured, which includes
    `logging` output -- `run` rebinds the log handler to the current stderr.

    Use `run_subprocess` when a test needs a genuinely separate process.
    """
    out, err = io.StringIO(), io.StringIO()
    stdin = io.StringIO(input or "")
    original_stdin = sys.stdin
    sys.stdin = stdin
    try:
      with redirect_stdout(out), redirect_stderr(err):
        code = cli_run(list(args))
    finally:
      sys.stdin = original_stdin
    return Result(code, out.getvalue(), err.getvalue())

  def run_subprocess(
    self,
    *args: str,
    input: str | None = None,
    timeout: float | None = None,
  ) -> subprocess.CompletedProcess[str]:
    """Run a kelso command as a real child process.

    Only for tests about what happens *between* processes -- lock contention.
    `timeout` raises `subprocess.TimeoutExpired` (killing the child) rather
    than hanging, which is how a test asserts that a command blocked.
    """
    return subprocess.run(
      [sys.executable, "-m", "kelso.cli", *args],
      cwd=self.root,
      env={**os.environ},
      capture_output=True,
      text=True,
      input=input,
      timeout=timeout,
    )

  def set_containers(self, containers: list[dict[str, str]]) -> None:
    self.docker_state.write_text(json.dumps(containers))


# The executable twin of `GuardDocker`, first on PATH, for anything that runs
# `docker` without going through the modules `use_fake_docker` patches.
DOCKER_GUARD = """#!/usr/bin/env python3
import os
import sys

invocation = "docker " + " ".join(sys.argv[1:])
log = os.environ.get("DOCKER_GUARD_LOG")
if log:
    with open(log, "a") as f:
        f.write(invocation + "\\n")

sys.exit(
    "tests must not shell out to the real docker daemon, but one invoked: "
    + invocation
)
"""


def use_fake_docker(monkeypatch: pytest.MonkeyPatch, docker) -> None:
  """Route kelso's docker calls to `docker` in-process instead of a child.

  These two modules are the only places kelso starts docker, and a function
  call costs nothing where an interpreter per call cost most of the suite's
  time. What is skipped is the process itself; the one test that keeps a real
  one is `test_a_streamed_failure_hands_the_error_a_tail` in test_docker.py.
  """
  fake = FakeSubprocess(docker)
  monkeypatch.setattr(kelso.lib.docker, "subprocess", fake)
  monkeypatch.setattr(kelso.lib.lifecycle.rootfs, "subprocess", fake)


@pytest.fixture(autouse=True)
def block_real_docker(
  request: pytest.FixtureRequest,
  tmp_path_factory: pytest.TempPathFactory,
  monkeypatch: pytest.MonkeyPatch,
):
  """Shadow the real `docker` binary for every test that has not opted in.

  Autouse so this holds even for tests that never touch `kelso_env`. Two
  things happen: the call is refused, so a developer's own containers are never
  read or disturbed, and it is recorded, so the test fails even when kelso
  swallows the error. Tests marked `docker` want the real thing and are left
  alone.

  Yields the invocation log. A test that means to trip the guard should depend
  on `expect_docker_calls` instead of using this directly.
  """
  if "docker" in request.keywords:
    yield None
    return

  guard_dir = tmp_path_factory.mktemp("docker-guard")
  guard = guard_dir / "docker"
  guard.write_text(DOCKER_GUARD)
  guard.chmod(0o755)
  log = guard_dir / "invocations.log"

  monkeypatch.setenv("DOCKER_GUARD_LOG", str(log))
  monkeypatch.setenv("PATH", f"{guard_dir}{os.pathsep}{os.environ['PATH']}")
  use_fake_docker(monkeypatch, GuardDocker(log))

  yield log

  if log.exists():
    pytest.fail(
      "this test reached for the real docker binary:\n  "
      + "  ".join(log.read_text().splitlines(keepends=True))
      + "\nUse the kelso_env fixture, whose fake docker is safe to call."
    )


@pytest.fixture(autouse=True)
def block_real_systemd(tmp_path_factory: pytest.TempPathFactory, monkeypatch):
  """No test writes a real user unit or asks the real systemd to run one."""
  monkeypatch.setattr("kelso.lib.service.has_systemd", lambda: False)
  monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path_factory.mktemp("xdg-config")))


@pytest.fixture
def expect_docker_calls(block_real_docker: Path):
  """For tests that deliberately trip the guard, so it does not fail them."""
  yield block_real_docker
  block_real_docker.unlink(missing_ok=True)


@pytest.fixture
def kelso_env(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
  block_real_docker: Path | None,
) -> KelsoEnv:
  root = tmp_path / "kelso"
  apps = root / "repos" / "local"
  apps.mkdir(parents=True)
  (root / "conf" / "apps").mkdir(parents=True)
  (root / "volumes").mkdir()
  for name in VAR_DIRS:
    (root / "var" / name).mkdir(parents=True)

  for _, rel_path in scan_bundles(FIXTURES):
    source = FIXTURES / rel_path
    if source.is_dir():
      shutil.copytree(source, apps / source.name)
    else:
      shutil.copy2(source, apps / source.name)

  LogTab(root / "conf" / "master.key").write("master_key", "0" * 64)
  config = root / "config.toml"
  config.write_text(CONFIG)

  bin_dir = root / "bin"
  bin_dir.mkdir()
  docker = bin_dir / "docker"
  docker.write_text(FAKE_DOCKER)
  docker.chmod(0o755)

  env = KelsoEnv(
    root=root,
    config=config,
    docker_state=root / "docker-state",
    docker_log=root / "docker.log",
  )

  # Both replace the `block_real_docker` guard: in-process for kelso run in
  # this process, and first on PATH for kelso run as a child.
  use_fake_docker(monkeypatch, FakeDocker(env.docker_state, env.docker_log))
  monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
  monkeypatch.setenv("FAKE_DOCKER_STATE", str(env.docker_state))
  monkeypatch.setenv("FAKE_DOCKER_LOG", str(env.docker_log))
  # Commands run in-process now, so what used to be `subprocess.run` arguments
  # have to be real process state: the config location and the working
  # directory kelso resolves relative paths against.
  monkeypatch.setenv("KELSO_CONFIG", str(env.config))
  monkeypatch.setenv("KELSO_LOCK_TIMEOUT", str(LOCK_TIMEOUT))
  monkeypatch.chdir(root)
  return env
