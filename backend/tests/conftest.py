import os
import sys
from datetime import timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import seed as seed_module  # noqa: E402
from campaignflow import create_app, db  # noqa: E402
from campaignflow.security import throttle  # noqa: E402
from campaignflow.util import today_utc  # noqa: E402

PASSWORD = seed_module.PASSWORD


@pytest.fixture()
def app(tmp_path):
    path = str(tmp_path / "test.db")
    app = create_app({"DATABASE": path, "RUN_WORKER": False, "TESTING": True})
    seed_module.seed(app)
    throttle._hits.clear()
    return app


@pytest.fixture()
def client(app):
    return app.test_client()


def login(app, email, password=PASSWORD):
    c = app.test_client()
    r = c.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.get_json()
    return c


@pytest.fixture()
def admin(app):
    return login(app, "admin@northwind.test")


@pytest.fixture()
def manager(app):
    return login(app, "manager@northwind.test")


@pytest.fixture()
def analyst(app):
    return login(app, "analyst@northwind.test")


@pytest.fixture()
def viewer(app):
    return login(app, "kova@client.test")


@pytest.fixture()
def raw(app):
    """Direct DB connection for assertions."""
    conn = db.connect(app.config["DATABASE"])
    yield conn
    conn.close()


def iso_day(offset=0):
    return (today_utc() + timedelta(days=offset)).isoformat()


def new_campaign(c, client_id=1, **over):
    body = dict(client_id=client_id, channel_id=1, name="Test campaign", objective="sales",
                start_date=iso_day(-1), end_date=iso_day(10), budget_cents=100_00)
    body.update(over)
    return c.post("/api/campaigns", json=body)
