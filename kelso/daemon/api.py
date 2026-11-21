import asyncio
import socket
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from kelso import VERSION
from kelso.daemon import shell
from kelso.jobs import JobRunner
from kelso.lib import views
from kelso.lib.apps import AppID
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
from kelso.lib.lifecycle.routes import remove_published_route
from kelso.lib.lifecycle.shell import shell_command
from kelso.lib.lifecycle.volumes import remove_orphaned_volume
from kelso.lib.net import decide_many, net_view
from kelso.lib.util import Identifier, validate_identifier

# Bumped when a response shape changes in a way a client would notice: the web
# UI ships separately from the daemon, and checks it.
API_VERSION = 2

CtxFactory = Callable[[], KelsoCtx]

# The logs page polls, so an uncapped `tail` would ask docker for an app's
# whole history every couple of seconds.
DEFAULT_TAIL = 200
MAX_TAIL = 2000


class HostVolumeBody(BaseModel):
  model_config = ConfigDict(extra="forbid")

  path: str
  readonly: bool = False
  require_mount: bool = False


class NewHostVolume(HostVolumeBody):
  tag: str


class ConfigValues(BaseModel):
  model_config = ConfigDict(extra="forbid")

  values: dict[str, str] = Field(default_factory=dict)


class NetDecisions(BaseModel):
  model_config = ConfigDict(extra="forbid")

  decisions: dict[str, str | None]


class JobSubmission(BaseModel):
  model_config = ConfigDict(extra="forbid")

  verb: str
  args: dict[str, str] = Field(default_factory=dict)
  # Who is asking, as the activity log records it; kelso-ui says `kelso_ui`.
  started_by: Identifier


def _ctx(request: Request) -> KelsoCtx:
  return request.app.state.ctx_factory()


def _runner(request: Request) -> JobRunner:
  return request.app.state.jobs


Ctx = Annotated[KelsoCtx, Depends(_ctx)]
Jobs = Annotated[JobRunner, Depends(_runner)]


def _started_by_refusal(started_by: str) -> str | None:
  if not started_by:
    return "started_by is required, e.g. ?started_by=kelso_ui"
  try:
    validate_identifier(started_by)
  except ValueError as e:
    return f"started_by: {e}"
  return None


