"""Checks behind `kelso system doctor`: orphaned or inconsistent kelso state.

Diagnosis only reads; the caller holds the kelso lock and renders the result.
"""

import re
import socket
import time
from dataclasses import dataclass
from pathlib import Path

from kelso.lib import git as git_lib
from kelso.lib.apps import AppID
from kelso.lib.docker import DOCKER, DockerError, docker_run_command
from kelso.lib.git import git
from kelso.lib.kelso import KelsoCtx, ambiguity_message
from kelso.lib.lifecycle.load import bound_entry
from kelso.lib.observations import AppObservation
from kelso.lib.recovery import read_keyfile
from kelso.lib.spec import AppSpec


@dataclass(frozen=True)
class Finding:
  subject: str
  message: str


@dataclass(frozen=True)
class DoctorPrognosis:
  problems: tuple[Finding, ...]
  warnings: tuple[Finding, ...]

  @property
  def healthy(self) -> bool:
    return not self.problems


def diagnose(ctx: KelsoCtx) -> DoctorPrognosis:
  """Collect problems and warnings across volumes, the catalog, and every app."""
  problems = [
    *tool_problems(),
    *kelsod_problems(ctx),
    *_volume_problems(ctx),
    *_catalog_problems(ctx),
    *_key_problems(ctx),
  ]
  warnings = _repo_warnings(ctx)
  for observation in ctx.observations():
    subject = observation.app_id
    problems += [Finding(subject, m) for m in _app_problems(observation, ctx)]
    warnings += [Finding(subject, m) for m in _app_warnings(observation)]
  return DoctorPrognosis(tuple(problems), tuple(warnings))


def _key_problems(ctx: KelsoCtx) -> list[Finding]:
  """A root with no seed has no key: every secret is out of reach."""
  if read_keyfile(ctx.config.master_keyfile).seed is None:
    return [
      Finding(
        "the recovery phrase",
        f"there is none in {ctx.config.master_keyfile}, so kelso cannot encrypt "
        "or read secrets. Make one with `kelso system rekey`.",
      )
    ]
  return []


# `git sparse-checkout set`, which mirroring needs, arrived in 2.25.
MIN_GIT = (2, 25)


def tool_problems() -> list[Finding]:
  """git, docker, and docker compose answering on this host, docker as root."""
  findings = []
  try:
    version = git("--version")
  except RuntimeError as e:
    findings.append(Finding("git", f"{e}. Kelso mirrors repos with it."))
  else:
    found = re.search(r"(\d+)\.(\d+)", version)
    if found and (int(found[1]), int(found[2])) < MIN_GIT:
      findings.append(
        Finding(
          "git",
          f"{version} is too old: kelso mirrors repos with `git sparse-checkout`, "
          f"which needs git {MIN_GIT[0]}.{MIN_GIT[1]} or newer. Upgrade git.",
        )
      )

  try:
    info = docker_run_command(["info"]).data
  except DockerError as e:
    findings.append(Finding("the docker daemon", _docker_unreachable(e.stderr)))
  except OSError:
    findings.append(Finding("the docker daemon", "docker is not installed."))
  else:
    if _rootless(info):
      findings.append(
        Finding(
          "the docker daemon",
          "docker is running rootless, which kelso does not support yet: "
          "snapshots and restores read volume files as root and would "
          "silently miss them. Use rootful docker, with this user in the "
          "docker group.",
        )
      )

  try:
    docker_run_command(["compose", "version"])
  except (DockerError, OSError):
    findings.append(
      Finding(
        "docker compose",
        f"`{DOCKER} compose version` failed. Install the docker compose plugin.",
      )
    )
  return findings


def _docker_unreachable(stderr: str) -> str:
  """Why `docker info` failed, as the fix for it."""
  if "permission denied" in stderr.lower():
    return (
      "this user cannot reach the docker daemon. If you just added it to the "
      "docker group (`sudo usermod -aG docker $USER`), only processes started "
      "since have it: log out of every session, console included, and back "
      "in. kelsod runs under systemd's user manager, which keeps the groups it "
      "started with until `sudo systemctl restart user@$(id -u)` or a reboot."
    )
  detail = stderr.strip().splitlines()[-1] if stderr.strip() else "no output"
  return f"`{DOCKER} info` failed ({detail}). Is docker installed and running?"


def _rootless(info: list[dict]) -> bool:
  """Whether `docker info` describes a rootless daemon."""
  options = (info[0].get("SecurityOptions") or []) if info else []
  return any("name=rootless" in option for option in options)


