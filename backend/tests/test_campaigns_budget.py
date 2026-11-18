"""Campaign lifecycle, budget rules, transactions and concurrency."""
import sqlite3
import threading

import pytest

from campaignflow import db, scheduler
from conftest import iso_day, new_campaign


# ---------------------------------------------------------------- validation + lifecycle
def test_create_validation(manager):
    assert new_campaign(manager, end_date=iso_day(-5), start_date=iso_day(0)).status_code == 422
    assert new_campaign(manager, budget_cents=0).status_code == 422
    assert new_campaign(manager, budget_cents=12.5).status_code == 422  # floats rejected: money is integer cents
    assert new_campaign(manager, client_id=9999).status_code == 422
    assert new_campaign(manager, channel_id=9999).status_code == 422
    assert new_campaign(manager, start_date="2026-13-40").status_code == 422
    r = manager.post("/api/campaigns", data="not json", headers={"Content-Type": "text/plain"})
    assert r.status_code == 415


def test_state_machine(manager):
    cid = new_campaign(manager).get_json()["id"]
    st = lambda s: manager.post(f"/api/campaigns/{cid}/status", json={"status": s})  # noqa: E731
    assert st("completed").status_code == 409          # draft -> completed not allowed
    assert st("scheduled").status_code == 200
    assert st("active").status_code == 200              # started yesterday
    assert st("paused").status_code == 200
    assert st("active").status_code == 200
    assert st("completed").status_code == 200
    assert st("active").status_code == 409              # terminal
    assert manager.patch(f"/api/campaigns/{cid}", json={"name": "x"}).status_code == 409
    assert manager.post(f"/api/campaigns/{cid}/budget", json={"budget_cents": 5000}).status_code == 409


def test_future_campaign_cannot_be_activated_manually(manager):
    cid = new_campaign(manager, start_date=iso_day(5), end_date=iso_day(9), schedule=True).get_json()["id"]
    assert manager.post(f"/api/campaigns/{cid}/status", json={"status": "active"}).status_code == 409


def test_filter_sort_paginate_search(admin):
    r = admin.get("/api/campaigns?status=active&sort=budget_cents&dir=desc&per_page=3").get_json()
    assert len(r["items"]) <= 3 and r["total"] >= len(r["items"])
    budgets = [c["budget_cents"] for c in r["items"]]
    assert budgets == sorted(budgets, reverse=True)
    assert all(c["status"] == "active" for c in r["items"])
    q = admin.get("/api/campaigns?q=Kova").get_json()["items"]
    assert q and all("Kova" in c["name"] or "Kova" in c["client_name"] for c in q)
    # LIKE wildcards are escaped, and SQL injection through sort is impossible
    assert admin.get("/api/campaigns?q=%25").get_json()["total"] == 0
    assert admin.get("/api/campaigns?sort=name;DROP TABLE campaigns").status_code == 200
    assert admin.get("/api/campaigns").get_json()["total"] > 0


def test_csv_export_neutralises_formulas(admin):
    cid = new_campaign(admin, name="=HYPERLINK(\"http://evil\")").get_json()["id"]
    csv_text = admin.get("/api/campaigns/export.csv").get_data(as_text=True)
    assert "'=HYPERLINK" in csv_text and ",=HYPERLINK" not in csv_text and cid


# ---------------------------------------------------------------- budget logic
def test_spend_within_and_over_budget(manager, raw):
    cid = new_campaign(manager, budget_cents=10_000, schedule=True).get_json()["id"]
    manager.post(f"/api/campaigns/{cid}/status", json={"status": "active"})
    ok = manager.post(f"/api/campaigns/{cid}/metrics", json={"spend_cents": 6000, "impressions": 1000, "clicks": 50, "conversions": 5})
    assert ok.status_code == 201 and ok.get_json()["remaining_cents"] == 4000
    over = manager.post(f"/api/campaigns/{cid}/metrics", json={"spend_cents": 4001})
    assert over.status_code == 409 and over.get_json()["error"]["code"] == "budget_exceeded"
    # rejected request left no trace
    assert raw.execute("SELECT spent_cents FROM campaigns WHERE id=?", (cid,)).fetchone()[0] == 6000


