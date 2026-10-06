"""`kelso app` -- working on a bundle where it lives, in its repo."""

import argparse
import logging
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

from kelso.lib.kelso import KelsoCtx
from kelso.lib.lifecycle.edit import ManifestMoved, edit_manifest, editable_manifest

logger = logging.getLogger("kelso.cli")


def register(subparsers) -> None:
  parser = subparsers.add_parser("app", help="Work on an app's bundle in its repo")
  sub = parser.add_subparsers(dest="app_command", required=True)

  edit = sub.add_parser(
    "edit",
    help="Edit an app's manifest in its repo, check it, and commit it",
  )
  edit.add_argument("app", help="App ID, or <app>@<repo>")
  edit.add_argument(
    "-m", "--message", default="", help="What changed, for the commit message"
  )
  edit.set_defaults(func=run_edit, activity="app-edit")


def run_edit(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  manifest = editable_manifest(ctx, args.app)
  suffix = "".join(manifest.path.suffixes) or ".toml"
  fd, name = tempfile.mkstemp(prefix=f"{manifest.app_id}-", suffix=suffix)
  draft = Path(name)
  with os.fdopen(fd, "w") as f:
    f.write(manifest.text)

  while True:
    _open_editor(draft)
    text = draft.read_text()
    try:
      result = edit_manifest(ctx, args.app, text, manifest.base, args.message)
    except ManifestMoved:
      logger.error("Your edit is kept in %s", draft)
      raise
    except ValueError as e:
      if sys.stdin.isatty() and input(f"{e}\nEdit again? [Y/n] ").strip().lower() in (
        "",
        "y",
        "yes",
      ):
        continue
      logger.error("Your edit is kept in %s", draft)
      raise
    break

  draft.unlink()
  if not result.diff:
    print("No changes")
    return
  print(result.diff, end="")
  logger.info("Committed %s as %s", manifest.path, result.commit[:8])


def _open_editor(path: Path) -> None:
  editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
  if subprocess.run([*shlex.split(editor), str(path)]).returncode != 0:
    raise RuntimeError(f"{editor} exited with an error; your edit is kept in {path}")
