"""Checks behind `kelso system doctor`: orphaned or inconsistent kelso state.

Diagnosis only reads; the caller holds the kelso lock and renders the result.
"""

from dataclasses import dataclass

from kelso.lib.kelso import KelsoCtx, ambiguity_message
from kelso.lib.observations import AppObservation


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
  problems = [*_volume_problems(ctx), *_catalog_problems(ctx)]
  warnings = []
  for observation in ctx.observations():
    subject = observation.app_id
    problems += [Finding(subject, m) for m in _app_problems(observation, ctx)]
    warnings += [Finding(subject, m) for m in _app_warnings(observation)]
  return DoctorPrognosis(tuple(problems), tuple(warnings))


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
    if len(entries) > 1:
      findings.append(Finding("", ambiguity_message(app_id, entries)))
  return findings


def _app_problems(observation: AppObservation, ctx: KelsoCtx) -> list[str]:
  notes = []
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