def test_exhausting_budget_auto_pauses(manager):
    cid = new_campaign(manager, budget_cents=5000, schedule=True).get_json()["id"]
    manager.post(f"/api/campaigns/{cid}/status", json={"status": "active"})
    r = manager.post(f"/api/campaigns/{cid}/metrics", json={"spend_cents": 5000, "impressions": 10})
    assert r.get_json()["auto_paused"] is True
    c = manager.get(f"/api/campaigns/{cid}").get_json()
    assert c["status"] == "paused"
    assert any(a["action"] == "campaign.auto_paused" for a in c["activity"])
    # cannot resume until the budget is raised
    assert manager.post(f"/api/campaigns/{cid}/status", json={"status": "active"}).status_code == 409
    assert manager.post(f"/api/campaigns/{cid}/budget", json={"budget_cents": 8000, "reason": "top-up"}).status_code == 200
    assert manager.post(f"/api/campaigns/{cid}/status", json={"status": "active"}).status_code == 200


def test_budget_cannot_drop_below_spend(manager):
    cid = new_campaign(manager, budget_cents=10_000, schedule=True).get_json()["id"]
    manager.post(f"/api/campaigns/{cid}/status", json={"status": "active"})
    manager.post(f"/api/campaigns/{cid}/metrics", json={"spend_cents": 4000})
    r = manager.post(f"/api/campaigns/{cid}/budget", json={"budget_cents": 3999})
    assert r.status_code == 409 and r.get_json()["error"]["code"] == "budget_below_spend"
    assert manager.post(f"/api/campaigns/{cid}/budget", json={"budget_cents": 4000}).status_code == 200


def test_metrics_validation(manager):
    cid = new_campaign(manager, schedule=True).get_json()["id"]
    assert manager.post(f"/api/campaigns/{cid}/metrics", json={"spend_cents": 1}).status_code == 409  # not active yet
    manager.post(f"/api/campaigns/{cid}/status", json={"status": "active"})
    p = f"/api/campaigns/{cid}/metrics"
    assert manager.post(p, json={"spend_cents": -1}).status_code == 422
    assert manager.post(p, json={"spend_cents": 1, "impressions": 5, "clicks": 9}).status_code == 422
    assert manager.post(p, json={"spend_cents": 1, "date": iso_day(3)}).status_code == 422
    assert manager.post(p, json={"spend_cents": 1, "date": iso_day(-30)}).status_code == 422  # before flight
    # same-day entries accumulate in one row (upsert)
    manager.post(p, json={"spend_cents": 100, "impressions": 100, "clicks": 4})
    manager.post(p, json={"spend_cents": 200, "impressions": 50, "clicks": 1})
    daily = manager.get(f"/api/campaigns/{cid}").get_json()["daily"]
    assert len(daily) == 1 and daily[0]["spend_cents"] == 300 and daily[0]["impressions"] == 150


def test_budget_history_and_audit_trail(admin):
    cid = new_campaign(admin, schedule=True).get_json()["id"]
    admin.post(f"/api/campaigns/{cid}/budget", json={"budget_cents": 20_000, "reason": "client asked"})
    d = admin.get(f"/api/campaigns/{cid}").get_json()
    assert d["budget_history"][0]["new_budget_cents"] == 20_000 and d["budget_history"][0]["reason"] == "client asked"
    actions = [a["action"] for a in d["activity"]]
    assert "campaign.created" in actions and "campaign.budget_changed" in actions


