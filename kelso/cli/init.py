import argparse
import os
import secrets
from pathlib import Path

from kelso.cli.service import NO_SYSTEMD, install_service
from kelso.lib import service
from kelso.lib.config import (
  CONF_DIR,
  MASTER_KEYFILE,
  VAR_DIRS,
  VOLUME_KINDS,
  load_config_file,
)
from kelso.lib.logtab import LogTab
from kelso.lib.receipt import volume_root_lines
from kelso.lib.repo import LOCAL_REPO

DEFAULT_ROOT = Path("~/.kelso")

CONFIG_TEMPLATE = """\
# Kelso configuration — edit this file to change your setup.
# Paths are relative to the directory containing this file unless absolute.
# Installed apps live in var/run/, and the master key, kelsodb and per-app
# config in conf/; those two are fixed.

repos_root = "repos"
port_base = 41000

# Repos are where the catalog comes from. `repos/local` is always there and is
# where you drop bundles by hand. Add more with `kelso repo add`, which writes
# tables like the ones below -- a directory on this machine, or a folder in a
# GitHub repository that kelso mirrors into repos/<name>.
#
# An app id carried by two repos is ambiguous: `kelso doctor` reports those,
# and you install one by naming its repo, `kelso install <app>@<repo>`.
#
# Adding a repo is a standing commitment to whatever appears in it later, not
# just to what is in it today. These two ship enabled; remove either table to
# drop it, or run `kelso repo remove <name>`.

# The apps kelso maintains and expects you to actually run.
[repo.staples]
url = "github://nepthar/kelso/main/apps"

# Small apps that demonstrate one feature each. Useful while learning what a
# manifest can do, and safe to remove once you are done.
[repo.demos]
url = "github://nepthar/kelso/main/demo-apps"

# A directory on this machine, for bundles you are writing yourself:
#
# [repo.dev]
# path = "~/code/bundles"

# The address by which kelso is reachable on your network, used for setting
# up routes. Every route provider that proxies traffic points at it, so it is
# required as soon as one is configured.
# kelso_address = "10.0.0.5"

# Routes are auto-assigned to this provider tag on first stage (like a config
# default), unless marked private=true in the manifest. The reserved tag
# "none" is a built-in noop and is the default when this key is omitted.
# default_route_provider = "web"

# Optional: reverse-proxy (or other) providers that publish app routes.
# Each block is tagged by you ("web", "lan", "homelab", …); `kind` selects
# the implementation. Kind-specific settings go under `args`. Store the
# password with `kelso config-sys --stdin route_provider.web.password`,
# then verify with `kelso routes check web`. Assign routes with
# `kelso config <app> --route main=web`.
#
# [route_provider.web]
# kind   = "nginx_proxy_manager"
# domain = "example.com"
# [route_provider.web.args]
# endpoint        = "http://npm-host:81"
# email           = "admin@example.com"
# password_secret = "route_provider.web.password"

# Pangolin publishes each route as a public HTTP resource on `domain`, with a
# target on `site` pointing at kelso_address. `endpoint` must be https -- the
# API key is a bearer token on every call. `org_id` and `site` are the names in
# the Pangolin dashboard URL: .../<org_id>/settings/sites/<site>/general. Store
# the key with `kelso config-sys --stdin route_provider.tunnel.api_key`.
#
# [route_provider.tunnel]
# kind   = "pangolin"
# domain = "example.com"
# [route_provider.tunnel.args]
# endpoint       = "https://pangolin-host:3003"
# org_id         = "my-org"
# site           = "substantial-atractaspis-branchi"
# api_key_secret = "route_provider.tunnel.api_key"

# Cloudflare Tunnel publishes each route as an ingress rule on a remotely-managed
# tunnel plus a proxied CNAME in the zone, so nothing is exposed on your router.
# Run the connector itself with `kelso install cloudflared`. `account_id` and
# `tunnel_id` are in the Zero Trust dashboard; the API token needs Account >
# Cloudflare Tunnel: Edit and Zone > DNS: Edit. Store the token with
# `kelso config-sys --stdin route_provider.cf.api_token`.
#
# [route_provider.cf]
# kind   = "cloudflare_tunnel"
# domain = "example.com"
# [route_provider.cf.args]
# account_id       = "0123456789abcdef0123456789abcdef"
# tunnel_id        = "8a7b6c5d-4e3f-2a1b-0c9d-8e7f6a5b4c3d"
# api_token_secret = "route_provider.cf.api_token"
# # zone_id is optional; kelso looks the zone up by domain when it is omitted.

# Optional: tagged host paths that apps with kind = "host" volumes can bind to.
# Paths must exist before `kelso config|start --bind`. Assign with
# `kelso config <app> --bind media=media`.
# Set require_mount = true for network shares or external drives so an empty
# mount-point directory is refused when the share is not mounted.
#
# [host_volume.media]
# path          = "/mnt/media"
# readonly      = true
# require_mount = true
"""


