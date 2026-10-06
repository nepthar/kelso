from kelso.lib.apps import AppID
from kelso.lib.config import NONE_ROUTE_PROVIDER_TAG
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle._common import logger
from kelso.lib.routes import (
  RouteProviderError,
  get_route_provider,
  refuse_foreign_route,
)
from kelso.lib.run_layout import AppRunData, AssignedRoute, loaded_routes


def assigned_routes(
  run_data: AppRunData, ctx: KelsoCtx
) -> list[tuple[str, AssignedRoute, str]]:
  """Routes loaded with a provider other than none: (name, route, provider_tag)."""
  out: list[tuple[str, AssignedRoute, str]] = []
  for route_name, route in run_data.routes.items():
    tag = route.provider
    if not tag or tag == NONE_ROUTE_PROVIDER_TAG:
      logger.debug(
        "route %s has no provider assignment (or none); skipping", route_name
      )
      continue
    out.append((route_name, route, tag))
  return out


def preflight_app_routes(run_data: AppRunData, ctx: KelsoCtx) -> None:
  """Sanity check that assigned routes can be satisfied by their providers."""
  routes = assigned_routes(run_data, ctx)
  if not routes:
    return

  for _, route, tag in routes:
    provider = get_route_provider(ctx, tag)
    owners = provider.route_owners()
    subdomain = route.subdomain
    if subdomain not in owners:
      continue
    owner = owners[subdomain]
    if owner == run_data.app:
      continue
    domain = ctx.config.provider_domain(tag)
    raise refuse_foreign_route(f"{subdomain}.{domain}", owner)


def register_app_routes(run_data: AppRunData, ctx: KelsoCtx) -> None:
  routes = assigned_routes(run_data, ctx)
  if not routes:
    return

  for route_name, route, tag in routes:
    host_port = run_data.routes[route_name].host_port
    if host_port < 0:
      raise RouteProviderError(
        f"route {route_name!r} has no allocated host port; run `kelso load` first"
      )

    provider = get_route_provider(ctx, tag)
    domain = ctx.config.provider_domain(tag)
    provider.register_route(
      run_data.app, host_port, route.subdomain, domain, scheme=route.scheme
    )
    logger.info(
      "registered route %s via %s: %s.%s -> %s://%s:%d",
      route_name,
      tag,
      route.subdomain,
      domain,
      route.scheme,
      ctx.config.kelso_address or "<kelso_address unset>",
      host_port,
    )


def take_down_routes(app: AppID, ctx: KelsoCtx) -> None:
  """Unpublish an app's routes, best effort: a provider that will not answer
  must not stop a reload or a removal."""
  try:
    unregister_app_routes(app, ctx)
  except Exception as e:
    logger.error("failed to unregister routes for %s: %s", app, e)


def unregister_app_routes(app: AppID, ctx: KelsoCtx) -> None:
  """Unpublish what kelsodb records for `app`, skipping what the provider no
  longer has or what belongs to someone else."""
  owners: dict[str, dict[str, str | None]] = {}
  try:
    routes = loaded_routes(app, ctx)
  except TypeError as e:
    logger.error("skipping malformed route records for %s: %s", app, e)
    return
  for route in routes.values():
    tag = route.provider
    if not tag or tag == NONE_ROUTE_PROVIDER_TAG:
      continue

    provider = get_route_provider(ctx, tag)
    domain = ctx.config.provider_domain(tag)
    if tag not in owners:
      owners[tag] = provider.route_owners()
    if route.subdomain not in owners[tag]:
      logger.warning(
        "route %s of %s: no route found for %s.%s at %s; nothing to remove",
        route.name,
        app,
        route.subdomain,
        domain,
        tag,
      )
      continue
    if owners[tag][route.subdomain] != app:
      logger.warning(
        "route %s of %s: %s.%s at %s belongs to %s now; leaving it alone",
        route.name,
        app,
        route.subdomain,
        domain,
        tag,
        owners[tag][route.subdomain] or "something outside kelso",
      )
      continue
    try:
      provider.unregister_route(route.subdomain, domain)
      logger.info(
        "unregistered route %s via %s: %s.%s",
        route.name,
        tag,
        route.subdomain,
        domain,
      )
    except RouteProviderError as e:
      logger.error(
        "failed to unregister route %s for %s: %s",
        route.name,
        app,
        e,
      )
