"""`kelso net`: decisions kelso keeps, and what it reads from a running kproxy."""

import json

from kelso.lib import kproxy
from tests.test_restricted import RESTRICTED, _write

APP = "restricted-demo"


def _rules(kelso_env) -> str:
  return (kelso_env.run_root / APP / "kproxy" / "rules.txt").read_text()


def _kproxy_answers(kelso_env, answers: dict) -> None:
  path = kelso_env.docker_state.with_name("fake-kproxy.json")
  path.write_text(json.dumps({APP: answers}))


def _admin_calls(kelso_env) -> list[list[str]]:
  calls = [
    json.loads(line)["args"] for line in kelso_env.docker_log.read_text().splitlines()
  ]
  return [c[8:] for c in calls if c[:4] == ["compose", "exec", "-T", "kproxy"]]


def test_rules_json_is_the_rules_file_in_long_form():
  text = kproxy.rules_text(
    ["tmdb.org"],
    {"nas": "10.0.0.5:5000"},
    [("main", 41000, "main", 8080, "tcp")],
    default="ask",
    denied=["ads.example.com"],
  )
  assert kproxy.rules_json(text) == {
    "default": "ask",
    "rules": [
      {"kind": "http", "port": 80, "name": "*.ads.example.com", "verdict": "deny"},
      {"kind": "http", "port": 80, "name": "*.tmdb.org", "verdict": "allow"},
      {
        "kind": "http",
        "port": 80,
        "name": "nas",
        "verdict": "allow",
        "dest": "10.0.0.5:5000",
      },
      {"kind": "tls", "port": 443, "name": "*.ads.example.com", "verdict": "deny"},
      {"kind": "tls", "port": 443, "name": "*.tmdb.org", "verdict": "allow"},
      {
        "kind": "tcp",
        "port": kproxy.ingress_port(0),
        "ingress": True,
        "name": "main",
        "verdict": "allow",
        "dest": "main:8080",
      },
    ],
  }


def test_decisions_are_kept_and_written_into_the_rules(kelso_env):
  _write(kelso_env, RESTRICTED)
  assert kelso_env.run("load", APP).returncode == 0

  allowed = kelso_env.run("net", APP, "allow", "Image.TMDB.org.")
  assert allowed.returncode == 0, allowed.stderr
  assert "allowed image.tmdb.org and its subdomains" in allowed.stdout
  assert kelso_env.run("net", APP, "deny", "weather.com").returncode == 0
  assert _admin_calls(kelso_env) == []  # not running: nothing to push to

  assert kelso_env.run("load", APP).returncode == 0
  rules = _rules(kelso_env)
  assert "https *.image.tmdb.org\n" in rules
  # Your deny beats the manifest's web host.
  assert "https *.weather.com deny\n" in rules
  assert "https *.weather.com\n" not in rules

  assert kelso_env.run("net", APP, "forget", "weather.com").returncode == 0
  assert kelso_env.run("load", APP).returncode == 0
  assert "https *.weather.com\n" in _rules(kelso_env)


def test_a_decision_reaches_a_running_kproxy_at_once(kelso_env):
  _write(kelso_env, RESTRICTED)
  assert kelso_env.run("start", APP).returncode == 0

  assert kelso_env.run("net", APP, "allow", "tmdb.org").returncode == 0
  (call,) = _admin_calls(kelso_env)
  assert call[:2] == ["PUT", "/rules"]
  body = json.loads(call[2])
  assert body["default"] == "deny"
  assert {"kind": "tls", "port": 443, "name": "*.tmdb.org", "verdict": "allow"} in body[
    "rules"
  ]


