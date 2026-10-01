"""Bringing the whole box up or down, one `start_order` group at a time.

`up` starts a group, then waits for it to be ready before starting the next:
healthy where a container's image has a healthcheck, and otherwise still
running `SETTLE` seconds in. A group that is not ready in time is reported and
the next one starts anyway.

Group 0 belongs to kelsod, which brings it up as it starts and down as it
exits; `kelso up` and `kelso down` act on `BOX_GROUPS`. Progress is logged to
`kelso.lifecycle.updown`.
"""

import time
from collections.abc import Iterable
from logging import getLogger

from kelso.lib.apps import AppID, read_last_app_action
from kelso.lib.docker import KelsoRunUnitStatus, load_kelso_run_unit_status
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle.run import start, stop
from kelso.lib.options import APP_OPTIONS, start_group_name

logger = getLogger("kelso.lifecycle.updown")

DEFAULT_WAIT = 60
SETTLE = 10
KELSOD_GROUPS = range(0, 1)
BOX_GROUPS = range(1, 10)
# What `down` records, so the `up` after it starts these apps again.
DOWN_ACTION = "down"


def start_groups(ctx: KelsoCtx, which: Iterable[int]) -> list[tuple[int, list[AppID]]]:
  """Installed apps in the groups `which`, by `start_order`, lowest first."""
  which = set(which)
  groups: dict[int, list[AppID]] = {}
  for app_id in sorted(ctx.staged_app_ids()):
    app = AppID(app_id)
    order = _start_order(app, ctx)
    if order in which:
      groups.setdefault(order, []).append(app)
  return sorted(groups.items())


def _start_order(app: AppID, ctx: KelsoCtx) -> int:
  _, value = ctx.app_store(app).get_config("start_order")
  if value is None:
    spec = ctx.staged_spec(app)
    value = spec.config["start_order"].default if spec else None
  # A bundle may make start_order required; unset, it waits with the default
  # group, and `start` is what says it needs setting.
  return int(value or APP_OPTIONS["start_order"].default(app))


def up(
  ctx: KelsoCtx, groups: Iterable[int] = BOX_GROUPS, *, wait: float = DEFAULT_WAIT
) -> list[str]:
  """Start the groups' apps, except those stopped on purpose; what went wrong."""
  problems: list[str] = []
  for order, apps in start_groups(ctx, groups):
    logger.info("Starting run group %s", start_group_name(order))
    waiting = []
    for app in apps:
      if read_last_app_action(app, ctx) == "stopped":
        logger.info("  %s: stopped with `kelso stop`, left stopped", app)
        continue
      try:
        with ctx.locked(f"up {app}", app):
          if ctx.run_state(app).running_count:
            logger.info("  %s: already running", app)
          else:
            start(app, ctx.config.app_run_path(app), ctx)
            logger.info("  %s: started", app)
        waiting.append(app)
      except (ValueError, RuntimeError) as error:
        problems.append(f"{app}: {error}")
        logger.warning("  %s: failed: %s", app, error)
    for app in _wait_ready(waiting, wait):
      problems.append(f"{app}: not ready after {wait:g}s")
      logger.warning("  %s: not ready after %gs; going on", app, wait)
  return problems


def down(ctx: KelsoCtx, groups: Iterable[int] = BOX_GROUPS) -> list[str]:
  """Stop the groups' running apps, highest group first; what went wrong."""
  problems: list[str] = []
  for order, apps in reversed(start_groups(ctx, groups)):
    running = [app for app in apps if ctx.run_state(app).running_count]
    if not running:
      continue
    logger.info("Stopping run group %s", start_group_name(order))
    for app in running:
      try:
        with ctx.locked(f"down {app}", app):
          stop(app, ctx, action=DOWN_ACTION)
        logger.info("  %s: stopped", app)
      except (ValueError, RuntimeError) as error:
        problems.append(f"{app}: {error}")
        logger.warning("  %s: failed: %s", app, error)
  return problems


def _wait_ready(apps: list[AppID], wait: float) -> list[AppID]:
  """Poll docker until every app is ready or `wait` runs out; the ones still not."""
  began = time.monotonic()
  while True:
    settled = time.monotonic() - began >= SETTLE
    units = load_kelso_run_unit_status()
    pending = [app for app in apps if not _ready(units.get(app, ()), settled)]
    if not pending or time.monotonic() - began >= wait:
      return pending
    time.sleep(1)


def _ready(units: tuple[KelsoRunUnitStatus, ...], settled: bool) -> bool:
  return bool(units) and all(_unit_ready(unit, settled) for unit in units)


def _unit_ready(unit: KelsoRunUnitStatus, settled: bool) -> bool:
  if unit.state == "running":
    # With no healthcheck, still running once SETTLE has passed is the test.
    return unit.health == "healthy" or (unit.health == "" and settled)
  # A one-shot command that finished cleanly has done what it was started for.
  return unit.state == "exited" and unit.status.startswith("Exited (0)")
