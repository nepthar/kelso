"""Finding and loading kelso scripts: the ones kelso ships, then the root's."""

import importlib.util
from pathlib import Path

from kelso.lib.kelso import KelsoCtx
from kelso.script import KelsoScript

SHIPPED = Path(__file__).parent.parent / "scripts"


def script_paths(ctx: KelsoCtx) -> dict[str, Path]:
  """Every script by name. One in the root's `scripts/` replaces kelso's."""
  found = {p.stem: p for p in sorted(SHIPPED.glob("*.py"))}
  found |= {p.stem: p for p in sorted(ctx.config.scripts_root.glob("*.py"))}
  return found


def load_script(path: Path) -> type[KelsoScript]:
  """The one KelsoScript subclass `path` defines. Runs the file's top level."""
  spec = importlib.util.spec_from_file_location(f"kelso_script_{path.stem}", path)
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  found = [
    value
    for value in vars(module).values()
    if isinstance(value, type)
    and issubclass(value, KelsoScript)
    and value.__module__ == module.__name__
  ]
  if len(found) != 1:
    raise ValueError(
      f"{path} must define exactly one KelsoScript subclass; it defines {len(found)}"
    )
  return found[0]
