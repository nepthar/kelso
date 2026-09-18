"""The `run/<app_id>/` layout and the stage / start / stop / rm lifecycle.

Everything here goes through the CLI against the `kelso_env` fixture, whose
fake docker is the only docker these tests are allowed to see.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from kelso.lib.config import load_config_file
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle import dev_plan, source_volume_links

BASIC = "io.p2net.basic-features"


def _write_bundle(kelso_env, app_id: str, manifest: str) -> Path:
  """Create (or overwrite) a catalog entry with the given manifest."""
  bundle = kelso_env.local_repo / f"{app_id}.klso"
  bundle.mkdir(parents=True, exist_ok=True)
  (bundle / "manifest.toml").write_text(manifest)
  return bundle


def _compose(kelso_env, app_id: str) -> dict:
  return yaml.safe_load((kelso_env.run_root / app_id / "compose.yml").read_text())


def _volumes_manifest(volumes: str) -> str:
  return f"""\
[app]
version = "1"

[volumes]
{volumes}

[run.main]
image   = "alpine:latest"
cmd     = ["true"]
volumes = {{ }}
"""


# --- stage ------------------------------------------------------------------


def test_stage_copies_the_bundle_into_the_run_dir(kelso_env):
  """What is installed is a fact on disk, not a pointer to something else."""
  app_id = "ports-demo"
  staged = kelso_env.run("install", app_id)
  assert staged.returncode == 0, staged.stderr

  run_dir = kelso_env.run_root / app_id
  catalog = kelso_env.local_repo / f"{app_id}.klso"
  copied = run_dir / "staged" / "manifest.toml"
  assert copied.is_file()
  assert not copied.is_symlink()
  assert copied.read_text() == (catalog / "manifest.toml").read_text()
  assert not (run_dir / "source").exists()
  assert (kelso_env.app_logtab(app_id)).is_file()
  assert (run_dir / "compose.yml").is_file()


def test_editing_the_catalog_then_restaging_recopies(kelso_env):
  app_id = "ports-demo"
  assert kelso_env.run("install", app_id).returncode == 0

  catalog = kelso_env.local_repo / f"{app_id}.klso" / "manifest.toml"
  catalog.write_text(
    catalog.read_text().replace('version      = "0.1.0"', 'version = "9"')
  )

  assert kelso_env.run("install", app_id).returncode == 0
  assert (
    'version = "9"'
    in (kelso_env.run_root / app_id / "staged" / "manifest.toml").read_text()
  )


def test_editing_the_run_copy_is_lost_on_the_next_stage(kelso_env):
  """The run copy is kelso's output, never its input: apps/ is the source."""
  app_id = "ports-demo"
  assert kelso_env.run("install", app_id).returncode == 0

  copied = kelso_env.run_root / app_id / "staged" / "manifest.toml"
  copied.write_text(copied.read_text() + "\n# hand edit\n")
  (kelso_env.run_root / app_id / "staged" / "stowaway.txt").write_text("hi")

  assert kelso_env.run("install", app_id).returncode == 0
  assert "# hand edit" not in copied.read_text()
  assert not (kelso_env.run_root / app_id / "staged" / "stowaway.txt").exists()


def test_stage_preserves_config_and_volume_contents(kelso_env):
  assert kelso_env.run("install", BASIC).returncode == 0
  assert kelso_env.run("config", BASIC, "--set", "admin_user=alice").returncode == 0

  payload = kelso_env.volumes_root / "data" / BASIC / "config" / "state.txt"
  payload.write_text("precious")

  assert kelso_env.run("install", BASIC).returncode == 0

  got = kelso_env.run("config", BASIC, "--get", "admin_user")
  assert got.stdout.strip() == "alice"
  assert payload.read_text() == "precious"


def test_stage_by_path_adds_nothing_to_a_repo(kelso_env):
  app_id = "ports-demo"
  elsewhere = kelso_env.root / "checkout" / f"{app_id}.klso"
  elsewhere.parent.mkdir()
  (kelso_env.local_repo / f"{app_id}.klso").rename(elsewhere)

  staged = kelso_env.run("install", str(elsewhere))
  assert staged.returncode == 0, staged.stderr

  assert not (kelso_env.local_repo / f"{app_id}.klso").exists()
  assert (kelso_env.run_root / app_id / "staged" / "manifest.toml").is_file()


