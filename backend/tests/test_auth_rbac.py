"""Authentication, role-based access control and tenant isolation."""
import pytest

from conftest import PASSWORD, iso_day, login, new_campaign


def test_login_and_me(client):
    r = client.post("/api/auth/login", json={"email": "ADMIN@northwind.test", "password": PASSWORD})
    assert r.status_code == 200
    assert "cf_session" in r.headers.get("Set-Cookie", "")
    assert "HttpOnly" in r.headers["Set-Cookie"]
    me = client.get("/api/auth/me").get_json()["user"]
    assert me["role"] == "admin" and "users.manage" in me["permissions"]
    assert "password_hash" not in str(me)


def test_bad_password_and_unknown_user_same_error(client):
    a = client.post("/api/auth/login", json={"email": "admin@northwind.test", "password": "nope-nope"})
    b = client.post("/api/auth/login", json={"email": "ghost@x.test", "password": "nope-nope"})
    assert a.status_code == b.status_code == 401
    assert a.get_json()["error"]["message"] == b.get_json()["error"]["message"]


def test_login_throttle(client):
    for _ in range(10):
        client.post("/api/auth/login", json={"email": "admin@northwind.test", "password": "wrong-pass"})
    r = client.post("/api/auth/login", json={"email": "admin@northwind.test", "password": PASSWORD})
    assert r.status_code == 429


def test_unauthenticated_rejected(client):
    for path in ("/api/campaigns", "/api/clients", "/api/dashboard", "/api/audit-logs", "/api/users"):
        assert client.get(path).status_code == 401


def test_logout_invalidates_session(app):
    c = login(app, "admin@northwind.test")
    token = c.get("/api/auth/me").get_json()  # sanity
    assert token["user"]["email"] == "admin@northwind.test"
    c.post("/api/auth/logout")
    assert c.get("/api/auth/me").status_code == 401


def test_bearer_token_works(app, client):
    tok = client.post("/api/auth/login", json={"email": "admin@northwind.test", "password": PASSWORD}).get_json()["token"]
    fresh = app.test_client()
    assert fresh.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 200


def test_password_stored_hashed(raw):
    h = raw.execute("SELECT password_hash FROM users WHERE email='admin@northwind.test'").fetchone()[0]
    assert h.startswith("scrypt$") and PASSWORD not in h


# ---------------------------------------------------------------- RBAC matrix
@pytest.mark.parametrize("who,method,path,body,expected", [
    # admin-only
    ("manager", "get", "/api/users", None, 403),
    ("analyst", "get", "/api/audit-logs", None, 403),
    ("manager", "get", "/api/audit-logs", None, 403),
    ("viewer", "get", "/api/audit-logs", None, 403),
    # writes
    ("analyst", "post", "/api/clients", {"name": "X Co"}, 403),
    ("viewer", "post", "/api/clients", {"name": "X Co"}, 403),
    ("analyst", "post", "/api/campaigns", {}, 403),
    ("viewer", "post", "/api/campaigns/1/budget", {"budget_cents": 5}, 403),
    ("analyst", "post", "/api/campaigns/1/budget", {"budget_cents": 5}, 403),
    ("viewer", "post", "/api/campaigns/1/metrics", {"spend_cents": 1}, 403),
    ("viewer", "post", "/api/reports", {}, 403),
    ("viewer", "get", "/api/tasks", None, 403),
    ("analyst", "post", "/api/campaigns/1/status", {"status": "paused"}, 403),
    # allowed
    ("manager", "get", "/api/campaigns", None, 200),
    ("analyst", "get", "/api/campaigns", None, 200),
    ("viewer", "get", "/api/campaigns", None, 200),
    ("manager", "get", "/api/tasks", None, 200),
])
def test_rbac_matrix(request, who, method, path, body, expected):
    c = request.getfixturevalue(who)
    r = getattr(c, method)(path, **({"json": body} if body is not None else {}))
    assert r.status_code == expected, r.get_json()


def test_admin_can_do_everything(admin):
    assert admin.get("/api/users").status_code == 200
    assert admin.get("/api/audit-logs").status_code == 200


