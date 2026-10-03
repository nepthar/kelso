"""Tests for application repos: addressing, mirroring, and what a mirror holds.

The remote is a real git repository in the test's tmp dir, fetched over
file://, so the suite never touches the network. `GithubFolder.clone_url` is
the only seam needed for that.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from kelso.jobs.repo import RepoAddJob, RepoRemoveJob, RepoUpdateJob
from kelso.lib import repo as repo_lib
from kelso.lib.config import load_config_file
from kelso.lib.kelso import KelsoCtx
from kelso.lib.repo import (
  MAX_BUNDLES,
  GithubFolder,
  Repo,
  mirror,
  name_from_url,
  parse_github_url,
)

FOLDER = "apps"
URL = f"github://nepthar/kelso/main/{FOLDER}"

MANIFEST = b"""\
[app]
version      = "0.1.0"
display_name = "Hello world"
description  = "Says hello!"

[run.main]
image   = "alpine:latest"
cmd     = ["/bin/sh", "-c", "echo hello"]
restart = "no"
"""

MANIFEST_WITH_APP_DIR = b"""\
[app]
version      = "0.1.0"
display_name = "Scripted"

[volumes]
app = { kind = "app", desc = "shipped alongside the manifest" }

[run.main]
image   = "alpine:latest"
cmd     = ["/bin/sh", "-c", "/demo/app/go.sh"]
volumes = { app = "/demo/app" }
restart = "no"
"""


MD_BUNDLE = b"""\
# Solo