def test_stage_by_path_refuses_to_take_over_another_bundles_id(kelso_env):
  """An id installed from one bundle is not silently taken over by another."""
  app_id = "ports-demo"
  other = kelso_env.root / "checkout" / f"{app_id}.klso"
  other.parent.mkdir()
  _write_bundle(
    kelso_env, "scratch", '[app]\nversion = "1"\n\n[run.main]\nimage = "alpine"\n'
  )
  (kelso_env.local_repo / "scratch.klso").rename(other)

  assert kelso_env.run("install", app_id).returncode == 0

  refused = kelso_env.run("install", str(other))
  assert refused.returncode == 1
  assert "previously installed from" in refused.stderr


def test_stage_refuses_while_containers_are_running(kelso_env):
  app_id = "ports-demo"
  assert kelso_env.run("start", app_id).returncode == 0

  refused = kelso_env.run("install", app_id)
  assert refused.returncode == 1
  assert f"kelso stop {app_id}" in refused.stderr


def test_stage_refuses_when_config_is_gone_but_data_remains(kelso_env):
  """Deleting the config logtab by hand must not silently regenerate secrets.

  Fresh `auto` secrets against data that expects the old ones produce an app
  that fails to authenticate for reasons nothing in the error explains.
  """
  assert kelso_env.run("install", BASIC).returncode == 0
  (kelso_env.volumes_root / "data" / BASIC / "config" / "db").write_text("rows")
  kelso_env.app_logtab(BASIC).unlink()

  refused = kelso_env.run("install", BASIC)
  assert refused.returncode == 1
  assert "volume data but no config" in refused.stderr
  assert f"kelso rm {BASIC}" in refused.stderr


def test_restaging_after_a_deleted_run_dir_reuses_config(kelso_env):
  """Config lives in config/, so wiping the run dir does not lose secrets."""
  assert kelso_env.run("install", BASIC).returncode == 0
  minted = kelso_env.run(
    "config", BASIC, "--get", "admin_pass", "--show-secret"
  ).stdout.strip()
  shutil.rmtree(kelso_env.run_root / BASIC)

  assert kelso_env.run("install", BASIC).returncode == 0
  again = kelso_env.run(
    "config", BASIC, "--get", "admin_pass", "--show-secret"
  ).stdout.strip()
  assert again == minted


# --- volume links -----------------------------------------------------------


def test_app_links_are_relative_and_managed_links_are_absolute(kelso_env):
  """Relative app links survive a moved run dir; managed roots may be elsewhere."""
  assert kelso_env.run("install", BASIC).returncode == 0
  volumes = kelso_env.run_root / BASIC / "volumes"

  app_link = volumes / "app" / "bin"
  assert app_link.is_symlink()
  assert app_link.readlink() == Path("../../staged/bin")
  assert app_link.resolve() == (kelso_env.run_root / BASIC / "staged" / "bin").resolve()

  data_link = volumes / "data" / "config"
  assert data_link.is_symlink()
  assert data_link.readlink().is_absolute()
  assert data_link.readlink() == kelso_env.volumes_root / "data" / BASIC / "config"

  temp_link = volumes / "temp" / "cache"
  assert temp_link.readlink() == kelso_env.volumes_root / "temp" / BASIC / "cache"


def test_app_volumes_are_mounted_read_only(kelso_env):
  assert kelso_env.run("install", BASIC).returncode == 0
  mounts = _compose(kelso_env, BASIC)["services"]["main"]["volumes"]

  assert "./volumes/app/bin:/myapp/bin:ro" in mounts
  assert "./volumes/data/config:/myapp/config" in mounts
  assert "./volumes/temp/cache:/myapp/cache" in mounts


def test_readonly_false_on_an_app_volume_is_a_manifest_error(kelso_env):
  """An author who wrote it meant something, so say it is impossible."""
  _write_bundle(
    kelso_env,
    "bad-app-volume",
    _volumes_manifest('assets = { kind = "app", readonly = false }'),
  )

  refused = kelso_env.run("install", "bad-app-volume")
  assert refused.returncode == 1
  assert "app volumes are always mounted read-only" in refused.stderr
  assert "assets" in refused.stderr
  assert not (kelso_env.run_root / "bad-app-volume").exists()


