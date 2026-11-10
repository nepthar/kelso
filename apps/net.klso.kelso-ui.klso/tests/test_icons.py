"""An app's icon is found on its route, served from here, and kept for a while."""

import datetime
import http.server
import ssl
import threading
import time

import fakekelsod
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from pages import appicons
from web import STATIC

SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="1" height="1"/></svg>'
ICO = b"\x00\x00\x01\x00icon"


def _drawn(name):
  return (STATIC / "icons" / f"{name}.svg").read_bytes()


class Site:
  """A web server on a loopback port that answers with what it was given."""

  def __init__(self, files, tls=None):
    self.files = files
    self.hits = []
    site = self

    class Handler(http.server.BaseHTTPRequestHandler):
      def log_message(self, *args):
        pass

      def do_GET(self):
        site.hits.append(self.path)
        kind, body = site.files.get(self.path, (None, b""))
        if kind == "redirect":
          self.send_response(302)
          self.send_header("Location", body.decode())
          self.end_headers()
          return
        self.send_response(200 if kind else 404)
        if kind:
          self.send_header("Content-Type", kind)
        self.end_headers()
        self.wfile.write(body)

    self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    scheme = "http"
    if tls:
      context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
      context.load_cert_chain(*tls)
      self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
      scheme = "https"
    threading.Thread(
      target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    ).start()
    self.url = f"{scheme}://127.0.0.1:{self.server.server_port}"


@pytest.fixture
def site():
  made = []

  def make(files, tls=None):
    made.append(Site(files, tls))
    return made[-1]

  yield make
  for one in made:
    one.server.shutdown()
    one.server.server_close()


