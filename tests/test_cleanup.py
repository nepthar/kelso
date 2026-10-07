"""`kelso cleanup`."""

BASIC = "io.p2net.basic-features"

ORPHAN_ROUTE = {
  "routes/io.example.abandoned/web": {
    "name": "web",
    "subdomain": "",
    "run_unit_name": "main",
    "host_port": 41000,
    "container_port": 8080,
    "proto": "tcp",
    "scheme": "http",
  }
}


def test_cleanup_lists_by_default_and_deletes_only_with_apply(kelso_env):
  kelso_env.seed_db(ORPHAN_ROUTE)

  shown = kelso_env.run("cleanup")
  assert shown.returncode == 0, shown.stderr
  assert "io.example.abandoned" in shown.stdout
  assert "io.example.abandoned" in kelso_env.read_db().get("routes", {})

  applied = kelso_env.run("cleanup", "--apply")
  assert applied.returncode == 0, applied.stderr
  assert "io.example.abandoned" not in kelso_env.read_db().get("routes", {})


def test_cleanup_removes_what_an_interrupted_restore_left(kelso_env):
  left = kelso_env.root / "var" / "temp" / "restore" / BASIC / "kelso"
  left.mkdir(parents=True)
  (left / "half.txt").write_text("x")

  shown = kelso_env.run("cleanup")
  assert "incomplete restore" in shown.stdout
  assert kelso_env.run("cleanup", "--apply").returncode == 0
  assert not left.parent.exists()


def test_cleanup_keeps_the_images_kelso_runs_itself(kelso_env):
  from kelso.lib.lifecycle.rootfs import ROOTFS_IMAGE
  from kelso.lib.restic import RESTIC_IMAGE

  repo, _, rest = RESTIC_IMAGE.partition(":")
  tag = rest.partition("@")[0]
  alpine, _, alpine_tag = ROOTFS_IMAGE.partition(":")
  kelso_env.set_images(
    [
      {"ID": "r1", "Repository": repo, "Tag": tag, "Size": "30MB"},
      {"ID": "a1", "Repository": alpine, "Tag": alpine_tag, "Size": "8MB"},
    ]
  )
  shown = kelso_env.run("cleanup")
  assert repo not in shown.stdout
  assert alpine not in shown.stdout


def test_cleanup_removes_only_images_nothing_uses(kelso_env):
  assert kelso_env.run("load", BASIC).returncode == 0
  kelso_env.set_images(
    [
      {"ID": "aaa", "Repository": "alpine", "Tag": "latest", "Size": "8MB"},
      {"ID": "bbb", "Repository": "alpine", "Tag": "3.0", "Size": "5MB"},
      {"ID": "ccc", "Repository": "<none>", "Tag": "<none>", "Size": "1kB"},
      {"ID": "ddd", "Repository": "someone/else", "Tag": "1", "Size": "2GB"},
    ]
  )
  kelso_env.set_containers(
    [
      {
        "app_id": "",
        "run_unit": "",
        "id": "c1",
        "state": "exited",
        "image": "someone/else:1",
      }
    ]
  )

  shown = kelso_env.run("cleanup")
  assert "alpine:3.0" in shown.stdout
  assert "ccc" in shown.stdout
  assert "alpine:latest" not in shown.stdout
  assert "someone/else" not in shown.stdout
  assert "Up to 4.8 MB" in shown.stdout

  assert kelso_env.run("cleanup", "--apply").returncode == 0
  remaining = kelso_env.run("cleanup").stdout
  assert "alpine:3.0" not in remaining
  assert "ccc" not in remaining


def test_cleanup_temp_empties_stopped_apps_and_skips_running_ones(kelso_env):
  assert kelso_env.run("load", BASIC).returncode == 0
  cache = kelso_env.volumes_root / "temp" / BASIC / "cache"
  (cache / "junk").write_text("x")
  assert kelso_env.run("config", BASIC, "--set", "admin_user=root").returncode == 0

  assert kelso_env.run("start", BASIC).returncode == 0
  running = kelso_env.run("cleanup", "--temp", "--apply")
  assert f"Leaving the temp and logs of {BASIC}" in running.stderr
  assert (cache / "junk").exists()

  assert kelso_env.run("stop", BASIC).returncode == 0
  assert kelso_env.run("cleanup", "--temp").returncode == 0
  assert (cache / "junk").exists()
  assert kelso_env.run("cleanup", "--temp", "--apply").returncode == 0
  assert cache.is_dir()
  assert not (cache / "junk").exists()
