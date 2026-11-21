import io
import re
from dataclasses import dataclass
from pathlib import Path

from kelso.lib import kproxy
from kelso.lib.activity import Detached
from kelso.lib.app_inventory import no_bundle_message
from kelso.lib.apps import AppID, record_app_action
from kelso.lib.docker import (
  DockerError,
  DockerTimeout,
  docker_run_command,
  image_exists,
  server_major,
  sink_output,
)
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle._common import container_recovery_message, logger
from kelso.lib.lifecycle.load import (
  LoadResult,
  check_same_version,
  link_host_volumes,
  load,
  materialize,
  unlink_host_volumes,
  write_kproxy_rules,
)
from kelso.lib.lifecycle.routes import (
  preflight_app_routes,
  register_app_routes,
  take_down_routes,
)
from kelso.lib.observations import changes_since_start
from kelso.lib.routes import RouteProviderError, RouteProviderUnreachable
from kelso.lib.run_layout import ConfigIssue, command_argv, load_run_data
from kelso.lib.spec import AppSpec
from kelso.lib.store import ROUTES_UNPUBLISHED
from kelso.lib.util import now_ts


def recovery_lines(app_id: AppID, issues: tuple[ConfigIssue, ...]) -> list[str]:
  lines = [f"{app_id} cannot start:"]
  for issue in issues:
    lines.append(f"  - {issue.problem}")
    if issue.fix:
      lines.append(f"    {issue.fix}")
  return lines


def start(app: AppID, ctx: KelsoCtx, bundle: Path | None = None) -> LoadResult:
  """Start the app, loading it from `bundle` first if it is not loaded."""
  with ctx.locked(f"start {app}", app):
    paths = ctx.loaded_paths(app)

    if not ctx.is_loaded(app):
      if bundle is None:
        raise ValueError(no_bundle_message(app))
      result = load(app, bundle, ctx)
    else:
      spec = ctx.require_loaded_spec(app)
      if changes_since_start(ctx.config.app_config_path(app)).routes:
        take_down_routes(app, ctx)
        run_data, _ = materialize(spec, ctx)
        result = LoadResult(spec, run_data)
      else:
        result = LoadResult(spec, load_run_data(spec, ctx))

    spec, run_data = result.spec, result.run_data
    if run_data.start_blockers:
      raise ValueError("\n".join(recovery_lines(app, run_data.start_blockers)))
    if run_data.restricted is not None:
      major = server_major()
      if major is None:
        raise RuntimeError(
          "Unable to communicate with the docker daemon. Is it running?"
        )
      if problem := kproxy.docker_too_old(major):
        raise RuntimeError(
          f"{app} runs behind kproxy ({run_data.network_mode}), and {problem}"
        )

    if not paths.compose_path.is_file():
      raise ValueError(f"App {app} is not loaded; run `kelso load {app}` first")

    unreachable: RouteProviderUnreachable | None = None
    try:
      preflight_app_routes(run_data, ctx)
    except RouteProviderUnreachable as e:
      unreachable = e
    except RouteProviderError as e:
      record_app_action("start-failed", app, ctx)
      raise ValueError(str(e)) from e

    # Rebuilt from scratch every start, so a bind recorded since the last one --
    # loading does not touch these -- takes effect now.
    link_host_volumes(spec, run_data)
    write_kproxy_rules(run_data)
    if run_data.restricted is not None and not image_exists(
      kproxy.image_tag(run_data.restricted.source)
    ):
      logger.info(
        "Building kproxy, the proxy apps behind kproxy reach the network through. "
        "This happens once per kelso version and takes about a minute."
      )

    try:
      docker_run_command(
        ["compose", "up", "-d"],
        cwd=paths.run_path,
        json_output=False,
        check=True,
        env={**blank_env(paths.run_path), **run_data.config_env()},
      )
    except DockerError as e:
      record_app_action("start-failed", app, ctx)
      raise ValueError(str(e)) from e

    try:
      if unreachable is None:
        register_app_routes(run_data, ctx)
    except RouteProviderUnreachable as e:
      unreachable = e
    except RouteProviderError as e:
      record_app_action("start-failed", app, ctx)
      raise ValueError(
        f"{e}. Containers may still be running; run `kelso stop {app}` to stop them."
      ) from e
    if unreachable:
      logger.warning(
        "%s is running, but its routes are not published: %s. "
        "Run `kelso start %s` again once the route provider answers.",
        app,
        unreachable,
        app,
      )

    store = ctx.app_store(app)
    store.set_meta(ROUTES_UNPUBLISHED, str(unreachable) if unreachable else None)
    # Everything on file is now what runs; `changes_since_start` counts from here.
    store.set_meta("started_at", now_ts())
    record_app_action("started", app, ctx)
    return result


def restart(app: AppID, ctx: KelsoCtx) -> LoadResult:
  with ctx.locked(f"restart {app}", app):
    if not ctx.is_loaded(app):
      raise ValueError(f"App {app} is not loaded; run `kelso start {app}` instead")
    if ctx.run_state(app).running_count:
      stop(app, ctx)
    return start(app, ctx)


def compose_env(app_id: AppID, ctx: KelsoCtx) -> dict[str, str]:
  paths = ctx.loaded_paths(app_id)
  env = blank_env(paths.run_path)
  try:
    spec = AppSpec.from_file(paths.manifest_path, app_id)
    env.update(load_run_data(spec, ctx).config_env())
  except ValueError as e:
    logger.debug("no config env for %s: %s", app_id, e)
  return env


