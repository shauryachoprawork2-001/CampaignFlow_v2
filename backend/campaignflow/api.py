"""REST API. Every route is agency-scoped; client_viewers are further confined to one client."""
import csv
import io
import json
import sqlite3
from datetime import date, timedelta

from flask import Blueprint, Response, current_app, g, jsonify, make_response, request

from . import reports as rpt
from . import scheduler, security, service
from .db import get_db, transaction
from .security import client_scope, public_user, require
from .util import (ApiError, like_escape, page_args, parse_json, rows_to_dicts, today_utc, utcnow,
                   v_bool, v_date, v_email, v_enum, v_int, v_str)

bp = Blueprint("api", __name__, url_prefix="/api")

OBJECTIVES = {"awareness", "traffic", "leads", "sales"}
CAMPAIGN_SORT = {
    "name": "ca.name", "status": "ca.status", "start_date": "ca.start_date", "end_date": "ca.end_date",
    "budget_cents": "ca.budget_cents", "spent_cents": "ca.spent_cents", "created_at": "ca.created_at",
    "client_name": "c.name", "channel_name": "ch.name",
}


def with_session_cookie(payload: dict, token: str, status=200):
    resp = make_response(jsonify(payload), status)
    resp.set_cookie(
        security.COOKIE, token, max_age=current_app.config["SESSION_DAYS"] * 86400,
        httponly=True, samesite="Lax", secure=request.is_secure, path="/",
    )
    return resp


def duplicate(msg):
    return ApiError(409, "duplicate", msg)


# =================================================================== health
@bp.get("/health")
def health():
    get_db().execute("SELECT 1").fetchone()
    return {"status": "ok"}


# =================================================================== auth
@bp.post("/auth/register")
def register():
    if not current_app.config["ALLOW_SIGNUP"]:
        raise ApiError(403, "signup_disabled", "Self-service sign-up is disabled")
    d = parse_json()
    agency_name = v_str(d, "agency_name", min_len=2, max_len=100)
    name = v_str(d, "name", max_len=100)
    email = v_email(d, "email")
    password = v_str(d, "password", min_len=8, max_len=128)
    conn = get_db()
    try:
        with transaction(conn):
            now = utcnow()
            aid = conn.execute("INSERT INTO agencies (name, created_at) VALUES (?,?)", (agency_name, now)).lastrowid
            uid = conn.execute(
                "INSERT INTO users (agency_id, email, name, password_hash, role, created_at) VALUES (?,?,?,?, 'admin', ?)",
                (aid, email, name, security.hash_password(password), now),
            ).lastrowid
            scheduler.ensure_agency_tasks(conn, aid)
            service.audit(conn, aid, {"id": uid, "email": email}, "agency.created", "agency", aid, {"name": agency_name})
            token = security.create_session(conn, uid)
    except sqlite3.IntegrityError:
        raise duplicate("That agency name or email is already registered")
    user = security.load_user(conn, token)
    return with_session_cookie({"user": public_user(user), "token": token}, token, 201)


@bp.post("/auth/login")
def login():
    d = parse_json()
    email = v_email(d, "email")
    password = v_str(d, "password", max_len=128)
    key = f"{email}|{request.remote_addr}"
    security.throttle.check(key)
    conn = get_db()
    row = conn.execute(
        "SELECT id, agency_id, email, password_hash, is_active FROM users WHERE email = ?", (email,)
    ).fetchone()
    ok = security.verify_password(password, row["password_hash"] if row else security._DUMMY_HASH)
    if not (row and ok and row["is_active"]):
        security.throttle.fail(key)
        if row:
            with transaction(conn):
                service.audit(conn, row["agency_id"], {"id": row["id"], "email": row["email"]},
                              "auth.login_failed", "user", row["id"])
        raise ApiError(401, "invalid_credentials", "Incorrect email or password")
    security.throttle.reset(key)
    with transaction(conn):
        token = security.create_session(conn, row["id"])
        conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (utcnow(), row["id"]))
        service.audit(conn, row["agency_id"], {"id": row["id"], "email": row["email"]}, "auth.login", "user", row["id"])
    user = security.load_user(conn, token)
    return with_session_cookie({"user": public_user(user), "token": token}, token)


