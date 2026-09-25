"""The admin API's routes.

Reads do not take the kelso lock; writes go through `JobRunner`, which does.
Every handler is a plain `def`: `kelso.lib` blocks, and FastAPI runs a
non-async endpoint in a threadpool. Errors have one shape, `{"error": "..."}`.
"""

import asyncio
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from kelso import VERSION
from kelso.daemon import console
from kelso.jobs import JobRunner
from kelso.lib import views
from kelso.lib.apps import AppID
from kelso.lib.bundle import load_bundle
from kelso.lib.config import load_config_file
from kelso.lib.config_edit import (
  add_host_volume,
  remove_host_volume,
  set_host_volume,
)
from kelso.lib.configflow import ConfigResponse
from kelso.lib.configflow.app import app_config_request, apply_app_config
from kelso.lib.configflow.route_provider import (
  apply_route_provider_config,
  configurable_kinds,
  resolve_route_provider,
  route_provider_config_request,
)
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle.console import console_command
from kelso.lib.spec import AppSpec

# Bumped when a response shape changes in a way a client would notice. The web
# UI ships separately from the daemon, so it has to be able to tell.
# 3: /activity endpoints; jobs carry a `log` path.
# 4: /snapshots; restore is a job.
# 5: jobs no longer carry `output`; read the file `log` names via /activity.
# 6: activity files are flat under var/logs; /activity/{filename}.
# 7: the `stage` verb is now `install`.
# 8: apps and catalog carry `state` (installed/uninstalled/available)
#    in place of the `staged` and `installed` booleans.
# 9: uninstall and reset are job verbs.
# 10: GET /metrics (gauge history).
# 11: /volumes bytes come from gauges; sizes=1 is gone; host volumes carry bytes.
# 12: restart is a job verb.
# 15: catalog apps carry `warnings` -- unmodelled [run.<unit>.compose] keys.
# 16: /volumes carries `kelso_dirs` (name, description, bytes) in place of
#     the flat `<name>_bytes` keys.
# 17: the `restart` job verb is now `reload`.
# 18: GET /apps/{id}/logs (container logs, tail only).
# 19: snapshot-delete is a job verb.
# 20: WS /apps/{id}/console (a shell in a running unit).
API_VERSION = 20

CtxFactory = Callable[[], KelsoCtx]

# The logs page polls, so an uncapped `tail` would ask docker for an app's
# whole history every couple of seconds.
DEFAULT_TAIL = 200
MAX_TAIL = 2000


class HostVolumeBody(BaseModel):
  """A `[host_volume]` entry, as the UI declares one."""

  model_config = ConfigDict(extra="forbid")

  path: str
  readonly: bool = False
  require_mount: bool = False


class NewHostVolume(HostVolumeBody):
  tag: str


class ConfigValues(BaseModel):
  """A ConfigResponse: what a front end collected for one ConfigRequest."""

  model_config = ConfigDict(extra="forbid")

  values: dict[str, str] = Field(default_factory=dict)


class JobSubmission(BaseModel):
  """What `POST /jobs` accepts. Shape only; `JobRunner.submit` validates."""

  model_config = ConfigDict(extra="forbid")

  verb: str
  args: dict[str, str] = Field(default_factory=dict)


def _ctx(request: Request) -> KelsoCtx:
  """A context per request."""
  return request.app.state.ctx_factory()


def _runner(request: Request) -> JobRunner:
  return request.app.state.jobs


Ctx = Annotated[KelsoCtx, Depends(_ctx)]
Jobs = Annotated[JobRunner, Depends(_runner)]


def _bundle_spec(app: AppID, ctx: KelsoCtx) -> AppSpec:
  """The schema for an app that is not installed yet."""
  return load_bundle(ctx.bundle_path(app)).app_spec()


def _ctx_again(ctx: KelsoCtx) -> KelsoCtx:
  """A context reading config.toml as it is *after* an edit."""
  return KelsoCtx(load_config_file(ctx.config.config_path))