# How long kelso watches a command before it detaches.
WATCH_SECONDS = 5 * 60

_PLACEHOLDER = re.compile(r"(?<!\$)\$\{([A-Za-z_][A-Za-z0-9_]*)")


def blank_env(run_path: Path) -> dict[str, str]:
  try:
    text = (run_path / "compose.yml").read_text()
  except FileNotFoundError:
    return {}
  return dict.fromkeys(_PLACEHOLDER.findall(text), "")


def logs(app_id: AppID, extra_args: list[str], ctx: KelsoCtx) -> None:
  state = ctx.run_state(app_id)
  if not state.compose_exists:
    raise ValueError(f"App {app_id} is not loaded; run `kelso load {app_id}` first")

  docker_run_command(
    ["compose", "logs", *(extra_args or [])],
    cwd=state.run_path,
    json_output=False,
    check=True,
    env=compose_env(app_id, ctx),
  )


def logs_text(app_id: AppID, ctx: KelsoCtx, *, tail: int) -> str:
  state = ctx.run_state(app_id)
  if not state.compose_exists:
    raise ValueError(f"App {app_id} is not loaded; run `kelso load {app_id}` first")

  captured = io.StringIO()
  with sink_output(captured):
    docker_run_command(
      ["compose", "logs", "--no-color", "--tail", str(tail)],
      cwd=state.run_path,
      json_output=False,
      check=False,
      env=compose_env(app_id, ctx),
    )
  return captured.getvalue()


def require_command(app_id: AppID, cmd_name: str, ctx: KelsoCtx) -> AppSpec:
  """The loaded spec, once it is known to declare `cmd_name`."""
  if not ctx.loaded_paths(app_id).compose_path.is_file():
    raise ValueError(f"App {app_id} is not loaded; run `kelso load {app_id}` first")
  spec = ctx.require_loaded_spec(app_id)
  if cmd_name not in spec.commands:
    available = ", ".join(sorted(spec.commands)) or "(none)"
    raise ValueError(
      f"Unknown command {cmd_name!r} for {app_id}; "
      f"available: {available}. List with `kelso cmd {app_id}`"
    )
  return spec


def run_command(
  app_id: AppID,
  cmd_name: str,
  args: list[str],
  ctx: KelsoCtx,
) -> int:
  """Run a manifest command, holding only the app lock."""
  with ctx.app_lock(app_id, f"cmd {app_id}"):
    spec = require_command(app_id, cmd_name, ctx)
    entry = spec.commands[cmd_name]
    state = ctx.run_state(app_id)

    running = {c.run_unit for c in state.containers if c.state.lower() == "running"}
    argv = command_argv(spec.run_units[entry.run_unit], entry, args)
    env = compose_env(app_id, ctx)

    try:
      if entry.run_unit in running:
        return docker_run_command(
          ["compose", "exec", entry.run_unit, *argv],
          cwd=state.run_path,
          json_output=False,
          check=False,
          env=env,
          timeout=WATCH_SECONDS,
        ).returncode

      # Host binds are only linked while an app runs; restore them for the one-off
      # so compose mounts resolve, then tear them down again if nothing else is up.
      was_fully_stopped = state.running_count == 0
      if was_fully_stopped:
        link_host_volumes(spec, load_run_data(spec, ctx))
      try:
        return docker_run_command(
          ["compose", "run", "--rm", "--no-deps", entry.run_unit, *argv],
          cwd=state.run_path,
          json_output=False,
          check=False,
          env=env,
          timeout=WATCH_SECONDS,
        ).returncode
      finally:
        if was_fully_stopped:
          unlink_host_volumes(state.run_path)
    except DockerTimeout as e:
      raise Detached(f"{cmd_name!r} is still running after {WATCH_SECONDS}s") from e


@dataclass(frozen=True)
class ReloadResult:
  load: LoadResult
  # Whether the app was running when the reload began, and so was started
  # again at the end. A reload never starts an app that was not running.
  was_running: bool


def reload_app(
  app: AppID,
  bundle: Path,
  ctx: KelsoCtx,
) -> ReloadResult:
  with ctx.locked(f"load {app}", app):
    try:
      running = bool(ctx.run_state(app).running_count)
    except ValueError:
      # Never loaded, so nothing to stop -- loading below is the whole job.
      running = False

    check_same_version(app, bundle, ctx)
    if running:
      stop(app, ctx)
    result = load(app, bundle, ctx)
    if running:
      start(app, ctx)
    return ReloadResult(load=result, was_running=running)


def stop(app_id: AppID, ctx: KelsoCtx, *, action: str = "stopped") -> None:
  with ctx.locked(f"stop {app_id}", app_id):
    # Routes stay published; `up` skips an app whose last action is "stopped".
    state = ctx.run_state(app_id)
    if not state.compose_exists:
      if state.containers:
        raise ValueError(container_recovery_message(app_id, ctx))
      raise ValueError(f"App {app_id} is not loaded; run `kelso load {app_id}` first")

    try:
      docker_run_command(
        ["compose", "down"],
        cwd=state.run_path,
        json_output=False,
        check=True,
        env=blank_env(state.run_path),
      )
      unlink_host_volumes(state.run_path)
      record_app_action(action, app_id, ctx)
    except DockerError as e:
      record_app_action("stop-failed", app_id, ctx)
      raise ValueError(str(e)) from e
