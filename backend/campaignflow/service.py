"""Business rules for campaigns, budgets and spend.

Every mutating function here MUST be called inside `db.transaction(conn)`
(BEGIN IMMEDIATE). That makes each read-check-write sequence atomic with
respect to other writers, which is what prevents double-spend races.
"""
from __future__ import annotations

import json
from datetime import date

from .util import ApiError, iso, today_utc, utcnow, utcnow_dt

TRANSITIONS = {
    "draft": {"scheduled", "cancelled"},
    "scheduled": {"active", "draft", "cancelled"},
    "active": {"paused", "completed", "cancelled"},
    "paused": {"active", "completed", "cancelled"},
    "completed": set(),
    "cancelled": set(),
}
EDITABLE_DATES = {"draft", "scheduled"}
TERMINAL = {"completed", "cancelled"}


# ------------------------------------------------------------------ audit
def audit(conn, agency_id, actor, action, entity_type, entity_id=None, detail=None):
    conn.execute(
        """INSERT INTO audit_logs (agency_id, user_id, actor_label, action, entity_type,
                                   entity_id, detail, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            agency_id,
            actor["id"] if actor else None,
            actor["email"] if actor else "system",
            action,
            entity_type,
            entity_id,
            json.dumps(detail, separators=(",", ":")) if detail else None,
            utcnow(),
        ),
    )


# ------------------------------------------------------------------ reads
_CAMPAIGN_SELECT = """
SELECT ca.*, c.name AS client_name, c.agency_id,
       ch.code AS channel_code, ch.name AS channel_name,
       COALESCE(m.imp, 0)  AS impressions,
       COALESCE(m.clk, 0)  AS clicks,
       COALESCE(m.conv, 0) AS conversions
  FROM campaigns ca
  JOIN clients  c  ON c.id  = ca.client_id
  JOIN channels ch ON ch.id = ca.channel_id
  LEFT JOIN (SELECT campaign_id, SUM(impressions) AS imp, SUM(clicks) AS clk,
                    SUM(conversions) AS conv
               FROM daily_metrics {mfilter} GROUP BY campaign_id) m ON m.campaign_id = ca.id
"""


def campaign_select(single: bool = False) -> str:
    return _CAMPAIGN_SELECT.format(mfilter="WHERE campaign_id = ?" if single else "")


def fetch_campaign(conn, campaign_id, agency_id, client_scope=None):
    sql = campaign_select(single=True) + " WHERE ca.id = ? AND c.agency_id = ?"
    params = [campaign_id, campaign_id, agency_id]
    if client_scope is not None:
        sql += " AND c.id = ?"
        params.append(client_scope)
    row = conn.execute(sql, params).fetchone()
    if row is None:
        raise ApiError(404, "not_found", "Campaign not found")
    return row


def campaign_dict(r, today: date | None = None) -> dict:
    today = today or today_utc()
    d = dict(r)
    d.pop("agency_id", None)
    budget, spent = d["budget_cents"], d["spent_cents"]
    d["remaining_cents"] = budget - spent
    d["budget_used_pct"] = round(spent * 100 / budget, 1) if budget else 0.0
    start, end = date.fromisoformat(d["start_date"]), date.fromisoformat(d["end_date"])
    total_days = (end - start).days + 1
    elapsed = min(max((today - start).days + 1, 0), total_days)
    d["time_elapsed_pct"] = round(elapsed * 100 / total_days, 1)
    imp, clk, conv = d["impressions"], d["clicks"], d["conversions"]
    d["ctr"] = round(clk * 100 / imp, 2) if imp else None
    d["cpa_cents"] = round(spent / conv) if conv else None
    d["allowed_transitions"] = sorted(TRANSITIONS[d["status"]])

    alerts = []
    if d["status"] == "active":
        if d["budget_used_pct"] >= 80:
            alerts.append("budget_80")
        if 0 <= (end - today).days <= 3:
            alerts.append("ending_soon")
        if d["time_elapsed_pct"] >= 20:
            gap = d["budget_used_pct"] - d["time_elapsed_pct"]
            if gap >= 25:
                alerts.append("overpacing")
            elif gap <= -25:
                alerts.append("underpacing")
    d["alerts"] = alerts
    return d


# ------------------------------------------------------------------ writes
def create_campaign(conn, agency_id, actor, *, client_id, channel_id, name, objective,
                    start_date, end_date, budget_cents, schedule, today: date):
    client = conn.execute(
        "SELECT id, status FROM clients WHERE id = ? AND agency_id = ?", (client_id, agency_id)
    ).fetchone()
    if client is None:
        raise ApiError(422, "validation_error", "client_id: unknown client", {"client_id": "unknown client"})
    if client["status"] != "active":
        raise ApiError(409, "client_archived", "Cannot create campaigns for an archived client")
    if conn.execute("SELECT 1 FROM channels WHERE id = ?", (channel_id,)).fetchone() is None:
        raise ApiError(422, "validation_error", "channel_id: unknown channel", {"channel_id": "unknown channel"})
    if end_date < start_date:
        raise ApiError(422, "validation_error", "end_date: must be on or after start_date",
                       {"end_date": "must be on or after start_date"})
    if budget_cents <= 0:
        raise ApiError(422, "validation_error", "budget_cents: must be greater than 0",
                       {"budget_cents": "must be greater than 0"})
    now = utcnow()
    cur = conn.execute(
        """INSERT INTO campaigns (client_id, channel_id, name, objective, status, start_date,
                                  end_date, budget_cents, created_by, created_at, updated_at)
           VALUES (?,?,?,?, 'draft', ?,?,?,?,?,?)""",
        (client_id, channel_id, name, objective, start_date, end_date, budget_cents,
         actor["id"], now, now),
    )
    cid = cur.lastrowid
    audit(conn, agency_id, actor, "campaign.created", "campaign", cid,
          {"name": name, "budget_cents": budget_cents})
    if schedule:
        row = fetch_campaign(conn, cid, agency_id)
        change_status(conn, row, "scheduled", actor, today)
    return cid


def update_campaign(conn, row, actor, fields: dict, expected_version=None):
    """Edit name / objective / dates. Optimistic locking via `version`."""
    if row["status"] in TERMINAL:
        raise ApiError(409, "campaign_locked", f"A {row['status']} campaign can no longer be edited")
    if expected_version is not None and row["version"] != expected_version:
        raise ApiError(409, "version_conflict",
                       "This campaign was changed by someone else. Reload and try again.",
                       {"current_version": row["version"]})
    if ("start_date" in fields or "end_date" in fields) and row["status"] not in EDITABLE_DATES:
        raise ApiError(409, "campaign_locked", "Dates can only be changed while draft or scheduled")
    new = {**dict(row), **fields}
    if new["end_date"] < new["start_date"]:
        raise ApiError(422, "validation_error", "end_date: must be on or after start_date",
                       {"end_date": "must be on or after start_date"})
    n = conn.execute(
        """UPDATE campaigns SET name=?, objective=?, start_date=?, end_date=?,
                  version = version + 1, updated_at = ?
            WHERE id = ? AND version = ?""",
        (new["name"], new["objective"], new["start_date"], new["end_date"], utcnow(),
         row["id"], row["version"]),
    ).rowcount
    if n != 1:
        raise ApiError(409, "version_conflict", "Concurrent modification, please retry")
    audit(conn, row["agency_id"], actor, "campaign.updated", "campaign", row["id"],
          {k: v for k, v in fields.items()})


def change_status(conn, row, new_status, actor, today: date, reason=None, expected_version=None):
    cur = row["status"]
    if expected_version is not None and row["version"] != expected_version:
        raise ApiError(409, "version_conflict",
                       "This campaign was changed by someone else. Reload and try again.",
                       {"current_version": row["version"]})
    if new_status not in TRANSITIONS[cur]:
        raise ApiError(409, "invalid_transition", f"Cannot move a {cur} campaign to {new_status}",
                       {"allowed": sorted(TRANSITIONS[cur])})
    today_s = today.isoformat()
    if new_status == "scheduled" and row["end_date"] < today_s:
        raise ApiError(409, "campaign_ended", "End date is in the past; update the dates first")
    if new_status == "active":
        if row["start_date"] > today_s:
            raise ApiError(409, "not_started",
                           "Start date is in the future. Schedule the campaign and the "
                           "scheduler will activate it on that day.")
        if row["end_date"] < today_s:
            raise ApiError(409, "campaign_ended", "End date has passed")
        if row["spent_cents"] >= row["budget_cents"]:
            raise ApiError(409, "budget_exhausted", "Budget is fully spent; increase the budget first")
    n = conn.execute(
        "UPDATE campaigns SET status = ?, version = version + 1, updated_at = ? WHERE id = ? AND version = ?",
        (new_status, utcnow(), row["id"], row["version"]),
    ).rowcount
    if n != 1:
        raise ApiError(409, "version_conflict", "Concurrent modification, please retry")
    audit(conn, row["agency_id"], actor, "campaign.status_changed", "campaign", row["id"],
          {"from": cur, "to": new_status, **({"reason": reason} if reason else {})})


def adjust_budget(conn, row, actor, new_budget_cents: int, reason, expected_version=None):
    if row["status"] in TERMINAL:
        raise ApiError(409, "campaign_locked", f"Budget of a {row['status']} campaign cannot change")
    if expected_version is not None and row["version"] != expected_version:
        raise ApiError(409, "version_conflict", "Campaign changed, reload and retry",
                       {"current_version": row["version"]})
    if new_budget_cents <= 0:
        raise ApiError(422, "validation_error", "budget_cents: must be greater than 0",
                       {"budget_cents": "must be greater than 0"})
    if new_budget_cents < row["spent_cents"]:
        raise ApiError(409, "budget_below_spend",
                       "New budget is lower than the amount already spent",
                       {"spent_cents": row["spent_cents"]})
    if new_budget_cents == row["budget_cents"]:
        return
    n = conn.execute(
        "UPDATE campaigns SET budget_cents = ?, version = version + 1, updated_at = ? "
        "WHERE id = ? AND version = ?",
        (new_budget_cents, utcnow(), row["id"], row["version"]),
    ).rowcount
    if n != 1:
        raise ApiError(409, "version_conflict", "Concurrent modification, please retry")
    conn.execute(
        """INSERT INTO budget_adjustments (campaign_id, old_budget_cents, new_budget_cents,
                                           reason, changed_by, created_at) VALUES (?,?,?,?,?,?)""",
        (row["id"], row["budget_cents"], new_budget_cents, reason,
         actor["id"] if actor else None, utcnow()),
    )
    audit(conn, row["agency_id"], actor, "campaign.budget_changed", "campaign", row["id"],
          {"from": row["budget_cents"], "to": new_budget_cents, **({"reason": reason} if reason else {})})


def record_metrics(conn, agency_id, campaign_id, actor, *, metric_date, spend_cents,
                   impressions, clicks, conversions, today: date, client_scope=None):
    """Add delivery numbers for one day and charge the campaign budget.

    Runs in the caller's BEGIN IMMEDIATE transaction, so two concurrent
    requests can never both spend the last dollar. The UPDATE's WHERE clause
    and the table CHECK constraint are additional guards.
    """
    row = fetch_campaign(conn, campaign_id, agency_id, client_scope)
    if row["status"] != "active":
        raise ApiError(409, "campaign_not_active",
                       f"Metrics can only be recorded for active campaigns (this one is {row['status']})")
    if metric_date > today.isoformat():
        raise ApiError(422, "validation_error", "date: cannot be in the future", {"date": "cannot be in the future"})
    if not (row["start_date"] <= metric_date <= row["end_date"]):
        raise ApiError(422, "validation_error", "date: outside the campaign's flight dates",
                       {"date": "outside the campaign's flight dates"})
    remaining = row["budget_cents"] - row["spent_cents"]
    if spend_cents > remaining:
        raise ApiError(409, "budget_exceeded", "This spend would exceed the campaign budget",
                       {"remaining_cents": remaining, "attempted_cents": spend_cents})
    conn.execute(
        """INSERT INTO daily_metrics (campaign_id, metric_date, spend_cents, impressions, clicks, conversions)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT (campaign_id, metric_date) DO UPDATE SET
              spend_cents = spend_cents + excluded.spend_cents,
              impressions = impressions + excluded.impressions,
              clicks      = clicks      + excluded.clicks,
              conversions = conversions + excluded.conversions""",
        (campaign_id, metric_date, spend_cents, impressions, clicks, conversions),
    )
    n = conn.execute(
        """UPDATE campaigns SET spent_cents = spent_cents + ?, version = version + 1, updated_at = ?
            WHERE id = ? AND spent_cents + ? <= budget_cents""",
        (spend_cents, utcnow(), campaign_id, spend_cents),
    ).rowcount
    if n != 1:
        raise ApiError(409, "budget_exceeded", "This spend would exceed the campaign budget")
    new_spent = row["spent_cents"] + spend_cents
    if actor is not None:  # simulator ticks would flood the log; user entries are always audited
        audit(conn, agency_id, actor, "metrics.recorded", "campaign", campaign_id,
              {"date": metric_date, "spend_cents": spend_cents, "impressions": impressions,
               "clicks": clicks, "conversions": conversions})
    exhausted = new_spent >= row["budget_cents"]
    if exhausted:
        conn.execute(
            "UPDATE campaigns SET status = 'paused', version = version + 1, updated_at = ? WHERE id = ?",
            (utcnow(), campaign_id),
        )
        audit(conn, agency_id, None, "campaign.auto_paused", "campaign", campaign_id,
              {"reason": "budget_exhausted", "spent_cents": new_spent})
    return {"spent_cents": new_spent, "remaining_cents": row["budget_cents"] - new_spent,
            "auto_paused": exhausted}
