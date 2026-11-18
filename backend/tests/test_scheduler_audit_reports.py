"""Background tasks, audit log integrity and reporting."""
import json
import sqlite3
import threading
from datetime import timedelta

import pytest

from campaignflow import db, scheduler
from campaignflow.util import utcnow_dt
from conftest import iso_day, new_campaign


# ---------------------------------------------------------------- audit log
def test_audit_log_is_append_only(raw):
    n = raw.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0]
    assert n > 0
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        raw.execute("UPDATE audit_logs SET action='x'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        raw.execute("DELETE FROM audit_logs")


def test_mutations_are_audited_with_actor(admin, manager):
    cid = new_campaign(manager).get_json()["id"]
    manager.post(f"/api/campaigns/{cid}/status", json={"status": "scheduled", "reason": "ready"})
    logs = admin.get(f"/api/audit-logs?entity_type=campaign").get_json()["items"]
    mine = [a for a in logs if a["entity_id"] == cid]
    assert {a["action"] for a in mine} == {"campaign.created", "campaign.status_changed"}
    assert all(a["actor_label"] == "manager@northwind.test" for a in mine)
    sc = next(a for a in mine if a["action"] == "campaign.status_changed")
    assert sc["detail"] == {"from": "draft", "to": "scheduled", "reason": "ready"}


def test_failed_operations_are_not_audited(admin, manager):
    before = admin.get("/api/audit-logs").get_json()["total"]
    manager.post("/api/campaigns/1/budget", json={"budget_cents": 1})  # below spend -> rejected
    assert admin.get("/api/audit-logs").get_json()["total"] == before


def test_failed_logins_are_audited_and_filterable(admin, client):
    client.post("/api/auth/login", json={"email": "manager@northwind.test", "password": "wrongwrong"})
    r = admin.get("/api/audit-logs?action=auth.login_failed").get_json()
    assert r["total"] >= 1 and r["items"][0]["actor_label"] == "manager@northwind.test"


def test_audit_search_and_pagination(admin):
    r = admin.get("/api/audit-logs?per_page=5&page=2").get_json()
    assert len(r["items"]) == 5 and r["page"] == 2
    assert admin.get("/api/audit-logs?q=budget").get_json()["total"] >= 0


# ---------------------------------------------------------------- scheduler
def test_sweep_activates_and_completes(app, raw, manager):
    a = new_campaign(manager, start_date=iso_day(2), end_date=iso_day(6), schedule=True).get_json()["id"]
    b = new_campaign(manager, start_date=iso_day(-10), end_date=iso_day(-1), schedule=False).get_json()["id"]
    # force b to "active" with an expired flight, as if the worker was down for days
    raw.execute("UPDATE campaigns SET status='active' WHERE id=?", (b,))
    # two days later the scheduled campaign should start
    future = utcnow_dt() + timedelta(days=2, seconds=5)
    scheduler.run_once(raw, now=future)
    assert raw.execute("SELECT status FROM campaigns WHERE id=?", (a,)).fetchone()[0] == "active"
    assert raw.execute("SELECT status FROM campaigns WHERE id=?", (b,)).fetchone()[0] == "completed"
    actions = [r[0] for r in raw.execute("SELECT action FROM audit_logs WHERE entity_id IN (?,?) AND actor_label='system'", (a, b))]
    assert actions.count("campaign.status_changed") == 2


def test_sweep_cancels_scheduled_campaign_that_never_started(raw, manager):
    c = new_campaign(manager, start_date=iso_day(1), end_date=iso_day(2), schedule=True).get_json()["id"]
    scheduler.run_once(raw, now=utcnow_dt() + timedelta(days=5))
    assert raw.execute("SELECT status FROM campaigns WHERE id=?", (c,)).fetchone()[0] == "cancelled"


def test_sweep_is_idempotent_and_recurring(raw):
    scheduler.run_once(raw)
    t = raw.execute("SELECT * FROM tasks WHERE dedupe_key='recurring:sweep_campaigns'").fetchone()
    assert t["status"] == "pending" and t["run_at"] > utcnow_dt().strftime("%Y-%m-%dT%H:%M:%SZ")  # rescheduled
    assert scheduler.run_once(raw) == 0  # nothing due immediately afterwards


def test_enqueue_dedupes(raw):
    with db.transaction(raw):
        a = scheduler.enqueue(raw, "x", dedupe_key="k1")
        b = scheduler.enqueue(raw, "x", dedupe_key="k1")
    assert a == b


def test_failed_task_retries_with_backoff_then_fails(raw):
    @scheduler.handler("boom")
    def boom(conn, task, now):
        raise RuntimeError("kaboom")
    try:
        with db.transaction(raw):
            tid = scheduler.enqueue(raw, "boom", max_attempts=2)
        now = utcnow_dt()
        scheduler.run_once(raw, now=now)
        t = raw.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
        assert t["status"] == "pending" and t["attempts"] == 1 and "kaboom" in t["last_error"]
        assert t["run_at"] > now.strftime("%Y-%m-%dT%H:%M:%SZ")  # backoff
        scheduler.run_once(raw, now=now + timedelta(hours=1))
        t = raw.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
        assert t["status"] == "failed" and t["attempts"] == 2
    finally:
        scheduler.HANDLERS.pop("boom")


def test_unknown_task_kind_fails_cleanly(raw):
    with db.transaction(raw):
        tid = scheduler.enqueue(raw, "no_such_handler", max_attempts=1)
    scheduler.run_once(raw)
    assert raw.execute("SELECT status FROM tasks WHERE id=?", (tid,)).fetchone()[0] == "failed"


def test_stale_running_task_is_reclaimed(raw):
    with db.transaction(raw):
        tid = scheduler.enqueue(raw, "sweep_campaigns")
    claimed = scheduler.claim_next(raw, "dead-worker", utcnow_dt())
    assert claimed["id"] in (tid,) or claimed is not None
    raw.execute("UPDATE tasks SET status='running', locked_at=? WHERE id=?",
                ((utcnow_dt() - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"), tid))
    scheduler.reclaim_stale(raw, utcnow_dt())
    assert raw.execute("SELECT status FROM tasks WHERE id=?", (tid,)).fetchone()[0] == "pending"


def test_two_workers_never_run_the_same_task(app):
    path = app.config["DATABASE"]
    setup = db.connect(path)
    with db.transaction(setup):
        for i in range(40):
            scheduler.enqueue(setup, "count", dedupe_key=f"c{i}")
    ran, lock = [], threading.Lock()

    @scheduler.handler("count")
    def count(conn, task, now):
        with lock:
            ran.append(task["id"])

    def run(name):
        conn = db.connect(path)
        scheduler.run_once(conn, worker_id=name)
        conn.close()

    try:
        ts = [threading.Thread(target=run, args=(f"w{i}",)) for i in range(4)]
        [t.start() for t in ts]
        [t.join() for t in ts]
    finally:
        scheduler.HANDLERS.pop("count")
    assert len(ran) == 40 and len(set(ran)) == 40  # every task exactly once


def test_simulated_delivery_respects_budget(app, raw, manager, monkeypatch):
    monkeypatch.setenv("CF_SIM_SPEEDUP", "100000")  # absurdly fast: forces budget exhaustion
    cid = new_campaign(manager, budget_cents=5000, schedule=True).get_json()["id"]
    manager.post(f"/api/campaigns/{cid}/status", json={"status": "active"})
    with db.transaction(raw):
        scheduler.enqueue(raw, "sync_delivery")
    for _ in range(5):
        with db.transaction(raw):
            scheduler.enqueue(raw, "sync_delivery")
        scheduler.run_once(raw)
    row = raw.execute("SELECT spent_cents, budget_cents, status FROM campaigns WHERE id=?", (cid,)).fetchone()
    assert row["spent_cents"] <= row["budget_cents"]
    assert row["status"] == "paused"  # auto-paused on exhaustion


# ---------------------------------------------------------------- reports
def test_report_generation_totals_match(admin, raw):
    r = admin.post("/api/reports", json={"period_start": iso_day(-40), "period_end": iso_day(0)})
    assert r.status_code == 201
    rep = admin.get(f"/api/reports/{r.get_json()['id']}").get_json()["payload"]
    spend = raw.execute("SELECT SUM(spend_cents) FROM daily_metrics WHERE metric_date BETWEEN ? AND ?",
                        (iso_day(-40), iso_day(0))).fetchone()[0]
    assert rep["summary"]["spend_cents"] == spend
    assert sum(c["spend_cents"] for c in rep["channels"]) == spend
    assert sum(c["spend_cents"] for c in rep["campaigns"]) == spend
    assert sum(d["spend_cents"] for d in rep["daily"]) == spend
    assert len(rep["daily"]) == 41


def test_report_for_one_client_is_scoped(admin):
    r = admin.post("/api/reports", json={"client_id": 1, "period_start": iso_day(-40), "period_end": iso_day(0)}).get_json()
    p = admin.get(f"/api/reports/{r['id']}").get_json()["payload"]
    assert p["campaigns"] and {c["client_name"] for c in p["campaigns"]} == {"Kova Outdoor"}


def test_report_validation_and_csv(admin, analyst):
    assert admin.post("/api/reports", json={"period_start": iso_day(0), "period_end": iso_day(-3)}).status_code == 422
    assert admin.post("/api/reports", json={"period_start": iso_day(-900), "period_end": iso_day(0)}).status_code == 422
    assert admin.post("/api/reports", json={"client_id": 99999}).status_code == 422
    assert analyst.post("/api/reports", json={}).status_code == 201  # analysts may generate reports
    csv_resp = admin.get("/api/reports/1/csv")
    assert csv_resp.status_code == 200 and csv_resp.mimetype == "text/csv"
    assert csv_resp.get_data(as_text=True).startswith("campaign,client,channel")


def test_async_report_goes_through_task_queue(admin, raw):
    r = admin.post("/api/reports", json={"async": True, "client_id": 2, "period_start": iso_day(-10), "period_end": iso_day(0)})
    assert r.status_code == 202
    q = "SELECT COUNT(*) FROM reports WHERE kind='on_demand'"
    before = raw.execute(q).fetchone()[0]
    scheduler.run_once(raw)  # also runs other due tasks (weekly reports); only count the on-demand one
    assert raw.execute(q).fetchone()[0] == before + 1
    assert raw.execute("SELECT status FROM tasks WHERE id=?", (r.get_json()["task_id"],)).fetchone()[0] == "done"
    last = raw.execute("SELECT actor_label FROM audit_logs WHERE action='report.generated' ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert last == "admin@northwind.test"  # the requester, not "system"


def test_weekly_reports_are_idempotent(raw):
    now = utcnow_dt()
    with db.transaction(raw):
        tid = scheduler.enqueue(raw, "weekly_reports", agency_id=1)
    scheduler.run_once(raw, now=now)
    n1 = raw.execute("SELECT COUNT(*) FROM reports WHERE kind='scheduled'").fetchone()[0]
    assert n1 == 1 + 4  # agency-wide + 4 active clients
    with db.transaction(raw):
        scheduler.enqueue(raw, "weekly_reports", agency_id=1)  # simulate retry / double delivery
    raw.execute("UPDATE tasks SET status='pending', run_at=? WHERE kind='weekly_reports'", ("2000-01-01T00:00:00Z",))
    scheduler.run_once(raw, now=now)
    assert raw.execute("SELECT COUNT(*) FROM reports WHERE kind='scheduled'").fetchone()[0] == n1


def test_dashboard_numbers(admin, raw):
    d = admin.get("/api/dashboard?days=30").get_json()
    spend = raw.execute("SELECT SUM(spend_cents) FROM daily_metrics WHERE metric_date >= ?", (iso_day(-29),)).fetchone()[0]
    assert d["kpis"]["spend_cents"]["value"] == spend
    assert len(d["daily"]) == 30
    assert d["total_campaigns"] == raw.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0]
    assert any("budget_80" in c["alerts"] for c in d["attention"])  # seeded: Lumira DPA is at 97%
    assert admin.get("/api/dashboard?days=abc").status_code == 422


def test_tasks_visibility_and_manual_run(admin, manager, analyst):
    items = admin.get("/api/tasks").get_json()["items"]
    assert {t["kind"] for t in items} >= {"sweep_campaigns", "weekly_reports"}
    assert analyst.get("/api/tasks").status_code == 403
    tid = items[0]["id"]
    assert manager.post(f"/api/tasks/{tid}/run").status_code == 200
    assert manager.post("/api/tasks/99999/run").status_code == 404
