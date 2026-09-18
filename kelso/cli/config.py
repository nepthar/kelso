import argparse

from tabulate import tabulate

from kelso.cli.configform import collect
from kelso.cli.kv import parse_kv
from kelso.lib.apps import AppID
from kelso.lib.bundle import load_bundle
from kelso.lib.configflow.app import app_config_request, apply_app_config
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle import apply_config_sets, assign_route, bind
from kelso.lib.spec import AppSpec
from kelso.lib.store import AppStore


def register(subparsers) -> None:
  parser = subparsers.add_parser(
    "config",
    help="List or set app config, route assignments, and host volume binds",
  )
  parser.add_argument(
    "app",
    metavar="APP",
    help="App ID of an installed app, or of an app in the catalog",
  )
  parser.add_argument(
    "--set",
    action="append",
    default=[],
    dest="sets",
    metavar="KEY=VALUE",
    help="Set a config value (repeatable)",
  )
  parser.add_argument(
    "--route",
    action="append",
    default=[],
    dest="routes",
    metavar="ROUTE=PROVIDER",
    help="Assign a route to a route-provider tag (repeatable; use none to clear)",
  )
  parser.add_argument(
    "--bind",
    action="append",
    default=[],
    dest="binds",
    metavar="VOLUME=HOST_VOLUME",
    help="Bind an app volume to a host_volume tag from config.toml (repeatable)",
  )
  parser.add_argument(
    "--get",
    dest="get_name",
    metavar="NAME",
    help="Print a single config value (secrets show as 'set' unless --show-secret)",
  )
  parser.add_argument(
    "--edit",
    "-e",
    action="store_true",
    help="Fill in this app's config interactively",
  )
  parser.add_argument(
    "--show-secret",
    action="store_true",
    help="With --get, print secret values in plaintext",
  )
  parser.set_defaults(func=run)


def run(args: argparse.Namespace, ctx: KelsoCtx, conn) -> None:
  app = ctx.resolve_app(args.app)
  with ctx.locked(f"config {app}", app):
    store = ctx.app_store(app)
    spec = _config_spec(app, ctx)

    if args.get_name is not None:
      if args.sets or args.binds or args.routes:
        raise ValueError("--get cannot be combined with --set, --route, or --bind")
      _get(spec, store, args.get_name, conn, show_secret=args.show_secret)
      return

    if args.show_secret:
      raise ValueError("--show-secret requires --get")

    if args.sets or args.binds or args.routes:
      _apply(app, spec, args.sets, args.binds, args.routes, ctx, conn)
      return

    if args.edit:
      _edit(app, spec, ctx, conn)
      return

    _list(app, spec, store, conn)


def _edit(app: AppID, spec: AppSpec, ctx: KelsoCtx, conn) -> None:
  request = app_config_request(spec, ctx)
  if not request.fields:
    conn.out(f"App {app} declares no config")
    return

  response = collect(request, conn)
  if response is None:
    conn.out("Cancelled; nothing was written")
    return

  written = apply_app_config(spec, response, ctx)
  conn.out(f"Set {', '.join(written)}" if written else "No changes")


def _config_spec(app: AppID, ctx: KelsoCtx) -> AppSpec:
  """The manifest this command reads its schema from."""
  paths = ctx.staged_paths(app)
  if paths.exists():
    return AppSpec.from_file(paths.manifest_path, app)
  # Refuses an id carried by two sources, and a bundle with no manifest.
  return load_bundle(ctx.bundle_path(app)).app_spec()


def _get(
  spec: AppSpec,
  store: AppStore,
  name: str,
  conn,
  *,
  show_secret: bool,
) -> None:
  config = spec.config.get(name)
  if not config:
    raise ValueError(f"config {name!r} not declared in manifest")

  secret, value = store.get_config(name)
  if value is None:
    if config.has_default():
      conn.err(f"Config {config.name} using default value")
      conn.out(config.default)
    else:
      raise SystemExit(1)
  elif secret and not show_secret:
    conn.out("set")
  else:
    conn.out(value)


def _apply(
  app: AppID,
  spec: AppSpec,
  sets_raw: list[str],
  binds_raw: list[str],
  routes_raw: list[str],
  ctx: KelsoCtx,
  conn,
) -> None:
  sets = [parse_kv(item, "--set") for item in sets_raw]
  binds = [parse_kv(item, "--bind") for item in binds_raw]
  routes = [parse_kv(item, "--route") for item in routes_raw]

  if sets:
    apply_config_sets(spec, sets, ctx)
  for volname, host_volume_tag in binds:
    bind(spec, volname, host_volume_tag, ctx)
  if routes:
    _apply_routes(app, spec, routes, ctx, conn)

  try:
    state = ctx.run_state(app)
  except ValueError:
    state = None
  if state is not None and state.running_count:
    if sets or binds:
      conn.err(
        f"App {app} is running; run `kelso stop {app}` "
        f"&& `kelso start {app}` to apply new config"
      )
    if routes:
      conn.err(
        f"App {app} is running; route provider updates were applied, but "
        f"containers still have the previous route URLs in their environment. "
        f"Run `kelso stop {app}` && `kelso start {app}` to refresh them."
      )


def _apply_routes(
  app: AppID,
  spec: AppSpec,
  routes: list[tuple[str, str]],
  ctx: KelsoCtx,
  conn,
) -> None:
  for route_name, tag in routes:
    assign_route(spec, route_name, tag, ctx)
    conn.out(f"route {route_name} -> {tag} (applied on next start)")


def _list(app: AppID, spec: AppSpec, store: AppStore, conn) -> None:
  rows = []
  for name, entry in spec.config.items():
    secret, value = store.get_config(name)
    if value is None:
      if entry.has_default():
        display = f"{entry.default} (default)"
      else:
        display = "(required)"
    elif secret:
      display = "(secret)"
    else:
      display = value
    rows.append([name, display, entry.desc or ""])
  conn.out(f"Configuration parameters for: {app}")
  conn.out(tabulate(rows, headers=["name", "value", "description"]))

  if spec.routes:
    assignments = store.list_route_assignments()
    route_rows = []
    for name, route in spec.routes.items():
      tag = assignments.get(name)
      if tag is None:
        display = "(unassigned)"
      else:
        display = tag
      private = "yes" if route.private else ""
      route_rows.append([name, display, private, route.desc or ""])
    conn.out("")
    conn.out("Route assignments:")
    conn.out(
      tabulate(
        route_rows,
        headers=["route", "provider", "private", "description"],
        tablefmt="simple",
      )
    )

  host = [(n, v) for n, v in spec.volumes.items() if v.kind == "host"]
  if not host:
    return

  binds = store.list_binds()
  bind_rows = []
  for name, _volume in host:
    tag = binds.get(name)
    display = tag if tag else "(not bound)"
    bind_rows.append([name, display])
  conn.out("")
  conn.out("Host volume binds:")
  conn.out(
    tabulate(bind_rows, headers=["volume_name", "host_volume"], tablefmt="simple")
  )
