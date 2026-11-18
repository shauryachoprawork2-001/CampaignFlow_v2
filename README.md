# CampaignFlow

Advertising-operations platform for marketing agencies. Python/Flask + SQLite backend, dependency-free JS frontend.

## Setup in VS Code

1. **Open the `CampaignFlow` folder** in VS Code (File → Open Folder). Accept the recommended Python extension.
2. Open a terminal (Ctrl+`) and run:

```bash
cd backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python seed.py                     # demo agency, 13 campaigns, ~40 days of history
CF_SIMULATE=1 python run.py        # Windows PowerShell: $env:CF_SIMULATE=1; python run.py
```

3. Visit **http://127.0.0.1:8000**. Sign in with `admin@northwind.test` / `demo1234` (also `manager@`, `analyst@northwind.test`, or `kova@client.test`, a client who sees only their own data).

Or press **F5** and pick "CampaignFlow: run server" (select the `backend/.venv` interpreter first: Ctrl+Shift+P → *Python: Select Interpreter*). Run tests with `cd backend && python -m pytest` or the VS Code Testing panel.

`CF_SIMULATE=1` makes active campaigns "deliver" live so charts move; leave it off for a static demo. Reset demo data any time with `python seed.py --reset`.

## Folders

- `backend/` Flask API, SQLite schema, scheduler/worker, seed script, 70 tests.
- `frontend/` `index.html`, `app.css`, `app.js` (no build step). The backend serves this folder, so there is one server and no CORS setup. Edit a frontend file and refresh the browser.


A lightweight advertising-operations platform for marketing agencies: manage clients, campaigns, budgets and
performance reports from one app. Python + Flask + SQLite, with a dependency-free single-page UI.


Demo logins (password `demo1234`): `admin@northwind.test`, `manager@northwind.test`, `analyst@northwind.test`,
`kova@client.test` (client viewer, sees only Kova Outdoor). Or create your own agency on the sign-up tab.

## What's in it

| Feature | Where |
|---|---|
| Multi-user auth, sessions, login throttling | `security.py`, `/api/auth/*` |
| Role-based access (4 roles) | `security.PERMISSIONS`, `@require(...)` |
| Agency + client management (multi-tenant) | `/api/clients`, `/api/users` |
| Campaign creation, scheduling, status state machine | `service.py`, `/api/campaigns` |
| Budget + spend ledger with overspend protection | `service.record_metrics`, `adjust_budget` |
| Automated reporting (weekly + on demand + CSV) | `reports.py`, `scheduler.py` |
| Append-only audit log | `audit_logs` table + triggers, `/api/audit-logs` |
| Background tasks (DB-backed queue, retries, recurring) | `scheduler.py`, Scheduler page |

## Roles

| | admin | manager | analyst | client_viewer |
|---|:-:|:-:|:-:|:-:|
| Read campaigns, dashboard, reports | all | all | all | **own client only** |
| Create/edit clients and campaigns, change status | ✓ | ✓ | | |
| Adjust budgets | ✓ | ✓ | | |
| Record delivery metrics, generate reports | ✓ | ✓ | ✓ | |
| Run scheduler jobs | ✓ | ✓ | | |
| Manage users, read audit log | ✓ | | | |

Every query is scoped by agency; client viewers are additionally pinned to one client. Cross-tenant and
cross-client lookups return **404, not 403**, so the existence of other tenants' data never leaks.

## Design decisions (the interesting parts)

**Schema / normalization.** `agencies → clients → campaigns → daily_metrics`. A campaign has no `agency_id`;
its agency is derived through `clients` (3NF, one source of truth). Channels are a lookup table. Money is
**integer cents** everywhere. One deliberate denormalization: `campaigns.spent_cents`, maintained in the same
transaction as the `daily_metrics` row, so budget checks are O(1). A test asserts the two never diverge.

**Indexes** match the hot queries (`EXPLAIN QUERY PLAN` is asserted in tests): `(client_id, status)` for
campaign lists, `(status, start_date, end_date)` for the scheduler sweep, `(agency_id, id DESC)` for audit and
report pagination, `(status, run_at)` for the task queue, and a unique `(campaign_id, metric_date)` for upserts.

**Transactions & concurrency.** Writes run in `BEGIN IMMEDIATE`, so read-check-write sequences can't interleave.
Overspend is blocked three times over: the check inside the transaction, a guarded
`UPDATE … WHERE spent + ? <= budget`, and a `CHECK (spent_cents <= budget_cents)` constraint. A test fires 30
simultaneous $10 spends at a $100 budget and asserts exactly 10 succeed. Campaigns also carry a `version`
column for **optimistic locking**: stale edits get a `409 version_conflict` instead of silently overwriting.

**Status state machine.** `draft → scheduled → active ⇄ paused → completed`, `cancelled` from any live state;
terminal states are locked. Exhausting the budget auto-pauses the campaign (and it can't resume until the budget
is raised). A future-dated campaign can't be activated by hand; the scheduler starts it on its start date.

**Task queue.** Tasks live in a table (so they survive restarts and are visible in the UI). Claiming is one
atomic `UPDATE … WHERE id = (SELECT …) RETURNING *`, which is why two workers never run the same job (tested
with 4 threads × 40 tasks). Failures retry with exponential backoff; recurring jobs reschedule themselves;
jobs stuck in `running` after a crash are reclaimed; handlers are idempotent (e.g. a unique index makes the weekly
report safe to run twice), so at-least-once delivery is fine.

**Audit log.** Written in the *same transaction* as the change it describes (no change without a record, no
record without a change), and made append-only by `BEFORE UPDATE/DELETE` triggers. Rejected operations leave no entry.

**Security basics.** scrypt password hashing, constant-time login for unknown emails, hashed session tokens,
HttpOnly + SameSite cookies, JSON-only writes (a CSRF guard), parameterized SQL with whitelisted sort columns,
LIKE-wildcard escaping, CSV formula-injection neutralisation, a strict CSP, and output escaping in the UI.

## REST API (all under `/api`, JSON; errors are `{"error": {"code", "message", "details"}}`)

```
POST   /auth/register | /auth/login | /auth/logout        GET /auth/me
GET    /dashboard?days=30
GET    /campaigns?status=&client_id=&channel=&q=&sort=&dir=&page=&per_page=
POST   /campaigns                       GET|PATCH /campaigns/:id
POST   /campaigns/:id/status            POST /campaigns/:id/budget        POST /campaigns/:id/metrics
GET    /campaigns/export.csv
GET|POST /clients                       PATCH /clients/:id
GET|POST /users                         PATCH /users/:id                  (admin)
GET|POST /reports                       GET /reports/:id  /reports/:id/csv   (POST {"async": true} queues it)
GET    /audit-logs?entity_type=&action=&q=                                 (admin)
GET    /tasks                           POST /tasks/:id/run                (admin, manager)
```

## Layout

```
backend/campaignflow/   db.py (schema, transactions) · security.py (auth, RBAC) · service.py (campaign/budget rules)
                api.py (routes) · reports.py (SQL aggregation) · scheduler.py (queue + worker) · 
backend/tests/  RBAC & tenancy · lifecycle, budget, concurrency · scheduler, audit, reports
backend/seed.py, run.py   frontend/ index.html, app.css, app.js
```

## Known limits (and what I'd change for production)

- **SQLite** has one writer at a time, which is fine for one app server. For multiple servers, move to Postgres and
  swap `BEGIN IMMEDIATE` for `SELECT … FOR UPDATE` / `FOR UPDATE SKIP LOCKED` on the queue; the SQL is otherwise portable.
- Login throttling is in-memory (per process); use Redis or the DB when scaling out.
- Ad-platform delivery is **simulated** (`CF_SIMULATE=1`). Real integrations would be new task handlers that pull
  platform numbers into `record_metrics`.
- Reports are stored and downloadable but not emailed; add an `email_report` task handler to deliver them.
- Times are UTC, and "today" is the UTC date.