def test_client_viewer_only_sees_own_client(viewer, admin):
    items = viewer.get("/api/campaigns").get_json()["items"]
    assert items and {c["client_name"] for c in items} == {"Kova Outdoor"}
    assert [c["name"] for c in viewer.get("/api/clients").get_json()["items"]] == ["Kova Outdoor"]
    # a campaign of another client: 404, not 403 (does not leak existence)
    all_c = admin.get("/api/campaigns?per_page=100").get_json()["items"]
    other = next(c for c in all_c if c["client_name"] != "Kova Outdoor")
    assert viewer.get(f"/api/campaigns/{other['id']}").status_code == 404
    # client_id filter cannot widen scope
    items = viewer.get(f"/api/campaigns?client_id={other['client_id']}").get_json()["items"]
    assert items and {c["client_name"] for c in items} == {"Kova Outdoor"}


def test_viewer_sees_no_internal_history(viewer):
    cid = viewer.get("/api/campaigns").get_json()["items"][0]["id"]
    d = viewer.get(f"/api/campaigns/{cid}").get_json()
    assert "activity" not in d and "budget_history" not in d


def test_viewer_dashboard_and_reports_scoped(viewer, admin):
    dash = viewer.get("/api/dashboard").get_json()
    assert all(c["client_name"] == "Kova Outdoor" for c in dash["attention"])
    admin_total = admin.get("/api/dashboard").get_json()["total_campaigns"]
    assert dash["total_campaigns"] < admin_total
    # the seeded agency-wide report (client_id NULL) must be invisible to a client
    assert viewer.get("/api/reports/1").status_code == 404
    assert viewer.get("/api/reports").get_json()["total"] == 0


def test_tenant_isolation_between_agencies(app, admin):
    other = app.test_client()
    r = other.post("/api/auth/register", json={"agency_name": "Rival Agency", "name": "Rae", "email": "rae@rival.test",
                                              "password": "password123"})
    assert r.status_code == 201
    assert other.get("/api/campaigns").get_json()["total"] == 0
    assert other.get("/api/clients").get_json()["items"] == []
    assert other.get("/api/campaigns/1").status_code == 404
    assert other.post("/api/campaigns/1/budget", json={"budget_cents": 99999}).status_code == 404
    assert other.post("/api/campaigns/1/metrics", json={"spend_cents": 1}).status_code == 404
    assert other.get("/api/reports/1").status_code == 404
    audit = other.get("/api/audit-logs").get_json()
    assert all(a["agency_id"] == audit["items"][0]["agency_id"] for a in audit["items"])
    # cross-tenant campaign creation against a foreign client id
    assert new_campaign(other, client_id=1).status_code == 422
    # and the original agency is untouched
    assert admin.get("/api/campaigns/1").status_code == 200


# ---------------------------------------------------------------- user management
def test_user_management_rules(admin, app):
    r = admin.post("/api/users", json={"name": "New Mgr", "email": "n@northwind.test", "password": "longenough1", "role": "manager"})
    assert r.status_code == 201
    uid = r.get_json()["id"]
    # client_viewer needs a client
    assert admin.post("/api/users", json={"name": "V", "email": "v@x.test", "password": "longenough1", "role": "client_viewer"}).status_code == 422
    # duplicate email
    assert admin.post("/api/users", json={"name": "Dup", "email": "N@northwind.test", "password": "longenough1", "role": "analyst"}).status_code == 409
    # new user can log in, then deactivation kills access immediately
    u = login(app, "n@northwind.test", "longenough1")
    assert u.get("/api/campaigns").status_code == 200
    assert admin.patch(f"/api/users/{uid}", json={"is_active": False}).status_code == 200
    assert u.get("/api/campaigns").status_code == 401


def test_cannot_remove_last_admin_or_self_deactivate(admin):
    me = admin.get("/api/auth/me").get_json()["user"]["id"]
    assert admin.patch(f"/api/users/{me}", json={"is_active": False}).status_code == 409
    assert admin.patch(f"/api/users/{me}", json={"role": "manager"}).status_code == 409


def test_role_change_revokes_sessions(admin, app):
    mgr = login(app, "manager@northwind.test")
    uid = mgr.get("/api/auth/me").get_json()["user"]["id"]
    admin.patch(f"/api/users/{uid}", json={"role": "analyst"})
    assert mgr.get("/api/campaigns").status_code == 401


def test_signup_can_be_disabled(app):
    app.config["ALLOW_SIGNUP"] = False
    r = app.test_client().post("/api/auth/register", json={"agency_name": "Zed", "name": "Z", "email": "z@z.test", "password": "password123"})
    assert r.status_code == 403