# ---------------------------------------------------------------- concurrency
def test_concurrent_spend_never_exceeds_budget(app, manager, raw):
    """30 simultaneous requests of $10 against a $100 budget: exactly 10 succeed."""
    cid = new_campaign(manager, budget_cents=10_000, schedule=True).get_json()["id"]
    manager.post(f"/api/campaigns/{cid}/status", json={"status": "active"})
    results, barrier = [], threading.Barrier(30)

    def worker():
        c = app.test_client()
        c.post("/api/auth/login", json={"email": "manager@northwind.test", "password": "demo1234"})
        barrier.wait()
        results.append(c.post(f"/api/campaigns/{cid}/metrics", json={"spend_cents": 1000, "impressions": 10}).status_code)

    threads = [threading.Thread(target=worker) for _ in range(30)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count(201) == 10, results
    assert all(s in (201, 409) for s in results), results
    row = raw.execute("SELECT spent_cents, budget_cents FROM campaigns WHERE id=?", (cid,)).fetchone()
    assert row["spent_cents"] == 10_000 <= row["budget_cents"]
    ledger = raw.execute("SELECT SUM(spend_cents) FROM daily_metrics WHERE campaign_id=?", (cid,)).fetchone()[0]
    assert ledger == row["spent_cents"]  # denormalised total and ledger agree


def test_db_constraint_is_last_line_of_defence(raw):
    """Even raw SQL that bypasses the app cannot overspend."""
    with pytest.raises(sqlite3.IntegrityError):
        raw.execute("UPDATE campaigns SET spent_cents = budget_cents + 1 WHERE id = 1")


def test_optimistic_locking_detects_stale_edit(manager):
    c = new_campaign(manager).get_json()
    v = c["version"]
    assert manager.patch(f"/api/campaigns/{c['id']}", json={"name": "A", "version": v}).status_code == 200
    r = manager.patch(f"/api/campaigns/{c['id']}", json={"name": "B", "version": v})
    assert r.status_code == 409 and r.get_json()["error"]["code"] == "version_conflict"


def test_transaction_rolls_back_atomically(raw):
    before = raw.execute("SELECT COUNT(*) FROM clients").fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError):
        with db.transaction(raw):
            raw.execute("INSERT INTO clients (agency_id,name,created_at,updated_at) VALUES (1,'Temp','x','x')")
            raw.execute("INSERT INTO clients (agency_id,name,created_at,updated_at) VALUES (1,'Temp','x','x')")  # dup
    assert raw.execute("SELECT COUNT(*) FROM clients").fetchone()[0] == before


# ---------------------------------------------------------------- clients
def test_client_archive_rules(manager):
    # Kova has live campaigns -> cannot archive
    assert manager.patch("/api/clients/1", json={"status": "archived"}).status_code == 409
    c = manager.post("/api/clients", json={"name": "Fresh Co", "contact_email": "a@fresh.test"}).get_json()
    assert manager.post("/api/clients", json={"name": "fresh co"}).status_code in (201, 409)  # UNIQUE is case-sensitive in SQLite
    assert manager.patch(f"/api/clients/{c['id']}", json={"status": "archived"}).status_code == 200
    assert new_campaign(manager, client_id=c["id"]).status_code == 409
    assert manager.post("/api/clients", json={"name": "Fresh Co"}).status_code == 409


def test_indexes_used_for_hot_queries(raw):
    plan = " ".join(r[3] for r in raw.execute(
        "EXPLAIN QUERY PLAN SELECT * FROM campaigns WHERE client_id=1 AND status='active'"))
    assert "idx_campaigns_client_status" in plan
    plan = " ".join(r[3] for r in raw.execute(
        "EXPLAIN QUERY PLAN SELECT * FROM audit_logs WHERE agency_id=1 ORDER BY id DESC LIMIT 30"))
    assert "idx_audit_agency" in plan
    plan = " ".join(r[3] for r in raw.execute(
        "EXPLAIN QUERY PLAN SELECT * FROM tasks WHERE status='pending' AND run_at<='z'"))
    assert "idx_tasks_due" in plan
