"""Cost/time aggregates over runs and steps for the dashboard. Pure reads; all grouping happens in SQLite."""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from studio_core.store.db import Database

TERMINAL = ("succeeded", "failed", "cancelled", "interrupted")
MAX_DAYS = 366


def _num(doc: str, path: str) -> str:
    return f"(CASE WHEN json_type({doc}, '{path}') IN ('integer', 'real') THEN json_extract({doc}, '{path}') ELSE 0 END)"


def _cost(doc: str) -> str:
    return f"({_num(doc, '$.cost_usd')} + {_num(doc, '$.claude_cost_usd')})"


# Executors report Claude spend as cost_usd or claude_cost_usd; mapped steps keep one meta per item under items[];
# the runner carries spend of failed or reset attempts in prior_cost_usd. meta_cost() is the Python twin.
_STEP_COST = (f"(CASE WHEN json_valid(st.meta) THEN {_cost('st.meta')} + {_num('st.meta', '$.prior_cost_usd')} + "
              f"COALESCE((SELECT SUM({_cost('j.value')}) FROM json_each(st.meta, '$.items') AS j "
              "WHERE j.type = 'object'), 0) ELSE 0 END)")
_TIMED = "st.started_at IS NOT NULL AND st.ended_at >= st.started_at"
_STEP_S = f"(CASE WHEN {_TIMED} THEN st.ended_at - st.started_at ELSE 0 END)"


def _n(v: Any) -> float:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0


def meta_cost(meta: Any) -> float:
    if not isinstance(meta, dict):
        return 0.0
    items = meta.get("items")
    own = _n(meta.get("cost_usd")) + _n(meta.get("claude_cost_usd")) + _n(meta.get("prior_cost_usd"))
    return own + sum(_n(i.get("cost_usd")) + _n(i.get("claude_cost_usd"))
                     for i in (items if isinstance(items, list) else []) if isinstance(i, dict))


@dataclass(frozen=True)
class Filters:
    since: float | None = None
    until: float | None = None
    project_id: str | None = None
    recipe_id: str | None = None

    def where(self) -> tuple[str, list[Any]]:
        parts: list[str] = []
        params: list[Any] = []
        for sql, value in (("created_at >= ?", self.since), ("created_at < ?", self.until),
                           ("project_id = ?", self.project_id), ("recipe_id = ?", self.recipe_id)):
            if value is not None:
                parts.append(sql)
                params.append(value)
        return (" WHERE " + " AND ".join(parts)) if parts else "", params


def _per_run_cte(f: Filters) -> tuple[str, list[Any]]:
    """One row per run: run fields + step rollups. `active_s` is the union of non-gate step intervals: gate waits,
    idle days before a retry and overlap of parallel lanes do not count, and a reset step only counts its last try."""
    where, params = f.where()
    term = ", ".join(f"'{s}'" for s in TERMINAL)
    sql = f"""
    WITH r AS (SELECT id, recipe_id, status, created_at, ended_at FROM runs{where}),
    s AS (
        SELECT st.run_id,
               SUM({_STEP_COST}) AS cost,
               SUM(CASE WHEN st.lane != 'none' AND st.status IN ('succeeded', 'failed') THEN {_STEP_S} ELSE 0 END) AS work_s,
               SUM(CASE WHEN st.lane = 'gpu' AND st.status IN ('succeeded', 'failed') THEN {_STEP_S} ELSE 0 END) AS gpu_s,
               SUM(st.lane != 'none' AND st.status = 'cached') AS cached,
               SUM(st.lane != 'none' AND st.status IN ('succeeded', 'cached')) AS done
        FROM steps AS st JOIN r ON r.id = st.run_id
        GROUP BY st.run_id
    ),
    iv AS (
        SELECT st.run_id, st.started_at AS a, st.ended_at AS b
        FROM steps AS st JOIN r ON r.id = st.run_id
        WHERE st.lane != 'none' AND st.status IN ('succeeded', 'failed', 'cached') AND {_TIMED}
    ),
    iv_prev AS (
        SELECT run_id, a, b, MAX(b) OVER (PARTITION BY run_id ORDER BY a, b
                                          ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS reach
        FROM iv
    ),
    iv_isl AS (
        SELECT run_id, a, b, SUM(CASE WHEN reach IS NULL OR a > reach THEN 1 ELSE 0 END)
               OVER (PARTITION BY run_id ORDER BY a, b ROWS UNBOUNDED PRECEDING) AS isl
        FROM iv_prev
    ),
    act AS (
        SELECT run_id, SUM(hi - lo) AS active_s
        FROM (SELECT run_id, MIN(a) AS lo, MAX(b) AS hi FROM iv_isl GROUP BY run_id, isl)
        GROUP BY run_id
    ),
    pr AS (
        SELECT r.id, r.recipe_id, r.status, r.created_at,
               COALESCE(s.cost, 0) AS cost, s.cost > 0 AS has_cost,
               COALESCE(s.work_s, 0) AS work_s, COALESCE(s.gpu_s, 0) AS gpu_s,
               COALESCE(s.cached, 0) AS cached, COALESCE(s.done, 0) AS done,
               CASE WHEN r.status IN ({term}) AND r.ended_at >= r.created_at THEN r.ended_at - r.created_at END AS wall_s,
               CASE WHEN r.status IN ({term}) THEN act.active_s END AS active_s
        FROM r LEFT JOIN s ON s.run_id = r.id LEFT JOIN act ON act.run_id = r.id
    )"""
    return sql, params


