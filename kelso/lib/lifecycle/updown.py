import time
from collections.abc import Iterable
from logging import getLogger

from kelso.lib.apps import AppID, read_last_app_action
from kelso.lib.docker import KelsoRunUnitStatus, load_kelso_run_unit_status
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle.run import start, stop
from kelso.lib.options import start_group_name

logger = getLogger("kelso.lifecycle.updown")

DEFAULT_WAIT = 60
SETTLE = 10
KELSOD_GROUPS = range(0, 1)
# What stopping a group records for each app, so raising it starts them again.
DOWN_ACTION = "down"


def start_groups(ctx: KelsoCtx, which: Iterable[int]) -> list[tuple[int, list[AppID]]]:
  which = set(which)
  groups: dict[int, list[AppID]] = {}
  for app_id in sorted(ctx.inventory.loaded):
    app = AppID(app_id)
    order = _start_group(app, ctx)
    if order in which:
      groups.setdefault(order, []).append(app)
  return sorted(groups.items())


def _start_group(app: AppID, ctx: KelsoCtx) -> int:
  # A bundle may make start_group required; unset, it waits with the default
  # group, and `start` is what says it needs setting.
  return int(ctx.app_option(app, "start_group"))


def up(
  ctx: KelsoCtx, target: int = 9, *, wait: float = DEFAULT_WAIT, resume: bool = False
) -> list[str]:
  return _up(ctx, range(1, target + 1), wait, resume=resume)


def down(ctx: KelsoCtx, target: int = 0) -> list[str]:
  return _down(ctx, range(target + 1, 10))


def start_kelsod_group(ctx: KelsoCtx) -> list[str]:
  return _up(ctx, KELSOD_GROUPS, None)


def stop_kelsod_group(ctx: KelsoCtx) -> list[str]:
  return _down(ctx, KELSOD_GROUPS)


def _up(
  ctx: KelsoCtx, groups: Iterable[int], wait: float | None, *, resume: bool = False
) -> list[str]:
  problems: list[str] = []
  for order, apps in start_groups(ctx, groups):
    logger.info("Starting run group %s", start_group_name(order))
    waiting = []
    for app in apps:
      last = read_last_app_action(app, ctx)
      if last == "stopped":
        logger.info("  %s: stopped with `kelso stop`, left stopped", app)
        continue
      if resume and last != "started":
        logger.info("  %s: not running before, left alone", app)
        continue
      try:
        with ctx.locked(f"up {app}", app):
          # docker leaves a unit it stopped cleanly at shutdown down, so some
          # units running does not mean the app is.
          units = ctx.run_state(app).containers
          if units and all(unit.state.lower() == "running" for unit in units):
            logger.info("  %s: already running", app)
          else:
            start(app, ctx)
            logger.info("  %s: started", app)
        waiting.append(app)
      except (ValueError, RuntimeError) as error:
        problems.append(f"{app}: {error}")
        logger.warning("  %s: failed: %s", app, error)
    for app in wait_ready(waiting, wait) if wait is not None else ():
      problems.append(f"{app}: not ready after {wait:g}s")
      logger.warning("  %s: not ready after %gs; going on", app, wait)
  return problems


def _down(ctx: KelsoCtx, groups: Iterable[int]) -> list[str]:
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


def wait_ready(apps: list[AppID], wait: float = DEFAULT_WAIT) -> list[AppID]:
  began = time.monotonic()
  while True:
    settled = time.monotonic() - began >= SETTLE
    units = load_kelso_run_unit_status()
    pending = [app for app in apps if not _ready(units.get(app, ()), settled)]
    if (
      not pending
      or time.monotonic() - began >= wait
      or all(any(u.health == "unhealthy" for u in units.get(a, ())) for a in pending)
    ):
      return pending
    time.sleep(1)


def ensure_ready(
  app: AppID, *, wait: float = DEFAULT_WAIT, backup: str | None = None
) -> None:
  """Wait until `app` is ready, or raise with its unit states and rollback hint."""
  if not wait_ready([app], wait):
    return
  units = load_kelso_run_unit_status().get(app, ())
  detail = (
    ", ".join(f"{u.run_unit}: {u.status or u.state}" for u in units)
    or "no containers running"
  )
  back = (
    f" To go back, run `kelso stop {app} && kelso restore {app} {backup}`."
    if backup
    else ""
  )
  raise RuntimeError(
    f"{app} did not become healthy ({detail}). Check `kelso logs {app}`.{back}"
  )


def _ready(units: tuple[KelsoRunUnitStatus, ...], settled: bool) -> bool:
  return bool(units) and all(_unit_ready(unit, settled) for unit in units)


def _unit_ready(unit: KelsoRunUnitStatus, settled: bool) -> bool:
  if unit.state == "running":
    # With no healthcheck, still running once SETTLE has passed is the test.
    return unit.health == "healthy" or (unit.health == "" and settled)
  # A one-shot command that finished cleanly has done what it was started for.
  return unit.finished
