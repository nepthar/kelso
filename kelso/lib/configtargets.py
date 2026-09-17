"""Resolve a target string to the thing it configures."""

from __future__ import annotations

from typing import TYPE_CHECKING

from kelso.lib.appconfig import TARGET_PREFIX as APP_PREFIX
from kelso.lib.appconfig import AppTarget
from kelso.lib.configreq import HasConfig
from kelso.lib.providerconfig import TARGET_PREFIX as PROVIDER_PREFIX
from kelso.lib.providerconfig import ProviderTarget

if TYPE_CHECKING:
  from kelso.lib.kelso import KelsoCtx


def app_config_target(app: str, ctx: KelsoCtx) -> AppTarget:
  """The config target for an installed app, or one still in the catalog."""
  from kelso.lib.bundle import load_bundle

  resolved = ctx.resolve_app(app)
  spec = ctx.staged_spec(resolved)
  if spec is None:
    spec = load_bundle(ctx.bundle_path(resolved)).app_spec()
  return AppTarget(spec=spec, ctx=ctx)


def resolve_target(target: str, ctx: KelsoCtx, *, kind: str = "") -> HasConfig:
  """Build the HasConfig named by `target`.

  `kind` is only consulted for a route provider tag that is not configured yet;
  an existing one is read from config.toml.
  """
  if target.startswith(APP_PREFIX):
    return app_config_target(target.removeprefix(APP_PREFIX), ctx)

  if target.startswith(PROVIDER_PREFIX):
    tag = target.removeprefix(PROVIDER_PREFIX)
    existing = ctx.config.route_providers.get(tag)
    resolved_kind = kind or (existing.kind if existing else "")
    if not resolved_kind:
      raise ValueError(
        f"Route provider {tag!r} is not configured yet, so it needs a kind; pass --kind"
      )
    if existing and kind and kind != existing.kind:
      raise ValueError(
        f"Route provider {tag!r} is already {existing.kind!r}; remove it from "
        f"config.toml before configuring it as {kind!r}"
      )
    return ProviderTarget(kind=resolved_kind, tag=tag, ctx=ctx)

  raise ValueError(
    f"Unknown config target {target!r}; expected {APP_PREFIX}<app_id> "
    f"or {PROVIDER_PREFIX}<tag>"
  )