@bp.post("/auth/logout")
def logout():
    token = security._token_from_request()
    if token:
        with transaction(get_db()) as conn:
            security.destroy_session(conn, token)
    resp = make_response(jsonify({"ok": True}))
    resp.delete_cookie(security.COOKIE, path="/")
    return resp


@bp.get("/auth/me")
@require()
def me():
    return {"user": public_user(g.user)}


# =================================================================== reference data
@bp.get("/channels")
@require()
def channels():
    return {"items": rows_to_dicts(get_db().execute("SELECT id, code, name FROM channels ORDER BY name"))}


# =================================================================== users (admin)
def _user_row(conn, agency_id, uid):
    r = conn.execute(
        """SELECT u.id, u.name, u.email, u.role, u.client_id, u.is_active, u.created_at, u.last_login_at,
                  c.name AS client_name
             FROM users u LEFT JOIN clients c ON c.id = u.client_id
            WHERE u.id = ? AND u.agency_id = ?""", (uid, agency_id)).fetchone()
    if r is None:
        raise ApiError(404, "not_found", "User not found")
    return dict(r)


def _check_client_for_role(conn, agency_id, role, client_id):
    if role == "client_viewer":
        if client_id is None:
            raise ApiError(422, "validation_error", "client_id: required for client_viewer", {"client_id": "required for client_viewer"})
        if conn.execute("SELECT 1 FROM clients WHERE id = ? AND agency_id = ?", (client_id, agency_id)).fetchone() is None:
            raise ApiError(422, "validation_error", "client_id: unknown client", {"client_id": "unknown client"})
        return client_id
    return None


def _ensure_other_admin(conn, agency_id, excluding_uid):
    n = conn.execute(
        "SELECT COUNT(*) FROM users WHERE agency_id = ? AND role = 'admin' AND is_active = 1 AND id <> ?",
        (agency_id, excluding_uid)).fetchone()[0]
    if n == 0:
        raise ApiError(409, "last_admin", "An agency must keep at least one active admin")


@bp.get("/users")
@require("users.manage")
def list_users():
    rows = get_db().execute(
        """SELECT u.id, u.name, u.email, u.role, u.client_id, u.is_active, u.created_at, u.last_login_at,
                  c.name AS client_name
             FROM users u LEFT JOIN clients c ON c.id = u.client_id
            WHERE u.agency_id = ? ORDER BY u.name""", (g.user["agency_id"],))
    return {"items": rows_to_dicts(rows)}


@bp.post("/users")
@require("users.manage")
def create_user():
    d = parse_json()
    u = g.user
    name = v_str(d, "name", max_len=100)
    email = v_email(d, "email")
    password = v_str(d, "password", min_len=8, max_len=128)
    role = v_enum(d, "role", security.ROLES)
    conn = get_db()
    try:
        with transaction(conn):
            client_id = _check_client_for_role(conn, u["agency_id"], role, d.get("client_id"))
            uid = conn.execute(
                "INSERT INTO users (agency_id, client_id, email, name, password_hash, role, created_at) VALUES (?,?,?,?,?,?,?)",
                (u["agency_id"], client_id, email, name, security.hash_password(password), role, utcnow())).lastrowid
            service.audit(conn, u["agency_id"], u, "user.created", "user", uid, {"email": email, "role": role})
    except sqlite3.IntegrityError:
        raise duplicate("A user with that email already exists")
    return _user_row(conn, u["agency_id"], uid), 201


