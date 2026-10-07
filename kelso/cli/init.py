import argparse
import logging
import os
import socket
from pathlib import Path

from kelso.cli import phrase
from kelso.cli.service import NO_SYSTEMD, install_service
from kelso.lib import git, recovery, service
from kelso.lib.config import (
  CONF_DIR,
  MASTER_KEYFILE,
  ROOT_LOCATIONS,
  VAR_DIRS,
  VOLUME_KINDS,
  load_config_file,
)
from kelso.lib.doctor import tool_problems
from kelso.lib.kelso import KelsoCtx
from kelso.lib.receipt import volume_root_lines
from kelso.lib.repo import LOCAL_REPO

logger = logging.getLogger("kelso.cli")

DEFAULT_ROOT = Path("~/kelso")
DEMO_REPO = "github://nepthar/kelso/main/demo-apps"

CONFIG_TEMPLATE = """\
# Kelso configuration — edit this file to change your setup.
# Paths are relative to the directory containing this file unless absolute.
# Loaded apps live in var/run/, and the master key, kelsodb and per-app
# config in conf/; those two are fixed.

repos_root = "repos"
port_base = 41000

# The address by which kelso is reachable on your network, used for setting
# up routes. Every route provider that proxies traffic points at it, so it is
# required as soon as one is configured. A top-level key: keep it above the
# first [table].
# kelso_address = "10.0.0.5"

# Routes are auto-assigned to this provider tag on first load (like a config
# default), unless marked private=true in the manifest. The reserved tag
# "none" is a built-in noop and is the default when this key is omitted.
# default_route_provider = "web"

# Repos are where the catalog comes from. `repos/local` is always there and is
# where you drop bundles by hand. Add more with `kelso repo add`, which writes
# tables like the ones below -- a directory on this machine, or a folder in a
# GitHub repository that kelso mirrors into repos/<name> with git.
#
# An app id carried by two repos is ambiguous: `kelso system doctor` reports those,
# and you load one by naming its repo, `kelso load <app>@<repo>`.
#
# Adding a repo is a standing commitment to whatever appears in it later, not
# just to what is in it today. This one ships enabled; remove the table to drop
# it, or run `kelso repo remove <name>`.

# The apps kelso maintains and expects you to actually run.
[repo.staples]
url = "github://nepthar/kelso/main/apps"

# A directory on this machine, for bundles you are writing yourself:
#
# [repo.dev]
# path = "~/code/bundles"

# Backups: every app's data volumes, its configuration and the bundle it was
# loaded from, plus kelso's own state, into backups/ next to this file. Point
# backups/ at another disk by replacing it with a link, as with a volume root;
# require_mount refuses to back up while that disk is not mounted. Keep is per
# app, and small on purpose.
#
# [backup]
# schedule      = "0 3 * * *"
# require_mount = false
# keep          = { daily = 3, weekly = 2, monthly = 1, manual = 5 }

# Optional: reverse-proxy (or other) providers that publish app routes.
# Each block is tagged by you ("web", "lan", "homelab", …); `kind` selects
# the implementation. Kind-specific settings go under `args`. Store the
# password with `kelso system secret --stdin route_provider.web.password`,
# then verify with `kelso route check web`. Assign routes with
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
# the key with `kelso system secret --stdin route_provider.tunnel.api_key`.
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
# Run the connector itself with `kelso load cloudflared`. `account_id` and
# `tunnel_id` are in the Zero Trust dashboard; the API token needs Account >
# Cloudflare Tunnel: Edit and Zone > DNS: Edit. Store the token with
# `kelso system secret --stdin route_provider.cf.api_token`.
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


TUTORIAL = f"""
Take kelso for a spin with its demo apps, small ones that each show off a
feature. Add them as a repo, then start one and run its commands:
  kelso repo add {DEMO_REPO} --name demos
  kelso start demo-cron              load and start it
  kelso cmd demo-cron                list the commands it declares
  kelso cmd demo-cron stamp hello    run one
  kelso cmd demo-cron show           and another, to see what it wrote
  kelso logs demo-cron               its containers' output
  kelso stop demo-cron
`kelso repo list` shows every app you can start. When you are done exploring,
`kelso unload demo-cron` and `kelso repo remove demos` tidy up.

For the web UI, choose its admin password and start it; it prints the address
to open:
  kelso start kelso-ui --set admin_pass=<password>"""


# kelsod runs under systemd's user manager, which keeps the groups it started
# with: one started before `usermod -aG docker` cannot reach docker until it
# restarts, however many times you log in again.
RESTART = """
Finally, restart this machine before trying any of that (`sudo reboot`). It
completes the installation: kelsod comes back up with every group it needs, and
you see that it starts at boot on its own."""


def lan_address() -> str | None:
  """This machine's address on the network its default route leaves by."""
  # A UDP connect sends nothing; it only makes the kernel pick a source address.
  with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
    try:
      probe.connect(("192.0.2.1", 9))
      address = probe.getsockname()[0]
    except OSError:
      return None
  return None if address.startswith(("127.", "0.")) else address


