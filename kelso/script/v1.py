"""Version 1 of what a kelso script can do.

A script is a Python file defining one `KelsoScript` subclass, run by
`kelso script NAME` with kelso's own interpreter. Yours go in `scripts/` in the
kelso root; one there replaces a script kelso ships with the same name.

`Kelso` is the supported way in. Anything else under `kelso` can be imported
too, but may change without notice.
"""

from dataclasses import dataclass
from pathlib import Path

from kelso.lib.config import load_config, load_config_file
from kelso.lib.config_edit import (
  set_default_route_provider,
  set_kelso_address,
  set_route_provider,
)
from kelso.lib.configflow.route_provider import resolve_route_provider, secret_ref
from kelso.lib.doctor import DoctorPrognosis, diagnose
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle import apply_config_sets, load_target, start, stop
from kelso.lib.observations import LOADED
from kelso.lib.routes import get_route_provider


@dataclass(frozen=True)
class App:
  id: str
  version: str | None
  running: bool


@dataclass(frozen=True)
class RouteProvider:
  tag: str
  kind: str
  domain: str


class Kelso:
  """One kelso root. Each method takes the locks the matching command would."""

  def __init__(self, ctx: KelsoCtx) -> None:
    self._ctx = ctx

  def _reload(self) -> None:
    """After writing config.toml: read it again."""
    self._ctx = KelsoCtx(load_config_file(self._ctx.config.config_path))

  @property
  def id(self) -> str:
    return self._ctx.kelso_db.kelso_id()

  @property
  def root(self) -> Path:
    return self._ctx.config.kelso_root

  @property
  def address(self) -> str:
    """The LAN address routes point traffic at; empty when unset."""
    return self._ctx.config.kelso_address

  def set_address(self, address: str) -> None:
    with self._ctx.kelso_lock("script set address"):
      set_kelso_address(self._ctx, address)
    self._reload()

  # --- apps ---------------------------------------------------------------

  def apps(self) -> list[App]:
    """Every loaded app."""
    return [
      App(str(o.app_id), o.loaded_version, o.running_count > 0)
      for o in self._ctx.observations()
      if o.state == LOADED
    ]

  def app(self, app_id: str) -> App | None:
    """A loaded app, or None."""
    return next((a for a in self.apps() if a.id == app_id), None)

  def start(self, app_id: str, config: dict[str, str] | None = None) -> None:
    """Start an app from the catalog, loading it first if needed. `config` is
    stored first, as `kelso start --set` would."""
    target = load_target(self._ctx, app_id)
    app = target.app_id
    sets = list((config or {}).items())
    if sets or not self._ctx.is_loaded(app):
      bundle = target.bundle or self._ctx.bundle_path(app)
    else:
      bundle = self._ctx.config.app_run_path(app)
    with self._ctx.locked(f"script start {app}", app):
      start(app, bundle, self._ctx, sets=sets, bound=target.bound_to)

  def stop(self, app_id: str) -> None:
    app = self._ctx.resolve_app(app_id)
    with self._ctx.locked(f"script stop {app}", app):
      stop(app, self._ctx)

  def set_config(self, app_id: str, values: dict[str, str]) -> None:
    """Store config values for a loaded app; it reads them at its next start."""
    app = self._ctx.resolve_app(app_id)
    spec = self._ctx.loaded_spec(app)
    if spec is None:
      raise ValueError(f"{app} is not loaded; pass its config to start instead")
    with self._ctx.locked(f"script config {app}", app):
      apply_config_sets(spec, list(values.items()), self._ctx)

  # --- secrets ------------------------------------------------------------

  def secret(self, name: str) -> str | None:
    return self._ctx.kelso_db.get_secret(name)

  def set_secret(self, name: str, value: str) -> None:
    with self._ctx.kelso_lock("script secret"):
      self._ctx.kelso_db.set_secret(name, value)

  # --- routes -------------------------------------------------------------

  def route_providers(self) -> list[RouteProvider]:
    return [
      RouteProvider(tag, entry.kind, entry.domain)
      for tag, entry in sorted(self._ctx.config.route_providers.items())
    ]

  @property
  def default_route_provider(self) -> str:
    return self._ctx.config.default_route_provider

  def set_route_provider(
    self,
    tag: str,
    kind: str,
    domain: str,
    args: dict[str, str] | None = None,
    secrets: dict[str, str] | None = None,
  ) -> None:
    """Write `[route_provider.<tag>]`, replacing one already there. Each of
    `secrets` is stored in kelsodb, and the block names it as `<name>_secret`."""
    resolve_route_provider(tag, self._ctx, kind)
    secrets = secrets or {}
    refs = {f"{name}_secret": secret_ref(tag, name) for name in secrets}
    with self._ctx.kelso_lock(f"script route provider {tag}"):
      # config.toml first: it refuses a bad block, and no secret is left behind.
      set_route_provider(
        self._ctx, tag, kind=kind, domain=domain, args={**(args or {}), **refs}
      )
      for name, value in secrets.items():
        self._ctx.kelso_db.set_secret(secret_ref(tag, name), value)
    self._reload()

  def set_default_route_provider(self, tag: str) -> None:
    with self._ctx.kelso_lock("script default route provider"):
      set_default_route_provider(self._ctx, tag)
    self._reload()

  def check_route_provider(self, tag: str) -> list[str]:
    """What is wrong with a route provider; empty when it is usable."""
    return get_route_provider(self._ctx, tag).validate()

  # --- health -------------------------------------------------------------

  def diagnose(self) -> DoctorPrognosis:
    """What `kelso script doctor` reports."""
    with self._ctx.kelso_lock("script diagnose"):
      return diagnose(self._ctx)


class KelsoScript:
  desc = ""

  def run(self, args: list[str]) -> None:
    raise NotImplementedError

  def kelso(self) -> Kelso:
    """A new view of this kelso root, its config read afresh."""
    config = load_config()
    if not config:
      raise ValueError("Kelso is not initialized; run `kelso init` first")
    return Kelso(KelsoCtx(config))
