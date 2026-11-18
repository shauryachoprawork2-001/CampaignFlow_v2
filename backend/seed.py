"""Populate a database with a realistic demo agency.

    python seed.py            # creates demo data in ./campaignflow.db (skips if it already exists)
    python seed.py --reset    # wipes and recreates

Logins (password for all: demo1234):
    admin@northwind.test    admin
    manager@northwind.test  manager
    analyst@northwind.test  analyst
    kova@client.test        client_viewer (sees only Kova Outdoor)
"""
import os
import random
import sys
from datetime import timedelta

from campaignflow import create_app, db, scheduler, security, service, reports
from campaignflow.util import today_utc, utcnow

PASSWORD = "demo1234"


def seed(app):
    conn = db.connect(app.config["DATABASE"])
    rng = random.Random(42)
    today = today_utc()
    d = lambda n: (today + timedelta(days=n)).isoformat()  # noqa: E731
    ch = {r["code"]: r["id"] for r in conn.execute("SELECT id, code FROM channels")}
    with db.transaction(conn):
        now = utcnow()
        aid = conn.execute("INSERT INTO agencies (name, currency, created_at) VALUES ('Northwind Media', 'USD', ?)", (now,)).lastrowid
        clients = {}
        for name, industry, contact in [
            ("Kova Outdoor", "Retail", "Mira Kova"), ("Lumira Skincare", "Beauty", "Jonas Berg"),
            ("Wevion Sports", "Apparel", "Sam Ortiz"), ("Nova Travel", "Travel", "Ines Moreau"),
        ]:
            clients[name] = conn.execute(
                "INSERT INTO clients (agency_id, name, industry, contact_name, contact_email, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                (aid, name, industry, contact, f"{contact.split()[0].lower()}@{name.split()[0].lower()}.test", now, now)).lastrowid
        h = security.hash_password(PASSWORD)
        admin = None
        for email, name, role, cid in [
            ("admin@northwind.test", "Ava Admin", "admin", None),
            ("manager@northwind.test", "Mateo Manager", "manager", None),
            ("analyst@northwind.test", "Anya Analyst", "analyst", None),
            ("kova@client.test", "Mira Kova", "client_viewer", clients["Kova Outdoor"]),
        ]:
            uid = conn.execute(
                "INSERT INTO users (agency_id, client_id, email, name, password_hash, role, created_at) VALUES (?,?,?,?,?,?,?)",
                (aid, cid, email, name, h, role, now)).lastrowid
            if role == "admin":
                admin = {"id": uid, "email": email}
        service.audit(conn, aid, admin, "agency.created", "agency", aid, {"name": "Northwind Media"})

        # (client, channel, name, objective, start_off, end_off, budget $, target status, burn)
        plan = [
            ("Kova Outdoor", "google_ads", "Brand Search · DACH", "leads", -25, 20, 12000, "active", 0.55),
            ("Kova Outdoor", "meta", "Acquisition · EU", "sales", -20, 10, 15000, "active", 0.86),
            ("Kova Outdoor", "tiktok", "Native Prospecting", "awareness", -30, -3, 6000, "completed", 1.0),
            ("Kova Outdoor", "meta", "Winter Launch", "sales", 5, 40, 18000, "scheduled", 0),
            ("Lumira Skincare", "meta", "DPA All Products · IT", "sales", -18, 2, 9000, "active", 0.97),
            ("Lumira Skincare", "tiktok", "Spark Ads · IT/ES", "awareness", -12, 25, 7500, "active", 0.22),
            ("Lumira Skincare", "google_ads", "Shopping · Holiday", "sales", 0, 45, 20000, "draft", 0),
            ("Wevion Sports", "google_ads", "Shopping · EU", "sales", -28, 30, 24000, "active", 0.48),
            ("Wevion Sports", "tiktok", "Runner Pro · 18-34", "traffic", -14, 14, 8000, "paused", 0.40),
            ("Wevion Sports", "youtube", "Marathon Season", "awareness", -40, -10, 11000, "completed", 1.0),
            ("Nova Travel", "google_ads", "Performance Max · EU", "leads", -22, 38, 16000, "active", 0.30),
            ("Nova Travel", "linkedin", "Corporate Retreats", "leads", -10, 20, 5000, "active", 0.35),
            ("Nova Travel", "pinterest", "Summer Collection", "traffic", 10, 60, 6000, "scheduled", 0),
        ]
        for cl, chan, name, obj, so, eo, budget, status, burn in plan:
            start, end = today + timedelta(days=so), today + timedelta(days=eo)
            cid = conn.execute(
                """INSERT INTO campaigns (client_id, channel_id, name, objective, status, start_date, end_date,
                                          budget_cents, created_by, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (clients[cl], ch[chan], name, obj, "draft", start.isoformat(), end.isoformat(),
                 budget * 100, admin["id"], now, now)).lastrowid
            service.audit(conn, aid, admin, "campaign.created", "campaign", cid, {"name": name, "budget_cents": budget * 100})
            if burn:
                # history: spread burn * budget across elapsed days with noise, in real transactions
                total = int(budget * 100 * burn)
                days = [start + timedelta(days=i) for i in range((min(today, end) - start).days + 1)]
                weights = [rng.uniform(0.6, 1.4) for _ in days]
                conn.execute("UPDATE campaigns SET status = 'active' WHERE id = ?", (cid,))
                scale = total / sum(weights)
                spent_so_far = 0
                for i, (day, w) in enumerate(zip(days, weights)):
                    cents = int(w * scale) if i < len(days) - 1 else total - spent_so_far
                    cents = max(0, min(cents, total - spent_so_far))
                    if cents == 0:
                        continue
                    cpm = rng.uniform(5, 16)
                    imp = int(cents / 100 / cpm * 1000)
                    clk = int(imp * rng.uniform(0.009, 0.038))
                    conv = int(clk * rng.uniform(0.02, 0.1))
                    service.record_metrics(conn, aid, cid, None, metric_date=day.isoformat(), spend_cents=cents,
                                           impressions=imp, clicks=clk, conversions=conv, today=today)
                    spent_so_far += cents
            # land on the target status (bypassing the state machine only for seeding history)
            conn.execute("UPDATE campaigns SET status = ? WHERE id = ?", (status, cid))
        conn.execute("DELETE FROM audit_logs WHERE 0")  # no-op; audit_logs is append-only by trigger

    with db.transaction(conn):
        scheduler.ensure_system_tasks(conn, app.config["SIMULATE"], app.config["SIM_INTERVAL"])
        reports.create_report(conn, aid, admin, None, d(-30), d(0))
    conn.close()


if __name__ == "__main__":
    path = os.environ.get("CF_DATABASE", os.path.join(os.getcwd(), "campaignflow.db"))
    if "--reset" in sys.argv:
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(path + suffix):
                os.remove(path + suffix)
    elif os.path.exists(path) and os.path.getsize(path) > 0:
        import sqlite3
        if sqlite3.connect(path).execute("SELECT name FROM sqlite_master WHERE name='agencies'").fetchone():
            n = sqlite3.connect(path).execute("SELECT COUNT(*) FROM agencies").fetchone()[0]
            if n:
                print("Database already has data. Use --reset to recreate.")
                sys.exit(0)
    app = create_app({"RUN_WORKER": False})
    seed(app)
    print(f"Seeded {path}. Log in as admin@northwind.test / {PASSWORD}")