def test_a_dropped_volume_keeps_its_data_and_is_reported(kelso_env):
  app_id = "vol-churn"
  _write_bundle(
    kelso_env,
    app_id,
    _volumes_manifest('one = { kind = "data" }\ntwo = { kind = "data" }'),
  )
  assert kelso_env.run("install", app_id).returncode == 0
  payload = kelso_env.volumes_root / "data" / app_id / "two" / "keep.txt"
  payload.write_text("still here")

  _write_bundle(kelso_env, app_id, _volumes_manifest('one = { kind = "data" }'))
  restaged = kelso_env.run("install", app_id)
  assert restaged.returncode == 0, restaged.stderr

  assert not (kelso_env.run_root / app_id / "volumes" / "data" / "two").exists()
  assert payload.read_text() == "still here"
  assert "two" in restaged.stderr


def test_an_added_volume_is_created_empty(kelso_env):
  app_id = "vol-churn"
  _write_bundle(kelso_env, app_id, _volumes_manifest('one = { kind = "data" }'))
  assert kelso_env.run("install", app_id).returncode == 0

  _write_bundle(
    kelso_env,
    app_id,
    _volumes_manifest('one = { kind = "data" }\ntwo = { kind = "data" }'),
  )
  assert kelso_env.run("install", app_id).returncode == 0

  added = kelso_env.volumes_root / "data" / app_id / "two"
  assert added.is_dir()
  assert list(added.iterdir()) == []


def test_changing_a_volumes_kind_is_refused(kelso_env):
  """The bytes live under the old kind's root; moving them silently is worse."""
  app_id = "vol-churn"
  _write_bundle(kelso_env, app_id, _volumes_manifest('one = { kind = "data" }'))
  assert kelso_env.run("install", app_id).returncode == 0

  _write_bundle(kelso_env, app_id, _volumes_manifest('one = { kind = "bulk" }'))
  refused = kelso_env.run("install", app_id)
  assert refused.returncode == 1
  assert "changed kind from data to bulk" in refused.stderr
  assert (kelso_env.run_root / app_id / "volumes" / "data" / "one").is_symlink()


# --- config -----------------------------------------------------------------


def test_config_round_trips_through_config_dir(kelso_env):
  assert kelso_env.run("install", BASIC).returncode == 0
  assert kelso_env.run("config", BASIC, "--set", "admin_user=alice").returncode == 0

  logtab = kelso_env.app_logtab(BASIC).read_text()
  assert "config/admin_user" in logtab
  assert "meta/origin" in logtab
  assert "meta/installed_at" in logtab

  # ...and nothing about this app is left in the central db.
  assert BASIC not in kelso_env.read_db().get("apps", {})


def test_start_set_is_the_one_shot_for_an_unstaged_app(kelso_env):
  started = kelso_env.run("start", BASIC, "--set", "admin_user=alice")
  assert started.returncode == 0, started.stderr

  got = kelso_env.run("config", BASIC, "--get", "admin_user")
  assert got.stdout.strip() == "alice"


def test_restaging_does_not_regenerate_an_existing_auto_secret(kelso_env):
  """The worst possible bug here: a new secret against data expecting the old.

  `admin_pass` is `{ secret = true, default = "auto" }`, so it is minted on the
  first stage and must survive every one after it.
  """
  assert kelso_env.run("install", BASIC).returncode == 0
  first = kelso_env.run(
    "config", BASIC, "--get", "admin_pass", "--show-secret"
  ).stdout.strip()
  assert first

  assert kelso_env.run("install", BASIC).returncode == 0
  assert kelso_env.run("install", BASIC).returncode == 0

  again = kelso_env.run(
    "config", BASIC, "--get", "admin_pass", "--show-secret"
  ).stdout.strip()
  assert again == first


# --- dev --------------------------------------------------------------------


def _docker_calls(kelso_env) -> list[dict]:
  return [json.loads(line) for line in kelso_env.docker_log.read_text().splitlines()]


def _bundle(kelso_env, app_id: str = BASIC) -> str:
  return str(kelso_env.local_repo / f"{app_id}.klso")


def _configured_for_dev(kelso_env) -> Path:
  """Stage BASIC with its required config set, and return the source bundle."""
  assert kelso_env.run("install", BASIC).returncode == 0
  assert kelso_env.run("config", BASIC, "--set", "admin_user=alice").returncode == 0
  return kelso_env.local_repo / f"{BASIC}.klso"


