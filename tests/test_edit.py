"""Editing a bundle's manifest in its repo: what is editable, the checks, the commit."""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kelso.daemon.api import create_app
from kelso.jobs import JobRunner
from kelso.lib import git
from kelso.lib.config import load_config_file
from kelso.lib.doctor import diagnose
from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle.edit import ManifestMoved, edit_manifest, editable_manifest
from kelso.lib.views import catalog_view

MD_BUNDLE = """\
# Notes

```toml klso_path="manifest.toml"
[app]
version = "0.1.0"
display_name = "Notes"

[run.main]
image = "alpine:latest"
```
"""


@pytest.fixture
def local(kelso_env) -> Path:
  """`repos/local` as its own git repository, fixtures committed."""
  path = kelso_env.root / "repos" / "local"
  (path / "notes.klso.md").write_text(MD_BUNDLE)
  assert git.adopt(path)
  return path


def ctx_for(kelso_env) -> KelsoCtx:
  return KelsoCtx(load_config_file(kelso_env.config))


def last_commit(repo: Path) -> str:
  return git.git("log", "-1", "--format=%s", cwd=repo)


def test_an_edit_is_checked_written_and_committed(kelso_env, local):
  ctx = ctx_for(kelso_env)
  manifest = editable_manifest(ctx, "ports-demo")
  text = manifest.text.replace("Port allocation fixture", "Ports, edited")

  result = edit_manifest(ctx, "ports-demo", text, manifest.base, "Say edited")

  assert '-description  = "Port allocation fixture"' in result.diff
  assert '+description  = "Ports, edited"' in result.diff
  assert result.diff.startswith("--- a/ports-demo.klso/manifest.toml")
  assert (local / "ports-demo.klso" / "manifest.toml").read_text() == text
  assert last_commit(local) == "Say edited"
  assert git.uncommitted(local) == 0


def test_a_commit_message_defaults_to_the_app_and_date(kelso_env, local):
  ctx = ctx_for(kelso_env)
  manifest = editable_manifest(ctx, "ports-demo")

  edit_manifest(ctx, "ports-demo", manifest.text + "\n# more\n", manifest.base)

  assert last_commit(local).startswith("Edited ports-demo on ")


def test_an_unchanged_manifest_succeeds_and_commits_nothing(kelso_env, local):
  ctx = ctx_for(kelso_env)
  manifest = editable_manifest(ctx, "ports-demo")
  before = git.head(local)

  result = edit_manifest(ctx, "ports-demo", manifest.text, manifest.base)

  assert result.diff == ""
  assert result.commit is None
  assert git.head(local) == before


def test_an_invalid_manifest_is_refused_and_nothing_is_written(kelso_env, local):
  ctx = ctx_for(kelso_env)
  manifest = editable_manifest(ctx, "ports-demo")

  with pytest.raises(ValueError, match="validation error"):
    edit_manifest(ctx, "ports-demo", "[run.main]\nimage = 3\n", manifest.base)

  assert editable_manifest(ctx, "ports-demo").text == manifest.text
  assert git.uncommitted(local) == 0


def test_a_markdown_bundle_is_edited_whole(kelso_env, local):
  ctx = ctx_for(kelso_env)
  manifest = editable_manifest(ctx, "notes")
  assert manifest.text == MD_BUNDLE

  edited = MD_BUNDLE.replace("# Notes", "# Notes, edited")
  edit_manifest(ctx, "notes", edited, manifest.base)
  assert (local / "notes.klso.md").read_text() == edited

  unclosed = edited.rstrip().removesuffix("```")
  with pytest.raises(ValueError, match="unclosed file block"):
    edit_manifest(ctx, "notes", unclosed, editable_manifest(ctx, "notes").base)


def test_a_file_changed_since_it_was_read_is_not_overwritten(kelso_env, local):
  ctx = ctx_for(kelso_env)
  manifest = editable_manifest(ctx, "ports-demo")
  file = local / "ports-demo.klso" / "manifest.toml"
  file.write_text(manifest.text + "\n# someone else\n")
  git.commit(local, file, "Someone else")

  with pytest.raises(ManifestMoved):
    edit_manifest(ctx, "ports-demo", manifest.text + "\n# me\n", manifest.base)


def test_browser_line_endings_are_not_an_edit(kelso_env, local):
  ctx = ctx_for(kelso_env)
  manifest = editable_manifest(ctx, "ports-demo")

  crlf = manifest.text.replace("\n", "\r\n")
  assert edit_manifest(ctx, "ports-demo", crlf, manifest.base).diff == ""


# --- what makes a manifest editable -----------------------------------------


def refusal(kelso_env, target: str = "ports-demo") -> str:
  with pytest.raises(ValueError) as refused:
    editable_manifest(ctx_for(kelso_env), target)
  return str(refused.value)


def test_a_repo_without_its_own_git_repository_is_not_editable(kelso_env):
  assert "no git repository of its own" in refusal(kelso_env)


def test_a_repo_inside_another_git_repository_is_not_editable(kelso_env):
  git.git("init", "-q", cwd=kelso_env.root)
  assert "no git repository of its own" in refusal(kelso_env)


def test_staged_changes_anywhere_in_the_repo_block_editing(kelso_env, local):
  (local / "scratch.txt").write_text("x")
  git.git("add", "scratch.txt", cwd=local)
  assert "staged changes" in refusal(kelso_env)


def test_an_operation_in_progress_blocks_editing(kelso_env, local):
  (local / ".git" / "MERGE_HEAD").write_text(git.head(local) or "")
  assert "middle of a merge" in refusal(kelso_env)


