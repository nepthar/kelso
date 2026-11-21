import argparse
from pathlib import Path

from kelso.lib.kelso import KelsoCtx

_TOPICS = Path(__file__).parent.parent / "agent_help"
# pyproject.toml copies these in from docs/ when kelso is built.
_FROM_DOCS = ("install", "manifest", "volumes")
INDEX = "index"


def register(subparsers) -> None:
  parser = subparsers.add_parser(
    "agent-help", help="Instructions for an agent driving kelso, by topic"
  )
  parser.add_argument("topic", nargs="?", default=INDEX)
  parser.set_defaults(func=run)


def _topics() -> dict[str, Path]:
  found = {path.stem: path for path in _TOPICS.glob("*.md")}
  for name in _FROM_DOCS:
    found.setdefault(name, _TOPICS.parent.parent / "docs" / f"{name}.md")
  return {name: path for name, path in sorted(found.items()) if path.is_file()}


def run(args: argparse.Namespace, ctx: KelsoCtx | None) -> None:
  topics = _topics()
  if args.topic not in topics:
    raise ValueError(
      f"No agent-help topic {args.topic!r}; `kelso agent-help` lists them"
    )
  print(topics[args.topic].read_text().rstrip())
  if args.topic == INDEX:
    print("\n## Topics\n")
    for name, path in topics.items():
      if name != INDEX:
        title = path.read_text().partition("\n")[0].lstrip("# ")
        print(f"  kelso agent-help {name:<13} {title}")