@bp.patch("/users/<int:uid>")
@require("users.manage")
def update_user(uid):
    d = parse_json()
    u = g.user
    conn = get_db()
    with transaction(conn):
        cur = _user_row(conn, u["agency_id"], uid)
        name = v_str(d, "name", max_len=100) if "name" in d else cur["name"]
        role = v_enum(d, "role", security.ROLES) if "role" in d else cur["role"]
        active = v_bool(d, "is_active") if "is_active" in d else bool(cur["is_active"])
        client_id = d.get("client_id", cur["client_id"])
        client_id = _check_client_for_role(conn, u["agency_id"], role, client_id)
        if uid == u["id"] and not active:
            raise ApiError(409, "self_deactivate", "You cannot deactivate your own account")
        if cur["role"] == "admin" and (role != "admin" or not active):
            _ensure_other_admin(conn, u["agency_id"], uid)
        pw_hash = security.hash_password(v_str(d, "password", min_len=8, max_len=128)) if d.get("password") else None
        conn.execute(
            "UPDATE users SET name=?, role=?, is_active=?, client_id=?, password_hash=COALESCE(?, password_hash) WHERE id=?",
            (name, role, int(active), client_id, pw_hash, uid))
        if not active or pw_hash or role != cur["role"]:
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))  # force re-login
        changes = {k: v for k, v in {"role": role if role != cur["role"] else None,
                                     "is_active": active if active != bool(cur["is_active"]) else None,
                                     "password_reset": True if pw_hash else None}.items() if v is not None}
        service.audit(conn, u["agency_id"], u, "user.updated", "user", uid, changes or None)
    return _user_row(conn, u["agency_id"], uid)


# =================================================================== clients
_CLIENT_LIST_SQL = """
SELECT c.id, c.name, c.industry, c.contact_name, c.contact_email, c.status, c.created_at,
       COUNT(ca.id) AS campaigns,
       COALESCE(SUM(CASE WHEN ca.status = 'active' THEN 1 ELSE 0 END), 0) AS active_campaigns,
       COALESCE(SUM(ca.budget_cents), 0) AS budget_cents,
       COALESCE(SUM(ca.spent_cents), 0)  AS spent_cents
  FROM clients c
  LEFT JOIN campaigns ca ON ca.client_id = c.id AND ca.status <> 'cancelled'
 WHERE c.agency_id = ?"""


@bp.get("/clients")
@require()
def list_clients():
    u = g.user
    sql, params = _CLIENT_LIST_SQL, [u["agency_id"]]
    scope = client_scope(u)
    if scope is not None:
        sql += " AND c.id = ?"
        params.append(scope)
    status = request.args.get("status")
    if status in ("active", "archived"):
        sql += " AND c.status = ?"
        params.append(status)
    sql += " GROUP BY c.id ORDER BY c.name"
    return {"items": rows_to_dicts(get_db().execute(sql, params))}


@bp.post("/clients")
@require("clients.write")
def create_client():
    d = parse_json()
    u = g.user
    name = v_str(d, "name", max_len=120)
    industry = v_str(d, "industry", required=False, max_len=80)
    cname = v_str(d, "contact_name", required=False, max_len=100)
    cemail = v_email(d, "contact_email", required=False)
    conn = get_db()
    try:
        with transaction(conn):
            now = utcnow()
            cid = conn.execute(
                "INSERT INTO clients (agency_id, name, industry, contact_name, contact_email, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?)", (u["agency_id"], name, industry, cname, cemail, now, now)).lastrowid
            service.audit(conn, u["agency_id"], u, "client.created", "client", cid, {"name": name})
    except sqlite3.IntegrityError:
        raise duplicate("A client with that name already exists")
    row = conn.execute(_CLIENT_LIST_SQL + " AND c.id = ? GROUP BY c.id", (u["agency_id"], cid)).fetchone()
    return dict(row), 201