```toml klso_path="manifest.toml"
[app]
version = "0.1.0"
display_name = "Solo"

[run.main]
image   = "alpine:latest"
cmd     = ["/bin/sh", "-c", "echo solo"]
restart = "no"
```
"""


# --- the remote ------------------------------------------------------------


class Remote:
  """A git repository standing in for github.com/nepthar/kelso."""

  def __init__(self, root: Path) -> None:
    self.root = root
    root.mkdir()
    self._git("init", "-q", "-b", "main")

  def _git(self, *args: str) -> str:
    return subprocess.run(
      ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
      cwd=self.root,
      check=True,
      capture_output=True,
      text=True,
    ).stdout.strip()

  def add(self, path: str, content: bytes, *, executable: bool = False) -> None:
    dest = self.root / FOLDER / path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)
    dest.chmod(0o755 if executable else 0o644)

  def remove(self, path: str) -> None:
    (self.root / FOLDER / path).unlink()

  def commit(self) -> str:
    self._git("add", "-A")
    self._git("commit", "-q", "-m", "change")
    return self._git("rev-parse", "HEAD")

  def hello_world(self) -> str:
    self.add("hello-world.klso/manifest.toml", MANIFEST)
    return self.commit()


@pytest.fixture
def github(monkeypatch, tmp_path):
  fake = Remote(tmp_path / "remote")
  monkeypatch.setattr(
    GithubFolder, "clone_url", property(lambda self: f"file://{fake.root}")
  )
  return fake


@pytest.fixture
def ctx(kelso_env) -> KelsoCtx:
  return KelsoCtx(load_config_file(kelso_env.config))


def a_repo(ctx, name: str = "up", ref: str = "main") -> Repo:
  checkout = ctx.config.repos_root / name
  return Repo(
    name=name,
    path=checkout / FOLDER,
    kind="github",
    remote=parse_github_url(f"github://nepthar/kelso/{ref}/{FOLDER}"),
    checkout=checkout,
  )


# --- addressing ------------------------------------------------------------


def test_a_url_reads_repo_coordinates():
  folder = parse_github_url("github://nepthar/kelso/main/apps")
  assert (folder.user, folder.repo, folder.ref) == ("nepthar", "kelso", "main")
  assert folder.path == ("apps",)
  assert folder.repo_path == "apps"


def test_a_folder_is_optional_and_means_the_repository_root():
  folder = parse_github_url("github://nepthar/bundles/main")
  assert folder.path == ()
  assert folder.repo_path == ""


def test_a_url_round_trips():
  url = "github://nepthar/kelso/v1.2/deep/apps"
  assert parse_github_url(url).url == url


@pytest.mark.parametrize(
  "raw",
  [
    "nepthar/kelso/main/apps",
    "github:nepthar/kelso/main/apps",
    "https://github.com/nepthar/kelso",
    "",
  ],
)
def test_only_github_urls_are_accepted(raw):
  with pytest.raises(ValueError, match="repo url"):
    parse_github_url(raw)


@pytest.mark.parametrize("raw", ["github://nepthar", "github://nepthar/kelso"])
def test_a_url_must_name_user_repo_and_ref(raw):
  with pytest.raises(ValueError, match="Malformed repo url"):
    parse_github_url(raw)


def test_an_empty_ref_is_refused():
  with pytest.raises(ValueError, match="empty ref"):
    parse_github_url("github://nepthar/kelso//apps")


@pytest.mark.parametrize("segment", ["..", "."])
def test_path_traversal_is_refused_in_a_url(segment):
  with pytest.raises(ValueError, match="Malformed path segment"):
    parse_github_url(f"github://nepthar/kelso/main/{segment}")


@pytest.mark.parametrize(
  "raw", ["github://ne pthar/kelso/main", "github://nepthar/har bor/main"]
)
def test_github_names_are_validated(raw):
  with pytest.raises(ValueError):
    parse_github_url(raw)


def test_a_name_is_taken_from_the_repository():
  assert name_from_url("github://nepthar/kelso/main/apps") == "kelso"


def test_a_repository_name_kelso_cannot_use_says_to_pass_one():
  with pytest.raises(ValueError, match="--name"):
    name_from_url("github://nepthar/kelso.js/main")


@pytest.mark.parametrize("ref", ["--upload-pack=touch", "a..b", "a:b"])
def test_a_ref_that_git_would_misread_is_refused(ref):
  with pytest.raises(ValueError, match="not a branch, tag, or commit sha"):
    parse_github_url(f"github://nepthar/kelso/{ref}/apps")


# --- mirroring ---------------------------------------------------------------


def test_a_mirror_lands_every_bundle_in_the_folder(github, ctx):
  github.add("solo.klso.md", MD_BUNDLE)
  sha = github.hello_world()
  result = mirror(a_repo(ctx), ctx)

  assert result.bundles == ("hello-world", "solo")
  assert result.sha == sha
  assert result.previous_sha is None
  mirrored = ctx.config.repos_root / "up" / FOLDER
  assert (mirrored / "hello-world.klso" / "manifest.toml").read_bytes() == MANIFEST
  assert (mirrored / "solo.klso.md").read_bytes() == MD_BUNDLE


def test_only_the_folder_is_checked_out(github, ctx):
  (github.root / "elsewhere").mkdir()
  (github.root / "elsewhere" / "big.bin").write_bytes(b"x" * 1024)
  github.hello_world()
  mirror(a_repo(ctx), ctx)
  assert not (ctx.config.repos_root / "up" / "elsewhere").exists()


def test_the_executable_bit_survives_a_mirror(github, ctx):
  github.add("hello-world.klso/go.sh", b"#!/bin/sh\n", executable=True)
  github.hello_world()
  mirror(a_repo(ctx), ctx)
  script = ctx.config.repos_root / "up" / FOLDER / "hello-world.klso" / "go.sh"
  assert script.stat().st_mode & 0o111


def test_a_second_mirror_replaces_what_the_first_left(github, ctx):
  github.add("gone.klso.md", MD_BUNDLE)
  first = github.hello_world()
  mirror(a_repo(ctx), ctx)

  github.remove("gone.klso.md")
  github.commit()
  result = mirror(a_repo(ctx), ctx)

  assert result.previous_sha == first
  assert not result.unchanged
  assert not (ctx.config.repos_root / "up" / FOLDER / "gone.klso.md").exists()


def test_an_unchanged_remote_is_reported_as_such(github, ctx):
  github.hello_world()
  mirror(a_repo(ctx), ctx)
  assert mirror(a_repo(ctx), ctx).unchanged


def test_an_older_mirror_is_replaced_by_a_checkout(github, ctx):
  stale = ctx.config.repos_root / "up" / "old.klso.md"
  stale.parent.mkdir(parents=True)
  stale.write_bytes(MD_BUNDLE)
  github.hello_world()

  assert mirror(a_repo(ctx), ctx).bundles == ("hello-world",)
  assert not stale.exists()


def test_a_failed_mirror_leaves_the_previous_copy_alone(github, ctx):
  sha = github.hello_world()
  mirror(a_repo(ctx), ctx)

  with pytest.raises(ValueError, match="Could not update up"):
    mirror(a_repo(ctx, ref="no-such-branch"), ctx)

  mirrored = ctx.config.repos_root / "up" / FOLDER
  assert (mirrored / "hello-world.klso" / "manifest.toml").read_bytes() == MANIFEST
  assert ctx.kelso_db.get_repo_state("up")["sha"] == sha


def test_a_missing_folder_is_named(github, ctx):
  (github.root / "README").write_text("no apps here")
  github.commit()
  with pytest.raises(ValueError, match="no folder 'apps'"):
    mirror(a_repo(ctx), ctx)


def test_a_mirrored_bundle_is_in_the_catalog(github, ctx, kelso_env):
  github.hello_world()
  kelso_env.config.write_text(
    f'{kelso_env.config.read_text()}\n[repo.up]\nurl = "{URL}"\n'
  )
  fresh = KelsoCtx(load_config_file(kelso_env.config))
  mirror(fresh.config.repos["up"], fresh)
  assert fresh.app_catalog()["hello-world"][0].source == "up"


def test_a_local_repo_cannot_be_updated(ctx):
  local = Repo("dev", ctx.config.repos_root / "dev", "local")
  with pytest.raises(ValueError, match="local directory"):
    mirror(local, ctx)


def test_too_many_bundles_are_refused_and_not_kept(github, ctx):
  for n in range(MAX_BUNDLES + 1):
    github.add(f"app{n}.klso.md", MD_BUNDLE)
  github.commit()
  with pytest.raises(ValueError, match=f"limit of {MAX_BUNDLES} apps"):
    mirror(a_repo(ctx), ctx)
  assert not (ctx.config.repos_root / "up").exists()


# --- the repo verbs ---------------------------------------------------------


def a_local_repo(kelso_env, name: str = "dev"):
  path = kelso_env.root / name
  path.mkdir()
  with open(kelso_env.config, "a") as f:
    f.write(f'\n[repo.{name}]\npath = "{path}"\n')
  return path


def test_add_writes_the_repo_and_mirrors_it(github, ctx, kelso_env):
  github.hello_world()
  result = repo_lib.add(ctx, URL)

  assert result.repo.name == "kelso"
  assert result.mirrored is not None
  assert result.mirrored.bundles == ("hello-world",)
  assert "[repo.kelso]" in kelso_env.config.read_text()

  fresh = KelsoCtx(load_config_file(kelso_env.config))
  assert fresh.app_catalog()["hello-world"][0].source == "kelso"


def test_add_takes_a_name_of_its_own(github, ctx, kelso_env):
  github.hello_world()
  assert repo_lib.add(ctx, URL, name="mine").repo.name == "mine"


def test_add_refuses_a_name_already_taken(github, ctx):
  github.hello_world()
  repo_lib.add(ctx, URL)
  with pytest.raises(ValueError, match="already exists"):
    repo_lib.add(ctx, URL)


def test_a_local_repo_needs_a_name(ctx, kelso_env):
  with pytest.raises(ValueError, match="needs a name"):
    repo_lib.add(ctx, str(kelso_env.root / "somewhere"))


def test_remove_drops_the_entry_and_the_mirror(github, ctx, kelso_env):
  github.hello_world()
  repo_lib.add(ctx, URL)
  fresh = KelsoCtx(load_config_file(kelso_env.config))
  mirrored = fresh.config.repos["kelso"].checkout
  assert mirrored.is_dir()

  result = repo_lib.remove(fresh, "kelso")

  assert result.name == "kelso"
  assert not mirrored.exists()
  assert fresh.kelso_db.get_repo_state("kelso") is None
  assert "kelso" not in load_config_file(kelso_env.config).repos


def test_local_cannot_be_removed(ctx):
  with pytest.raises(ValueError, match="built in"):
    repo_lib.remove(ctx, "local")


def test_removing_an_unknown_repo_names_the_known_ones(ctx):
  with pytest.raises(ValueError, match="configured repos: local"):
    repo_lib.remove(ctx, "nope")


def test_update_refuses_a_local_repo_by_name(ctx, kelso_env):
  a_local_repo(kelso_env)
  fresh = KelsoCtx(load_config_file(kelso_env.config))
  with pytest.raises(ValueError, match="local directory"):
    repo_lib.update(fresh, "dev")


def test_update_with_no_name_skips_local_repos(ctx, kelso_env):
  a_local_repo(kelso_env)
  fresh = KelsoCtx(load_config_file(kelso_env.config))
  assert repo_lib.update(fresh) == ()


def test_contested_lines_name_every_repo_carrying_an_id(github, ctx, kelso_env):
  github.hello_world()
  repo_lib.add(ctx, URL)
  fresh = KelsoCtx(load_config_file(kelso_env.config))
  repo_lib.add(fresh, URL, name="mirror")
  fresh = KelsoCtx(load_config_file(kelso_env.config))

  [line] = repo_lib.contested_lines(fresh)
  assert "hello-world is in 2 repos (kelso, mirror)" in line
  assert "hello-world@<repo>" in line


# --- the repo jobs ----------------------------------------------------------


def test_repo_add_job_mirrors_the_folder(github, ctx, kelso_env):
  github.hello_world()
  RepoAddJob.call({"url": URL}, ctx, started_by="test")

  fresh = KelsoCtx(load_config_file(kelso_env.config))
  assert "hello-world" in fresh.app_catalog()


@pytest.mark.parametrize(
  "url", ["/etc", "~/bundles", "./apps", "apps", "https://github.com/a/b"]
)
def test_repo_add_job_takes_a_url_and_never_a_path(ctx, url):
  """Local repos are CLI-only; see the note above `runner.JOBS`."""
  with pytest.raises(ValueError, match="takes a github:// url"):
    RepoAddJob.prepare({"url": url}, ctx, started_by="test")


def test_repo_add_job_refuses_a_malformed_url_before_writing(ctx, kelso_env):
  before = kelso_env.config.read_text()
  with pytest.raises(ValueError, match="Malformed repo url"):
    RepoAddJob.prepare({"url": "github://nepthar"}, ctx, started_by="test")
  assert kelso_env.config.read_text() == before


def test_repo_update_job_brings_the_mirror_forward(github, ctx, kelso_env):
  github.hello_world()
  RepoAddJob.call({"url": URL}, ctx, started_by="test")

  github.add("second.klso.md", MD_BUNDLE)
  sha = github.commit()
  fresh = KelsoCtx(load_config_file(kelso_env.config))
  RepoUpdateJob.call({"name": "kelso"}, fresh, started_by="test")

  fresh = KelsoCtx(load_config_file(kelso_env.config))
  assert set(fresh.app_catalog()) >= {"hello-world", "second"}
  assert fresh.kelso_db.get_repo_state("kelso")["sha"] == sha


def test_repo_update_job_refuses_an_unknown_repo(ctx):
  with pytest.raises(ValueError, match="No repo 'nope'"):
    RepoUpdateJob.prepare({"name": "nope"}, ctx, started_by="test")


def test_repo_remove_job_drops_it(github, ctx, kelso_env):
  github.hello_world()
  RepoAddJob.call({"url": URL}, ctx, started_by="test")

  fresh = KelsoCtx(load_config_file(kelso_env.config))
  RepoRemoveJob.call({"name": "kelso"}, fresh, started_by="test")

  assert "kelso" not in load_config_file(kelso_env.config).repos


def test_repo_remove_job_refuses_local(ctx):
  with pytest.raises(ValueError, match="built in"):
    RepoRemoveJob.call({"name": "local"}, ctx, started_by="test")


def test_repo_jobs_are_recorded_as_activity(github, ctx):
  github.hello_world()
  job = RepoAddJob.call({"url": URL}, ctx, started_by="test")

  assert job.state == "done"
  assert job.log
  assert "Mirrored 1 apps" in (ctx.config.activity_root / job.log).read_text()


def test_a_duplicate_name_never_reaches_the_config_file(github, ctx, kelso_env):
  """The name is a table key, so a second write would overwrite the first."""
  github.hello_world()
  repo_lib.add(ctx, URL)
  written = kelso_env.config.read_text()

  # A stale ctx is the realistic case: the caller loaded config before the add.
  with pytest.raises(ValueError, match="already exists"):
    repo_lib.add(ctx, URL)

  assert kelso_env.config.read_text() == written
  assert "kelso" in load_config_file(kelso_env.config).repos


def test_local_cannot_be_shadowed_by_a_configured_repo(ctx, kelso_env):
  before = kelso_env.config.read_text()
  with pytest.raises(ValueError, match="built-in repo"):
    repo_lib.add(ctx, URL, name="local")
  assert kelso_env.config.read_text() == before
