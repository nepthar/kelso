import argparse

from kelso.lib.doctor import Finding, diagnose
from kelso.lib.kelso import KelsoCtx


def register(subparsers) -> None:
  parser = subparsers.add_parser(
    "doctor", help="Report orphaned or inconsistent Kelso state"
  )
  parser.set_defaults(func=run)


def run(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  with ctx.kelso_lock("doctor"):
    prognosis = diagnose(ctx)

  if not prognosis.problems and not prognosis.warnings:
    print("No problems found")
    return

  _section("Problems", prognosis.problems)
  _section("Warnings", prognosis.warnings)
  if not prognosis.healthy:
    raise SystemExit(1)


def _section(title: str, findings: tuple[Finding, ...]) -> None:
  if not findings:
    return
  print(f"{title}:")
  for finding in findings:
    line = (
      f"{finding.subject}: {finding.message}" if finding.subject else finding.message
    )
    print(f"  {line}")
