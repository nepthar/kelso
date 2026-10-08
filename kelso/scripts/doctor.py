from kelso.lib.doctor import Finding
from kelso.script.v1 import KelsoScript


class Doctor(KelsoScript):
  desc = "Report orphaned or inconsistent kelso state"

  def run(self, args: list[str]) -> None:
    prognosis = self.kelso().diagnose()
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
