"""`network_mode = "restricted"`: the app behind kproxy, from manifest to compose.

The kproxy program has its own tests in kproxy/; these cover what kelso
generates to run it, and that loading allocates and releases a subnet.
"""

import dataclasses

import pytest
import yaml

from kelso.lib import kproxy
from kelso.lib.config import load_config
from kelso.lib.kelso import KelsoCtx
from kelso.lib.manifest import ConfigError
from kelso.lib.run_layout import make_compose_dict
from tests.conftest import spec_of
from tests.test_compose import run_data

RESTRICTED = """\
[app]
version = "1"
network_mode = "restricted"

[adv_config]
subdomain = { default = "demo" }

[endpoints]
weather = ["api.weather.com", "Weather.COM."]
fonts   = ["fonts.gstatic.com"]

[run.main]
image = "nginx:1.27"
compose = { depends_on = ["db"] }

[run.main.routes]
main = { port = "8080" }
dns  = { port = "53/udp" }

[run.db]
image = "postgres:16"
"""


def test_restricted_compose_puts_units_behind_kproxy(tmp_path):
  spec = spec_of(tmp_path, RESTRICTED)
  data = dataclasses.replace(
    run_data(spec, host_ports={"main": 41000, "dns": 41001}),
    subnet="10.250.3.0/24",
    kproxy_source=kproxy.source_dir(),
  )
  compose = make_compose_dict(spec, data)
  main, db, proxy = (compose["services"][n] for n in ("main", "db", "kproxy"))

  for unit in (main, db):
    assert unit["networks"] == ["internal"]
    assert unit["dns"] == ["10.250.3.2"]
    assert "ports" not in unit
  assert main["depends_on"] == {
    "db": {"condition": "service_started"},
    "kproxy": {"condition": "service_healthy"},
  }
  assert db["depends_on"] == {"kproxy": {"condition": "service_healthy"}}

  # Routes are published on kproxy, in route-name order, and relayed in.
  assert proxy["ports"] == ["41001:20000/udp", "41000:20001"]
  assert proxy["command"][-4:] == [
    "-ingress",
    "20000=main:53/udp",
    "-ingress",
    "20001=main:8080",
  ]
  assert proxy["networks"] == {
    "internal": {"ipv4_address": "10.250.3.2"},
    "egress": {},
  }
  assert proxy["build"] == {"context": str(kproxy.source_dir())}
  assert proxy["image"].startswith("kelso-kproxy:")
  assert compose["networks"]["internal"] == {
    "internal": True,
    "ipam": {"config": [{"subnet": "10.250.3.0/24", "ip_range": "10.250.3.128/25"}]},
  }


def test_rules_file_allows_every_endpoint_and_denies_the_rest(tmp_path):
  spec = spec_of(tmp_path, RESTRICTED)
  assert kproxy.rules_text(spec.endpoints).splitlines()[1:] == [
    "default deny",
    "allow api.weather.com",
    "allow fonts.gstatic.com",
    "allow weather.com",
  ]


@pytest.mark.parametrize(
  ("change", "message"),
  [
    (("[run.db]", "[run.kproxy]"), "uses that name for its proxy"),
    (
      (
        'image = "postgres:16"',
        'image = "postgres:16"\ncompose = { dns = ["1.1.1.1"] }',
      ),
      "manages dns",
    ),
    (
      ('fonts   = ["fonts.gstatic.com"]', 'fonts = ["10.0.0.1"]'),
      "is not a domain name",
    ),
    (
      ('network_mode = "restricted"', 'network_mode = "host"'),
      "can't limit outbound traffic",
    ),
  ],
)
def test_restricted_manifest_mistakes_are_refused(tmp_path, change, message):
  manifest = RESTRICTED.replace(*change)
  if "host" in change[1]:
    manifest = manifest.replace(manifest[manifest.index("[run.main.routes]") :], "")
  with pytest.raises(ConfigError, match=message):
    spec_of(tmp_path, manifest)


def test_endpoints_are_allowed_but_unused_in_open_mode(tmp_path):
  spec = spec_of(tmp_path, RESTRICTED.replace('network_mode = "restricted"', ""))
  compose = make_compose_dict(spec, run_data(spec))
  assert "kproxy" not in compose["services"]
  assert "networks" not in compose


def _write(kelso_env, manifest: str) -> None:
  bundle = kelso_env.local_repo / "restricted-demo.klso"
  bundle.mkdir(parents=True, exist_ok=True)
  (bundle / "manifest.toml").write_text(manifest)


def _subnet(app: str) -> str | None:
  cfg = load_config()
  assert cfg is not None
  return KelsoCtx(cfg).kelso_db.get_subnet(app)


def test_loading_allocates_a_subnet_that_reloading_keeps(kelso_env):
  _write(kelso_env, RESTRICTED)
  assert kelso_env.run("load", "restricted-demo").returncode == 0
  subnet = _subnet("restricted-demo")
  assert subnet in {str(n) for n in kproxy.subnets()}

  run_dir = kelso_env.run_root / "restricted-demo"
  compose = yaml.safe_load((run_dir / "compose.yml").read_text())
  assert compose["services"]["main"]["dns"] == [kproxy.proxy_address(subnet)]
  assert "allow api.weather.com" in (run_dir / "kproxy" / "rules.txt").read_text()

  assert kelso_env.run("load", "restricted-demo").returncode == 0
  assert _subnet("restricted-demo") == subnet


def test_an_app_that_stops_being_restricted_gives_its_subnet_back(kelso_env):
  _write(kelso_env, RESTRICTED)
  assert kelso_env.run("load", "restricted-demo").returncode == 0
  _write(kelso_env, RESTRICTED.replace('network_mode = "restricted"', ""))
  assert kelso_env.run("load", "restricted-demo").returncode == 0

  assert _subnet("restricted-demo") is None
  assert not (kelso_env.run_root / "restricted-demo" / "kproxy").exists()