# kelsod records host metrics when it starts and every 5 minutes after.
STALE_METRICS_SECONDS = 15 * 60


def kelsod_problems(ctx: KelsoCtx) -> list[Finding]:
  """kelsod listening on its admin socket, and its scheduler still running."""
  path = ctx.config.admin_socket_path
  if not _listening(path):
    return [
      Finding(
        "kelsod",
        f"nothing is listening on {path}, so apps do not resume at boot, cron "
        "jobs do not run, and volume sizes go blank. Start it with "
        "`systemctl --user start kelsod`; `journalctl --user -u kelsod` says "
        "why it stopped.",
      )
    ]
  readings = ctx.metrics_log.scan("gauge/host_cpu_used_ratio").values()
  newest = max((entry.unix_seconds for entry in readings), default=0)
  if time.time() - newest > STALE_METRICS_SECONDS:
    return [
      Finding(
        "kelsod",
        f"it is listening but has recorded no host metrics for over "
        f"{STALE_METRICS_SECONDS // 60} minutes, so its scheduler is stuck and "
        "cron jobs are not running either. Restart it with "
        "`systemctl --user restart kelsod`.",
      )
    ]
  return []


def _listening(path: Path) -> bool:
  probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
  try:
    probe.settimeout(1.0)
    probe.connect(str(path))
  except OSError:
    return False
  finally:
    probe.close()
  return True


def _volume_problems(ctx: KelsoCtx) -> list[Finding]:
  findings = []
  for kind, root in ctx.config.volume_roots.items():
    target = ctx.config.dangling_volume_root(kind)
    if target is not None:
      findings.append(
        Finding(
          f"volumes {kind}",
          f"{root} links to {target}, which does not exist. "
          f"Apps with {kind} volumes will not load or start until it does.",
        )
      )
  return findings


def _catalog_problems(ctx: KelsoCtx) -> list[Finding]:
  findings = []
  for name, repo in ctx.config.repos.items():
    if not repo.path.is_dir():
      hint = (
        f"Run `kelso repo update {name}` to mirror it."
        if repo.mirrored
        else "Create it, fix its path in config.toml, or drop the entry."
      )
      findings.append(
        Finding(f"repo {name}", f"{repo.path} is not a directory. {hint}")
      )

  catalog = ctx.app_catalog()
  for app_id in sorted(catalog):
    entries = catalog[app_id]
    if len(entries) > 1 and bound_entry(ctx, AppID(app_id), entries) is None:
      findings.append(Finding("", ambiguity_message(app_id, entries)))
  return findings


def _repo_warnings(ctx: KelsoCtx) -> list[Finding]:
  """Local repos with a git repository of their own and changes not committed."""
  findings = []
  for name, repo in ctx.config.repos.items():
    checkout = repo.path.resolve()
    if repo.mirrored or not (checkout / ".git").exists():
      continue
    try:
      changed = git_lib.uncommitted(checkout)
    except RuntimeError:
      continue
    if changed:
      findings.append(
        Finding(
          f"repo {name}",
          f"{changed} uncommitted change(s) in {checkout}; commit them with git.",
        )
      )
  return findings


def _app_problems(observation: AppObservation, ctx: KelsoCtx) -> list[str]:
  notes = []
  manifest = ctx.loaded_paths(observation.app_id).manifest_path
  if observation.run_dir_exists and manifest.is_file():
    try:
      AppSpec.from_file(manifest, observation.app_id)
    except ValueError as e:
      notes.append(
        f"its loaded manifest no longer parses; `kelso load "
        f"{observation.app_id}` from a bundle that does. {e}"
      )
  # A route entry alone is the orphaned allocation warned about, not this.
  if observation.bundle_path is None and (
    observation.run_dir_exists or observation.containers
  ):
    origin = ctx.loaded_origin(observation.app_id)
    if origin is None:
      notes.append("app bundle missing (no load source recorded)")
    else:
      notes.append(f"app bundle missing, was: {origin}")
  if not observation.run_dir_exists and observation.containers:
    notes.append("run directory missing")
  elif observation.run_dir_exists and not observation.compose_exists:
    notes.append("compose missing")
  if observation.containers and not observation.compose_exists:
    notes.append("manual container recovery required")
  if any(not container.run_unit for container in observation.containers):
    notes.append("container run-unit label missing")
  return notes


def _app_warnings(observation: AppObservation) -> list[str]:
  notes = []
  if 0 < observation.running_count < len(observation.containers):
    notes.append("mixed container states")
  if observation.orphaned_routes:
    notes.append("orphaned route allocation; `kelso cleanup` releases it")
  return notes