def test_a_detached_head_blocks_editing(kelso_env, local):
  git.git("checkout", "-q", "--detach", cwd=local)
  assert "not on a branch" in refusal(kelso_env)


def test_a_manifest_with_uncommitted_changes_is_not_editable(kelso_env, local):
  file = local / "ports-demo.klso" / "manifest.toml"
  file.write_text(file.read_text() + "\n# by hand\n")
  assert "changes git has not recorded" in refusal(kelso_env)
  # Other bundles in the repo are still editable.
  editable_manifest(ctx_for(kelso_env), "routes-demo")


def test_an_untracked_manifest_is_not_editable(kelso_env, local):
  (local / "late.klso.md").write_text(MD_BUNDLE)
  assert "changes git has not recorded" in refusal(kelso_env, "late")


def test_a_bundle_linked_from_outside_the_repo_is_not_editable(kelso_env, local):
  elsewhere = kelso_env.root / "elsewhere"
  elsewhere.mkdir()
  (elsewhere / "linked.klso.md").write_text(MD_BUNDLE)
  (local / "linked.klso.md").symlink_to(elsewhere / "linked.klso.md")
  git.commit(local, local / "linked.klso.md", "Link")
  assert "is outside" in refusal(kelso_env, "linked")


# --- the catalog, the API, the CLI ------------------------------------------


def catalog_entry(kelso_env, app_id: str) -> dict:
  (repo,) = [c for c in catalog_view(ctx_for(kelso_env)) if c["name"] == "local"]
  return next(a for a in repo["apps"] if a["app_id"] == app_id)


def test_the_catalog_says_what_is_editable_and_shows_markdown_whole(kelso_env):
  assert catalog_entry(kelso_env, "ports-demo")["editable"] is False
  local = kelso_env.root / "repos" / "local"
  (local / "notes.klso.md").write_text(MD_BUNDLE)
  git.adopt(local)

  entry = catalog_entry(kelso_env, "ports-demo")
  assert entry["editable"] is True
  assert entry["base"] == editable_manifest(ctx_for(kelso_env), "ports-demo").base
  notes = catalog_entry(kelso_env, "notes")
  assert notes["text"] == MD_BUNDLE
  assert notes["markdown"] is True


@pytest.fixture
def client() -> TestClient:
  def ctx():
    config = load_config_file(Path("config.toml").resolve())
    assert config is not None
    return KelsoCtx(config)

  return TestClient(create_app(ctx, JobRunner(ctx)))


def test_editing_through_the_api(kelso_env, local, client):
  base = catalog_entry(kelso_env, "ports-demo")["base"]
  text = catalog_entry(kelso_env, "ports-demo")["text"] + "\n# api\n"

  saved = client.post("/manifests/ports-demo@local", json={"text": text, "base": base})
  assert saved.status_code == 200, saved.text
  assert "+# api" in saved.json()["diff"]
  assert saved.json()["commit"] == git.head(local)

  stale = client.post("/manifests/ports-demo", json={"text": text, "base": base})
  assert stale.status_code == 409

  bad = client.post(
    "/manifests/ports-demo",
    json={"text": "nope = [", "base": catalog_entry(kelso_env, "ports-demo")["base"]},
  )
  assert bad.status_code == 400
  assert "manifest" in bad.json()["error"]


def test_kelso_app_edit_runs_the_editor_then_commits(
  kelso_env, local, tmp_path, monkeypatch
):
  editor = tmp_path / "editor.py"
  editor.write_text(
    "import sys\n"
    "path = sys.argv[1]\n"
    "text = open(path).read().replace('Port allocation fixture', 'From the CLI')\n"
    "open(path, 'w').write(text)\n"
  )
  monkeypatch.setenv("EDITOR", f"{sys.executable} {editor}")

  result = kelso_env.run("app", "edit", "ports-demo", "-m", "From the CLI")

  assert result.returncode == 0, result.stderr
  assert '+description  = "From the CLI"' in result.stdout
  assert last_commit(local) == "From the CLI"


def test_kelso_app_edit_with_no_change_says_so(kelso_env, local, monkeypatch):
  monkeypatch.setenv("EDITOR", "true")
  result = kelso_env.run("app", "edit", "ports-demo")
  assert result.returncode == 0, result.stderr
  assert result.stdout == "No changes\n"


# --- repos and doctor ---------------------------------------------------------


def test_adding_a_local_repo_puts_it_under_git(kelso_env):
  dev = kelso_env.root.parent / "dev-apps"
  (dev / "dev-app.klso").mkdir(parents=True)
  (dev / "dev-app.klso" / "manifest.toml").write_text(
    '[app]\nversion = "0.1.0"\n\n[run.main]\nimage = "alpine:latest"\n'
  )

  assert kelso_env.run("repo", "add", str(dev), "--name", "dev").returncode == 0

  assert (dev / ".git").is_dir()
  assert git.uncommitted(dev) == 0
  assert last_commit(dev) == "Added as a kelso repo"


def test_adding_a_folder_already_under_git_leaves_it_alone(kelso_env):
  outer = kelso_env.root.parent / "outer"
  (outer / "apps").mkdir(parents=True)
  git.git("init", "-q", cwd=outer)

  assert (
    kelso_env.run("repo", "add", str(outer / "apps"), "--name", "dev").returncode == 0
  )

  assert not (outer / "apps" / ".git").exists()


def test_doctor_warns_of_uncommitted_changes_in_a_repo_kelso_commits_to(
  kelso_env, local
):
  assert diagnose(ctx_for(kelso_env)).warnings == ()
  (local / "scratch.txt").write_text("x")

  (warning,) = diagnose(ctx_for(kelso_env)).warnings
  assert warning.subject == "repo local"
  assert "1 uncommitted change(s)" in warning.message
