"""Rate limits, the origin check, the session, and the headers on the way out."""

import pytest
from conftest import ORIGIN, PASSWORD
from frontdoor import same_origin


@pytest.mark.parametrize(
  "headers, ok",
  [
    ({"host": "kelso.test", "origin": "https://kelso.test"}, True),
    ({"host": "kelso.test:443", "origin": "https://kelso.test"}, True),
    ({"host": "10.0.0.5:10001", "origin": "https://10.0.0.5:10001"}, True),
    ({"host": "KELSO.test", "origin": "https://kelso.TEST"}, True),
    ({"host": "kelso.test", "referer": "https://kelso.test/volumes"}, True),
    # A proxy that rewrote Host still says who the browser asked for.
    (
      {"host": "10.0.0.5:10001", "x-forwarded-host": "kelso.test", "origin": ORIGIN},
      True,
    ),
    # A sibling subdomain: same *site*, so the cookie rides along. Refused.
    ({"host": "kelso.test", "origin": "https://jellyfin.kelso.test"}, False),
    ({"host": "kelso.test", "origin": "https://kelso.test.evil"}, False),
    ({"host": "kelso.test", "origin": "null"}, False),
    ({"host": "kelso.test"}, False),
  ],
)
def test_same_origin(headers, ok):
  assert same_origin(headers) is ok


def test_cross_site_job_is_refused(client, fake):
  response = client.post(
    "/jobs",
    json={"verb": "uninstall", "args": {"app": "kelso-ui"}},
    headers={"origin": "https://jellyfin.kelso.test", "accept": "*/*"},
  )
  assert response.status_code == 403
  assert fake.posts == []


def test_cross_site_form_gets_a_page(client, fake):
  response = client.post(
    "/volumes",
    data={"action": "delete", "tag": "media"},
    headers={"origin": "https://evil.test"},
  )
  assert response.status_code == 403
  assert "Refused a request from another site." in response.text
  assert fake.posts == []


def test_cross_site_logout_is_refused(client):
  response = client.post("/logout", headers={"origin": "https://evil.test"})
  assert response.status_code == 403


def test_signed_out_page_goes_to_the_door(anon):
  response = anon.get("/volumes?ok=x")
  assert response.status_code == 303
  assert response.headers["location"] == "/login?next=/volumes%3Fok%3Dx"


def test_signed_out_fetch_is_401(anon):
  response = anon.get("/jobs/j1", headers={"accept": "*/*"})
  assert response.status_code == 401


def test_wrong_password(anon):
  response = anon.post("/login", data={"password": "nope", "next": "/"})
  assert response.status_code == 401
  assert "That is not the admin password." in response.text


@pytest.mark.parametrize(
  "target, landing", [("/volumes", "/volumes"), ("//evil.test", "/")]
)
def test_sign_in_lands_on_a_local_next(anon, target, landing):
  response = anon.post("/login", data={"password": PASSWORD, "next": target})
  assert response.status_code == 303
  assert response.headers["location"] == landing
  cookie = response.headers["set-cookie"]
  assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Secure" in cookie


def test_logout_clears_the_cookie(client):
  response = client.post("/logout")
  assert response.headers["location"] == "/login"
  assert 'kelso_session=""' in response.headers["set-cookie"]


def test_general_rate_limit(client, fake):
  codes = [client.get("/login").status_code for _ in range(11)]
  assert codes[:10] == [200] * 10
  assert codes[10] == 429
  limited = client.get("/login")
  assert "Retry-After" in limited.headers
  assert "Rate limiter hit" in limited.text
  # Static files never answer to the limiter.
  assert client.get("/static/kelso.css").status_code == 200


@pytest.mark.parametrize("path", ["/login", "/static/kelso.css"])
def test_security_headers(anon, path):
  headers = anon.get(path).headers
  assert "script-src 'self'" in headers["content-security-policy"]
  assert "frame-ancestors 'none'" in headers["content-security-policy"]
  assert headers["x-content-type-options"] == "nosniff"
