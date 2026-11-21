import argparse
import json
import logging
import shlex
import sys

from tabulate import tabulate

from kelso.cli.configform import collect
from kelso.cli.kv import parse_kv, read_secret
from kelso.cli.remote import run_remote
from kelso.lib.apps import AppID
from kelso.lib.config import NONE_ROUTE_PROVIDER_TAG
from kelso.lib.configflow import EMPTY_CONFIG_RESPONSE, ConfigRequest, ConfigResponse
from kelso.lib.configflow.app import (
  BACKUP_PREFIX,
  NETWORK_FIELD,
  app_config_request,
  apply_app_config,
)
from kelso.lib.kelso import KelsoCtx
from kelso.lib.observations import observe
from kelso.lib.spec import AppSpec
from kelso.lib.store import AppStore
from kelso.lib.views import config_request_view

logger = logging.getLogger("kelso.cli")

FROM_STDIN = "-"


def register(subparsers) -> None:
  parser = subparsers.add_parser(
    "config",
    help="View or set an app's config, routes, volume binds, and network mode",
    formatter_class=argparse.RawDescriptionHelpFormatter,
    description=(
      "With no NAME, lists everything. A NAME prints that one value;\n"
      "NAME=VALUE sets it. Besides the app's own config, the names are\n"
      "route.<route>, volume.<volume>, backup.<volume> and network.mode.\n"
      "\n"
      "A secret is never given on the command line: NAME=- asks for it on a\n"
      "terminal, or reads it from stdin. One NAME=- per call.\n"
      "\n"
      "--host HOST runs the same command on HOST over ssh.\n"
      "\n"
      "Changes to a loaded app take effect when it next starts;\n"
      "`kelso restart <app>` applies them to a running one."
    ),
    epilog=(
      "examples:\n"
      "  kelso config immich                        list everything\n"
      "  kelso config immich route.main             print one value\n"
      "  kelso config immich route.main=web,lan network.mode=watch\n"
      "  kelso config immich db_password=-          asks for it, hidden\n"
      "  kelso config immich network.mode=default   back to the manifest's\n"
      "  kelso config immich --edit --host den       the form, for den's immich"
    ),
  )
  parser.add_argument(
    "app",
    metavar="APP",
    help="App ID of a loaded app, or of an app in a repo",
  )
  parser.add_argument(
    "items",
    nargs="*",
    metavar="NAME[=VALUE]",
    help="A name to print, or NAME=VALUE to set (repeatable)",
  )
  parser.add_argument(
    "--edit",
    "-e",
    action="store_true",
    help="Fill in this app's config interactively",
  )
  parser.add_argument(
    "--json",
    action="store_true",
    help="Print everything this app can be asked, as a ConfigRequest in JSON",
  )
  parser.add_argument(
    "--json-response",
    action="store_true",
    help='Apply a ConfigResponse read from stdin: {"values": {NAME: VALUE}}',
  )
  parser.add_argument(
    "--host",
    metavar="HOST",
    help="Run this on HOST, a host you can `ssh` to, instead of here",
  )
  parser.set_defaults(
    func=run,
    activity=lambda a: (
      "config"
      if not a.host
      and (a.edit or a.json_response or any("=" in item for item in a.items))
      else None
    ),
  )


