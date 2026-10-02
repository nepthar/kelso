"""Every page renders, escapes what kelsod sends, and loads only what exists."""

import json
import re

import fakekelsod
import pytest
import web
from fakekelsod import EVIL

PAGES = [
  "/",
  "/apps/kelso-ui",
  "/apps/mealie",
  "/apps/kelso-ui/logs",
  "/apps/kelso-ui/console",
  "/apps/mealie/console",
  "/host/console",
  "/catalog",
  "/catalog?app=kelso-ui",
  "/volumes",
  "/snapshots",
  "/routes",
  "/routes/web",
  "/routes/fresh?kind=pangolin",
  "/activity",
  "/login",
]


@pytest.mark.parametrize("path", PAGES)
def test_page_renders_escaped(client, fake, path):
  response = client.get(path)
  assert response.status_code == 200, response.text
  assert response.headers["content-type"].startswith("text/html")
  assert response.headers["cache-control"] == "no-store"
  assert EVIL not in response.text
  assert "<i data-evil" not in response.text


@pytest.mark.parametrize("path", PAGES)
def test_no_inline_script(client, fake, path):
  """CSP allows 'self' scripts only; inline code would silently not run."""
  for tag in re.findall(r"<script\b[^>]*>", client.get(path).text):
    assert "src=" in tag or 'type="application/json"' in tag, tag


@pytest.mark.parametrize("path", PAGES)
def test_every_asset_is_served(client, fake, path):
  text = client.get(path).text
  for url in re.findall(r'(?:src|href)="(/static/[^"]+)"', text):
    assert "?v=dev" not in url, f"{url} names a file that does not exist"
    assert client.get(url).status_code == 200, url


@pytest.mark.parametrize("path", PAGES)
def test_nothing_loads_from_off_the_box(client, fake, path):
  """kelso-ui may run with no internet: everything it needs is under /static.

  The fonts are the exception, and boot.js adds them from script so that a
  page never waits on them; that is why no <link> for them is in the markup.
  """
  text = client.get(path).text
  for url in re.findall(r'<(?:script|link)\b[^>]*(?:src|href)="([^"]+)"', text):
    assert url.startswith(("/static/", "data:")), url


@pytest.mark.parametrize(
  "path, location",
  [
    ("/apps", "/"),
    ("/logs", "/activity"),
    ("/routes/new?tag=fresh&kind=pangolin", "/routes/fresh?kind=pangolin"),
  ],
)
def test_old_and_indirect_paths_redirect(client, path, location):
  response = client.get(path)
  assert response.status_code == 303
  assert response.headers["location"] == location


def test_unknown_path_is_a_framed_404(client):
  response = client.get("/nope/x")
  assert response.status_code == 404
  assert "No such page." in response.text
  assert "<nav>" in response.text


def test_missing_version_is_a_dash_not_an_entity(client, fake):
  row = client.get("/").text.split("/apps/jellyfin")[1].split("</tr>")[0]
  assert "—" in row
  assert "&amp;mdash;" not in row


def test_kelsod_down_renders_inside_the_frame(client, fake):
  fake.fail = "kelsod said <b>no</b>"
  response = client.get("/apps/kelso-ui")
  assert response.status_code == 502
  assert "Cannot reach kelsod" in response.text
  assert "kelsod said &lt;b&gt;no&lt;/b&gt;" in response.text
  # The handler named the page before kelsod failed it.
  assert "<h1>kelso-ui</h1>" in response.text


def test_kelsod_down_is_json_for_fetch(client, fake):
  fake.fail = "down"
  response = client.get("/apps/kelso-ui/logs.json", headers={"accept": "*/*"})
  assert response.status_code == 502
  assert response.json() == {"error": "down"}


def test_version_skew_is_announced(client, fake):
  assert "Version mismatch" not in client.get("/volumes").text
  fake.api = 18
  assert "but kelsod speaks 18" in client.get("/volumes").text


def test_notice_banner_escapes(client, fake):
  text = client.get("/routes?err=bad+%3Cb%3E").text
  assert '<div class="error"><p>bad &lt;b&gt;</p></div>' in text
  assert '<div class="notice">Added x</div>' in client.get("/volumes?ok=Added+x").text


def test_nav_marks_the_section(client, fake):
  text = client.get("/apps/kelso-ui").text
  assert re.search(r'<a href="/" title="Dashboard" class="active">', text)


def _job_buttons(text):
  buttons = re.findall(r"<button[^>]*class=\"job-open[^>]*>", text, re.S)
  return [
    {
      name: json.loads(value.replace("&#34;", '"'))
      for name, value in re.findall(r"data-(args|fields|choices)='([^']*)'", b)
    }
    | dict(re.findall(r'data-(verb|title)="([^"]*)"', b))
    for b in buttons
  ]


def test_job_buttons_carry_parseable_json(client, fake):
  buttons = _job_buttons(client.get("/apps/kelso-ui").text)
  remove = next(b for b in buttons if b["title"].startswith("Remove"))
  assert [c["verb"] for c in remove["choices"]] == ["unload", "rm", "rm", "rm", "rm"]
  assert remove["choices"][4]["args"] == {"app": "kelso-ui", "tier": "purge"}
  stop = next(b for b in buttons if b["verb"] == "stop")
  assert stop["args"] == {"app": "kelso-ui"}
  assert not any(b["verb"] == "start" for b in buttons)