def _write_md_bundle(kelso_env, app_id: str) -> Path:
  bundle = kelso_env.root / f"{app_id}.klso.md"
  bundle.write_text(
    '```toml klso_path="manifest.toml"\n'
    '[app]\nversion = "1"\n\n'
    '[volumes]\nhello = { kind = "app", src = "bin/hello.sh" }\n\n'
    "[run.main]\n"
    'image   = "alpine:latest"\n'
    'volumes = { hello = "/app/hello.sh" }\n'
    "```\n\n"
    '```bash klso_path="bin/hello.sh:+x"\n'
    'echo "hello"\n'
    "```\n"
  )
  return bundle


def test_dev_runs_in_the_foreground_against_the_source(kelso_env):
  """The point of the command: `up` without `-d`, app links on the source."""
  source = _configured_for_dev(kelso_env)

  result = kelso_env.run("dev", str(source))
  assert result.returncode == 0, result.stderr

  calls = _docker_calls(kelso_env)
  args = [call["args"] for call in calls]
  assert ["compose", "up"] in args
  assert ["compose", "up", "-d"] not in args
  assert ["compose", "down"] in args

  # What the link pointed at while docker was running -- not after.
  up = next(call for call in calls if call["args"] == ["compose", "up"])
  assert up["app_links"] == {"bin": str(source.resolve() / "bin")}


def test_dev_installs_a_new_bundle_and_asks_for_missing_config(kelso_env):
  # admin_pass keeps its generated secret, admin_user is asked for, the
  # advanced settings are skipped, then the form is submitted.
  result = kelso_env.run("dev", _bundle(kelso_env), input="\nalice\nn\ns\n")
  assert result.returncode == 0, result.stderr
  assert (kelso_env.run_root / BASIC / "staged" / "manifest.toml").is_file()
  assert ["compose", "up"] in [c["args"] for c in _docker_calls(kelso_env)]

  got = kelso_env.run("config", BASIC, "--get", "admin_user")
  assert got.stdout.strip() == "alice"


def test_dev_config_carries_over_to_the_next_run(kelso_env):
  assert (
    kelso_env.run("dev", _bundle(kelso_env), input="\nalice\nn\ns\n").returncode == 0
  )
  secret = kelso_env.run(
    "config", BASIC, "--get", "admin_pass", "--show-secret"
  ).stdout.strip()

  again = kelso_env.run("dev", _bundle(kelso_env))
  # No input: a prompt here would read EOF and the run would be refused.
  assert again.returncode == 0, again.stderr
  assert (
    kelso_env.run(
      "config", BASIC, "--get", "admin_pass", "--show-secret"
    ).stdout.strip()
    == secret
  )


def test_dev_refuses_when_config_is_still_missing(kelso_env):
  refused = kelso_env.run("dev", _bundle(kelso_env))
  assert refused.returncode == 1
  assert "admin_user" in refused.stderr
  assert ["compose", "up"] not in [c["args"] for c in _docker_calls(kelso_env)]


def test_dev_puts_the_app_links_back_when_it_is_over(kelso_env):
  source = _configured_for_dev(kelso_env)
  link = kelso_env.run_root / BASIC / "volumes" / "app" / "bin"

  assert kelso_env.run("dev", str(source)).returncode == 0

  assert link.readlink() == Path("../../staged/bin")
  assert link.resolve() == (kelso_env.run_root / BASIC / "staged" / "bin").resolve()


def test_dev_puts_the_app_links_back_after_a_failure(kelso_env):
  """The links are borrowed for the run; an exception is not a way to keep them."""
  source = _configured_for_dev(kelso_env).resolve()
  ctx = KelsoCtx(load_config_file(kelso_env.config))
  plan = dev_plan(ctx.resolve_app(BASIC), source, ctx)
  link = kelso_env.run_root / BASIC / "volumes" / "app" / "bin"

  with pytest.raises(RuntimeError):
    with source_volume_links(plan):
      assert link.readlink() == plan.source / "bin"
      raise RuntimeError("the spec blew up")

  assert link.readlink() == Path("../../staged/bin")


def test_dev_leaves_the_staged_bundle_copy_alone(kelso_env):
  """Only the links move. `staged/` is still what `stage` put there."""
  source = _configured_for_dev(kelso_env)
  copied = (kelso_env.run_root / BASIC / "staged" / "bin" / "hello.sh").read_text()

  assert kelso_env.run("dev", str(source)).returncode == 0

  (source / "bin" / "hello.sh").write_text("echo edited\n")
  assert (
    kelso_env.run_root / BASIC / "staged" / "bin" / "hello.sh"
  ).read_text() == copied


