"""Every page renders, escapes what kelsod sends, and loads only what exists."""

import json
import re

import fakekelsod
import pytest
import web
from fakekelsod import EVIL

PAGES = [
  "/",
  "/host",
  "/logs",
  "/apps/kelso-ui",
  "/apps/mealie",
  "/apps/kelso-ui/logs",
  "/apps/kelso-ui/console",
  "/apps/mealie/console",
  "/host/console",
  "/catalog",
  "/catalog?app=kelso-ui",
  "/volumes",
  "/backups",
  "/routes",
  "/routes/web",
  "/routes/fresh?kind=pangolin",
  "/routes/new",
  "/volumes/new",
  "/done?then=/routes&ok=Saved",
  "/settings",
  "/cron",
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
  """kelso-ui may run with no internet: everything it needs is under /static."""
  text = client.get(path).text
  for url in re.findall(r'<(?:script|link)\b[^>]*(?:src|href)="([^"]+)"', text):
    assert url.startswith(("/static/", "data:")), url


def test_an_editable_card_carries_its_editor(client, fake):
  text = client.get("/catalog").text
  assert text.count('class="edit-open"') == 2
  assert 'data-target="kelso-ui@examples"' in text
  # The parser eats one newline after <textarea>; the one the file starts with stays.
  assert (
    '<textarea name="text" spellcheck="false" aria-label="Bundle">\n\nnot toml' in text
  )
  assert 'class="language-markdown"' in text


def test_routes_page_lists_published_routes(client, fake):
  """One provider answers and one does not; the page shows both."""
  text = client.get("/routes").text
  assert "https://mealie.example.test" in text
  assert text.count('name="subdomain"') == 1
  assert "Could not list the routes at odd name" in text


@pytest.mark.parametrize(
  "path, location",
  [
    ("/apps", "/host"),
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
  assert 'class="head"' in response.text


def test_missing_version_is_a_dash_not_an_entity(client, fake):
  row = client.get("/host").text.split("/apps/jellyfin")[1].split("</tr>")[0]
  assert "—" in row
  assert "&amp;mdash;" not in row


def test_kelsod_down_renders_inside_the_frame(client, fake):
  fake.fail = "kelsod said <b>no</b>"
  response = client.get("/apps/kelso-ui")
  assert response.status_code == 502
  assert "Trouble talking to kelsod" in response.text
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


def test_the_desktop_has_an_icon_per_loaded_app_and_section(client, fake):
  text = client.get("/").text
  assert 'data-src="/apps/kelso-ui"' in text
  assert 'data-logs="/apps/kelso-ui/logs"' in text
  assert 'data-shell="/apps/kelso-ui/console"' in text
  assert 'data-src="/apps/immich"' in text
  assert 'data-src="/apps/mealie"' not in text, "unloaded"
  assert 'data-shell="/apps/jellyfin/console"' not in text, "nothing running"
  for item in web.NAV:
    assert f'data-src="{item.href}"' in text


def test_a_route_icon_is_its_primary_url(client, fake):
  text = client.get("/").text
  assert 'data-url="https://kelso.example.test"' in text
  assert "kelso.lan.test" not in text
  assert 'data-title="Kelso UI admin"' not in text, "not reachable"


def test_a_route_that_is_not_http_gets_no_icon(client, fake, monkeypatch):
  bad = {**fakekelsod.APP_DETAIL["routes"][0], "published_url": "javascript:x"}
  monkeypatch.setitem(
    fakekelsod.GET, "/apps/kelso-ui", {**fakekelsod.APP_DETAIL, "routes": [bad]}
  )
  assert 'data-url="http://10.0.0.5:10001"' not in client.get("/").text


def test_an_app_kelsod_cannot_describe_keeps_its_icon(client, fake):
  """immich has a list entry and no detail fixture: its routes are just absent."""
  text = client.get("/desktop/icons").text
  assert 'data-src="/apps/immich"' in text
  assert "<html" not in text


def test_pages_may_be_framed_only_by_each_other(client, fake):
  headers = client.get("/volumes").headers
  assert "frame-ancestors 'self'" in headers["content-security-policy"]
  assert headers["x-frame-options"] == "SAMEORIGIN"


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


def test_the_desktop_is_titled_with_the_daemons_hostname(client, fake):
  text = client.get("/").text
  assert '<span class="tray-host">tycho &lt;i' in text
  assert '<div class="start-band"><span>kelso <b>tycho &lt;i' in text


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


def test_the_tray_names_the_version_and_instance_id(client, fake, monkeypatch):
  monkeypatch.setitem(web.templates.globals, "INSTANCE_ID", "1a2b")
  tray = client.get("/").text.split('<div class="tray"')[1].split(">")[0]
  assert "kelso 0.1.0 over" in tray and "1a2b" in tray


def test_sign_in_is_titled_with_the_daemons_hostname(client, fake):
  assert '<div class="title-bar-text">tycho &lt;i' in client.get("/login").text


def test_sign_in_uses_the_hostname_kelsod_already_gave(client, fake):
  client.get("/")
  fake.fail = "down"
  assert '<div class="title-bar-text">tycho &lt;i' in client.get("/login").text


def test_sign_in_still_works_when_kelsod_does_not_answer(client, fake):
  fake.fail = "down"
  response = client.get("/login")
  assert response.status_code == 200
  assert '<div class="title-bar-text">Kelso' in response.text


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
  assert '<a href="#cfg-params">Params</a>' in form
  basic = form.split('id="cfg-params"')[1].split('role="tabpanel"')[0]
  options = form.split('id="cfg-options"')[1].split('role="tabpanel"')[0]
  advanced = form.split('id="cfg-advanced"')[1]
  # Missing fields are on the first tab even when advanced.
  assert 'name="set.tuning"' in basic
  assert 'name="set.debug"' in advanced
  assert 'placeholder="0 (default)"' in advanced
  assert 'name="set.start_order"' in options
  assert 'name="set.subdomain"' in options
  assert 'placeholder="set — type to replace"' in basic
  assert 'placeholder="not set"' in basic
  assert '<option value="kelso_conn" selected>' in basic
  # A pick-several field: no text box, only a tag per choice, the picks
  # pressed, the first marked primary, and the comma list in a hidden input.
  assert (
    '<input type="hidden" name="set.route.main" value="web,lan" data-tag-pick '
    'data-empty="none" data-primary-desc="Primary route: passed to the app as '
    "&lt;i" in basic
  )
  picks = basic.split('name="set.route.main"')[1].split("</div>")[0]
  tags = re.findall(r'<button type="button" class="tag" data-tag="([^"]*)"', picks)
  assert tags[:2] == ["lan", "web"]
  assert 'data-tag="none"' not in picks
  assert 'data-tag="lan" aria-pressed="true">lan<' in picks
  assert (
    'data-tag="web" aria-pressed="true" data-primary '
    'title="Primary route: passed to the app as &lt;i' in picks
  )
  assert picks.count("data-primary ") == 1
  assert 'aria-pressed="false">&lt;i' in picks
  none_defined = basic.split('name="set.route.none"')[1].split("</td>")[0]
  assert '<span class="muted">none defined yet</span>' in none_defined
  assert '<input type="hidden" name="action" value="config">' in form


def test_app_volumes_biggest_first(client, fake):
  text = client.get("/volumes").text
  table = text.split("<legend>App volumes</legend>")[1].split("<legend>")[0]
  sizes = re.findall(r'<td class="muted">([^<]*)</td>\s*<td class="act">', table)
  # 601653 B, 864 B, 0 B, then the one not measured yet.
  assert sizes == ["587.6 KB", "864.0 B", "0.0 B", "—"]


def test_only_an_orphaned_volume_is_flagged_and_deletable(client, fake):
  table = (
    client.get("/volumes")
    .text.split("<legend>App volumes</legend>")[1]
    .split("<legend>")[0]
  )
  rows = table.split("<tr>")[2:]
  orphaned = [row for row in rows if "orphaned" in row]
  assert len(orphaned) == 1
  assert 'class="dot bad"' in orphaned[0]
  assert 'value="delete-volume"' in orphaned[0]
  assert 'name="app_id" value="mealie"' in orphaned[0]
  assert not [row for row in rows if row not in orphaned and "delete-volume" in row]


def test_volume_disks_split_the_disk_into_volume_other_and_free(client, fake):
  text = client.get("/volumes").text
  section = text.split("<legend>Volume disks</legend>")[1].split("<legend>")[0]
  data, bulk = section.split("<tr>")[2:4]
  assert '<rect class="mine" x="0" width="25.0"' in data
  assert '<rect class="other" x="25.0" width="50.0"' in data
  assert "/dev/sda1" in data
  assert "path is missing" in bulk


def test_backups_page_shows_the_last_run_and_each_backup(client, fake):
  text = client.get("/backups").text
  assert "/mnt/nas/kelso" in text
  assert "3 daily, 2 weekly, 1 monthly, 5 manual" in text
  assert "1 failed" in text
  rows = text.split("<tbody>")[-1].split("<tr>")[1:]
  assert len(rows) == 2
  assert '"backup": "20260923-030000"' in rows[0] or "20260923-030000" in rows[0]


def test_backups_page_says_why_they_cannot_run(client, fake, monkeypatch):
  from fakekelsod import BACKUPS, GET

  monkeypatch.setitem(
    GET, "/backups", {**BACKUPS, "problem": "backups/ links to nothing"}
  )
  text = client.get("/backups").text
  assert "Backups cannot run" in text
  assert "backups/ links to nothing" in text


def test_a_detached_run_says_why(client, fake):
  rows = client.get("/activity").text.split("<tr>")
  detached = next(row for row in rows if ">cmd<" in row)
  assert "detached" in detached
  assert "ran for longer than 5 minutes and kelso stopped monitoring" in detached


def test_activity_says_who_started_each_run(client, fake):
  rows = client.get("/activity").text.split("<tr>")
  backup = next(row for row in rows if ">backup<" in row)
  assert ">kelso_ui<" in backup
  # Recorded before `started_by` existed.
  older = next(row for row in rows if ">repo-update<" in row)
  assert ">—<" in older


def test_cron_page_puts_upcoming_runs_above_now_and_finished_ones_below(client, fake):
  table = client.get("/cron").text.split("<tbody>")[1].split("</tbody>")[0]
  above, below = table.split('<tr class="now-cursor">')
  # Furthest first above the line, so time runs down the page.
  assert above.index("jellyfin") < above.index("mealie")
  assert 'class="dot running"></span>scheduled' in above
  assert 'class="dot exited"></span>skip' in above
  assert "nightly" in below and 'class="dot running"></span>ok' in below
  assert "2.1s" in below


def test_app_page_order_and_routes_in_the_info_box(client, fake):
  text = client.get("/apps/kelso-ui").text
  # The notices sit between the first row and the sections; not part of the order.
  headings = [
    h
    for h in re.findall(r"<legend>([^<]+)</legend>", text)
    if h != "Not ready to start"
  ]
  assert headings[:6] == [
    "Info",
    "Manifest",
    "Configuration",
    "Commands",
    "Volumes",
    "Run units",
  ]
  assert "Routes" not in headings
  reachable = text.split('<div class="reachable">')[1].split("</div>\n  </div>")[0]
  # One line per provider, primary first, each tagged with its provider.
  primary, second = (
    reachable.index('<a href="https://kelso.example.test"'),
    reachable.index('<a href="https://kelso.lan.test"'),
  )
  assert primary < second
  assert reachable.count('<span class="key">main:</span>') == 2
  assert 'title="main:8080 → 10001"' in reachable
  assert (
    '<span class="tag" title="primary: the URL the app is given for main">web</span>'
    in reachable
  )
  assert '<span class="tag" title="via lan">lan</span>' in reachable
  # No published address and no host port: nowhere to reach it, so not listed.
  assert "admin" not in reachable


def test_app_environment_shows_the_manifest_and_what_it_resolves_to(client, fake):
  text = client.get("/apps/kelso-ui").text
  env = text.split("<h3>Environment</h3>")[1].split("</table>")[0]
  assert "<th>Variable</th><th>In the manifest</th><th>Value</th>" in env
  row = env.split('<td class="key">PASS</td>')[1].split("</tr>")[0]
  assert "${admin_pass}" in row
  assert "&lt;secret&gt;" in row


def test_dashboard_lists_host_resources_with_a_usage_line_each(client, fake):
  text = client.get("/host").text
  system = text.split('id="host-system"')[1]
  cpu, memory, disk = system.split('<fieldset class="group resource">')[1:]
  assert "<legend>Host CPU · 6 CPUs</legend>" in cpu
  # 1.6% at 214s into the hour, then 50% at 514s, on a 600x100 box.
  assert '<polyline points="35.7,98.4 85.7,50.0"/>' in cpu
  assert "<span>50 %</span>" in cpu
  assert cpu.count('class="lit"') == 10, "five rows of two lit at 50%"
  assert "<legend>Host memory · 8.0 GB</legend>" in memory
  assert "no samples yet" in memory and "lit" not in memory
  # Named by where it is mounted; the device, often unreadable, on hover.
  assert "<legend>Disk / · 2.0 TB</legend>" in disk and 'title="/dev/sda2"' in disk
  assert "<span>42 %</span>" in disk
  assert "Holds bulk, backups, &lt;i" in disk
  assert "<option disabled>1 day</option>" in text


def test_host_opens_on_apps_with_the_status_bar_below(client, fake):
  text = client.get("/host").text
  assert (
    '<li role="tab" aria-selected="true"><a href="#host-apps">Apps</a></li>' in text
  )
  assert 'id="host-system" hidden' in text
  bar = text.split('<div class="status-bar">')[1].split("</div>")[0]
  fields = [f.strip() for f in bar.split("</p>") if f.strip()]
  assert [f.split(">")[-1] for f in fields] == ["Apps: 4", "CPU: 50%", "RAM: —"]


def test_volume_roots_name_their_disk_by_mount_point(client, fake):
  text = client.get("/volumes").text
  assert '<td class="muted path" title="/dev/sda1 &lt;i' in text
  assert ">/</td>" in text


def test_dashboard_status_pills_by_health(client, fake):
  rows = {
    row.split('href="/apps/')[1].split('"')[0]: row
    for row in client.get("/host").text.split("<tr>")
    if 'href="/apps/' in row
  }
  assert 'class="dot running"></span>healthy' in rows["kelso-ui"]
  assert 'class="dot exited"></span>degraded' in rows["immich"]
  assert 'class="dot"></span>stopped' in rows["jellyfin"]


def test_dashboard_offers_an_update_when_the_source_moved_on(client, fake):
  rows = {
    row.split('href="/apps/')[1].split('"')[0]: row
    for row in client.get("/host").text.split("<tr>")
    if 'href="/apps/' in row
  }
  assert 'data-verb="update"' in rows["immich"]
  assert 'title="Update 3.1.0 → 3.2.0"' in rows["immich"]
  assert 'data-verb="update"' not in rows["kelso-ui"]


def test_catalog_marks_a_bundle_that_does_not_parse(client, fake):
  page = client.get("/catalog").text
  assert 'title="manifest broken.klso/manifest.toml: not valid TOML"' in page


def test_app_page_asks_for_a_restart_only_while_running(client, fake, monkeypatch):
  from fakekelsod import APP_DETAIL, GET

  notice = "Restart to apply configuration changes."

  # The fixture: config changed while one container runs.
  text = client.get("/apps/kelso-ui").text
  assert notice in text
  assert 'value="restart"' in text or "Restart" in text

  stopped = {
    **APP_DETAIL,
    "status": "stopped",
    "containers": {"running": 0, "total": 1},
  }
  monkeypatch.setitem(GET, "/apps/kelso-ui", stopped)
  text = client.get("/apps/kelso-ui").text
  assert notice not in text


def test_a_bulk_volume_backup_is_a_checkbox(client, fake):
  text = client.get("/apps/kelso-ui").text
  row = next(r for r in text.split("<tr>") if "backup.movies" in r)
  # Unchecked posts the hidden "off" alone; checked posts "on" after it.
  assert row.index('type="hidden" name="set.backup.movies" value="off"') < row.index(
    'type="checkbox" name="set.backup.movies" value="on" checked'
  )
