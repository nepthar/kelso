import re
import socket
import time
from dataclasses import dataclass
from pathlib import Path

from kelso.lib import git as git_lib
from kelso.lib.backup import SCHEDULED, destination_problem, shares_kelso_disk
from kelso.lib.docker import DOCKER, DockerError, docker_run_command, server_major
from kelso.lib.git import git
from kelso.lib.kelso import KelsoCtx
from kelso.lib.kproxy import docker_too_old
from kelso.lib.observations import AppObservation
from kelso.lib.recovery import read_keyfile
from kelso.lib.spec import AppSpec
from kelso.lib.store import ROUTES_UNPUBLISHED


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
  problems = [
    *tool_problems(),
    *kelsod_problems(ctx),
    *_volume_problems(ctx),
    *_catalog_problems(ctx),
    *_key_problems(ctx),
    *_secret_problems(ctx),
    *_backup_problems(ctx),
  ]
  warnings = [*_repo_warnings(ctx), *_backup_warnings(ctx), *_docker_warnings()]
  for observation in ctx.observations():
    subject = observation.app_id
    problems += [Finding(subject, m) for m in _app_problems(observation, ctx)]
    warnings += [Finding(subject, m) for m in _app_warnings(observation, ctx)]
  return DoctorPrognosis(tuple(problems), tuple(warnings))


def _key_problems(ctx: KelsoCtx) -> list[Finding]:
  if read_keyfile(ctx.config.master_keyfile).seed is None:
    return [
      Finding(
        "the recovery phrase",
        f"there is none in {ctx.config.master_keyfile}, so kelso cannot encrypt "
        "or read secrets. Make one with `kelso secret rekey`.",
      )
    ]
  return []


def _secret_problems(ctx: KelsoCtx) -> list[Finding]:
  stored = set(ctx.kelso_db.list_secrets())
  return [
    Finding(
      ref.split(".args.")[0],
      f"{ref} names host secret {name!r}, which is not set. "
      f"Set it with `kelso secret set {name}`.",
    )
    for name, refs in ctx.config.secret_references().items()
    if name not in stored
    for ref in refs
  ]


# `git sparse-checkout set`, which mirroring needs, arrived in 2.25.
MIN_GIT = (2, 25)


def tool_problems() -> list[Finding]:
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
          "backups and restores read volume files as root and would "
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
  options = (info[0].get("SecurityOptions") or []) if info else []
  return any("name=rootless" in option for option in options)


# kelsod records host metrics when it starts and every 5 minutes after.
STALE_METRICS_SECONDS = 15 * 60


def kelsod_problems(ctx: KelsoCtx) -> list[Finding]:
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


def _backup_problems(ctx: KelsoCtx) -> list[Finding]:
  findings = []
  problem = destination_problem(ctx)
  if problem:
    findings.append(Finding("backups", problem))
  last = ctx.kelso_db.last_backup_run(SCHEDULED)
  if last and last["failed"]:
    findings.append(
      Finding(
        "backups",
        f"the scheduled backup at {last['time']} could not back up "
        + "; ".join(last["failed"])
        + ". `kelso backup run` tries again.",
      )
    )
  return findings


def _docker_warnings() -> list[Finding]:
  major = server_major()
  problem = docker_too_old(major) if major is not None else None
  return [Finding("the docker daemon", problem)] if problem else []


def _backup_warnings(ctx: KelsoCtx) -> list[Finding]:
  root = ctx.config.backups_root
  if not root.exists() or destination_problem(ctx) or not shares_kelso_disk(ctx):
    return []
  return [
    Finding(
      "backups",
      f"{root} is on the same disk as kelso, so a disk failure would take the "
      f"backups too. To keep them elsewhere, replace it with a link to another "
      f"disk, as with a volume root.",
    )
  ]


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

  for app_id in sorted(ctx.inventory.contested()):
    try:
      ctx.inventory.bundle(app_id)
    except ValueError as e:
      findings.append(Finding("", str(e)))
  return findings


def _repo_warnings(ctx: KelsoCtx) -> list[Finding]:
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
  app = observation.app_id
  manifest = ctx.loaded_paths(app).manifest_path
  if observation.run_dir_exists and manifest.is_file():
    try:
      spec = AppSpec.from_file(manifest, app)
    except ValueError as e:
      notes.append(
        f"its loaded manifest no longer parses; `kelso load "
        f"{app}` from a bundle that does. {e}"
      )
    else:
      if (
        set(spec.run_units) - spec.one_shots
        and observation.last_action in ("started", "start-failed")
        and not observation.running_count
      ):
        notes.append(f"should be running but is not; `kelso start {app}` says why")
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


def _app_warnings(observation: AppObservation, ctx: KelsoCtx) -> list[str]:
  notes = []
  if observation.running_count and (
    why := ctx.app_store(observation.app_id).get_meta(ROUTES_UNPUBLISHED)
  ):
    notes.append(
      f"running, but its routes are not published: {why}. Run `kelso start "
      f"{observation.app_id}` again once the route provider answers."
    )
  if 0 < observation.running_count < len(observation.containers):
    notes.append("mixed container states")
  if observation.orphaned_routes:
    notes.append("orphaned route allocation; `kelso cleanup` releases it")
  return notes