def _rollup_sql(f: Filters, group: str) -> tuple[str, list[Any]]:
    cte, params = _per_run_cte(f)
    # Nearest-rank percentiles via window functions; durations come from succeeded runs only, since a run that
    # failed fast or was cancelled says nothing about how long the recipe takes.
    sql = f"""{cte},
    ranked AS (
        SELECT {group} AS g, active_s,
               ROW_NUMBER() OVER (PARTITION BY {group} ORDER BY active_s) AS rn,
               COUNT(*) OVER (PARTITION BY {group}) AS n
        FROM pr WHERE status = 'succeeded' AND active_s IS NOT NULL
    ),
    pct AS (
        SELECT g, MIN(CASE WHEN rn >= 0.5 * n THEN active_s END) AS p50,
               MIN(CASE WHEN rn >= 0.9 * n THEN active_s END) AS p90
        FROM ranked GROUP BY g
    )
    SELECT {group} AS g, COUNT(*) AS runs,
           SUM(status = 'succeeded') AS succeeded, SUM(status = 'failed') AS failed,
           SUM(status = 'cancelled') AS cancelled, SUM(status = 'interrupted') AS interrupted,
           SUM(status IN ('running', 'awaiting_human')) AS active,
           SUM(cost) AS cost, SUM(has_cost) AS runs_with_cost,
           AVG(CASE WHEN status = 'succeeded' THEN active_s END) AS dur_avg,
           AVG(CASE WHEN status = 'succeeded' THEN wall_s END) AS wall_avg,
           SUM(work_s) AS work_s, SUM(gpu_s) AS gpu_s,
           SUM(cached) AS cached, SUM(done) AS done, MAX(created_at) AS last_run_at,
           pct.p50 AS p50, pct.p90 AS p90
    FROM pr LEFT JOIN pct ON pct.g = {group}
    GROUP BY {group}"""
    return sql, params


def _rate(num: float, den: float) -> float | None:
    return round(num / den, 4) if den else None


def _round(v: float | None, nd: int = 2) -> float | None:
    return None if v is None else round(v, nd)


def _shape(row: Any) -> dict[str, Any]:
    runs = row["runs"] or 0
    failed = (row["failed"] or 0) + (row["interrupted"] or 0)
    cost = row["cost"] or 0.0
    return {
        "runs": runs, "succeeded": row["succeeded"] or 0, "failed": row["failed"] or 0,
        "cancelled": row["cancelled"] or 0, "interrupted": row["interrupted"] or 0, "active": row["active"] or 0,
        # Cancelling is the user's call, not a recipe fault, so cancelled runs stay out of the failure rate.
        "failure_rate": _rate(failed, (row["succeeded"] or 0) + failed),
        "cost_usd": round(cost, 4), "avg_cost_usd": round(cost / runs, 4) if runs else 0.0,
        "runs_with_cost": row["runs_with_cost"] or 0,
        "duration_s": {"avg": _round(row["dur_avg"]), "p50": _round(row["p50"]), "p90": _round(row["p90"])},
        "wall_avg_s": _round(row["wall_avg"]),
        "work_s": round(row["work_s"] or 0, 2), "gpu_s": round(row["gpu_s"] or 0, 2),
        "cache_hit_ratio": _rate(row["cached"] or 0, row["done"] or 0),
        "last_run_at": row["last_run_at"],
    }


async def recipe_stats(db: Database, f: Filters) -> dict[str, Any]:
    sql, params = _rollup_sql(f, "recipe_id")
    rows = await db.fetchall(sql + " ORDER BY cost DESC, runs DESC, recipe_id", params)
    recipes = [{"recipe_id": r["g"], **_shape(r)} for r in rows]
    tsql, tparams = _rollup_sql(f, "'*'")
    total = await db.fetchone(tsql, tparams)
    totals = _shape(total) if total is not None and total["runs"] else _shape({k: None for k in _EMPTY_KEYS})
    return {"since": f.since, "until": f.until, "project_id": f.project_id, "totals": totals, "recipes": recipes}