@bp.patch("/clients/<int:cid>")
@require("clients.write")
def update_client(cid):
    d = parse_json()
    u = g.user
    conn = get_db()
    try:
        with transaction(conn):
            cur = conn.execute("SELECT * FROM clients WHERE id = ? AND agency_id = ?", (cid, u["agency_id"])).fetchone()
            if cur is None:
                raise ApiError(404, "not_found", "Client not found")
            vals = {
                "name": v_str(d, "name", max_len=120) if "name" in d else cur["name"],
                "industry": v_str(d, "industry", required=False, max_len=80) if "industry" in d else cur["industry"],
                "contact_name": v_str(d, "contact_name", required=False, max_len=100) if "contact_name" in d else cur["contact_name"],
                "contact_email": v_email(d, "contact_email", required=False) if "contact_email" in d else cur["contact_email"],
                "status": v_enum(d, "status", {"active", "archived"}) if "status" in d else cur["status"],
            }
            if vals["status"] == "archived" and cur["status"] != "archived":
                n = conn.execute("SELECT COUNT(*) FROM campaigns WHERE client_id = ? AND status IN ('scheduled','active','paused')",
                                 (cid,)).fetchone()[0]
                if n:
                    raise ApiError(409, "client_has_live_campaigns",
                                   f"Complete or cancel the client's {n} live campaign(s) before archiving")
            conn.execute("UPDATE clients SET name=?, industry=?, contact_name=?, contact_email=?, status=?, updated_at=? WHERE id=?",
                         (*vals.values(), utcnow(), cid))
            service.audit(conn, u["agency_id"], u, "client.updated", "client", cid,
                          {k: v for k, v in vals.items() if v != cur[k]} or None)
    except sqlite3.IntegrityError:
        raise duplicate("A client with that name already exists")
    return dict(conn.execute(_CLIENT_LIST_SQL + " AND c.id = ? GROUP BY c.id", (u["agency_id"], cid)).fetchone())


# =================================================================== campaigns
def _campaign_filters(u):
    where, params = ["c.agency_id = ?"], [u["agency_id"]]
    scope = client_scope(u)
    if scope is not None:
        where.append("c.id = ?")
        params.append(scope)
    elif request.args.get("client_id"):
        try:
            where.append("c.id = ?")
            params.append(int(request.args["client_id"]))
        except ValueError:
            raise ApiError(422, "validation_error", "client_id must be an integer")
    if request.args.get("status"):
        statuses = [s for s in request.args["status"].split(",") if s in service.TRANSITIONS]
        if statuses:
            where.append(f"ca.status IN ({','.join('?' * len(statuses))})")
            params += statuses
    if request.args.get("channel"):
        where.append("ch.code = ?")
        params.append(request.args["channel"])
    q = (request.args.get("q") or "").strip()
    if q:
        where.append("(ca.name LIKE ? ESCAPE '\\' OR c.name LIKE ? ESCAPE '\\')")
        params += [f"%{like_escape(q)}%"] * 2
    return " AND ".join(where), params


@bp.get("/campaigns")
@require()
def list_campaigns():
    u = g.user
    conn = get_db()
    where, params = _campaign_filters(u)
    page, per_page, offset = page_args()
    sort = CAMPAIGN_SORT.get(request.args.get("sort", "created_at"), "ca.created_at")
    direction = "ASC" if request.args.get("dir", "desc").lower() == "asc" else "DESC"
    total = conn.execute(
        f"""SELECT COUNT(*) FROM campaigns ca JOIN clients c ON c.id = ca.client_id
             JOIN channels ch ON ch.id = ca.channel_id WHERE {where}""", params).fetchone()[0]
    rows = conn.execute(
        f"{service.campaign_select()} WHERE {where} ORDER BY {sort} {direction}, ca.id DESC LIMIT ? OFFSET ?",
        [*params, per_page, offset]).fetchall()
    today = today_utc()
    return {"items": [service.campaign_dict(r, today) for r in rows], "total": total, "page": page, "per_page": per_page}