def test_net_shows_pending_and_traffic_by_domain(kelso_env):
  _write(kelso_env, RESTRICTED)
  assert kelso_env.run("config", APP, "--network", "ask").returncode == 0
  assert kelso_env.run("start", APP).returncode == 0
  assert kelso_env.run("net", APP, "deny", "ads.example.com").returncode == 0
  _kproxy_answers(
    kelso_env,
    {
      "GET /pending": [{"domain": "tmdb.org", "hosts": {"image.tmdb.org": {}}}],
      "GET /stats": {
        "weather.com": {
          "https": {
            "conns": 3,
            "allowed": 3,
            "denied": 0,
            "bytes_in": 2048,
            "bytes_out": 100,
            "last_seen": "2026-11-15T20:31:02.5Z",
          },
        },
        "main": {"ingress tcp :20000": {"conns": 9}},
      },
    },
  )

  shown = kelso_env.run("net", APP)
  assert shown.returncode == 0, shown.stderr
  out = shown.stdout
  assert "ask: reaches these" in out
  assert "tmdb.org  image.tmdb.org" in out
  assert f"kelso net {APP} allow|deny tmdb.org" in out
  lines = {line.split()[0]: line for line in out.splitlines() if line.strip()}
  assert "listed (weather)" in lines["weather.com"]
  assert "2026-11-15 20:31:02" in lines["weather.com"]
  assert "denied by you" in lines["ads.example.com"]
  assert "main" not in lines

  ps = kelso_env.run("ps")
  assert f"1 domain waiting: kelso net {APP}" in ps.stdout


def test_an_open_app_has_nothing_to_show(kelso_env):
  _write(kelso_env, RESTRICTED.replace('network_mode = "restrict"', ""))
  assert kelso_env.run("start", APP).returncode == 0
  shown = kelso_env.run("net", APP)
  assert shown.returncode == 0
  assert f"kelso config {APP} --network watch" in shown.stdout
  assert _admin_calls(kelso_env) == []


def test_a_bad_domain_or_half_a_decision_is_refused(kelso_env):
  _write(kelso_env, RESTRICTED)
  assert kelso_env.run("load", APP).returncode == 0
  assert "is not a domain name" in kelso_env.run("net", APP, "allow", "1.2.3.4").stderr
  assert "Give both" in kelso_env.run("net", APP, "allow").stderr


LAST_RUN = {
  "stats": {
    "weather.com": {"https": {"conns": 2, "allowed": 2, "bytes_in": 10}},
    "example-site.org": {"https": {"conns": 1, "allowed": 1, "bytes_in": 5}},
    "nas": {"http": {"conns": 1, "allowed": 1, "bytes_in": 7}},
    "main": {"ingress tcp :20000": {"conns": 4}},
  },
  "pending": [{"domain": "held.net", "hosts": {"held.net": {}}}],
}


def test_dev_says_what_it_reached_when_it_stops(kelso_env):
  nas = 'nas = { kind = "http", address = "10.0.0.5:80" }\n'
  _write(kelso_env, RESTRICTED.replace("[run.main]", nas + "\n[run.main]", 1))
  assert kelso_env.run("load", APP).returncode == 0
  assert kelso_env.run("config", APP, "--network", "watch").returncode == 0
  _kproxy_answers(kelso_env, {"stats-file": LAST_RUN})

  ran = kelso_env.run("dev", str(kelso_env.local_repo / f"{APP}.klso"))
  assert ran.returncode == 0, ran.stderr
  out = ran.stdout
  assert "watch: the domains it reaches are listed when it stops" in out
  assert "Traffic in its last run:" in out
  assert "Still waiting when it stopped: held.net" in out
  assert 'example_site = { kind = "web", hosts = ["example-site.org"] }' in out
  assert 'hosts = ["weather.com"]' not in out  # already listed
  assert 'hosts = ["nas"]' not in out

  shown = kelso_env.run("net", APP)
  assert "Not running" in shown.stdout
  assert "example-site.org" in shown.stdout


def test_a_restrict_dev_run_says_unlisted_names_are_not_counted(kelso_env):
  _write(kelso_env, RESTRICTED)
  assert kelso_env.run("load", APP).returncode == 0
  _kproxy_answers(kelso_env, {"stats-file": {"stats": {}, "pending": []}})
  ran = kelso_env.run("dev", str(kelso_env.local_repo / f"{APP}.klso"))
  assert "fails at its DNS lookup" in ran.stdout
  assert f"kelso config {APP} --network watch" in ran.stdout


