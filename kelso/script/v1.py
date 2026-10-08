"""Version 1 of what a kelso script can do.

A script is a Python file defining one `KelsoScript` subclass, run by
`kelso script NAME` with kelso's own interpreter. Yours go in `scripts/` in the
kelso root; one there replaces a script kelso ships with the same name.

`Kelso` and `App` are the supported way in. Anything that changes state needs
a lock held first, as kelso's own commands do: `with kelso.lock(...)` for
kelso-wide state, `with app.lock(...)` for an app. Anything else under `kelso`
can be imported too, but may change without notice.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

from tomlkit import TOMLDocument

from kelso.lib.apps import AppID
from kelso.lib.config_edit import edit_config
from kelso.lib.doctor import DoctorPrognosis, diagnose
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle import (
  UNLOAD,
  apply_config_sets,
  load_target,
  reload_app,
  removal_plan,
  rm,
  start,
  stop,
)
from kelso.lib.routes import get_route_provider

_current: ContextVar[KelsoCtx] = ContextVar("kelso_script_ctx")


@contextmanager
def using(ctx: KelsoCtx) -> Iterator[None]:
  """Make `ctx` the kelso that `Kelso` and `App` act on, for this block."""
  token = _current.set(ctx)
  try:
    yield
  finally:
    _current.reset(token)


def _ctx() -> KelsoCtx:
  try:
    return _current.get()
  except LookupError:
    raise RuntimeError(
      "No kelso to act on: run this with `kelso script NAME`"
    ) from None


@dataclass(frozen=True)
class RouteProvider:
  tag: str
  kind: str
  domain: str


class App:
  """One app kelso knows of, loaded or not."""

  def __init__(self, app_id: AppID) -> None:
    self.id = app_id

  def __repr__(self) -> str:
    return f"App({self.id!r})"

  def _require_app_lock(self, msg: str) -> None:
    ctx = _ctx()
    if not (ctx.holds_app_lock(self.id) and ctx.holds_kelso_lock()):
      raise RuntimeError(
        f"{msg} needs {self.id}'s lock: do it inside `with app.lock(...)`"
      )

  @contextmanager
  def lock(self, by: str) -> Iterator[None]:
    """Hold this app's lock, then kelso's, as an app command does."""
    with _ctx().locked(by, self.id):
      yield

  @property
  def loaded(self) -> bool:
    return _ctx().is_loaded(self.id)

  @property
  def running(self) -> bool:
    return _ctx().run_state(self.id).running_count > 0

  @property
  def version(self) -> str | None:
    """The version loaded; None when it is not loaded."""
    spec = _ctx().loaded_spec(self.id)
    return spec.version if spec else None

  def load(self) -> None:
    """Load or re-load from its catalog bundle, restarting it if running."""
    self._require_app_lock(f"Loading {self.id}")
    ctx = _ctx()
    target = load_target(ctx, self.id)
    bundle = target.bundle or ctx.bundle_path(self.id)
    reload_app(self.id, bundle, ctx, bound=target.bound_to)

  def unload(self) -> None:
    """Stop it and remove its loaded copy, keeping its data and config."""
    self._require_app_lock(f"Unloading {self.id}")
    ctx = _ctx()
    rm(removal_plan(self.id, ctx, mode=UNLOAD), ctx)

  def start(self, config: dict[str, str] | None = None) -> None:
    """Start it, loading it first if needed. `config` is stored first, as
    `kelso start --set` would."""
    self._require_app_lock(f"Starting {self.id}")
    ctx = _ctx()
    target = load_target(ctx, self.id)
    sets = list((config or {}).items())
    if sets or not self.loaded:
      bundle = target.bundle or ctx.bundle_path(self.id)
    else:
      bundle = ctx.config.app_run_path(self.id)
    start(self.id, bundle, ctx, sets=sets, bound=target.bound_to)

  def stop(self) -> None:
    self._require_app_lock(f"Stopping {self.id}")
    stop(self.id, _ctx())

  def set_config(self, values: dict[str, str]) -> None:
    """Store config values; a loaded app reads them at its next start."""
    self._require_app_lock(f"Configuring {self.id}")
    spec = _ctx().loaded_spec(self.id)
    if spec is None:
      raise ValueError(f"{self.id} is not loaded; pass its config to start()")
    apply_config_sets(spec, list(values.items()), _ctx())


class Kelso:
  """The kelso root the script runs against."""

  def _require_kelso_lock(self, msg: str) -> None:
    if not _ctx().holds_kelso_lock():
      raise RuntimeError(
        f"{msg} needs the kelso lock: do it inside `with kelso.lock(...)`"
      )

  @contextmanager
  def lock(self, by: str) -> Iterator[None]:
    """Hold the kelso-wide lock."""
    with _ctx().kelso_lock(by):
      yield

  @property
  def id(self) -> str:
    return _ctx().kelso_db.kelso_id()

  @property
  def root(self) -> Path:
    return _ctx().config.kelso_root

  @property
  def address(self) -> str:
    """The LAN address routes point traffic at; empty when unset."""
    return _ctx().config.kelso_address

  # --- config.toml --------------------------------------------------------

  @contextmanager
  def edit_kelso_config(self) -> Iterator[TOMLDocument]:
    """config.toml, to change in place. Written back when the block ends, only
    if it still loads as a kelso config; raises otherwise."""
    self._require_kelso_lock("Editing config.toml")
    with edit_config(_ctx()) as document:
      yield document
    self.reload_config()

  def reload_config(self) -> None:
    """Read config.toml again, keeping any lock this script holds."""
    _current.set(_ctx().reloaded())

  # --- apps ---------------------------------------------------------------

  def apps(self) -> list[App]:
    """Every loaded app."""
    return [App(AppID(app_id)) for app_id in sorted(_ctx().loaded_app_ids())]

  def app(self, app_id: str) -> App:
    """An app by id, whether loaded or only in a catalog."""
    return App(_ctx().resolve_app(app_id))

  # --- secrets ------------------------------------------------------------

  def secret(self, name: str) -> str | None:
    return _ctx().kelso_db.get_secret(name)

  def set_secret(self, name: str, value: str) -> None:
    self._require_kelso_lock(f"Setting secret {name}")
    _ctx().kelso_db.set_secret(name, value)

  # --- routes -------------------------------------------------------------

  def route_providers(self) -> list[RouteProvider]:
    return [
      RouteProvider(tag, entry.kind, entry.domain)
      for tag, entry in sorted(_ctx().config.route_providers.items())
    ]

  @property
  def default_route_provider(self) -> str:
    return _ctx().config.default_route_provider

  def check_route_provider(self, tag: str) -> list[str]:
    """What is wrong with a route provider; empty when it is usable."""
    return get_route_provider(_ctx(), tag).validate()

  # --- health -------------------------------------------------------------

  def diagnose(self) -> DoctorPrognosis:
    """What `kelso script doctor` reports."""
    self._require_kelso_lock("Diagnosing")
    return diagnose(_ctx())


class KelsoScript:
  desc = ""

  def run(self, args: list[str]) -> None:
    raise NotImplementedError

  @property
  def kelso(self) -> Kelso:
    return Kelso()