def test_dev_picks_up_manifest_edits_on_the_next_run(kelso_env):
  source = _configured_for_dev(kelso_env)
  manifest = source / "manifest.toml"
  manifest.write_text(
    manifest.read_text().replace('ADMIN_USER = "${admin_user}"', 'EDITED = "yes"')
  )

  assert kelso_env.run("dev", str(source)).returncode == 0
  env = _compose(kelso_env, BASIC)["services"]["main"]["environment"]
  assert env["EDITED"] == "yes"


def test_dev_refuses_an_app_id(kelso_env):
  refused = kelso_env.run("dev", "hello")
  assert refused.returncode == 1
  assert "not an app id" in refused.stderr
  assert "kelso dev ./hello.klso" in refused.stderr


def test_dev_refuses_an_app_installed_from_another_bundle(kelso_env):
  _configured_for_dev(kelso_env)
  elsewhere = kelso_env.root / "checkout" / f"{BASIC}.klso"
  shutil.copytree(kelso_env.local_repo / f"{BASIC}.klso", elsewhere)

  refused = kelso_env.run("dev", str(elsewhere))
  assert refused.returncode == 1
  assert "were made for" in refused.stderr
  assert f"kelso uninstall --purge {BASIC}" in refused.stderr
  assert ["compose", "up"] not in [c["args"] for c in _docker_calls(kelso_env)]


def _respelled(path: Path) -> Path:
  """`path` with its parent's name in the other case; skip where that is not it."""
  other = path.parent.parent / path.parent.name.swapcase() / path.name
  if not other.exists():
    pytest.skip("filesystem is case-sensitive")
  return other


def test_dev_accepts_its_own_bundle_spelled_in_another_case(kelso_env):
  source = kelso_env.local_repo / f"{BASIC}.klso"
  assert kelso_env.run("install", str(_respelled(source))).returncode == 0
  assert kelso_env.run("config", BASIC, "--set", "admin_user=alice").returncode == 0

  result = kelso_env.run("dev", str(source))
  assert result.returncode == 0, result.stderr


def test_dev_accepts_its_own_bundle_after_uninstall(kelso_env):
  source = _configured_for_dev(kelso_env)
  assert kelso_env.run("uninstall", BASIC, "-y").returncode == 0

  result = kelso_env.run("dev", str(source))
  assert result.returncode == 0, result.stderr


def test_install_accepts_its_own_bundle_spelled_in_another_case(kelso_env):
  source = kelso_env.local_repo / f"{BASIC}.klso"
  assert kelso_env.run("install", str(_respelled(source))).returncode == 0

  again = kelso_env.run("install", str(source))
  assert again.returncode == 0, again.stderr


def test_dev_refuses_a_running_app(kelso_env):
  source = _configured_for_dev(kelso_env)
  assert kelso_env.run("start", BASIC).returncode == 0

  refused = kelso_env.run("dev", str(source))
  assert refused.returncode == 1
  assert f"kelso stop {BASIC}" in refused.stderr


def test_dev_runs_a_markdown_bundle_from_its_fresh_install(kelso_env):
  """There is no folder to mount, so the links stay on the staged copy."""
  bundle = _write_md_bundle(kelso_env, "md-demo")

  result = kelso_env.run("dev", str(bundle))
  assert result.returncode == 0, result.stderr
  assert "edits take effect on the next run" in result.stdout

  up = next(c for c in _docker_calls(kelso_env) if c["args"] == ["compose", "up"])
  assert up["app_links"] == {"hello": "../../staged/bin/hello.sh"}


def test_dev_with_no_app_volumes_is_just_an_interactive_run(kelso_env):
  """A folder app with nothing to mount still runs: it is `compose up` here."""
  app_id = "no-app-volumes"
  bundle = _write_bundle(
    kelso_env, app_id, _volumes_manifest('one = { kind = "data" }')
  )

  result = kelso_env.run("dev", str(bundle))
  assert result.returncode == 0, result.stderr
  assert "nothing is mounted from it" in result.stdout

  args = [call["args"] for call in _docker_calls(kelso_env)]
  assert ["compose", "up"] in args
  assert ["compose", "up", "-d"] not in args


