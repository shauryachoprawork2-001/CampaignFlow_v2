"""SQLite connection handling, transactions and the normalized schema.

Design notes
------------
* Money is stored as integer cents (never floats).
* Tenancy: agencies -> clients -> campaigns. Campaigns carry no agency_id;
  the owning agency is derived through the clients join (3NF, one source of truth).
* Daily performance lives in `daily_metrics` (one row per campaign per day);
  `campaigns.spent_cents` is a deliberate, transactionally-maintained
  denormalization so budget checks are O(1). CHECK(spent <= budget) is the
  last line of defence against overspend even if application code is buggy.
* `audit_logs` is append-only, enforced by triggers.
* `tasks` is the job queue used by the background scheduler.
"""
import sqlite3
import contextlib

from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS agencies (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    currency    TEXT NOT NULL DEFAULT 'USD',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clients (
    id            INTEGER PRIMARY KEY,
    agency_id     INTEGER NOT NULL REFERENCES agencies(id),
    name          TEXT NOT NULL,
    industry      TEXT,
    contact_name  TEXT,
    contact_email TEXT,
    status        TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    UNIQUE (agency_id, name)
);

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    agency_id     INTEGER NOT NULL REFERENCES agencies(id),
    client_id     INTEGER REFERENCES clients(id),
    email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name          TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('admin','manager','analyst','client_viewer')),
    is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    created_at    TEXT NOT NULL,
    last_login_at TEXT,
    CHECK (role <> 'client_viewer' OR client_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_users_agency ON users(agency_id);

CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY,
    token_hash  TEXT NOT NULL UNIQUE,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at);

CREATE TABLE IF NOT EXISTS channels (
    id    INTEGER PRIMARY KEY,
    code  TEXT NOT NULL UNIQUE,
    name  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS campaigns (
    id            INTEGER PRIMARY KEY,
    client_id     INTEGER NOT NULL REFERENCES clients(id),
    channel_id    INTEGER NOT NULL REFERENCES channels(id),
    name          TEXT NOT NULL,
    objective     TEXT NOT NULL DEFAULT 'traffic'
                  CHECK (objective IN ('awareness','traffic','leads','sales')),
    status        TEXT NOT NULL DEFAULT 'draft'
                  CHECK (status IN ('draft','scheduled','active','paused','completed','cancelled')),
    start_date    TEXT NOT NULL,
    end_date      TEXT NOT NULL,
    budget_cents  INTEGER NOT NULL CHECK (budget_cents >= 0),
    spent_cents   INTEGER NOT NULL DEFAULT 0 CHECK (spent_cents >= 0),
    version       INTEGER NOT NULL DEFAULT 1,
    created_by    INTEGER REFERENCES users(id),
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    CHECK (end_date >= start_date),
    CHECK (spent_cents <= budget_cents)
);
CREATE INDEX IF NOT EXISTS idx_campaigns_client_status ON campaigns(client_id, status);
CREATE INDEX IF NOT EXISTS idx_campaigns_status_dates  ON campaigns(status, start_date, end_date);
CREATE INDEX IF NOT EXISTS idx_campaigns_channel       ON campaigns(channel_id);

CREATE TABLE IF NOT EXISTS daily_metrics (
    id           INTEGER PRIMARY KEY,
    campaign_id  INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    metric_date  TEXT NOT NULL,
    spend_cents  INTEGER NOT NULL DEFAULT 0 CHECK (spend_cents >= 0),
    impressions  INTEGER NOT NULL DEFAULT 0 CHECK (impressions >= 0),
    clicks       INTEGER NOT NULL DEFAULT 0 CHECK (clicks >= 0),
    conversions  INTEGER NOT NULL DEFAULT 0 CHECK (conversions >= 0),
    UNIQUE (campaign_id, metric_date)
);
CREATE INDEX IF NOT EXISTS idx_metrics_date ON daily_metrics(metric_date, campaign_id);

CREATE TABLE IF NOT EXISTS budget_adjustments (
    id               INTEGER PRIMARY KEY,
    campaign_id      INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    old_budget_cents INTEGER NOT NULL,
    new_budget_cents INTEGER NOT NULL,
    reason           TEXT,
    changed_by       INTEGER REFERENCES users(id),
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_budget_adj_campaign ON budget_adjustments(campaign_id);

CREATE TABLE IF NOT EXISTS reports (
    id           INTEGER PRIMARY KEY,
    agency_id    INTEGER NOT NULL REFERENCES agencies(id),
    client_id    INTEGER REFERENCES clients(id),
    title        TEXT NOT NULL,
    kind         TEXT NOT NULL CHECK (kind IN ('on_demand','scheduled')),
    period_start TEXT NOT NULL,
    period_end   TEXT NOT NULL,
    payload      TEXT NOT NULL,
    generated_by INTEGER REFERENCES users(id),
    created_at   TEXT NOT NULL,
    CHECK (period_end >= period_start)
);
CREATE INDEX IF NOT EXISTS idx_reports_agency_created ON reports(agency_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_reports_client ON reports(client_id);
-- makes the scheduled weekly job idempotent (safe to retry / run twice)
CREATE UNIQUE INDEX IF NOT EXISTS uq_reports_scheduled
    ON reports(agency_id, COALESCE(client_id, 0), period_start, period_end)
    WHERE kind = 'scheduled';

CREATE TABLE IF NOT EXISTS audit_logs (
    id           INTEGER PRIMARY KEY,
    agency_id    INTEGER REFERENCES agencies(id),
    user_id      INTEGER REFERENCES users(id),
    actor_label  TEXT NOT NULL,
    action       TEXT NOT NULL,
    entity_type  TEXT NOT NULL,
    entity_id    INTEGER,
    detail       TEXT,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_agency ON audit_logs(agency_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_logs(entity_type, entity_id);

CREATE TRIGGER IF NOT EXISTS audit_logs_no_update BEFORE UPDATE ON audit_logs
BEGIN SELECT RAISE(ABORT, 'audit_logs is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_logs_no_delete BEFORE DELETE ON audit_logs
BEGIN SELECT RAISE(ABORT, 'audit_logs is append-only'); END;

CREATE TABLE IF NOT EXISTS tasks (
    id               INTEGER PRIMARY KEY,
    agency_id        INTEGER REFERENCES agencies(id),
    kind             TEXT NOT NULL,
    payload          TEXT NOT NULL DEFAULT '{}',
    status           TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending','running','done','failed')),
    run_at           TEXT NOT NULL,
    attempts         INTEGER NOT NULL DEFAULT 0,
    max_attempts     INTEGER NOT NULL DEFAULT 3,
    interval_seconds INTEGER,
    dedupe_key       TEXT UNIQUE,
    last_error       TEXT,
    locked_by        TEXT,
    locked_at        TEXT,
    created_at       TEXT NOT NULL,
    finished_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(status, run_at);

INSERT OR IGNORE INTO channels (code, name) VALUES
  ('google_ads','Google Ads'), ('meta','Meta'), ('tiktok','TikTok'),
  ('linkedin','LinkedIn'), ('x','X (Twitter)'), ('pinterest','Pinterest'),
  ('email','Email'), ('youtube','YouTube');
"""


def connect(path: str) -> sqlite3.Connection:
    # isolation_level=None -> autocommit; we open transactions explicitly.
    conn = sqlite3.connect(path, timeout=10, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init_db(path: str) -> None:
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
    finally:
        conn.close()


@contextlib.contextmanager
def transaction(conn: sqlite3.Connection):
    """BEGIN IMMEDIATE takes the write lock up-front, so read-check-write
    sequences inside the block cannot interleave with another writer."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()
