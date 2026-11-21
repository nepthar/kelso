import json
from importlib.metadata import distribution


def _version() -> str:
  dist = distribution("kelso")
  source = json.loads(dist.read_text("direct_url.json") or "{}")
  commit = source.get("vcs_info", {}).get("commit_id")
  if commit:
    return f"{dist.version} ({commit[:7]})"
  if source.get("dir_info", {}).get("editable"):
    return f"{dist.version} (dev)"
  return dist.version


VERSION = _version()
