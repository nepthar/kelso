"""kelso-ui against a fake kelsod, signed in for real.

Run from ui/:  uv run pytest
"""

import os
import sys
from pathlib import Path

import pytest
from fakekelsod import FakeKelsod

ORIGIN = "https://kelso.test"
PASSWORD = "correct horse"

_fake = FakeKelsod().start()
os.environ["KELSO_API"] = _fake.address
os.environ["ADMIN_PASSWORD"] = PASSWORD
os.environ.pop("KELSO_UI_NO_AUTH", None)
sys.path.insert(0, str(Path(__file__).parent.parent / "ui"))

import frontdoor  # noqa: E402
import server  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402


@pytest.fixture
def fake():
  _fake.posts.clear()
  _fake.fail = None
  _fake.api = 19
  yield _fake
  _fake.fail = None
  _fake.api = 19


@pytest.fixture(autouse=True)
def fresh_buckets():
  for bucket in (frontdoor.general, frontdoor.signin):
    bucket.count = 0
    bucket.reset_at = 0.0


def _client():
  return TestClient(
    server.app,
    base_url=ORIGIN,
    follow_redirects=False,
    headers={"accept": "text/html", "origin": ORIGIN},
  )


@pytest.fixture
def anon():
  return _client()


@pytest.fixture
def client(anon):
  response = anon.post("/login", data={"password": PASSWORD, "next": "/"})
  assert response.status_code == 303
  frontdoor.general.count = 0
  return anon
