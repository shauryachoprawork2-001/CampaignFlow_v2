"""Report building (pure SQL aggregation) shared by the dashboard, the API and the scheduler."""
from __future__ import annotations

import json
from datetime import date, timedelta

from .service import audit
from .util import ApiError, utcnow


def _ratio(num, den, digits=2, scale=1):
    return round(num * scale / den, digits) if den else None


def _scope_sql(client_id):
    return (" AND c.id = ?", [client_id]) if client_id is not None else ("", [])


def summarize(rows_totals: dict) -> dict:
    spend, imp, clk, conv = (rows_totals[k] for k in ("spend_cents", "impressions", "clicks", "conversions"))
    return {
        **rows_totals,
        "ctr": _ratio(clk, imp, 2, 100),        # percent
        "cpc_cents": _ratio(spend, clk, 0),
        "cpa_cents": _ratio(spend, conv, 0),
        "cpm_cents": _ratio(spend, imp, 0, 1000),
    }


def period_totals(conn, agency_id, client_id, start, end) -> dict:
    extra, ep = _scope_sql(client_id)
    r = conn.execute(
        f"""SELECT COALESCE(SUM(dm.spend_cents),0) AS spend_cents,
                   COALESCE(SUM(dm.impressions),0) AS impressions,
                   COALESCE(SUM(dm.clicks),0)      AS clicks,
                   COALESCE(SUM(dm.conversions),0) AS conversions
              FROM daily_metrics dm
              JOIN campaigns ca ON ca.id = dm.campaign_id
              JOIN clients c    ON c.id  = ca.client_id
             WHERE c.agency_id = ? AND dm.metric_date BETWEEN ? AND ? {extra}""",
        [agency_id, start, end, *ep],
    ).fetchone()
    return summarize(dict(r))


def daily_series(conn, agency_id, client_id, start, end) -> list[dict]:
    extra, ep = _scope_sql(client_id)
    found = {
        r["metric_date"]: dict(r)
        for r in conn.execute(
            f"""SELECT dm.metric_date, SUM(dm.spend_cents) AS spend_cents,
                       SUM(dm.impressions) AS impressions, SUM(dm.clicks) AS clicks,
                       SUM(dm.conversions) AS conversions
                  FROM daily_metrics dm
                  JOIN campaigns ca ON ca.id = dm.campaign_id
                  JOIN clients c    ON c.id  = ca.client_id
                 WHERE c.agency_id = ? AND dm.metric_date BETWEEN ? AND ? {extra}
                 GROUP BY dm.metric_date""",
            [agency_id, start, end, *ep],
        )
    }
    out, d, last = [], date.fromisoformat(start), date.fromisoformat(end)
    while d <= last:
        key = d.isoformat()
        row = found.get(key, {"metric_date": key, "spend_cents": 0, "impressions": 0, "clicks": 0, "conversions": 0})
        out.append({"date": key, **{k: row[k] for k in ("spend_cents", "impressions", "clicks", "conversions")}})
        d += timedelta(days=1)
    return out


def channel_breakdown(conn, agency_id, client_id, start, end) -> list[dict]:
    extra, ep = _scope_sql(client_id)
    rows = conn.execute(
        f"""SELECT ch.code, ch.name, COUNT(DISTINCT ca.id) AS campaigns,
                   COALESCE(SUM(dm.spend_cents),0) AS spend_cents,
                   COALESCE(SUM(dm.impressions),0) AS impressions,
                   COALESCE(SUM(dm.clicks),0)      AS clicks,
                   COALESCE(SUM(dm.conversions),0) AS conversions
              FROM daily_metrics dm
              JOIN campaigns ca ON ca.id = dm.campaign_id
              JOIN clients c    ON c.id  = ca.client_id
              JOIN channels ch  ON ch.id = ca.channel_id
             WHERE c.agency_id = ? AND dm.metric_date BETWEEN ? AND ? {extra}
             GROUP BY ch.id ORDER BY spend_cents DESC""",
        [agency_id, start, end, *ep],
    ).fetchall()
    return [summarize(dict(r)) | {"code": r["code"], "name": r["name"], "campaigns": r["campaigns"]} for r in rows]


def campaign_rows(conn, agency_id, client_id, start, end) -> list[dict]:
    extra, ep = _scope_sql(client_id)
    rows = conn.execute(
        f"""SELECT ca.id AS campaign_id, ca.name, c.name AS client_name, ch.name AS channel,
                   ca.status, ca.budget_cents,
                   COALESCE(SUM(dm.spend_cents),0) AS spend_cents,
                   COALESCE(SUM(dm.impressions),0) AS impressions,
                   COALESCE(SUM(dm.clicks),0)      AS clicks,
                   COALESCE(SUM(dm.conversions),0) AS conversions
              FROM campaigns ca
              JOIN clients c   ON c.id  = ca.client_id
              JOIN channels ch ON ch.id = ca.channel_id
              LEFT JOIN daily_metrics dm ON dm.campaign_id = ca.id AND dm.metric_date BETWEEN ? AND ?
             WHERE c.agency_id = ? {extra}
             GROUP BY ca.id
            HAVING spend_cents > 0 OR ca.status IN ('active','paused')
             ORDER BY spend_cents DESC, ca.name""",
        [start, end, agency_id, *ep],
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d.update(
            ctr=_ratio(d["clicks"], d["impressions"], 2, 100),
            cpa_cents=_ratio(d["spend_cents"], d["conversions"], 0),
        )
        out.append(d)
    return out


def build_report(conn, agency_id, client_id, start, end) -> dict:
    return {
        "period": {"start": start, "end": end},
        "summary": period_totals(conn, agency_id, client_id, start, end),
        "channels": channel_breakdown(conn, agency_id, client_id, start, end),
        "campaigns": campaign_rows(conn, agency_id, client_id, start, end),
        "daily": daily_series(conn, agency_id, client_id, start, end),
        "generated_at": utcnow(),
    }


def create_report(conn, agency_id, actor, client_id, start, end, kind="on_demand") -> int | None:
    """Insert a report row. Returns None if an identical *scheduled* report already
    exists (idempotent retries). Must run inside a transaction."""
    client_name = "All clients"
    if client_id is not None:
        c = conn.execute("SELECT name FROM clients WHERE id = ? AND agency_id = ?", (client_id, agency_id)).fetchone()
        if c is None:
            raise ApiError(422, "validation_error", "client_id: unknown client", {"client_id": "unknown client"})
        client_name = c["name"]
    payload = build_report(conn, agency_id, client_id, start, end)
    cur = conn.execute(
        """INSERT OR IGNORE INTO reports (agency_id, client_id, title, kind, period_start, period_end,
                                          payload, generated_by, created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (agency_id, client_id, f"{client_name} · {start} → {end}", kind, start, end,
         json.dumps(payload, separators=(",", ":")), actor["id"] if actor else None, utcnow()),
    )
    if cur.rowcount == 0:
        return None
    audit(conn, agency_id, actor, "report.generated", "report", cur.lastrowid,
          {"client_id": client_id, "start": start, "end": end, "kind": kind})
    return cur.lastrowid
