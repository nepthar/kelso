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
  assert (
    response.headers["location"] == "/done?ok=Added+host+volume+media&then=%2Fvolumes"
  )
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


def test_orphaned_volume_delete_names_app_and_volume(client, fake):
  response = client.post(
    "/volumes",
    data={"action": "delete-volume", "app_id": "mealie", "name": "old db"},
  )
  assert response.headers["location"] == "/volumes?ok=Deleted+volume+old+db+of+mealie"
  assert fake.posts == [("DELETE", "/volumes/mealie/old db", None)]


def test_route_provider_new_keeps_kind_on_error(client, fake):
  fake.fail = "bad domain"
  response = client.post("/routes/fresh", data={"kind": "pangolin", "set.domain": "x"})
  assert response.headers["location"] == "/routes/fresh?kind=pangolin&err=bad+domain"


def test_route_provider_saved(client, fake):
  response = client.post("/routes/web", data={"set.domain": "example.test"})
  assert response.headers["location"] == "/done?ok=Saved+web&then=%2Froutes"
  assert fake.posts[0][1] == "/route-providers/web/config-response"


def test_published_route_removed(client, fake):
  response = client.post("/routes", data={"provider": "odd name", "subdomain": "a"})
  assert response.headers["location"] == "/routes?ok=Removed+route+a+at+odd+name"
  assert fake.posts == [("DELETE", "/route-providers/odd name/routes/a", None)]


def test_job_submit_and_poll(client, fake):
  response = client.post("/jobs", json={"verb": "stop", "args": {"app": "kelso-ui"}})
  assert response.status_code == 202
  assert fake.posts == [
    (
      "POST",
      "/jobs",
      {"verb": "stop", "args": {"app": "kelso-ui"}, "started_by": "kelso_ui"},
    )
  ]
  assert client.get("/jobs/j1").json()["state"] == "done"


def test_job_submit_rejects_malformed(client, fake):
  assert client.post("/jobs", json={"verb": "stop"}).status_code == 400
  assert client.post("/jobs", content=b"nope").status_code == 400
  assert fake.posts == []


def test_network_decisions_pass_through_as_json(client, fake):
  decisions = {"tmdb.org": "allow", "weather.com": None}
  response = client.post("/apps/kelso-ui/net.json", json={"decisions": decisions})
  assert response.status_code == 200
  assert fake.posts == [("POST", "/apps/kelso-ui/net", {"decisions": decisions})]
  assert client.post("/apps/kelso-ui/net.json", json=["x"]).status_code == 400
  fake.fail = "tmdb.org: decide allow, deny, or null to forget"
  refused = client.post("/apps/kelso-ui/net.json", json={"decisions": {}})
  assert refused.status_code == 400
  assert "decide allow" in refused.json()["error"]
