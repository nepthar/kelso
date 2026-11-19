import argparse
from collections.abc import Mapping
from typing import Any

from tabulate import tabulate

from kelso.lib.kelso import KelsoCtx
from kelso.lib.manifest import PROXIED_MODES
from kelso.lib.net import (
  decide,
  domain_rows,
  is_proxied_and_running,
  kproxy_admin,
  last_run,
  listed_as,
)
from kelso.lib.receipt import network_lines
from kelso.lib.run_layout import load_run_data
from kelso.lib.spec import AppSpec
from kelso.lib.util import fmt_size

VERDICTS = {"allow": "allow", "deny": "deny", "forget": None}
PAST = {"allow": "allowed", "deny": "denied", "forget": "forgot"}


def register(subparsers) -> None:
  parser = subparsers.add_parser(
    "net",
    help="Show what an app behind kproxy reaches, and allow or deny domains",
    description=(
      "With no action, shows the app's network mode, the domains it is waiting "
      "on, and its traffic by domain. A decision covers the domain and its "
      "subdomains, is kept across loads, and applies to a running app at once."
    ),
  )
  parser.add_argument("app", metavar="APP", help="App ID of a loaded app")
  parser.add_argument("action", nargs="?", choices=sorted(VERDICTS))
  parser.add_argument("domain", nargs="?", metavar="DOMAIN")
  parser.set_defaults(func=run, activity=lambda a: "net" if a.action else None)


def run(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  app = ctx.resolve_app_id(args.app)
  if (args.action is None) != (args.domain is None):
    raise ValueError(
      f"Give both an action and a domain: kelso net {app} allow tmdb.org"
    )
  with ctx.locked(f"net {app}", app):
    spec = ctx.require_loaded_spec(app)
    if args.action:
      name = decide(spec, args.domain, VERDICTS[args.action], ctx)
      print(f"{PAST[args.action]} {name} and its subdomains")
      return
    _show(spec, ctx)


def _show(spec: AppSpec, ctx: KelsoCtx) -> None:
  data = load_run_data(spec, ctx)
  for line in network_lines(spec, data) or [data.network_mode]:
    print(line)
  decided = ctx.app_store(spec.app).list_hosts()
  if data.network_mode not in PROXIED_MODES:
    print(
      f"\nNot behind kproxy, so nothing is counted. To see what it reaches: "
      f"kelso config {spec.app} --network watch"
    )
    if decided:
      print("\nYour decisions apply once it is:")
      print(_table(spec, {}, decided))
    return
  if not is_proxied_and_running(spec, ctx):
    print(f"\nNot running: kelso start {spec.app}")
    last = last_run(spec.app, ctx)
    if last is not None:
      print("\n" + summary(spec, last, decided, data.network_mode))
    elif decided:
      print("\n" + _table(spec, {}, decided))
    return

  pending = kproxy_admin(spec.app, ctx, "GET", "/pending") or []
  if pending:
    print("\nWaiting for you (each connection is held up to 60s):")
    rows = [(p["domain"], ", ".join(sorted(p["hosts"]))) for p in pending]
    print(tabulate(rows, headers=["DOMAIN", "HOSTS"], tablefmt="simple"))
    print(f"Decide with: kelso net {spec.app} allow|deny {pending[0]['domain']}")
  stats = kproxy_admin(spec.app, ctx, "GET", "/stats") or {}
  print("\nTraffic since kproxy started:")
  print(_table(spec, stats, decided))


def summary(
  spec: AppSpec, last: Mapping[str, Any], decided: Mapping[str, str], mode: str
) -> str:
  """What kproxy saw in its last run, and the `[connections]` lines it suggests."""
  stats = last.get("stats") or {}
  lines = ["Traffic in its last run:", _table(spec, stats, decided)]
  pending = [p["domain"] for p in last.get("pending") or []]
  if pending:
    lines.append(f"Still waiting when it stopped: {', '.join(pending)}")
  unlisted = sorted(
    domain
    for domain, listeners in stats.items()
    if any(
      lst in ("http", "https") and c.get("allowed") for lst, c in listeners.items()
    )
    and listed_as(spec, domain) is None
  )
  if unlisted:
    lines.append("Reached but not in the manifest; to list them, add to [connections]:")
    lines += [
      f'  {d.split(".")[0].replace("-", "_")} = {{ kind = "web", hosts = ["{d}"] }}'
      for d in unlisted
    ]
  if mode == "restrict":
    lines.append(
      "In restrict, a name not listed fails at its DNS lookup and isn't counted. "
      f"To see those: kelso config {spec.app} --network watch"
    )
  return "\n".join(lines)


def _table(spec: AppSpec, stats: Mapping[str, Any], decided: Mapping[str, str]) -> str:
  rows = [
    (
      row["domain"],
      _rule(row),
      row["conns"],
      row["refused"],
      fmt_size(row["bytes_in"]),
      fmt_size(row["bytes_out"]),
      row["last_seen"][:19].replace("T", " ") or "-",
    )
    for row in domain_rows(spec, stats, (), decided)
  ]
  if not rows:
    return "(nothing yet)"
  return tabulate(
    rows,
    headers=["DOMAIN", "RULE", "CONNS", "REFUSED", "IN", "OUT", "LAST SEEN (UTC)"],
    tablefmt="simple",
    disable_numparse=True,
  )


def _rule(row: Mapping[str, Any]) -> str:
  if row["verdict"]:
    return f"{PAST[row['verdict']]} by you"
  return f"listed ({row['listed']})" if row["listed"] else "-"
