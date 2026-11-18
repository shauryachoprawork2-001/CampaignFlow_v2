"""Database-backed task queue + background worker.

* Tasks live in the `tasks` table, so they survive restarts and can be
  inspected from the UI.
* Claiming is a single `UPDATE ... WHERE id = (SELECT ...) RETURNING *`
  statement, which is atomic: two workers can never run the same task.
* Failures retry with exponential backoff up to `max_attempts`.
* Recurring tasks (interval_seconds) reschedule themselves after each run.
* Tasks stuck in `running` (crashed worker) are reclaimed after a timeout.
* Handlers are idempotent (guarded by status checks / unique indexes), so
  at-least-once delivery is safe.
"""
from __future__ import annotations

import json
import logging
import os
import random
import threading
import traceback
from datetime import date, datetime, timedelta, timezone

from . import reports, service
from .db import connect, transaction
from .util import ApiError, iso, utcnow_dt

log = logging.getLogger("campaignflow.scheduler")

STALE_LOCK_SECONDS = 300
HANDLERS = {}


def handler(kind):
    def deco(fn):
        HANDLERS[kind] = fn
        return fn
    return deco


# ------------------------------------------------------------------ queue API
def enqueue(conn, kind, *, payload=None, run_at=None, agency_id=None,
            interval_seconds=None, dedupe_key=None, max_attempts=3):
    """Insert a task. With a dedupe_key, re-enqueueing is a no-op (returns existing id)."""
    now = utcnow_dt()
    cur = conn.execute(
        """INSERT OR IGNORE INTO tasks (agency_id, kind, payload, run_at, interval_seconds,
                                        dedupe_key, max_attempts, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (agency_id, kind, json.dumps(payload or {}), run_at or iso(now), interval_seconds,
         dedupe_key, max_attempts, iso(now)),
    )
    if cur.rowcount:
        return cur.lastrowid
    return conn.execute("SELECT id FROM tasks WHERE dedupe_key = ?", (dedupe_key,)).fetchone()["id"]


def ensure_system_tasks(conn, simulate: bool, sim_interval: int = 60):
    enqueue(conn, "sweep_campaigns", interval_seconds=60, dedupe_key="recurring:sweep_campaigns")
    if simulate:
        enqueue(conn, "sync_delivery", interval_seconds=sim_interval, dedupe_key="recurring:sync_delivery")
    for a in conn.execute("SELECT id FROM agencies"):
        ensure_agency_tasks(conn, a["id"])


def ensure_agency_tasks(conn, agency_id: int):
    enqueue(conn, "weekly_reports", agency_id=agency_id, interval_seconds=3600,
            dedupe_key=f"recurring:weekly_reports:{agency_id}")


def claim_next(conn, worker_id: str, now: datetime):
    row = conn.execute(
        """UPDATE tasks
              SET status = 'running', locked_by = ?, locked_at = ?, attempts = attempts + 1
            WHERE id = (SELECT id FROM tasks
                         WHERE status = 'pending' AND run_at <= ?
                         ORDER BY run_at, id LIMIT 1)
              AND status = 'pending'
        RETURNING *""",
        (worker_id, iso(now), iso(now)),
    ).fetchone()
    return row


def reclaim_stale(conn, now: datetime):
    cutoff = iso(now - timedelta(seconds=STALE_LOCK_SECONDS))
    conn.execute(
        "UPDATE tasks SET status = 'pending', locked_by = NULL, locked_at = NULL "
        "WHERE status = 'running' AND locked_at < ?",
        (cutoff,),
    )


def _finish(conn, task, now: datetime, error: str | None):
    interval = task["interval_seconds"]
    if error is None:
        if interval:
            conn.execute(
                "UPDATE tasks SET status='pending', run_at=?, attempts=0, last_error=NULL, locked_by=NULL, "
                "locked_at=NULL, finished_at=? WHERE id=?",
                (iso(now + timedelta(seconds=interval)), iso(now), task["id"]),
            )
        else:
            conn.execute(
                "UPDATE tasks SET status='done', last_error=NULL, locked_by=NULL, locked_at=NULL, finished_at=? WHERE id=?",
                (iso(now), task["id"]),
            )
        return
    if task["attempts"] < task["max_attempts"]:  # retry with exponential backoff
        delay = min(300, 5 * 2 ** task["attempts"])
        conn.execute(
            "UPDATE tasks SET status='pending', run_at=?, last_error=?, locked_by=NULL, locked_at=NULL WHERE id=?",
            (iso(now + timedelta(seconds=delay)), error, task["id"]),
        )
    elif interval:  # recurring tasks never die; record the error and wait for the next slot
        conn.execute(
            "UPDATE tasks SET status='pending', run_at=?, attempts=0, last_error=?, locked_by=NULL, "
            "locked_at=NULL, finished_at=? WHERE id=?",
            (iso(now + timedelta(seconds=interval)), error, iso(now), task["id"]),
        )
    else:
        conn.execute(
            "UPDATE tasks SET status='failed', last_error=?, locked_by=NULL, locked_at=NULL, finished_at=? WHERE id=?",
            (error, iso(now), task["id"]),
        )


def run_once(conn, worker_id="worker", now: datetime | None = None, limit: int = 50) -> int:
    """Run all currently-due tasks (up to `limit`). Returns how many were executed."""
    now = now or utcnow_dt()
    reclaim_stale(conn, now)
    ran = 0
    while ran < limit:
        task = claim_next(conn, worker_id, now)
        if task is None:
            break
        ran += 1
        error = None
        try:
            fn = HANDLERS.get(task["kind"])
            if fn is None:
                raise RuntimeError(f"no handler registered for task kind {task['kind']!r}")
            fn(conn, task, now)
        except Exception as exc:  # noqa: BLE001 - a task failure must never kill the worker
            error = f"{type(exc).__name__}: {exc}"
            log.warning("task %s (%s) failed: %s\n%s", task["id"], task["kind"], error, traceback.format_exc())
        _finish(conn, task, now, error)
    return ran


class Worker(threading.Thread):
    def __init__(self, db_path: str, poll_seconds: float = 1.0):
        super().__init__(name="campaignflow-worker", daemon=True)
        self.db_path, self.poll = db_path, poll_seconds
        self.stop_event = threading.Event()
        self.worker_id = f"{os.getpid()}-{id(self) % 10000}"

    def run(self):
        conn = connect(self.db_path)
        try:
            while not self.stop_event.is_set():
                try:
                    if run_once(conn, self.worker_id) == 0:
                        self.stop_event.wait(self.poll)
                except Exception:  # noqa: BLE001
                    log.exception("worker loop error")
                    self.stop_event.wait(self.poll * 3)
        finally:
            conn.close()

    def stop(self):
        self.stop_event.set()


# ------------------------------------------------------------------ handlers
@handler("sweep_campaigns")
def sweep_campaigns(conn, task, now: datetime):
    """Date-driven lifecycle: scheduled -> active on start day, active/paused ->
    completed after end date, stale scheduled -> cancelled."""
    today = now.date()
    t = today.isoformat()

    plan = [
        ("scheduled", "active", "ca.status='scheduled' AND ca.start_date <= ? AND ca.end_date >= ? AND ca.spent_cents < ca.budget_cents"),
        ("active", "completed", "ca.status IN ('active','paused') AND ca.end_date < ?"),
        ("scheduled", "cancelled", "ca.status='scheduled' AND ca.end_date < ?"),
    ]
    for _from, to, where in plan:
        params = (t, t) if where.count("?") == 2 else (t,)
        rows = conn.execute(
            f"SELECT ca.id FROM campaigns ca WHERE {where}", params
        ).fetchall()
        for r in rows:
            with transaction(conn):  # one campaign per transaction; re-read to stay race-free
                row = conn.execute(service.campaign_select(single=True) + " WHERE ca.id = ?", (r["id"], r["id"])).fetchone()
                if row is None or row["status"] not in ("scheduled", "active", "paused"):
                    continue
                try:
                    service.change_status(conn, row, to, None, today, reason="scheduler")
                except ApiError:
                    continue  # state changed under us (e.g. someone paused it): skip, don't fail the sweep


@handler("sync_delivery")
def sync_delivery(conn, task, now: datetime):
    """Demo stand-in for pulling numbers from ad platforms: simulates a slice of
    daily delivery for every active campaign. Enabled with CF_SIMULATE=1."""
    speedup = float(os.environ.get("CF_SIM_SPEEDUP", "60"))  # 60 -> each tick ~ one hour of delivery
    rng = random.Random()
    today = now.date()
    t = today.isoformat()
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM campaigns WHERE status='active' AND start_date <= ? AND end_date >= ?", (t, t))]
    for cid in ids:
        with transaction(conn):
            row = conn.execute(service.campaign_select(single=True) + " WHERE ca.id = ?", (cid, cid)).fetchone()
            if row is None or row["status"] != "active":
                continue
            days = (date.fromisoformat(row["end_date"]) - date.fromisoformat(row["start_date"])).days + 1
            daily = row["budget_cents"] / days
            slice_cents = int(daily * (speedup / 1440.0) * rng.uniform(0.6, 1.4))
            slice_cents = min(slice_cents, row["budget_cents"] - row["spent_cents"])
            if slice_cents <= 0:
                continue
            cpm = rng.uniform(4, 18)
            imp = int(slice_cents / 100 / cpm * 1000)
            clk = min(imp, int(imp * rng.uniform(0.008, 0.035)))
            conv = min(clk, int(clk * rng.uniform(0.02, 0.09)))
            service.record_metrics(conn, row["agency_id"], cid, None, metric_date=t, spend_cents=slice_cents,
                                   impressions=imp, clicks=clk, conversions=conv, today=today)


@handler("weekly_reports")
def weekly_reports(conn, task, now: datetime):
    """For each active client (+ one agency-wide report) generate last week's
    Mon-Sun report. The unique index on scheduled reports makes this idempotent."""
    agency_id = task["agency_id"]
    today = now.date()
    monday = today - timedelta(days=today.weekday() + 7)
    start, end = monday.isoformat(), (monday + timedelta(days=6)).isoformat()
    clients = [r["id"] for r in conn.execute(
        "SELECT id FROM clients WHERE agency_id = ? AND status = 'active'", (agency_id,))]
    for client_id in [None, *clients]:
        with transaction(conn):
            reports.create_report(conn, agency_id, None, client_id, start, end, kind="scheduled")


@handler("generate_report")
def generate_report(conn, task, now: datetime):
    p = json.loads(task["payload"])
    actor = {"id": p.get("requested_by"), "email": p.get("requested_by_email", "system")} if p.get("requested_by") else None
    with transaction(conn):
        reports.create_report(conn, task["agency_id"], actor, p.get("client_id"), p["start"], p["end"], kind="on_demand")