@bp.get("/campaigns/export.csv")
@require()
def export_campaigns():
    u = g.user
    where, params = _campaign_filters(u)
    rows = get_db().execute(f"{service.campaign_select()} WHERE {where} ORDER BY c.name, ca.name", params).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "campaign", "client", "channel", "objective", "status", "start_date", "end_date",
                "budget", "spent", "impressions", "clicks", "conversions", "ctr_pct", "cpa"])
    for r in rows:
        d = service.campaign_dict(r)
        w.writerow([_csv_safe(x) for x in [
            d["id"], d["name"], d["client_name"], d["channel_name"], d["objective"], d["status"], d["start_date"],
            d["end_date"], f"{d['budget_cents'] / 100:.2f}", f"{d['spent_cents'] / 100:.2f}", d["impressions"],
            d["clicks"], d["conversions"], d["ctr"] if d["ctr"] is not None else "",
            f"{d['cpa_cents'] / 100:.2f}" if d["cpa_cents"] is not None else ""]])
    return _csv_response(buf.getvalue(), f"campaigns-{today_utc().isoformat()}.csv")


def _csv_safe(v):
    """Neutralise spreadsheet formula injection (=, +, -, @ at cell start)."""
    s = str(v)
    return "'" + s if s and s[0] in "=+-@\t\r" else s


