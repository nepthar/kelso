import argparse
from pathlib import Path

from kelso.cli.configform import collect
from kelso.cli.install import confirm_install
from kelso.lib.bundle import KLSO_MD_SUFFIX, KLSO_SUFFIX, app_id_from_path, is_pathlike
from kelso.lib.configflow import EMPTY_CONFIG_RESPONSE
from kelso.lib.configflow.app import app_config_request, apply_app_config
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle import (
  DevPlan,
  bound_to,
  dev,
  dev_plan,
  refuse_other_origin,
  stage,
)
from kelso.lib.receipt import LABEL_WIDTH, route_lines
from kelso.lib.spec import AppSpec
from kelso.lib.util import Conn


def register(subparsers) -> None:
  parser = subparsers.add_parser(
    "dev",
    help="Install an app from its bundle and run it in this terminal",
  )
  parser.add_argument(
    "bundle",
    metavar="PATH",
    help=f"Path to a {KLSO_SUFFIX} folder or {KLSO_MD_SUFFIX} file",
  )
  parser.add_argument(
    "--routes",
    action="store_true",
    help="Publish the app's routes to their providers for the duration of the run",
  )
  parser.set_defaults(func=run)


def run(args: argparse.Namespace, ctx: KelsoCtx, conn: Conn) -> None:
  if not is_pathlike(args.bundle):
    raise ValueError(
      f"`kelso dev` takes a path to a bundle, not an app id; "
      f"try `kelso dev ./{args.bundle}{KLSO_SUFFIX}`"
    )
  source = Path(args.bundle).expanduser().resolve()
  app = app_id_from_path(source)

  with ctx.locked(f"dev {app}", app):
    refuse_other_origin(app, source, ctx)
    if not confirm_install(app, source, ctx, conn):
      conn.out("Nothing started.")
      return

    bound = None if bound_to(app, ctx) else str(source)
    result = stage(app, source, ctx, bound=bound)
    for name in result.dropped_volumes:
      conn.err(
        f"volume {name} is no longer declared in the manifest; "
        f"its link is gone but its data was left in place"
      )
    _fill_missing_config(result.spec, ctx, conn)

    plan = dev_plan(app, source, ctx, publish_routes=args.routes)
    conn.out(_receipt(plan, ctx))

    code = dev(plan, ctx)
    if code:
      raise SystemExit(code)


def _fill_missing_config(spec: AppSpec, ctx: KelsoCtx, conn: Conn) -> None:
  """Ask for what the app cannot start without; `dev_plan` refuses if still unset."""
  request = app_config_request(spec, ctx)
  if not request.missing():
    return
  response = collect(request, conn)
  if response != EMPTY_CONFIG_RESPONSE:
    apply_app_config(spec, response, ctx)


def _receipt(plan: DevPlan, ctx: KelsoCtx) -> str:
  """What is mounted live, and what a dev run deliberately does not do."""
  rows: list[tuple[str, str]] = [("Source:", str(plan.source))]
  for name, path in plan.mounts.items():
    rows.append((f"  {name}:", str(path)))
  if not plan.source.is_dir():
    rows.append(("", "(markdown bundle: edits take effect on the next run)"))
  elif not plan.mounts:
    rows.append(("", "(no app volumes: nothing is mounted from it)"))

  # The same block `kelso start` prints; only what is published differs.
  for i, line in enumerate(
    route_lines(
      plan.spec,
      plan.run_data,
      plan.published,
      host=ctx.config.kelso_address or "localhost",
    )
  ):
    rows.append(("Routes:" if i == 0 else "", line))

  if not plan.published:
    rows.append(("", "routes are not published; publish them with --routes"))

  width = max(LABEL_WIDTH, max(len(label) for label, _ in rows))
  lines = [f"Dev {plan.app_id} (ctrl-c to stop)"]
  lines.extend(f"  {label:<{width}}  {value}" for label, value in rows)
  return "\n".join(lines)