def test_catalog_link_opens_its_card(client, fake):
  text = client.get("/catalog?app=kelso-ui").text
  assert 'id="catalog-shade" class="shade" data-close="/catalog">' in text
  assert '<article class="app-card" id="card-examples--kelso-ui">' in text
  assert '<article class="app-card" id="card-examples--mealie" hidden>' in text
  closed = client.get("/catalog").text
  assert 'id="catalog-shade" class="shade" hidden>' in closed


def test_nav_is_titled_with_the_daemons_hostname(client, fake):
  text = client.get("/").text
  brand = text.split('<div class="brand">')[1].split("</div>")[0]
  assert '<span class="name" title="tycho &lt;i' in brand
  assert '<a class="mark" href="https://www.nps.gov/moja/kelso-dunes.htm"' in brand
  assert '<svg class="mdi"' in brand


@pytest.mark.parametrize(
  ("hostname", "expected"),
  [
    ("Neptune.local", "Neptune"),
    ("box.home.arpa", "box.home"),
    ("tycho", "tycho"),
    ("", "Kelso 1a2b"),
  ],
)
def test_brand_drops_the_last_dot_and_falls_back_to_the_instance_id(
  monkeypatch, hostname, expected
):
  monkeypatch.setattr(web, "INSTANCE_ID", "1a2b")
  assert web.brand(hostname) == expected


def test_nav_names_the_version_and_instance_id(client, fake, monkeypatch):
  monkeypatch.setitem(web.templates.globals, "INSTANCE_ID", "1a2b")
  brand = client.get("/").text.split('<div class="brand">')[1].split("</div>")[0]
  assert '<span class="ver">kelso 0.1.0</span><span class="ver">1a2b</span>' in brand


def test_sign_in_is_titled_with_the_daemons_hostname(client, fake):
  assert "<h1>tycho &lt;i" in client.get("/login").text


def test_sign_in_uses_the_hostname_kelsod_already_gave(client, fake):
  client.get("/")
  fake.fail = "down"
  assert "<h1>tycho &lt;i" in client.get("/login").text


def test_sign_in_still_works_when_kelsod_does_not_answer(client, fake):
  fake.fail = "down"
  response = client.get("/login")
  assert response.status_code == 200
  assert "<h1>Kelso</h1>" in response.text


def test_every_page_has_the_dune_favicon(client, fake):
  for path in ("/", "/login"):
    icon = re.search(
      r'<link rel="icon" type="image/svg\+xml" href="([^"]+)">', client.get(path).text
    )
    assert icon and icon[1].startswith("data:image/svg+xml,"), path


def test_byline_links_only_an_http_url(client, fake, monkeypatch):
  text = client.get("/apps/kelso-ui").text
  assert "by Jordan " in text
  assert '<a href="https://example.com/help" rel="noopener noreferrer"' in text

  monkeypatch.setitem(fakekelsod.APP_DETAIL, "url", "javascript:alert(1)")
  text = client.get("/apps/kelso-ui").text
  assert "javascript:" not in text
  assert "by Jordan " in text


def test_config_form(client, fake):
  text = client.get("/apps/kelso-ui").text
  form = text.split('class="cfg-form"')[1].split("</form>")[0]
  basic, rest = form.split("<summary>Advanced config</summary>")
  advanced, options = rest.split("<summary>App options</summary>")
  # Missing fields stay out of the fold even when advanced.
  assert 'name="set.tuning"' in basic
  assert 'name="set.debug"' in advanced
  assert 'placeholder="0 (default)"' in advanced
  assert 'name="set.start_order"' in options
  assert 'name="set.subdomain"' in options
  assert 'placeholder="set — type to replace"' in basic
  assert 'placeholder="not set"' in basic
  assert '<option value="">none defined yet</option>' in basic
  assert '<option value="harbor_conn" selected>' in basic
  assert '<input type="hidden" name="action" value="config">' in form


def test_app_volumes_biggest_first(client, fake):
  text = client.get("/volumes").text
  table = text.split("<h2>App volumes</h2>")[1].split("<h2>")[0]
  sizes = re.findall(r'<td class="muted">([^<]*)</td>\s*<td class="act">', table)
  # 601653 B, 864 B, 0 B, then the one not measured yet.
  assert sizes == ["587.6 KB", "864.0 B", "0.0 B", "—"]


def test_only_an_orphaned_volume_is_flagged_and_deletable(client, fake):
  table = client.get("/volumes").text.split("<h2>App volumes</h2>")[1].split("<h2>")[0]
  rows = table.split("<tr>")[2:]
  orphaned = [row for row in rows if "orphaned" in row]
  assert len(orphaned) == 1
  assert 'class="dot bad"' in orphaned[0]
  assert 'value="delete-volume"' in orphaned[0]
  assert 'name="app_id" value="mealie"' in orphaned[0]
  assert not [row for row in rows if row not in orphaned and "delete-volume" in row]


def test_volume_disks_split_the_disk_into_volume_other_and_free(client, fake):
  text = client.get("/volumes").text
  section = text.split("<h2>Volume disks</h2>")[1].split("<h2>")[0]
  data, bulk = section.split("<tr>")[2:4]
  assert '<rect class="mine" x="0" width="25.0"' in data
  assert '<rect class="other" x="25.0" width="50.0"' in data
  assert "/dev/sda1" in data
  assert "path is missing" in bulk


def test_snapshots_page_totals_every_archive(client, fake):
  # 4851 B plus one archive with no size, which counts as nothing.
  assert "Application snapshots · 4.7 KB in total" in client.get("/snapshots").text