def _csv_response(text, filename):
    return Response(text, mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@bp.post("/campaigns")
@require("campaigns.write")
def create_campaign():
    d = parse_json()
    u = g.user
    conn = get_db()
    args = dict(
        client_id=v_int(d, "client_id", min=1),
        channel_id=v_int(d, "channel_id", min=1),
        name=v_str(d, "name", max_len=150),
        objective=v_enum(d, "objective", OBJECTIVES, required=False, default="traffic"),
        start_date=v_date(d, "start_date"),
        end_date=v_date(d, "end_date"),
        budget_cents=v_int(d, "budget_cents", min=1, max=10**12),
        schedule=v_bool(d, "schedule"),
    )
    with transaction(conn):
        cid = service.create_campaign(conn, u["agency_id"], u, today=today_utc(), **args)
        row = service.fetch_campaign(conn, cid, u["agency_id"])
    return service.campaign_dict(row), 201


@bp.get("/campaigns/<int:cid>")
@require()
def get_campaign(cid):
    u = g.user
    conn = get_db()
    row = service.fetch_campaign(conn, cid, u["agency_id"], client_scope(u))
    out = service.campaign_dict(row)
    out["daily"] = rows_to_dicts(conn.execute(
        """SELECT metric_date AS date, spend_cents, impressions, clicks, conversions
             FROM daily_metrics WHERE campaign_id = ? ORDER BY metric_date""", (cid,)))
    if client_scope(u) is None:
        out["budget_history"] = rows_to_dicts(conn.execute(
            """SELECT b.old_budget_cents, b.new_budget_cents, b.reason, b.created_at, u.name AS changed_by
                 FROM budget_adjustments b LEFT JOIN users u ON u.id = b.changed_by
                WHERE b.campaign_id = ? ORDER BY b.id DESC LIMIT 20""", (cid,)))
        out["activity"] = [_audit_dict(r) for r in conn.execute(
            """SELECT * FROM audit_logs WHERE agency_id = ? AND entity_type = 'campaign' AND entity_id = ?
                ORDER BY id DESC LIMIT 25""", (u["agency_id"], cid))]
    return out


@bp.patch("/campaigns/<int:cid>")
@require("campaigns.write")
def update_campaign(cid):
    d = parse_json()
    u = g.user
    conn = get_db()
    fields = {}
    if "name" in d:
        fields["name"] = v_str(d, "name", max_len=150)
    if "objective" in d:
        fields["objective"] = v_enum(d, "objective", OBJECTIVES)
    if "start_date" in d:
        fields["start_date"] = v_date(d, "start_date")
    if "end_date" in d:
        fields["end_date"] = v_date(d, "end_date")
    version = v_int(d, "version", required=False)
    if not fields:
        raise ApiError(422, "validation_error", "Nothing to update")
    with transaction(conn):
        row = service.fetch_campaign(conn, cid, u["agency_id"])
        service.update_campaign(conn, row, u, fields, version)
        row = service.fetch_campaign(conn, cid, u["agency_id"])
    return service.campaign_dict(row)


@bp.post("/campaigns/<int:cid>/status")
@require("campaigns.write")
def campaign_status(cid):
    d = parse_json()
    u = g.user
    conn = get_db()
    status = v_enum(d, "status", set(service.TRANSITIONS))
    reason = v_str(d, "reason", required=False, max_len=300)
    version = v_int(d, "version", required=False)
    with transaction(conn):
        row = service.fetch_campaign(conn, cid, u["agency_id"])
        service.change_status(conn, row, status, u, today_utc(), reason, version)
        row = service.fetch_campaign(conn, cid, u["agency_id"])
    return service.campaign_dict(row)


@bp.post("/campaigns/<int:cid>/budget")
@require("budgets.write")
def campaign_budget(cid):
    d = parse_json()
    u = g.user
    conn = get_db()
    new_budget = v_int(d, "budget_cents", min=1, max=10**12)
    reason = v_str(d, "reason", required=False, max_len=300)
    version = v_int(d, "version", required=False)
    with transaction(conn):
        row = service.fetch_campaign(conn, cid, u["agency_id"])
        service.adjust_budget(conn, row, u, new_budget, reason, version)
        row = service.fetch_campaign(conn, cid, u["agency_id"])
    return service.campaign_dict(row)


@bp.post("/campaigns/<int:cid>/metrics")
@require("metrics.record")
def campaign_metrics(cid):
    d = parse_json()
    u = g.user
    conn = get_db()
    impressions = v_int(d, "impressions", required=False, default=0, min=0, max=10**10)
    clicks = v_int(d, "clicks", required=False, default=0, min=0, max=10**10)
    conversions = v_int(d, "conversions", required=False, default=0, min=0, max=10**10)
    if clicks > impressions:
        raise ApiError(422, "validation_error", "clicks: cannot exceed impressions", {"clicks": "cannot exceed impressions"})
    if conversions > clicks:
        raise ApiError(422, "validation_error", "conversions: cannot exceed clicks", {"conversions": "cannot exceed clicks"})
    args = dict(
        metric_date=v_date(d, "date", required=False, default=today_utc().isoformat()),
        spend_cents=v_int(d, "spend_cents", min=0, max=10**12),
        impressions=impressions, clicks=clicks, conversions=conversions,
    )
    with transaction(conn):
        result = service.record_metrics(conn, u["agency_id"], cid, u, today=today_utc(), **args)
        row = service.fetch_campaign(conn, cid, u["agency_id"])
    return {**result, "campaign": service.campaign_dict(row)}, 201


# =================================================================== dashboard
@bp.get("/dashboard")
@require()
def dashboard():
    u = g.user
    conn = get_db()
    try:
        days = int(request.args.get("days", 30))
    except ValueError:
        raise ApiError(422, "validation_error", "days must be an integer")
    days = max(1, min(days, 365))
    scope = client_scope(u)
    today = today_utc()
    end, start = today, today - timedelta(days=days - 1)
    prev_end, prev_start = start - timedelta(days=1), start - timedelta(days=days)
    cur = rpt.period_totals(conn, u["agency_id"], scope, start.isoformat(), end.isoformat())
    prev = rpt.period_totals(conn, u["agency_id"], scope, prev_start.isoformat(), prev_end.isoformat())

    def kpi(key):
        a, b = cur[key], prev[key]
        return {"value": a, "previous": b, "delta_pct": round((a - b) * 100 / b, 1) if b else None}

    where, params = "c.agency_id = ?", [u["agency_id"]]
    if scope is not None:
        where += " AND c.id = ?"
        params.append(scope)
    counts = {r["status"]: r["n"] for r in conn.execute(
        f"SELECT ca.status, COUNT(*) AS n FROM campaigns ca JOIN clients c ON c.id = ca.client_id WHERE {where} GROUP BY ca.status", params)}
    live = conn.execute(
        f"""SELECT COALESCE(SUM(ca.budget_cents),0) AS budget, COALESCE(SUM(ca.spent_cents),0) AS spent
              FROM campaigns ca JOIN clients c ON c.id = ca.client_id
             WHERE {where} AND ca.status = 'active'""", params).fetchone()
    active_rows = conn.execute(
        f"{service.campaign_select()} WHERE {where} AND ca.status = 'active'", params).fetchall()
    attention = [c for c in (service.campaign_dict(r, today) for r in active_rows) if c["alerts"]]
    attention.sort(key=lambda c: (-len(c["alerts"]), -c["budget_used_pct"]))
    return {
        "period": {"start": start.isoformat(), "end": end.isoformat(), "days": days},
        "kpis": {"spend_cents": kpi("spend_cents"), "impressions": kpi("impressions"), "clicks": kpi("clicks"),
                 "conversions": kpi("conversions"), "ctr": kpi("ctr"), "cpa_cents": kpi("cpa_cents")},
        "status_counts": counts,
        "total_campaigns": sum(counts.values()),
        "active_budget_cents": live["budget"], "active_spent_cents": live["spent"],
        "daily": rpt.daily_series(conn, u["agency_id"], scope, start.isoformat(), end.isoformat()),
        "channels": rpt.channel_breakdown(conn, u["agency_id"], scope, start.isoformat(), end.isoformat()),
        "attention": attention[:8],
    }


# =================================================================== reports
def _report_visible_clause(u):
    scope = client_scope(u)
    return (" AND client_id = ?", [scope]) if scope is not None else ("", [])


@bp.get("/reports")
@require()
def list_reports():
    u = g.user
    extra, ep = _report_visible_clause(u)
    page, per_page, offset = page_args(20)
    conn = get_db()
    total = conn.execute(f"SELECT COUNT(*) FROM reports WHERE agency_id = ? {extra}", [u["agency_id"], *ep]).fetchone()[0]
    rows = conn.execute(
        f"""SELECT r.id, r.title, r.kind, r.client_id, r.period_start, r.period_end, r.created_at,
                   COALESCE(u.name, 'Scheduler') AS generated_by
              FROM reports r LEFT JOIN users u ON u.id = r.generated_by
             WHERE r.agency_id = ? {extra.replace('client_id', 'r.client_id')}
             ORDER BY r.id DESC LIMIT ? OFFSET ?""", [u["agency_id"], *ep, per_page, offset]).fetchall()
    return {"items": rows_to_dicts(rows), "total": total, "page": page, "per_page": per_page}


def _get_report(conn, u, rid):
    extra, ep = _report_visible_clause(u)
    r = conn.execute(f"SELECT * FROM reports WHERE id = ? AND agency_id = ? {extra}", [rid, u["agency_id"], *ep]).fetchone()
    if r is None:
        raise ApiError(404, "not_found", "Report not found")
    return r


@bp.get("/reports/<int:rid>")
@require()
def get_report(rid):
    r = _get_report(get_db(), g.user, rid)
    d = {k: r[k] for k in ("id", "title", "kind", "client_id", "period_start", "period_end", "created_at")}
    d["payload"] = json.loads(r["payload"])
    return d


@bp.get("/reports/<int:rid>/csv")
@require()
def report_csv(rid):
    r = _get_report(get_db(), g.user, rid)
    p = json.loads(r["payload"])
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["campaign", "client", "channel", "status", "spend", "impressions", "clicks", "conversions", "ctr_pct", "cpa"])
    for c in p["campaigns"]:
        w.writerow([_csv_safe(x) for x in [
            c["name"], c["client_name"], c["channel"], c["status"], f"{c['spend_cents'] / 100:.2f}", c["impressions"],
            c["clicks"], c["conversions"], c["ctr"] if c["ctr"] is not None else "",
            f"{c['cpa_cents'] / 100:.2f}" if c["cpa_cents"] is not None else ""]])
    return _csv_response(buf.getvalue(), f"report-{rid}.csv")


