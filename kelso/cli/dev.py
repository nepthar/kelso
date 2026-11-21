import argparse
import logging
import signal
from pathlib import Path

from kelso.cli.configform import collect
from kelso.cli.load import confirm_compose_warnings
from kelso.cli.net import summary
from kelso.lib.bundle import KLSO_MD_SUFFIX, KLSO_SUFFIX, app_id_from_path, is_pathlike
from kelso.lib.configflow import EMPTY_CONFIG_RESPONSE
from kelso.lib.configflow.app import app_config_request, apply_app_config
from kelso.lib.describe import describe
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle import (
  DevPlan,
  dev,
  dev_plan,
  load,
  refuse_other_origin,
)
from kelso.lib.manifest import PROXIED_MODES
from kelso.lib.net import last_run
from kelso.lib.receipt import LABEL_WIDTH, port_lines, route_lines
from kelso.lib.spec import AppSpec

logger = logging.getLogger("kelso.cli")


def register(subparsers) -> None:
  parser = subparsers.add_parser(
    "dev",
    help="Load an app from its bundle and run it in this terminal",
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
  parser.add_argument(
    "--allow-compose",
    action="store_true",
    help=(
      "Go ahead although the manifest sets compose keys kelso does not model; "
      "the operator should review them first"
    ),
  )
  parser.set_defaults(func=run)


def run(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  if not is_pathlike(args.bundle):
    raise ValueError(
      f"`kelso dev` takes a path to a bundle, not an app id; "
      f"try `kelso dev ./{args.bundle}{KLSO_SUFFIX}`"
    )
  source = Path(args.bundle).expanduser().resolve()
  app = app_id_from_path(source)

  with ctx.locked(f"dev {app}", app):
    refuse_other_origin(app, source, ctx)
    if not args.allow_compose and not confirm_compose_warnings(app, source):
      print("Nothing started.")
      return

    result = load(app, source, ctx, version_change=True)
    for name in result.dropped_volumes:
      logger.warning(
        f"volume {name} is no longer declared in the manifest; "
        f"its link is gone but its data was left in place"
      )
    _fill_missing_config(result.spec, ctx)

    plan = dev_plan(app, source, ctx, publish_routes=args.routes)
    print(_receipt(plan, ctx))

    # Ctrl-C tears the run down through KeyboardInterrupt; a plain `kill` should too.
    signal.signal(signal.SIGTERM, _interrupt)
    try:
      code = dev(plan, ctx)
    finally:
      # ctrl-c reaches kelso too, as KeyboardInterrupt, after kproxy has stopped.
      if plan.run_data.network_mode in PROXIED_MODES:
        last = last_run(app, ctx)
        if last is not None:
          decided = ctx.app_store(app).list_hosts()
          print(summary(plan.spec, last, decided, plan.run_data.network_mode))
    if code:
      raise SystemExit(code)


def _interrupt(signum, frame) -> None:
  raise KeyboardInterrupt


def _fill_missing_config(spec: AppSpec, ctx: KelsoCtx) -> None:
  request = app_config_request(spec, ctx)
  if not request.missing():
    return
  response = collect(
    request,
    f"set them with `kelso config {spec.app} NAME=VALUE` (NAME=- for a secret), "
    f"then run this again",
  )
  if response != EMPTY_CONFIG_RESPONSE:
    apply_app_config(spec, response, ctx)


def _receipt(plan: DevPlan, ctx: KelsoCtx) -> str:
  rows: list[tuple[str, str]] = [("Source:", str(plan.source))]
  for name, path in plan.mounts.items():
    rows.append((f"  {name}:", str(path)))
  if not plan.source.is_dir():
    rows.append(("", "(markdown bundle: edits take effect on the next run)"))
  elif not plan.mounts:
    rows.append(("", "(no app volumes: nothing is mounted from it)"))

  # The same block `kelso start` prints; only what is published differs.
  description = describe(plan.spec, plan.run_data, ctx)
  for i, line in enumerate(route_lines(description.routes, plan.published)):
    rows.append(("Routes:" if i == 0 else "", line))
  if not plan.published:
    rows.append(("", "routes are not published; publish them with --routes"))
  for i, line in enumerate(port_lines(description.ports, ctx.config.link_address)):
    rows.append(("Ports:" if i == 0 else "", line))

  mode = plan.run_data.network_mode
  if mode in PROXIED_MODES:
    rows.append(
      ("Network:", f"{mode}: the domains it reaches are listed when it stops")
    )

  width = max(LABEL_WIDTH, max(len(label) for label, _ in rows))
  lines = [f"Dev {plan.app_id} (ctrl-c to stop)"]
  lines.extend(f"  {label:<{width}}  {value}" for label, value in rows)
  return "\n".join(lines)