def create_app(ctx_factory: CtxFactory, jobs: JobRunner) -> FastAPI:
  app = FastAPI(
    title="Kelso admin API",
    version=f"{VERSION} (api {API_VERSION})",
    description="Read kelso state, and run kelso verbs as jobs.",
  )
  app.state.ctx_factory = ctx_factory
  app.state.jobs = jobs

  @app.exception_handler(StarletteHTTPException)
  def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

  @app.exception_handler(RequestValidationError)
  def _bad_body(request: Request, exc: RequestValidationError) -> JSONResponse:
    problems = "; ".join(
      f"{'.'.join(str(p) for p in error['loc'] if p != 'body') or 'body'}: "
      f"{error['msg']}"
      for error in exc.errors()
    )
    return JSONResponse({"error": f"Invalid request body: {problems}"}, 400)

  @app.get("/", tags=["meta"])
  @app.get("/version", tags=["meta"])
  def get_version() -> dict:
    return {"kelso": VERSION, "api": API_VERSION}

  @app.get("/apps", tags=["apps"])
  def list_apps(ctx: Ctx) -> dict:
    return {"apps": views.apps_view(ctx)}

  @app.get("/catalog", tags=["catalog"])
  def list_catalog(ctx: Ctx) -> dict:
    return {
      "catalogs": views.catalog_view(ctx),
      "contested": views.contested_view(ctx),
    }

  @app.get("/repos", tags=["catalog"])
  def list_repos(ctx: Ctx) -> dict:
    return {"repos": views.repos_view(ctx)}

  @app.get("/apps/{app_id}", tags=["apps"])
  def get_app(app_id: str, ctx: Ctx) -> dict:
    try:
      resolved = AppID(app_id)
    except ValueError:
      raise HTTPException(404, f"No app {app_id!r}") from None
    try:
      return views.app_view(resolved, ctx)
    except (ValueError, RuntimeError) as e:
      raise HTTPException(404, str(e)) from e

  @app.get("/apps/{app_id}/logs", tags=["apps"])
  def get_app_logs(app_id: str, ctx: Ctx, tail: int = DEFAULT_TAIL) -> dict:
    """An app's container logs, newest `tail` lines. No lock: this is a read."""
    if not 1 <= tail <= MAX_TAIL:
      raise HTTPException(400, f"tail must be between 1 and {MAX_TAIL}")
    try:
      return views.app_logs_view(ctx.resolve_app(app_id), ctx, tail=tail)
    except (ValueError, RuntimeError) as e:
      raise HTTPException(404, str(e)) from e

  @app.websocket("/apps/{app_id}/console")
  async def app_console(websocket: WebSocket, app_id: str, unit: str = "main"):
    """A shell in one of the app's running units. See kelso/daemon/console.py."""
    try:
      ctx = await asyncio.to_thread(ctx_factory)
      cmd = await asyncio.to_thread(
        lambda: console_command(app_id, unit, ctx, announce_pid=True)
      )
    except (ValueError, RuntimeError) as e:
      await console.refuse(websocket, str(e))
      return
    await console.serve(websocket, cmd, ctx)

  @app.get("/apps/{app_id}/config-request", tags=["config"])
  def get_app_config_request(app_id: str, ctx: Ctx) -> dict:
    """The app's `[config]`, with what is on file now."""
    try:
      resolved = ctx.resolve_app(app_id)
      spec = ctx.staged_spec(resolved) or _bundle_spec(resolved, ctx)
    except (ValueError, RuntimeError) as e:
      raise HTTPException(404, str(e)) from e
    return views.config_request_view(app_config_request(spec, ctx))

  @app.post("/apps/{app_id}/config-response", tags=["config"])
  def apply_app_config_response(app_id: str, body: ConfigValues, ctx: Ctx) -> dict:
    """Write the values collected for the app; unchanged ones are skipped."""
    try:
      resolved = ctx.resolve_app(app_id)
      with ctx.locked(f"config {resolved}", resolved):
        spec = ctx.staged_spec(resolved) or _bundle_spec(resolved, ctx)
        apply_app_config(spec, ConfigResponse(values=body.values), ctx)
    except (ValueError, RuntimeError) as e:
      raise HTTPException(400, str(e)) from e
    return views.config_request_view(app_config_request(spec, _ctx_again(ctx)))

  @app.get("/route-providers", tags=["config"])
  def list_route_providers(ctx: Ctx) -> dict:
    return {
      "route_providers": views.route_providers_view(ctx),
      "kinds": configurable_kinds(),
    }

  @app.get("/route-providers/{tag}/config-request", tags=["config"])
  def get_route_provider_config_request(tag: str, ctx: Ctx, kind: str = "") -> dict:
    """What the provider needs; `kind` only for a tag not configured yet."""
    try:
      provider = resolve_route_provider(tag, ctx, kind)
    except ValueError as e:
      raise HTTPException(400, str(e)) from e
    return views.config_request_view(route_provider_config_request(tag, provider, ctx))

  @app.post("/route-providers/{tag}/config-response", tags=["config"])
  def apply_route_provider_config_response(
    tag: str, body: ConfigValues, ctx: Ctx, kind: str = ""
  ) -> dict:
    """Store its secrets and write `[route_provider.<tag>]` to config.toml."""
    try:
      with ctx.kelso_lock(f"route-provider {tag}"):
        provider = resolve_route_provider(tag, ctx, kind)
        apply_route_provider_config(
          tag, provider, ConfigResponse(values=body.values), ctx
        )
    except (ValueError, RuntimeError) as e:
      raise HTTPException(400, str(e)) from e
    return {"route_providers": views.route_providers_view(_ctx_again(ctx))}

  @app.get("/volumes", tags=["volumes"])
  def list_volumes(ctx: Ctx) -> dict:
    """Kelso-managed volumes, with sizes from the last volume-metrics run."""
    return {
      "volumes": views.volumes_view(ctx),
      "kelso_dirs": views.kelso_dirs_view(ctx),
    }

  @app.get("/host-volumes", tags=["host volumes"])
  def list_host_volumes(ctx: Ctx) -> dict:
    return {"host_volumes": views.host_volumes_view(ctx)}

  @app.get("/snapshots", tags=["snapshots"])
  def list_snapshots(ctx: Ctx) -> dict:
    return {"snapshots": views.snapshots_view(ctx)}

  @app.post("/host-volumes", status_code=201, tags=["host volumes"])
  def create_host_volume(body: NewHostVolume, ctx: Ctx) -> dict:
    try:
      with ctx.kelso_lock(f"host-volume add {body.tag}"):
        add_host_volume(
          ctx,
          body.tag,
          body.path,
          readonly=body.readonly,
          require_mount=body.require_mount,
        )
    except (ValueError, RuntimeError) as e:
      raise HTTPException(400, str(e)) from e
    return {"host_volumes": views.host_volumes_view(_ctx_again(ctx))}

  @app.put("/host-volumes/{tag}", tags=["host volumes"])
  def replace_host_volume(tag: str, body: HostVolumeBody, ctx: Ctx) -> dict:
    if tag not in ctx.config.host_volumes:
      raise HTTPException(404, f"No host volume {tag!r}")
    try:
      with ctx.kelso_lock(f"host-volume set {tag}"):
        set_host_volume(
          ctx,
          tag,
          body.path,
          readonly=body.readonly,
          require_mount=body.require_mount,
        )
    except (ValueError, RuntimeError) as e:
      raise HTTPException(400, str(e)) from e
    return {"host_volumes": views.host_volumes_view(_ctx_again(ctx))}

  @app.delete("/host-volumes/{tag}", tags=["host volumes"])
  def delete_host_volume(tag: str, ctx: Ctx) -> dict:
    if tag not in ctx.config.host_volumes:
      raise HTTPException(404, f"No host volume {tag!r}")
    try:
      with ctx.kelso_lock(f"host-volume rm {tag}"):
        remove_host_volume(ctx, tag)
    except (ValueError, RuntimeError) as e:
      raise HTTPException(400, str(e)) from e
    return {"host_volumes": views.host_volumes_view(_ctx_again(ctx))}

  @app.get("/activity", tags=["activity"])
  def list_activity(ctx: Ctx, app: str | None = None, limit: int = 20) -> dict:
    """Recorded unattended runs."""
    try:
      return {"activity": views.activity_view(ctx, app=app, limit=limit)}
    except ValueError as e:
      raise HTTPException(400, str(e)) from e

  @app.get("/activity/{filename}", tags=["activity"])
  def get_activity_log(filename: str, ctx: Ctx) -> dict:
    """One run's output file. The name is a validated filename, not a path."""
    try:
      return views.activity_log_view(ctx, filename)
    except ValueError as e:
      raise HTTPException(404, str(e)) from e

  @app.get("/metrics", tags=["metrics"])
  def get_metrics(ctx: Ctx, prefix: str = "", hours: int = 1) -> dict:
    """Gauge history. `prefix` is matched after `gauge/`."""
    if hours < 1:
      raise HTTPException(400, "hours must be >= 1")
    try:
      return views.metrics_view(ctx, prefix, hours)
    except ValueError as e:
      raise HTTPException(400, str(e)) from e

  @app.get("/jobs", tags=["jobs"])
  def list_jobs(jobs: Jobs) -> dict:
    return {"jobs": jobs.list()}

  @app.post("/jobs", status_code=202, tags=["jobs"])
  def submit_job(submission: JobSubmission, ctx: Ctx, jobs: Jobs) -> dict:
    try:
      return jobs.submit(submission.verb, submission.args, ctx)
    except (ValueError, RuntimeError) as e:
      raise HTTPException(400, str(e)) from e

  @app.get("/jobs/{job_id}", tags=["jobs"])
  def get_job(job_id: str, jobs: Jobs) -> dict:
    job = jobs.get(job_id)
    if job is None:
      raise HTTPException(404, f"No job {job_id!r}")
    return job

  return app