@bp.post("/reports")
@require("reports.generate")
def create_report():
    d = parse_json()
    u = g.user
    conn = get_db()
    client_id = d.get("client_id")
    if client_id is not None:
        client_id = v_int(d, "client_id", min=1)
    today = today_utc()
    end = v_date(d, "period_end", required=False, default=today.isoformat())
    start = v_date(d, "period_start", required=False,
                   default=(date.fromisoformat(end) - timedelta(days=29)).isoformat())
    if end < start:
        raise ApiError(422, "validation_error", "period_end: must be on or after period_start", {"period_end": "must be on or after period_start"})
    if (date.fromisoformat(end) - date.fromisoformat(start)).days > 366:
        raise ApiError(422, "validation_error", "Report period cannot exceed 366 days")
    if v_bool(d, "async"):
        with transaction(conn):
            tid = scheduler.enqueue(conn, "generate_report", agency_id=u["agency_id"],
                                    payload={"client_id": client_id, "start": start, "end": end,
                                             "requested_by": u["id"], "requested_by_email": u["email"]})
        return {"task_id": tid, "status": "queued"}, 202
    with transaction(conn):
        rid = rpt.create_report(conn, u["agency_id"], u, client_id, start, end)
    return _report_summary(conn, u, rid), 201


def _report_summary(conn, u, rid):
    r = _get_report(conn, u, rid)
    return {"id": r["id"], "title": r["title"], "kind": r["kind"], "period_start": r["period_start"],
            "period_end": r["period_end"], "created_at": r["created_at"]}


