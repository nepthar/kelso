import argparse
import logging

from tabulate import tabulate

from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle import cleanup, cleanup_plan
from kelso.lib.util import fmt_size

logger = logging.getLogger("kelso.cli")


def register(subparsers) -> None:
  parser = subparsers.add_parser(
    "cleanup",
    help="Show what kelso no longer needs; --apply deletes it",
  )
  parser.add_argument(
    "--temp",
    action="store_true",
    help="Also empty the temp and logs volumes of every stopped app",
  )
  parser.add_argument("--apply", action="store_true", help="Delete what is shown")
  parser.set_defaults(func=run, activity=lambda a: "cleanup" if a.apply else None)


def run(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  with ctx.kelso_lock("cleanup"):
    plan = cleanup_plan(ctx, temp=args.temp)
  for app in plan.skipped:
    logger.info("Leaving the temp and logs of %s: it is running", app)

  if not plan.items:
    print("Nothing to clean up.")
    return

  rows = [
    [
      item.kind,
      item.app or "",
      item.detail,
      fmt_size(item.size) if item.size is not None else "?",
    ]
    for item in plan.items
  ]
  print(tabulate(rows, headers=["WHAT", "APP", "DETAIL", "SIZE"], tablefmt="simple"))

  if not args.apply:
    print(f"\nUp to {fmt_size(plan.size)} can be freed.")
    logger.info("Nothing was deleted. Run `kelso cleanup --apply` to delete these.")
    return

  cleanup(plan, ctx)
  print(f"\nFreed up to {fmt_size(plan.size)}.")
