"""`kelso cleanup` and snapshot retention."""

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


def _archives(kelso_env, app: str) -> list[str]:
  folder = kelso_env.root / "snapshots" / app
  return sorted(p.name for p in folder.glob("*.tar.gz")) if folder.is_dir() else []


def _fake_archives(kelso_env, app: str, *names: str) -> None:
  folder = kelso_env.root / "snapshots" / app
  folder.mkdir(parents=True, exist_ok=True)
  for name in names:
    (folder / f"{name}.tar.gz").write_bytes(b"x" * 100)


def test_snapshot_take_keeps_only_snapshot_max_count(kelso_env):
  assert kelso_env.run("load", BASIC).returncode == 0
  assert kelso_env.run("config", BASIC, "--set", "snapshot_max_count=2").returncode == 0
  for label in ("a", "b", "c"):
    taken = kelso_env.run("snapshot", "take", BASIC, "--label", label)
    assert taken.returncode == 0, taken.stderr

  assert [name.split("_")[-1] for name in _archives(kelso_env, BASIC)] == [
    "b.tar.gz",
    "c.tar.gz",
  ]


def test_cleanup_lists_by_default_and_deletes_only_with_apply(kelso_env):
  assert kelso_env.run("load", BASIC).returncode == 0
  assert kelso_env.run("config", BASIC, "--set", "snapshot_max_count=1").returncode == 0
  _fake_archives(kelso_env, BASIC, "2026-01-01_00-00Z", "2026-01-02_00-00Z")
  kelso_env.seed_db(ORPHAN_ROUTE)

  shown = kelso_env.run("cleanup")
  assert shown.returncode == 0, shown.stderr
  assert "2026-01-01_00-00Z" in shown.stdout
  assert "2026-01-02_00-00Z" not in shown.stdout
  assert "io.example.abandoned" in shown.stdout
  assert len(_archives(kelso_env, BASIC)) == 2
  assert "io.example.abandoned" in kelso_env.read_db().get("routes", {})

  applied = kelso_env.run("cleanup", "--apply")
  assert applied.returncode == 0, applied.stderr
  assert _archives(kelso_env, BASIC) == ["2026-01-02_00-00Z.tar.gz"]
  assert "io.example.abandoned" not in kelso_env.read_db().get("routes", {})


def test_snapshots_of_a_purged_app_are_kept(kelso_env):
  _fake_archives(kelso_env, "io.example.gone", "2026-01-01_00-00Z", "2026-01-02_00-00Z")

  assert kelso_env.run("cleanup", "--apply").returncode == 0
  assert len(_archives(kelso_env, "io.example.gone")) == 2


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