def _client():
  from fastapi.testclient import TestClient

  from kelso.daemon.api import create_app
  from kelso.jobs import JobRunner
  from tests.test_api import ctx

  return TestClient(create_app(ctx, JobRunner(ctx)))


def test_the_api_lists_domains_and_decides_several_at_once(kelso_env):
  _write(kelso_env, RESTRICTED)
  assert kelso_env.run("config", APP, "--network", "ask").returncode == 0
  assert kelso_env.run("start", APP).returncode == 0
  assert kelso_env.run("net", APP, "deny", "weather.com").returncode == 0
  _kproxy_answers(
    kelso_env,
    {
      "GET /pending": [{"domain": "tmdb.org", "hosts": {"image.tmdb.org": {}}}],
      "GET /stats": {"api.weather.com": {"https": {"conns": 2, "denied": 2}}},
    },
  )
  client = _client()

  view = client.get(f"/apps/{APP}/net").json()
  assert (view["network_mode"], view["manifest_mode"]) == ("ask", "restrict")
  assert view["source"] == "live"
  rows = {r["domain"]: r for r in view["rows"]}
  assert set(rows) == {
    "api.weather.com",
    "weather.com",
    "fonts.gstatic.com",
    "tmdb.org",
  }
  # Your deny of weather.com overrides the manifest's listing of its subdomain.
  assert rows["api.weather.com"]["verdict"] == "deny"
  assert rows["api.weather.com"]["decided_as"] == "weather.com"
  assert rows["api.weather.com"]["listed"] == "weather"
  assert rows["api.weather.com"]["refused"] == 2
  assert rows["tmdb.org"]["waiting"] and rows["tmdb.org"]["hosts"] == ["image.tmdb.org"]
  assert rows["fonts.gstatic.com"]["verdict"] is None

  before = len(_admin_calls(kelso_env))
  decided = client.post(
    f"/apps/{APP}/net", json={"decisions": {"tmdb.org": "allow", "weather.com": None}}
  )
  assert decided.status_code == 200, decided.text
  puts = [c for c in _admin_calls(kelso_env)[before:] if c[:2] == ["PUT", "/rules"]]
  assert len(puts) == 1
  rows = {r["domain"]: r for r in decided.json()["rows"]}
  assert rows["tmdb.org"]["verdict"] == "allow"
  assert rows["api.weather.com"]["verdict"] is None

  refused = client.post(f"/apps/{APP}/net", json={"decisions": {"a.com": "maybe"}})
  assert refused.status_code == 400
  assert kelso_env.run("net", APP).stdout.count("allowed by you") == 1


def test_a_stopped_app_shows_its_last_run(kelso_env):
  _write(kelso_env, RESTRICTED)
  assert kelso_env.run("load", APP).returncode == 0
  path = kelso_env.run_root / APP / "kproxy" / kproxy.STATS_FILE
  path.write_text(json.dumps(LAST_RUN))
  view = _client().get(f"/apps/{APP}/net").json()
  assert view["source"] == "last_run"
  rows = {r["domain"]: r for r in view["rows"]}
  assert rows["example-site.org"]["conns"] == 1
  assert "held.net" not in rows  # held then, not now
  assert "main" not in rows


def test_the_network_mode_is_an_app_option(kelso_env):
  from kelso.lib.configflow import ConfigResponse
  from kelso.lib.configflow.app import app_config_request, apply_app_config
  from kelso.lib.kelso import KelsoCtx
  from tests.test_api import ctx

  _write(kelso_env, RESTRICTED)
  assert kelso_env.run("load", APP).returncode == 0
  c: KelsoCtx = ctx()
  spec = c.require_loaded_spec(c.resolve_app_id(APP))
  field = app_config_request(spec, c).field("network.mode")
  assert field is not None and field.section == "option"
  assert (field.value, field.choices) == (
    "restrict",
    ("open", "restrict", "ask", "watch"),
  )

  apply_app_config(spec, ConfigResponse(values={"network.mode": "watch"}), c)
  assert c.app_store(spec.app).get_network_mode() == "watch"
  apply_app_config(spec, ConfigResponse(values={"network.mode": "restrict"}), c)
  assert c.app_store(spec.app).get_network_mode() is None