def register(subparsers) -> None:
  parser = subparsers.add_parser("init", help="Initialize a kelso root directory")
  parser.add_argument(
    "--no-mirror",
    action="store_true",
    help="Skip fetching the default repos; `kelso repo update` gets them later",
  )
  parser.set_defaults(func=run)


def _mirror_default_repos(config, conn) -> None:
  """Fetch the repos the template ships with, so day one is not an empty store.

  Best-effort on purpose: `init` otherwise touches nothing but the filesystem,
  and an install on a plane should still produce a working kelso root. A repo
  that does not mirror now is still configured, and `kelso repo update` picks
  it up later.
  """
  from kelso.lib import repo as repo_lib
  from kelso.lib.kelso import KelsoCtx

  remotes = [r for r in config.repos.values() if r.mirrored]
  if not remotes:
    return

  conn.out("")
  ctx = KelsoCtx(config)
  for repo in remotes:
    try:
      result = repo_lib.mirror(repo, ctx)
    except Exception as e:
      conn.err(
        f"Could not mirror {repo.name} from {repo.describe()}: {e}\n"
        f"  It is still configured. Run `kelso repo update {repo.name}` "
        f"when you can reach GitHub."
      )
      continue
    conn.out(f"Mirrored {repo.name}: {len(result.bundles)} apps at {result.sha[:8]}")


def run(args: argparse.Namespace, _ctx, conn) -> None:
  default = Path(os.environ.get("KELSO_ROOT", DEFAULT_ROOT)).expanduser()
  if getattr(args, "root", None):
    default = Path(args.root).expanduser()
  response = conn.read(f"Kelso root directory [{default}]: ").strip()
  root = Path(response if response else default).expanduser().resolve()

  if root.exists() and not root.is_dir():
    conn.err(f"Error: {root} exists and is not a directory")
    raise SystemExit(1)

  config_path = root / "config.toml"
  if config_path.exists():
    conn.err(f"Error: config already exists at {config_path}")
    conn.err("If you want to re-initialize, remove it first.")
    raise SystemExit(1)

  (root / "repos" / LOCAL_REPO).mkdir(parents=True, exist_ok=True)
  (root / CONF_DIR / "apps").mkdir(parents=True, exist_ok=True)
  for kind in VOLUME_KINDS:
    # A link made before init is where the operator wants this kind to live.
    kind_root = root / "volumes" / kind
    if not kind_root.is_symlink():
      kind_root.mkdir(parents=True, exist_ok=True)
  for name in VAR_DIRS:
    (root / "var" / name).mkdir(parents=True, exist_ok=True)

  config_path.write_text(CONFIG_TEMPLATE)

  master_key_path = root / CONF_DIR / MASTER_KEYFILE
  LogTab(master_key_path, title="Kelso Master Key").write(
    "master_key", secrets.token_hex(128)
  )
  master_key_path.chmod(0o600)

  config = load_config_file(config_path)

  conn.out(f"Initialized kelso root at {root}")
  conn.out(f"  config:      {config_path}")
  conn.out(f"  conf:        {root / CONF_DIR} (master.key, kelsodb, apps)")
  conn.out(f"  repos:       {root / 'repos'}")
  conn.out(f"  var:         {root / 'var'} ({', '.join(VAR_DIRS)})")
  conn.out("  volumes:")
  for line in volume_root_lines(config):
    conn.out(f"    {line}")
  conn.out(
    "    To keep one of these somewhere else -- bulk on a NAS, say -- replace\n"
    '    its directory with a symlink. See "Where volumes live" in the README,\n'
    "    which covers what to link to on a share that may not be mounted."
  )
  if args.no_mirror:
    conn.out("\nSkipped mirroring the default repos (--no-mirror).")
    conn.out("  Fetch them with `kelso repo update`.")
  else:
    _mirror_default_repos(config, conn)

  conn.out("")
  if not service.has_systemd():
    conn.out(NO_SYSTEMD)
  else:
    try:
      install_service(config.config_path, conn)
    except RuntimeError as e:
      conn.err(str(e))

  conn.out(f"\nTo change your configuration, edit {config_path}")
  conn.out(
    "\nNext: pick something from `kelso catalog`, then\n"
    "  kelso install <app>   install it without starting it\n"
    "  kelso start <app>     start it (installing first if needed)\n"
    "  kelso stop <app>      stop it\n"
    "  kelso uninstall <app> remove the installation, keeping data and config"
  )
  conn.out(
    "\nThe `demos` repo is there to explore what an app can do. You may wish "
    "to remove\nit once you are finished: `kelso repo remove demos`."
  )

  default_root = DEFAULT_ROOT.expanduser().resolve()
  if root != default_root:
    conn.out(
      f"\nThis root is not the default. Persist it with:\n"
      f"  export KELSO_ROOT={root}\n"
      f"or pass `--root {root}` on every kelso command."
    )