def register(subparsers) -> None:
  parser = subparsers.add_parser("init", help="Initialize a kelso root directory")
  parser.add_argument(
    "-y",
    "--yes",
    action="store_true",
    help="Use the default root directory instead of asking for one",
  )
  parser.add_argument(
    "--no-mirror",
    action="store_true",
    help="Skip fetching the default repos; `kelso repo update` gets them later",
  )
  parser.set_defaults(func=run)


def _mirror_default_repos(config) -> None:
  """Fetch the repos the template ships with, so day one is not an empty store.

  Best-effort on purpose: `init` otherwise touches nothing but the filesystem,
  and an install on a plane should still produce a working kelso root. A repo
  that does not mirror now is still configured, and `kelso repo update` picks
  it up later.
  """
  from kelso.lib import repo as repo_lib

  remotes = [r for r in config.repos.values() if r.mirrored]
  if not remotes:
    return

  print("")
  ctx = KelsoCtx(config)
  for repo in remotes:
    try:
      result = repo_lib.mirror(repo, ctx)
    except Exception as e:
      logger.warning(
        f"Could not mirror {repo.name} from {repo.describe()}: {e}\n"
        f"  It is still configured. Run `kelso repo update {repo.name}` "
        f"when you can reach GitHub."
      )
      continue
    print(f"Mirrored {repo.name}: {len(result.bundles)} apps at {result.sha[:8]}")


def run(args: argparse.Namespace, _ctx) -> None:
  missing = tool_problems()
  if missing:
    raise RuntimeError(
      "kelso needs these working before it can be set up:\n"
      + "\n".join(f"  {f.subject}: {f.message}" for f in missing)
    )

  default = Path(os.environ.get("KELSO_ROOT") or DEFAULT_ROOT).expanduser()
  response = "" if args.yes else input(f"Kelso root directory [{default}]: ").strip()
  root = Path(response if response else default).expanduser().resolve()

  if root.exists() and not root.is_dir():
    raise ValueError(f"{root} exists and is not a directory")

  config_path = root / "config.toml"
  if config_path.exists():
    raise ValueError(
      f"config already exists at {config_path}. "
      f"If you want to re-initialize, remove it first."
    )

  (root / "repos" / LOCAL_REPO).mkdir(parents=True, exist_ok=True)
  git.adopt(root / "repos" / LOCAL_REPO)
  (root / CONF_DIR / "apps").mkdir(parents=True, exist_ok=True)
  for kind in VOLUME_KINDS:
    # A link made before init is where the operator wants this kind to live.
    kind_root = root / "volumes" / kind
    if not kind_root.is_symlink():
      kind_root.mkdir(parents=True, exist_ok=True)
  for name in VAR_DIRS:
    (root / "var" / name).mkdir(parents=True, exist_ok=True)
  # Like a volume root: a link made before init is where backups should go.
  backups = root / "backups"
  if not backups.is_symlink():
    backups.mkdir(exist_ok=True)

  address = lan_address()
  template = CONFIG_TEMPLATE
  if address:
    template = template.replace(
      '# kelso_address = "10.0.0.5"', f'kelso_address = "{address}"'
    )
  config_path.write_text(template)

  # A new root has no secrets, so making its key is all a rekey would do.
  master_key_path = root / CONF_DIR / MASTER_KEYFILE
  entropy = recovery.new_entropy()
  recovery.write_seed(master_key_path, entropy)

  config = load_config_file(config_path)
  kelso_id = KelsoCtx(config).kelso_db.kelso_id()

  print(f"Initialized kelso {kelso_id} at {root}")
  print(f"  config:      {config_path}")
  if address:
    print(f"  address:     {address} (detected; edit kelso_address if it is wrong)")
  else:
    print(
      "  address:     not detected; set kelso_address before adding a route provider"
    )
  print(f"  conf:        {root / CONF_DIR} (master.key, kelsodb, apps)")
  print(f"  repos:       {root / 'repos'}")
  print(f"  backups:     {root / 'backups'}")
  print(f"  var:         {root / 'var'} ({', '.join(VAR_DIRS)})")
  print("  volumes:")
  for line in volume_root_lines(config):
    print(f"    {line}")
  print(
    "    To keep one of these somewhere else -- bulk on a NAS, say -- replace\n"
    '    its directory with a symlink. See "Volume Storage Locations" in the README,\n'
    "    which covers what to link to on a share that may not be mounted."
  )
  if args.no_mirror:
    print("\nSkipped mirroring the default repos (--no-mirror).")
    print("  Fetch them with `kelso repo update`.")
  else:
    _mirror_default_repos(config)

  print("")
  if not service.has_systemd():
    print(NO_SYSTEMD)
  else:
    try:
      install_service(config.kelso_root)
    except RuntimeError as e:
      logger.warning(str(e))

  if root not in (r.expanduser().resolve() for r in ROOT_LOCATIONS):
    print(
      f"\nKelso only finds {root} through KELSO_ROOT. Add this to your shell "
      f"profile:\n  export KELSO_ROOT={root}"
    )

  print(f"\nTo change your configuration, edit {config_path}")
  print(TUTORIAL)

  # Last, so it is what is on screen when init is done.
  print("")
  phrase.show(recovery.phrase(entropy))
  print("`kelso system recovery-phrase` shows it again.")

  if service.has_systemd():
    print(RESTART)
