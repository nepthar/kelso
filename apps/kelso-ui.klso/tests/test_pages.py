"""Every page renders, escapes what kelsod sends, and loads only what exists."""

import json
import re

import pytest
from fakekelsod import EVIL

PAGES = [
  "/",
  "/apps/kelso-ui",
  "/apps/mealie",
  "/apps/kelso-ui/logs",
  "/apps/kelso-ui/console",
  "/apps/mealie/console",
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
    assert url.startswith("/static/"), url


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
  assert [c["verb"] for c in remove["choices"]] == ["uninstall", "reset", "uninstall"]
  assert remove["choices"][2]["args"] == {"app": "kelso-ui", "purge": "1"}
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


def test_config_form(client, fake):
  text = client.get("/apps/kelso-ui").text
  form = text.split('class="cfg-form"')[1].split("</form>")[0]
  basic, advanced = form.split("Show advanced configuration options")
  # Missing fields stay out of the fold even when advanced.
  assert 'name="set.tuning"' in basic
  assert 'name="set.debug"' in advanced
  assert 'placeholder="0 (default)"' in advanced
  assert 'placeholder="set — type to replace"' in basic
  assert 'placeholder="not set"' in basic
  assert '<option value="">none defined yet</option>' in basic
  assert '<option value="harbor_conn" selected>' in basic
  assert '<input type="hidden" name="action" value="config">' in form


def test_app_volumes_biggest_first(client, fake):
  text = client.get("/volumes").text
  table = text.split("<h2>App volumes</h2>")[1].split("<h2>")[0]
  sizes = re.findall(r'<td class="muted">([^<]*)</td>\s*</tr>', table)
  # 601653 B, 864 B, 0 B, then the one not measured yet.
  assert sizes == ["587.6 KB", "864.0 B", "0.0 B", "—"]
