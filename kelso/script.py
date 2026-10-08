"""What a kelso script imports.

A script is a Python file defining one `KelsoScript` subclass, run by
`kelso script NAME` with kelso's own interpreter. Yours go in `scripts/` in the
kelso root; one there replaces a script kelso ships with the same name.
Anything else under `kelso` can be imported too, but may change without notice.
"""

from kelso.lib.kelso import KelsoCtx


class KelsoScript:
  desc = ""

  def __init__(self, argv: list[str]) -> None:
    self.argv = argv

  def run(self, ctx: KelsoCtx) -> None:
    raise NotImplementedError


__all__ = ["KelsoCtx", "KelsoScript"]
