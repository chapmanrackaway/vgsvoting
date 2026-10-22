import os
import re

import pytest

from app import create_app
from app.extensions import db
from app.models import User


@pytest.fixture
def app(tmp_path):
    # Set TEST_DATABASE_URL=postgresql+psycopg://... to run the suite against PostgreSQL.
    url = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'test.db'}"
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": url, "SQLALCHEMY_ENGINE_OPTIONS": {}})
    with app.app_context():
        db.drop_all()
        db.create_all()
        user = User(username="clerk", role="admin")
        user.set_password("password123")
        db.session.add(user)
        db.session.commit()
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


class Client:
    """Test client that pulls the CSRF token from each page like a browser would."""

    def __init__(self, app):
        self.c = app.test_client()
        self.token = None

    def get(self, url, **kw):
        resp = self.c.get(url, **kw)
        m = re.search(rb'name="csrf_token" value="([^"]+)"', resp.data)
        if m:
            self.token = m.group(1).decode()
        return resp

    def post(self, url, data=None, **kw):
        if self.token is None:
            self.get("/login")
        data = dict(data or {})
        data.setdefault("csrf_token", self.token)
        return self.c.post(url, data=data, **kw)


@pytest.fixture
def client(app):
    return Client(app)


@pytest.fixture
def clerk(client):
    client.get("/login")
    resp = client.post("/login", {"username": "clerk", "password": "password123"})
    assert resp.status_code == 302
    client.get("/")
    return client
