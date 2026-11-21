from kelso.lib.describe import describe
from kelso.lib.run_layout import load_run_data
from kelso.lib.spec import AppSpec
from tests.conftest import spec_of
from tests.test_api import ctx

MANIFEST = """\
[app]
version = "0.1.0"

[config]
subdomain = { default = "probe" }

[volumes]
state = { kind = "data" }
files = { kind = "host", readonly = true }

[run.main]
image = "alpine:latest"
volumes = { state = "/state", files = "/files" }

[run.main.routes]
main = { port = "8080" }

[run.main.ports]
dyn = { port = "9000/udp" }
fixed = { port = "41999:9001" }
"""


def a_bundle(kelso_env):
  bundle = kelso_env.root / "repos" / "local" / "probe.klso"
  bundle.mkdir()
  (bundle / "manifest.toml").write_text(MANIFEST)


def test_a_loaded_app(kelso_env):
  a_bundle(kelso_env)
  assert kelso_env.run("load", "probe").returncode == 0
  (kelso_env.root / "external-data").mkdir()
  assert kelso_env.run("config", "probe", "volume.files=media").returncode == 0
  c = ctx()
  spec = c.loaded_spec(c.resolve_app_id("probe"))
  d = describe(spec, load_run_data(spec, c), c)

  (route,) = d.routes
  assert route.host_port is not None and route.host_port > 0
  assert route.host_url == f"http://localhost:{route.host_port}"
  assert route.providers == (("web", "https://probe.kelso.localhost"),)
  assert route.url == "https://probe.kelso.localhost"

  dyn, fixed = d.ports
  assert dyn.proto == "udp" and dyn.declared_host_port is None
  assert dyn.host_port is not None and dyn.host_port > 0
  assert fixed.host_port == fixed.declared_host_port == 41999

  state, files = d.volumes
  assert state.path == state.default_path
  assert state.path == str(kelso_env.root / "volumes" / "data" / "probe" / "state")
  assert files.path == str(kelso_env.root / "external-data")
  assert files.bind == "media" and files.default_path is None


def test_a_bundle_that_is_not_loaded(kelso_env, tmp_path):
  d = describe(spec_of(tmp_path, MANIFEST, "probe"), None, ctx())

  (route,) = d.routes
  assert (route.host_port, route.host_url, route.url) == (None, None, None)
  assert route.providers == ()
  assert [p.host_port for p in d.ports] == [None, None]
  assert [p.declared_host_port for p in d.ports] == [None, 41999]
  state, files = d.volumes
  assert state.path is None
  assert state.default_path == str(
    kelso_env.root / "volumes" / "data" / "probe" / "state"
  )


def test_an_unbound_host_volume_has_no_path(kelso_env):
  a_bundle(kelso_env)
  assert kelso_env.run("load", "probe").returncode == 0
  c = ctx()
  spec = c.loaded_spec(c.resolve_app_id("probe"))
  files = describe(spec, load_run_data(spec, c), c).volumes[1]
  assert (files.kind, files.path, files.bind) == ("host", None, None)


def test_a_host_network_route_is_its_container_port(kelso_env, tmp_path, monkeypatch):
  spec = spec_of(tmp_path, MANIFEST, "probe")
  monkeypatch.setattr(AppSpec, "network_mode", property(lambda self: "host"))
  (route,) = describe(spec, None, ctx()).routes
  assert route.host_url == "http://localhost:8080"
  assert route.host_port is None