_EMPTY_KEYS = ("runs", "succeeded", "failed", "cancelled", "interrupted", "active", "cost", "runs_with_cost",
               "dur_avg", "wall_avg", "work_s", "gpu_s", "cached", "done", "last_run_at", "p50", "p90")


async def step_stats(db: Database, f: Filters, limit: int = 10) -> list[dict[str, Any]]:
    where, params = f.where()
    sql = f"""
    SELECT st.kind AS kind, SUM(st.status IN ('succeeded', 'failed', 'cached')) AS steps,
           SUM(st.status = 'failed') AS failed, SUM(st.status = 'cached') AS cached,
           SUM({_STEP_COST}) AS cost,
           SUM(CASE WHEN st.status IN ('succeeded', 'failed') THEN {_STEP_S} ELSE 0 END) AS total_s,
           AVG(CASE WHEN st.status = 'succeeded' AND {_TIMED} THEN st.ended_at - st.started_at END) AS avg_s
    FROM steps AS st JOIN (SELECT id FROM runs{where}) AS r ON r.id = st.run_id
    WHERE st.lane != 'none'
    GROUP BY st.kind
    HAVING steps > 0 OR cost > 0
    ORDER BY cost DESC, total_s DESC, st.kind
    LIMIT ?"""
    rows = await db.fetchall(sql, [*params, limit])
    return [{"kind": r["kind"], "steps": r["steps"] or 0, "failed": r["failed"] or 0, "cached": r["cached"] or 0,
             "cost_usd": round(r["cost"] or 0, 4), "total_s": round(r["total_s"] or 0, 2),
             "avg_s": _round(r["avg_s"])} for r in rows]


def local_offset_s(at: float | None = None) -> int:
    t = time.localtime(at if at is not None else time.time())
    return int(t.tm_gmtoff or 0)


async def daily_stats(db: Database, days: int, project_id: str | None = None, until: float | None = None,
                      tz_offset_s: int | None = None) -> dict[str, Any]:
    """`days` local calendar days ending with the one holding the last instant before `until` (default: today),
    zero-filled."""
    days = max(1, min(MAX_DAYS, days))
    off = local_offset_s(until) if tz_offset_s is None else tz_offset_s
    end = until - 0.001 if until is not None else time.time()
    last_day = datetime.fromtimestamp(end + off, tz=timezone.utc).date()
    first_day = last_day - timedelta(days=days - 1)
    since = datetime(first_day.year, first_day.month, first_day.day, tzinfo=timezone.utc).timestamp() - off
    upper = datetime(last_day.year, last_day.month, last_day.day, tzinfo=timezone.utc).timestamp() - off + 86400
    cte, params = _per_run_cte(Filters(since=since, until=upper if until is None else min(upper, until),
                                       project_id=project_id))
    sql = f"""{cte}
    SELECT date(created_at + ?, 'unixepoch') AS day, COUNT(*) AS runs,
           SUM(status = 'succeeded') AS succeeded, SUM(status IN ('failed', 'interrupted')) AS failed,
           SUM(cost) AS cost, SUM(work_s) AS work_s,
           AVG(CASE WHEN status = 'succeeded' THEN active_s END) AS dur_avg
    FROM pr GROUP BY day"""
    rows = {r["day"]: r for r in await db.fetchall(sql, [*params, off])}
    out: list[dict[str, Any]] = []
    for i in range(days):
        d: date = first_day + timedelta(days=i)
        r = rows.get(d.isoformat())
        out.append({"day": d.isoformat(), "runs": r["runs"] if r else 0,
                    "succeeded": (r["succeeded"] or 0) if r else 0, "failed": (r["failed"] or 0) if r else 0,
                    "cost_usd": round(r["cost"] or 0, 4) if r else 0.0,
                    "work_s": round(r["work_s"] or 0, 2) if r else 0.0,
                    "duration_avg_s": _round(r["dur_avg"]) if r else None})
    return {"days": out, "tz_offset_s": off, "since": since, "until": upper}


async def first_run_at(db: Database, project_id: str | None = None) -> float | None:
    if project_id is None:
        row = await db.fetchone("SELECT MIN(created_at) AS t FROM runs")
    else:
        row = await db.fetchone("SELECT MIN(created_at) AS t FROM runs WHERE project_id = ?", (project_id,))
    return row["t"] if row else None