def _ctx_again(ctx: KelsoCtx) -> KelsoCtx:
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
  def get_version(ctx: Ctx) -> dict:
    return {
      "kelso": VERSION,
      "api": API_VERSION,
      "hostname": socket.gethostname(),
      "kelso_root": str(ctx.config.kelso_root),
    }

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
    if not 1 <= tail <= MAX_TAIL:
      raise HTTPException(400, f"tail must be between 1 and {MAX_TAIL}")
    try:
      return views.app_logs_view(ctx.resolve_app_id(app_id), ctx, tail=tail)
    except (ValueError, RuntimeError) as e:
      raise HTTPException(404, str(e)) from e

  @app.get("/apps/{app_id}/net", tags=["apps"])
  def get_app_net(app_id: str, ctx: Ctx) -> dict:
    try:
      spec = ctx.require_loaded_spec(ctx.resolve_app_id(app_id))
    except (ValueError, RuntimeError) as e:
      raise HTTPException(404, str(e)) from e
    try:
      return net_view(spec, ctx)
    except RuntimeError as e:
      raise HTTPException(502, str(e)) from e

  @app.post("/apps/{app_id}/net", tags=["apps"])
  def post_app_net(app_id: str, body: NetDecisions, ctx: Ctx) -> dict:
    try:
      resolved = ctx.resolve_app_id(app_id)
      with ctx.locked(f"net {resolved}", resolved):
        spec = ctx.require_loaded_spec(resolved)
        decide_many(spec, body.decisions, ctx)
        return net_view(spec, ctx)
    except (ValueError, RuntimeError) as e:
      raise HTTPException(400, str(e)) from e

  @app.websocket("/apps/{app_id}/shell")
  async def app_shell(
    websocket: WebSocket, app_id: str, started_by: str = "", unit: str = "main"
  ):
    refusal = shell.network_refusal(websocket) or _started_by_refusal(started_by)
    if refusal:
      await shell.refuse(websocket, refusal)
      return
    try:
      ctx = await asyncio.to_thread(ctx_factory)
      cmd = await asyncio.to_thread(
        lambda: shell_command(app_id, unit, ctx, announce_pid=True)
      )
    except (ValueError, RuntimeError) as e:
      await shell.refuse(websocket, str(e))
      return
    await shell.serve(websocket, cmd, ctx, started_by=started_by)

  @app.websocket("/host/shell")
  async def host_shell(websocket: WebSocket, started_by: str = ""):
    refusal = shell.network_refusal(websocket) or _started_by_refusal(started_by)
    if refusal:
      await shell.refuse(websocket, refusal)
      return
    ctx = await asyncio.to_thread(ctx_factory)
    await shell.serve_host(websocket, ctx, started_by=started_by)

  @app.get("/apps/{app_id}/config-request", tags=["config"])
  def get_app_config_request(app_id: str, ctx: Ctx) -> dict:
    try:
      resolved = ctx.resolve_app_id(app_id)
      spec = ctx.loaded_spec(resolved) or ctx.bundle_spec(resolved)
    except (ValueError, RuntimeError) as e:
      raise HTTPException(404, str(e)) from e
    return views.config_request_view(app_config_request(spec, ctx))

  @app.post("/apps/{app_id}/config-response", tags=["config"])
  def apply_app_config_response(app_id: str, body: ConfigValues, ctx: Ctx) -> dict:
    try:
      resolved = ctx.resolve_app_id(app_id)
      with ctx.locked(f"config {resolved}", resolved):
        spec = ctx.loaded_spec(resolved) or ctx.bundle_spec(resolved)
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
    try:
      provider = resolve_route_provider(tag, ctx, kind)
    except ValueError as e:
      raise HTTPException(400, str(e)) from e
    return views.config_request_view(route_provider_config_request(tag, provider, ctx))

  @app.post("/route-providers/{tag}/config-response", tags=["config"])
  def apply_route_provider_config_response(
    tag: str, body: ConfigValues, ctx: Ctx, kind: str = ""
  ) -> dict:
    try:
      with ctx.kelso_lock(f"route-provider {tag}"):
        provider = resolve_route_provider(tag, ctx, kind)
        apply_route_provider_config(
          tag, provider, ConfigResponse(values=body.values), ctx
        )
    except (ValueError, RuntimeError) as e:
      raise HTTPException(400, str(e)) from e
    return {"route_providers": views.route_providers_view(_ctx_again(ctx))}

  @app.get("/route-providers/{tag}/routes", tags=["routes"])
  def list_published_routes(tag: str, ctx: Ctx) -> dict:
    if tag not in ctx.config.route_providers:
      raise HTTPException(404, f"No route provider {tag!r}")
    try:
      return {"routes": views.published_routes_view(ctx, tag)}
    except (ValueError, RuntimeError) as e:
      raise HTTPException(502, str(e)) from e

  @app.delete("/route-providers/{tag}/routes/{subdomain}", tags=["routes"])
  def delete_published_route(tag: str, subdomain: str, ctx: Ctx) -> dict:
    if tag not in ctx.config.route_providers:
      raise HTTPException(404, f"No route provider {tag!r}")
    try:
      with ctx.kelso_lock(f"route remove {subdomain} {tag}"):
        remove_published_route(tag, subdomain, ctx)
    except (ValueError, RuntimeError) as e:
      raise HTTPException(400, str(e)) from e
    return {"removed": subdomain}

  @app.get("/host", tags=["host"])
  def get_host(ctx: Ctx) -> dict:
    return views.host_view(ctx)

  @app.get("/volumes", tags=["volumes"])
  def list_volumes(ctx: Ctx) -> dict:
    return {
      "volumes": views.volumes_view(ctx),
      "kelso_dirs": views.kelso_dirs_view(ctx),
      "volume_roots": views.volume_roots_view(ctx),
    }

  @app.delete("/volumes/{app_id}/{name}", tags=["volumes"])
  def delete_orphaned_volume(app_id: str, name: str, ctx: Ctx) -> dict:
    try:
      app = AppID(app_id)
      with ctx.locked(f"volume rm {app} {name}", app):
        remove_orphaned_volume(app, name, ctx)
    except (ValueError, RuntimeError) as e:
      raise HTTPException(400, str(e)) from e
    return {"volumes": views.volumes_view(ctx)}

  @app.get("/host-volumes", tags=["host volumes"])
  def list_host_volumes(ctx: Ctx) -> dict:
    return {"host_volumes": views.host_volumes_view(ctx)}

  @app.get("/backups", tags=["backups"])
  def list_backups(ctx: Ctx) -> dict:
    return views.backups_view(ctx)

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

  @app.get("/cron", tags=["cron"])
  def list_cron(ctx: Ctx) -> dict:
    return {"cron": views.cron_view(ctx)}

  @app.get("/activity", tags=["activity"])
  def list_activity(
    ctx: Ctx, app: str | None = None, verb: str | None = None, limit: int = 20
  ) -> dict:
    try:
      return {"activity": views.activity_view(ctx, app=app, verb=verb, limit=limit)}
    except ValueError as e:
      raise HTTPException(400, str(e)) from e

  @app.get("/activity/{filename}", tags=["activity"])
  def get_activity_log(filename: str, ctx: Ctx) -> dict:
    try:
      return views.activity_log_view(ctx, filename)
    except ValueError as e:
      raise HTTPException(404, str(e)) from e

  @app.get("/metrics", tags=["metrics"])
  def get_metrics(ctx: Ctx, prefix: str = "", hours: int = 1) -> dict:
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
      return jobs.submit(
        submission.verb, submission.args, ctx, started_by=submission.started_by
      )
    except (ValueError, RuntimeError) as e:
      raise HTTPException(400, str(e)) from e

  @app.get("/jobs/{job_id}", tags=["jobs"])
  def get_job(job_id: str, jobs: Jobs) -> dict:
    job = jobs.get(job_id)
    if job is None:
      raise HTTPException(404, f"No job {job_id!r}")
    return job

  return app
