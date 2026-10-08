import argparse

from tabulate import tabulate

from kelso.lib.kelso import KelsoCtx
from kelso.lib.scripts import SHIPPED, load_script, script_paths
from kelso.script.v1 import using


def register(subparsers) -> None:
  parser = subparsers.add_parser(
    "script",
    help="Run a kelso script, or list them",
    description="Runs NAME with kelso's interpreter, passing ARGS to it. With no "
    "NAME, lists every script: kelso's own, and yours in scripts/ in the kelso "
    "root, where one with the same name replaces kelso's.",
  )
  parser.add_argument("name", nargs="?", metavar="NAME")
  parser.add_argument("args", nargs=argparse.REMAINDER, metavar="ARGS")
  parser.set_defaults(
    func=run, activity=lambda a: f"script {a.name}" if a.name else None
  )


def run(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  paths = script_paths(ctx)
  if args.name is None:
    rows = [
      (name, "kelso" if path.parent == SHIPPED else "yours", load_script(path).desc)
      for name, path in sorted(paths.items())
    ]
    print(tabulate(rows, headers=["SCRIPT", "FROM", "DESCRIPTION"]))
    return
  path = paths.get(args.name)
  if path is None:
    raise ValueError(
      f"No script {args.name!r}. `kelso script` lists them; add your own to "
      f"{ctx.config.scripts_root}"
    )
  with using(ctx):
    load_script(path)().run(args.args)