def run(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  if args.host:
    run_remote(args.host, f"kelso config {shlex.join(_remote_args(args))}")
    return
  app = ctx.resolve_app_id(args.app)
  # Asked for before the lock, which nothing else should wait on while you type.
  typed = _read_typed(app, args.items)
  with ctx.locked(f"config {app}", app):
    spec = _config_spec(app, ctx)
    modes = [args.edit, args.json, args.json_response, bool(args.items)]
    if sum(modes) > 1:
      raise ValueError(
        "Give one of NAME, NAME=VALUE, --edit, --json or --json-response"
      )

    if args.json:
      print(json.dumps(config_request_view(app_config_request(spec, ctx))))
    elif args.json_response:
      response = ConfigResponse(values=json.load(sys.stdin)["values"])
      _report(apply_app_config(spec, response, ctx), spec, ctx)
    elif args.edit:
      _edit(app, spec, ctx)
    elif not args.items:
      _list(app, spec, ctx)
    elif any("=" in item for item in args.items):
      _set(app, spec, args.items, typed, ctx)
    elif len(args.items) == 1:
      _get(app_config_request(spec, ctx), args.items[0])
    else:
      raise ValueError("Give one NAME to print, or NAME=VALUE pairs to set")


def _get(request: ConfigRequest, name: str) -> None:
  field = request.field(name)
  if field is None:
    raise ValueError(
      f"{request.title} has no config named {name!r}; "
      f"`kelso config {request.title}` lists them"
    )
  if field.secret:
    if not field.secret_set:
      raise SystemExit(1)
    print("set")
  elif field.value is not None:
    print(field.value)
  elif field.default is not None:
    logger.info("Config %s using default value", name)
    print(field.default)
  else:
    raise SystemExit(1)


def _read_typed(app: AppID, items: list[str]) -> dict[str, str]:
  """The NAME=- values, read from a prompt or stdin."""
  typed = [item.partition("=")[0] for item in items if item.endswith(f"={FROM_STDIN}")]
  if len(typed) > 1:
    raise ValueError(
      "Only one NAME=- per call, since there is one stdin; "
      f"`kelso config {app} --edit` sets several"
    )
  return {name: read_secret(name) for name in typed}


def _set(
  app: AppID, spec: AppSpec, items: list[str], typed: dict[str, str], ctx: KelsoCtx
) -> None:
  pairs = [parse_kv(item, "NAME=VALUE") for item in items]
  names = [name for name, _ in pairs]
  if repeated := sorted({n for n in names if names.count(n) > 1}):
    raise ValueError(f"{', '.join(repeated)} given more than once")

  request = app_config_request(spec, ctx)
  values = {}
  for name, value in pairs:
    field = request.field(name)
    if field is None:
      _refuse_unknown(spec, name)
    if field is not None and field.secret and name not in typed:
      raise ValueError(
        f"{name} is a secret, and would land in your shell history; "
        f"give it as {name}=- to type it, or pipe it on stdin"
      )
    value = typed.get(name, value)
    if name == NETWORK_FIELD and value == "default":
      value = spec.network_mode
    values[name] = value

  _report(apply_app_config(spec, ConfigResponse(values=values), ctx), spec, ctx)


def _refuse_unknown(spec: AppSpec, name: str) -> None:
  volume_name = name.removeprefix(BACKUP_PREFIX)
  volume = spec.volumes.get(volume_name)
  if name.startswith(BACKUP_PREFIX) and volume is not None:
    always = {"data": "always backed up"}.get(volume.kind, "never backed up")
    raise ValueError(
      f"Volume {volume_name} is a {volume.kind} volume, which is {always}; "
      f"only bulk volumes can be turned on or off"
    )
  raise ValueError(
    f"{spec.app} has no config named {name!r}; `kelso config {spec.app}` lists them"
  )


def _edit(app: AppID, spec: AppSpec, ctx: KelsoCtx) -> None:
  request = app_config_request(spec, ctx)
  if not request.fields:
    print(f"App {app} declares no config")
    return

  response = collect(
    request, f"set values with `kelso config {app} NAME=VALUE` (see --help)"
  )
  if response == EMPTY_CONFIG_RESPONSE:
    print("No changes")
    return
  _report(apply_app_config(spec, response, ctx), spec, ctx)


def _remote_args(args: argparse.Namespace) -> list[str]:
  flags = [("--edit", args.edit), ("--json", args.json)]
  flags.append(("--json-response", args.json_response))
  return [args.app, *args.items, *(flag for flag, on in flags if on)]


def _report(written: list[str], spec: AppSpec, ctx: KelsoCtx) -> None:
  """What was set, and what the app still needs before it can start."""
  print(f"Set {', '.join(written)}" if written else "No changes")
  request = app_config_request(spec, ctx)
  missing = [
    f"{name} (secret)" if request.field(name).secret else name
    for name in request.missing()
  ]
  print(
    f"Still needs: {', '.join(missing)}" if missing else "All required config is set"
  )
  if written:
    _what_applies_it(spec.app, ctx)


def _config_spec(app: AppID, ctx: KelsoCtx) -> AppSpec:
  """The manifest this command reads its schema from."""
  paths = ctx.loaded_paths(app)
  if paths.exists():
    return ctx.require_loaded_spec(app)
  # Refuses an id carried by two sources, and a bundle with no manifest.
  return ctx.bundle_spec(app)


def _what_applies_it(app: AppID, ctx: KelsoCtx) -> None:
  """The next start applies a change; say so only when one is not coming."""
  if ctx.is_loaded(app) and observe(app, ctx).running_count:
    logger.info(f"Takes effect on restart: kelso restart {app}")


def _network_display(spec: AppSpec, store: AppStore) -> str:
  mode = store.get_network_mode()
  if mode is None:
    return spec.network_mode
  return f"{mode} (set by you; the manifest says {spec.network_mode})"


def _list(app: AppID, spec: AppSpec, ctx: KelsoCtx) -> None:
  store = ctx.app_store(app)
  print(f"Configuration parameters for: {app}")
  print(f"Network: {_network_display(spec, store)}")
  for title, fields in app_config_request(spec, ctx).groups():
    # Binds and route assignments have their own tables below.
    rows = [[f.name, f.display(), f.desc] for f in fields if "." not in f.name]
    if rows:
      print("")
      print(f"{title}:")
      print(tabulate(rows, headers=["name", "value", "description"], tablefmt="simple"))

  if spec.routes:
    assignments = store.list_route_assignments()
    route_rows = []
    for name, route in spec.routes.items():
      tags = assignments.get(name)
      if tags is None:
        display = "(unassigned)"
      else:
        display = ", ".join(tags) or NONE_ROUTE_PROVIDER_TAG
      admin = "yes" if route.admin else ""
      route_rows.append([name, display, admin, route.desc or ""])
    print("")
    print("Route assignments:")
    print(
      tabulate(
        route_rows,
        headers=["route", "providers", "admin", "description"],
        tablefmt="simple",
      )
    )

  bulk = [name for name, v in spec.volumes.items() if v.kind == "bulk"]
  if bulk:
    on = store.backed_up_bulk()
    rows = [[name, "on" if name in on else "off"] for name in bulk]
    print("")
    print("Bulk volume backups:")
    print(tabulate(rows, headers=["volume_name", "backup"], tablefmt="simple"))

  host = [(n, v) for n, v in spec.volumes.items() if v.kind == "host"]
  if not host:
    return

  binds = store.list_binds()
  bind_rows = []
  for name, _volume in host:
    tag = binds.get(name)
    display = tag if tag else "(not bound)"
    bind_rows.append([name, display])
  print("")
  print("Host volume binds:")
  print(tabulate(bind_rows, headers=["volume_name", "host_volume"], tablefmt="simple"))
