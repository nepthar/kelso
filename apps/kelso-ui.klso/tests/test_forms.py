"""Form posts reach kelsod with the right body, and land back on a GET."""


def test_app_config_drops_blanks(client, fake):
  response = client.post(
    "/apps/kelso-ui",
    data={"action": "config", "set.subdomain": " web ", "set.admin_pass": ""},
  )
  assert response.status_code == 303
  assert response.headers["location"] == "/apps/kelso-ui?ok=Saved"
  assert fake.posts == [
    ("POST", "/apps/kelso-ui/config-response", {"values": {"subdomain": "web"}})
  ]


def test_app_config_error_comes_back_as_err(client, fake):
  fake.fail = "no such field"
  response = client.post("/apps/kelso-ui", data={"action": "config", "set.x": "1"})
  assert response.headers["location"] == "/apps/kelso-ui?err=no+such+field"


def test_host_volume_create_and_delete(client, fake):
  response = client.post(
    "/volumes",
    data={"action": "create", "tag": "media", "path": "/mnt/m", "readonly": "on"},
  )
  assert response.headers["location"] == "/volumes?ok=Added+host+volume+media"
  response = client.post("/volumes", data={"action": "delete", "tag": "media"})
  assert response.headers["location"] == "/volumes?ok=Removed+host+volume+media"
  assert fake.posts == [
    (
      "POST",
      "/host-volumes",
      {"tag": "media", "path": "/mnt/m", "readonly": True, "require_mount": False},
    ),
    ("DELETE", "/host-volumes/media", None),
  ]


def test_route_provider_new_keeps_kind_on_error(client, fake):
  fake.fail = "bad domain"
  response = client.post("/routes/fresh", data={"kind": "pangolin", "set.domain": "x"})
  assert response.headers["location"] == "/routes/fresh?kind=pangolin&err=bad+domain"


def test_route_provider_saved(client, fake):
  response = client.post("/routes/web", data={"set.domain": "example.test"})
  assert response.headers["location"] == "/routes?ok=Saved+web"
  assert fake.posts[0][1] == "/route-providers/web/config-response"


def test_job_submit_and_poll(client, fake):
  response = client.post("/jobs", json={"verb": "stop", "args": {"app": "kelso-ui"}})
  assert response.status_code == 202
  assert fake.posts == [
    ("POST", "/jobs", {"verb": "stop", "args": {"app": "kelso-ui"}})
  ]
  assert client.get("/jobs/j1").json()["state"] == "done"


def test_job_submit_rejects_malformed(client, fake):
  assert client.post("/jobs", json={"verb": "stop"}).status_code == 400
  assert client.post("/jobs", content=b"nope").status_code == 400
  assert fake.posts == []