# =================================================================== audit log
def _audit_dict(r):
    d = dict(r)
    d["detail"] = json.loads(d["detail"]) if d.get("detail") else None
    return d


@bp.get("/audit-logs")
@require("audit.read")
def audit_logs():
    u = g.user
    where, params = ["a.agency_id = ?"], [u["agency_id"]]
    for arg, col in (("entity_type", "a.entity_type"), ("action", "a.action")):
        if request.args.get(arg):
            where.append(f"{col} = ?")
            params.append(request.args[arg])
    if request.args.get("q"):
        where.append("(a.actor_label LIKE ? ESCAPE '\\' OR a.action LIKE ? ESCAPE '\\' OR a.detail LIKE ? ESCAPE '\\')")
        params += [f"%{like_escape(request.args['q'])}%"] * 3
    page, per_page, offset = page_args(30)
    conn = get_db()
    w = " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) FROM audit_logs a WHERE {w}", params).fetchone()[0]
    rows = conn.execute(f"SELECT a.* FROM audit_logs a WHERE {w} ORDER BY a.id DESC LIMIT ? OFFSET ?",
                        [*params, per_page, offset]).fetchall()
    return {"items": [_audit_dict(r) for r in rows], "total": total, "page": page, "per_page": per_page}


# =================================================================== tasks (scheduler visibility)
@bp.get("/tasks")
@require("tasks.read")
def list_tasks():
    u = g.user
    rows = get_db().execute(
        """SELECT id, agency_id, kind, status, run_at, attempts, max_attempts, interval_seconds,
                  last_error, finished_at, created_at
             FROM tasks WHERE agency_id = ? OR agency_id IS NULL
            ORDER BY (status = 'failed') DESC, run_at LIMIT 100""", (u["agency_id"],))
    return {"items": rows_to_dicts(rows)}


@bp.post("/tasks/<int:tid>/run")
@require("tasks.run")
def run_task_now(tid):
    u = g.user
    conn = get_db()
    with transaction(conn):
        t = conn.execute("SELECT * FROM tasks WHERE id = ? AND (agency_id = ? OR agency_id IS NULL)",
                         (tid, u["agency_id"])).fetchone()
        if t is None:
            raise ApiError(404, "not_found", "Task not found")
        if t["status"] == "running":
            raise ApiError(409, "task_running", "Task is already running")
        conn.execute("UPDATE tasks SET status = 'pending', run_at = ?, attempts = 0, last_error = NULL WHERE id = ?",
                     (utcnow(), tid))
        service.audit(conn, u["agency_id"], u, "task.run_requested", "task", tid, {"kind": t["kind"]})
    return {"ok": True}