def test_dev_lists_every_route_and_where_to_reach_it(kelso_env):
  result = kelso_env.run("dev", _bundle(kelso_env, "ports-demo"))
  assert result.returncode == 0, result.stderr

  assert "Routes:" in result.stdout
  assert "Host:" not in result.stdout
  # web is auto-allocated from port_base; admin is pinned at 9000:80.
  assert "main:8080/tcp <- http://localhost:41000" in result.stdout
  assert "main:80/tcp <- http://localhost:9000" in result.stdout
  # Unpublished: the local port is the whole story, and the receipt says why.
  assert "kelso.localhost" not in result.stdout
  assert "publish them with --routes" in result.stdout


def test_dev_lists_an_https_route_with_its_scheme(kelso_env):
  bundle = _write_bundle(
    kelso_env,
    "tls-demo",
    '[app]\nversion = "1"\nsubdomain = "tls"\n\n'
    "[run.main]\n"
    'image  = "alpine:latest"\n'
    'routes = { main = { port = "8443", scheme = "https" } }\n',
  )

  result = kelso_env.run("dev", str(bundle))
  assert result.returncode == 0, result.stderr
  assert "main:8443/tcp <- https://localhost:" in result.stdout


def test_dev_routes_publishes_for_the_length_of_the_run(kelso_env):
  result = kelso_env.run("dev", _bundle(kelso_env, "ports-demo"), "--routes")
  assert result.returncode == 0, result.stderr

  # Neither route is named `main`, so both subdomains are prefixed.
  assert (
    "main:8080/tcp <- http://localhost:41000 <- https://web-ports.kelso.localhost"
    in result.stdout
  )
  assert (
    "main:80/tcp <- http://localhost:9000 <- https://admin-ports.kelso.localhost"
    in result.stdout
  )
  assert "publish them with --routes" not in result.stdout


# --- rm ---------------------------------------------------------------------


def test_rm_removes_the_run_dir_volumes_and_routes(kelso_env):
  app_id = "ports-demo"
  assert kelso_env.run("start", app_id).returncode == 0
  assert kelso_env.read_db()["routes"][app_id]

  removed = kelso_env.run("rm", app_id, "-y")
  assert removed.returncode == 0, removed.stderr

  assert not (kelso_env.run_root / app_id).exists()
  assert not kelso_env.app_logtab(app_id).exists()
  for kind in ("data", "temp", "bulk", "logs"):
    assert not (kelso_env.volumes_root / kind / app_id).exists()
  assert app_id not in kelso_env.read_db().get("routes", {})
  # The catalog entry is the reinstall path, so it survives on purpose.
  assert (kelso_env.local_repo / f"{app_id}.klso").is_dir()


def test_rm_needs_confirmation_and_says_it_cannot_be_undone(kelso_env):
  app_id = "ports-demo"
  assert kelso_env.run("start", app_id).returncode == 0

  declined = kelso_env.run("rm", app_id, input="n\n")
  assert declined.returncode == 0, declined.stderr
  assert "take a snapshot first" in declined.stdout
  assert "Nothing removed" in declined.stdout
  assert (kelso_env.run_root / app_id).is_dir()

  confirmed = kelso_env.run("rm", app_id, input="y\n")
  assert confirmed.returncode == 0, confirmed.stderr
  assert not (kelso_env.run_root / app_id).exists()
  assert not kelso_env.app_logtab(app_id).exists()


def test_rm_reports_host_volumes_it_leaves_alone(kelso_env):
  app_id = "host-volumes"
  host_path = kelso_env.root / "external-data"
  host_path.mkdir()
  assert kelso_env.run("start", app_id, "--bind", "hostvol1=media").returncode == 0

  removed = kelso_env.run("rm", app_id, input="y\n")
  assert removed.returncode == 0, removed.stderr
  assert str(host_path) in removed.stdout
  assert host_path.is_dir()


def test_rm_then_start_is_a_clean_reinstall(kelso_env):
  assert kelso_env.run("start", BASIC, "--set", "admin_user=alice").returncode == 0
  minted = kelso_env.run(
    "config", BASIC, "--get", "admin_pass", "--show-secret"
  ).stdout.strip()

  assert kelso_env.run("rm", BASIC, "-y").returncode == 0

  restarted = kelso_env.run("start", BASIC, "--set", "admin_user=bob")
  assert restarted.returncode == 0, restarted.stderr
  assert (kelso_env.run_root / BASIC / "staged" / "manifest.toml").is_file()

  # Config and data went together, so a fresh secret is correct here.
  fresh = kelso_env.run(
    "config", BASIC, "--get", "admin_pass", "--show-secret"
  ).stdout.strip()
  assert fresh and fresh != minted
