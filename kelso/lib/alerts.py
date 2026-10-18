"""Alerts: activity-log records the operator should hear about until they say so.

An alert is a run that failed with nobody watching: kelsod's own work, such as
resuming apps at boot and scheduled backups, and cron jobs. Acknowledging
writes one `kelso/alerts_seen` record; the alerts are the failures after it.
"""

import json
from datetime import UTC, datetime
from typing import Any

from kelso.lib.activity import BY_CRON, BY_KELSOD, ERROR, list_runs, record_run
from kelso.lib.kelso import ACTIVITY_LOG_HISTORY, KelsoCtx

SEEN_KEY = "kelso/alerts_seen"
UNATTENDED = (BY_KELSOD, BY_CRON)
# The verb of an alert raised by hand rather than by a failed run.
RAISED = "alert"


def alerts(ctx: KelsoCtx) -> list[dict[str, Any]]:
  """Unattended runs that failed since the last acknowledgement, newest first."""
  seen = ctx.activity_log.read(SEEN_KEY)
  return [
    run
    for run in list_runs(ctx, limit=ACTIVITY_LOG_HISTORY)
    if run["status"] == ERROR
    and (run["started_by"] in UNATTENDED or run["verb"] == RAISED)
    and (seen is None or run["ts"] > seen.ts)
  ]


def raise_alert(ctx: KelsoCtx, text: str, by: str) -> None:
  """Record `text` as a new alert, as a failed run would be."""
  now = datetime.now(UTC)
  record_run(
    ctx,
    RAISED,
    {},
    app_id=None,
    status=ERROR,
    started=now,
    finished=now,
    output=text,
    started_by=by,
    error=text,
  )


def acknowledge(ctx: KelsoCtx, by: str) -> None:
  """Mark every alert so far as seen."""
  ctx.activity_log.write(SEEN_KEY, json.dumps({"by": by}, separators=(",", ":")))
