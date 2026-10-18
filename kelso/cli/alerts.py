import argparse

from kelso.lib import alerts
from kelso.lib.activity import BY_CLI
from kelso.lib.kelso import KelsoCtx


def register(subparsers) -> None:
  parser = subparsers.add_parser(
    "alerts", help="Unattended runs that failed since you last acknowledged"
  )
  parser.add_argument(
    "--new",
    metavar="TEXT",
    help="Raise an alert with this text, to try out where alerts show up",
  )
  sub = parser.add_subparsers(dest="alerts_command")
  ack = sub.add_parser("ack", help="Mark every alert so far as seen")
  ack.set_defaults(func=run_ack)
  parser.set_defaults(func=run)


def run(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  if args.new is not None:
    alerts.raise_alert(ctx, args.new, BY_CLI)
    print("Raised an alert.")
    return
  found = alerts.alerts(ctx)
  if not found:
    print("No new alerts.")
    return
  for alert in found:
    print(f"{alert['ts']}  {describe(alert)}")
    print(f"  var/logs/{alert['log']}")


def run_ack(_args: argparse.Namespace, ctx: KelsoCtx) -> None:
  count = len(alerts.alerts(ctx))
  alerts.acknowledge(ctx, BY_CLI)
  print(f"Acknowledged {count} alert(s).")


def describe(alert: dict) -> str:
  """`<verb> <app> failed: <error>`, one line."""
  if alert["verb"] == alerts.RAISED:
    return alert["error"]
  what = f"{alert['verb']} {alert['app_id'] or ''}".strip()
  return f"{what} failed: {alert['error'] or 'see its log'}"