def _self_signed(directory):
  """(certificate file, key file) of a certificate nobody vouches for."""
  key = ec.generate_private_key(ec.SECP256R1())
  name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "icons.test")])
  now = datetime.datetime.now(datetime.UTC)
  cert = (
    x509.CertificateBuilder()
    .subject_name(name)
    .issuer_name(name)
    .public_key(key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(now - datetime.timedelta(days=1))
    .not_valid_after(now + datetime.timedelta(days=1))
    .sign(key, hashes.SHA256())
  )
  cert_file, key_file = directory / "cert.pem", directory / "key.pem"
  cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
  key_file.write_bytes(
    key.private_bytes(
      serialization.Encoding.PEM,
      serialization.PrivateFormat.PKCS8,
      serialization.NoEncryption(),
    )
  )
  return cert_file, key_file


@pytest.fixture(autouse=True)
def forget():
  appicons._kept.clear()


def _routes(monkeypatch, *routes):
  detail = {**fakekelsod.APP_DETAIL, "routes": [_route(*r) for r in routes]}
  monkeypatch.setitem(fakekelsod.GET, "/apps/kelso-ui", detail)


def _route(name, published, host=None):
  return {
    "name": name,
    "published_url": published,
    "host_url": host,
    "providers": [],
  }


def test_icon_svg_comes_first(client, fake, monkeypatch, site):
  one = site(
    {"/icon.svg": ("image/svg+xml", SVG), "/favicon.ico": ("image/x-icon", ICO)}
  )
  _routes(monkeypatch, ("main", one.url))
  response = client.get("/icons/kelso-ui")
  assert response.status_code == 200
  assert response.headers["content-type"] == "image/svg+xml"
  assert response.content == SVG
  assert one.hits == ["/icon.svg"]


def test_favicon_ico_is_next(client, fake, monkeypatch, site):
  one = site({"/favicon.ico": ("image/x-icon", ICO)})
  _routes(monkeypatch, ("main", one.url))
  response = client.get("/icons/kelso-ui")
  assert response.headers["content-type"] == "image/x-icon"
  assert response.content == ICO
  assert one.hits == ["/icon.svg", "/favicon.ico"]


def test_nothing_found_is_the_drawn_icon(client, fake, monkeypatch, site):
  one = site({})
  _routes(monkeypatch, ("main", one.url))
  response = client.get("/icons/kelso-ui")
  assert response.status_code == 200
  assert response.headers["content-type"] == "image/svg+xml"
  assert response.content == _drawn("app")


def test_a_page_where_an_icon_should_be_is_not_one(client, fake, monkeypatch, site):
  one = site({"/icon.svg": ("text/html", b"<html>sign in</html>")})
  _routes(monkeypatch, ("main", one.url))
  assert client.get("/icons/kelso-ui").content == _drawn("app")


def test_a_route_that_is_not_published_is_looked_for_at_its_host_address(
  client, fake, monkeypatch, site
):
  one = site({"/icon.svg": ("image/svg+xml", SVG)})
  _routes(monkeypatch, ("main", None, one.url))
  assert client.get("/icons/kelso-ui").content == SVG


def test_a_route_with_no_address_at_all_is_the_drawn_icon(client, fake, monkeypatch):
  _routes(monkeypatch, ("main", None, None))
  assert client.get("/icons/kelso-ui").content == _drawn("app")


def test_localhost_is_where_kelsod_is_not_this_container(
  client, fake, monkeypatch, site
):
  one = site({"/icon.svg": ("image/svg+xml", SVG)})
  port = one.server.server_port
  monkeypatch.setattr(appicons, "API", "127.0.0.1:41997")
  _routes(monkeypatch, ("main", None, f"http://localhost:{port}"))
  assert client.get("/icons/kelso-ui").content == SVG
  assert one.hits == ["/icon.svg"]


def test_origins_are_published_then_host_with_localhost_swapped(monkeypatch):
  route = {"published_url": "https://a.test/x/", "host_url": "http://localhost:41001"}
  monkeypatch.setattr(appicons, "API", "host.docker.internal:41997")
  assert appicons._origins(route) == [
    "https://a.test",
    "http://host.docker.internal:41001",
  ]
  monkeypatch.setattr(appicons, "API", "")
  assert appicons._origins(route) == ["https://a.test", "http://localhost:41001"]
  assert appicons._origins({"published_url": "javascript:x", "host_url": ""}) == []
  assert appicons._origins({"host_url": "http://[::1"}) == []


def test_a_self_signed_route_gives_its_icon_and_nothing_else_loses_its_checks(
  client, fake, monkeypatch, site, tmp_path
):
  one = site({"/icon.svg": ("image/svg+xml", SVG)}, tls=_self_signed(tmp_path))
  _routes(monkeypatch, ("main", one.url))
  assert client.get("/icons/kelso-ui").content == SVG
  assert ssl.create_default_context().verify_mode == ssl.CERT_REQUIRED


def test_an_app_may_move_its_icon_within_itself(client, fake, monkeypatch, site):
  one = site(
    {
      "/favicon.ico": ("redirect", b"/static/favicon.ico"),
      "/static/favicon.ico": ("image/x-icon", ICO),
    }
  )
  _routes(monkeypatch, ("main", one.url))
  assert client.get("/icons/kelso-ui").content == ICO


def test_an_app_may_not_send_the_lookup_somewhere_else(client, fake, monkeypatch, site):
  elsewhere = site({"/icon.svg": ("image/svg+xml", SVG)})
  one = site({"/icon.svg": ("redirect", (elsewhere.url + "/icon.svg").encode())})
  _routes(monkeypatch, ("main", one.url))
  assert client.get("/icons/kelso-ui").content == _drawn("app")
  assert elsewhere.hits == []


def test_the_hosts_address_is_tried_when_the_published_one_is_not_there(
  client, fake, monkeypatch, site
):
  one = site({"/icon.svg": ("image/svg+xml", SVG)})
  _routes(monkeypatch, ("main", "http://127.0.0.1:1", one.url))
  assert client.get("/icons/kelso-ui").content == SVG


def test_an_app_icon_is_the_main_routes_and_another_route_has_its_own(
  client, fake, monkeypatch, site
):
  main = site({"/icon.svg": ("image/svg+xml", SVG)})
  admin = site({"/favicon.ico": ("image/x-icon", ICO)})
  _routes(monkeypatch, ("admin", admin.url), ("main", main.url))
  assert client.get("/icons/kelso-ui").content == SVG
  assert client.get("/icons/kelso-ui/admin").content == ICO
  assert client.get("/icons/kelso-ui/main").content == SVG


def test_a_route_with_nothing_falls_back_to_the_drawn_route_icon(
  client, fake, monkeypatch, site
):
  one = site({})
  _routes(monkeypatch, ("main", one.url), ("admin", one.url))
  assert client.get("/icons/kelso-ui/admin").content == _drawn("route")


def test_an_unknown_app_or_route_is_the_drawn_icon_and_is_not_kept(
  client, fake, monkeypatch, site
):
  one = site({"/icon.svg": ("image/svg+xml", SVG)})
  _routes(monkeypatch, ("main", one.url))
  assert client.get("/icons/no-such-app").content == _drawn("app")
  assert client.get("/icons/kelso-ui/no-such-route").content == _drawn("route")
  assert appicons._kept == {}


def test_what_was_found_is_kept_then_looked_for_again(client, fake, monkeypatch, site):
  one = site({"/icon.svg": ("image/svg+xml", SVG)})
  _routes(monkeypatch, ("main", one.url))
  client.get("/icons/kelso-ui")
  client.get("/icons/kelso-ui")
  assert one.hits == ["/icon.svg"]

  _, found = appicons._kept[("kelso-ui", "")]
  appicons._kept[("kelso-ui", "")] = (0, found)
  client.get("/icons/kelso-ui")
  assert one.hits == ["/icon.svg", "/icon.svg"]


def test_a_miss_is_kept_for_less_time_than_a_hit(client, fake, monkeypatch, site):
  one = site({})
  _routes(monkeypatch, ("main", one.url))
  client.get("/icons/kelso-ui")
  expires, found = appicons._kept[("kelso-ui", "")]
  assert found is None
  assert expires - time.monotonic() <= appicons.MISSING_FOR
  assert appicons.MISSING_FOR < appicons.FOUND_FOR


def test_an_icon_that_is_too_big_is_not_served(client, fake, monkeypatch, site):
  big = b"x" * (appicons.LIMIT + 1)
  one = site({"/icon.svg": ("image/svg+xml", big)})
  _routes(monkeypatch, ("main", one.url))
  assert client.get("/icons/kelso-ui").content == _drawn("app")


def test_what_an_app_sends_may_not_run_when_opened_directly(
  client, fake, monkeypatch, site
):
  one = site({"/icon.svg": ("image/svg+xml", SVG)})
  _routes(monkeypatch, ("main", one.url))
  headers = client.get("/icons/kelso-ui").headers
  assert headers["content-security-policy"].startswith("sandbox;")
  assert headers["x-content-type-options"] == "nosniff"


def test_icons_need_a_session(anon, fake):
  assert anon.get("/icons/kelso-ui").status_code == 303


def test_a_page_of_icons_does_not_spend_the_rate_limit(client, fake, monkeypatch, site):
  one = site({"/icon.svg": ("image/svg+xml", SVG)})
  _routes(monkeypatch, ("main", one.url))
  assert all(client.get("/icons/kelso-ui").status_code == 200 for _ in range(40))
  assert client.get("/host").status_code == 200


def test_the_start_menu_asks_for_each_icon_by_app_and_route(client, fake):
  text = client.get("/").text
  assert 'src="/icons/kelso-ui"' in text
  assert 'data-icon="/icons/kelso-ui"' in text
  assert "/icons/kelso-ui/main" in text
