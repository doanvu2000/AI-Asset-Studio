"""Read-only cost/time dashboard aggregates. Times are epoch seconds; `since` inclusive, `until` exclusive."""
from __future__ import annotations

import math
import time
from dataclasses import replace

from fastapi import APIRouter, Depends, Query, Request
from starlette.responses import Response

from studio_core.api.auth import require_token
from studio_core.api.errors import ApiError, OrjsonResponse
from studio_core.store.db import Database
from studio_core.store.stats import MAX_DAYS, Filters, daily_stats, first_run_at, recipe_stats, step_stats

router = APIRouter(prefix="/api/stats", dependencies=[Depends(require_token)])

_TZ = Query(None, ge=-14 * 3600, le=14 * 3600, description="viewer's UTC offset in seconds; default: core's")


def _db(request: Request) -> Database:
    return request.app.state.repo.db


def _filters(since: float | None, until: float | None, project_id: str | None,
             recipe_id: str | None = None) -> Filters:
    if any(v is not None and not math.isfinite(v) for v in (since, until)):
        raise ApiError(400, "invalid", "since/until must be finite epoch seconds")
    if since is not None and until is not None and until <= since:
        raise ApiError(400, "invalid", "until must be after since")
    return Filters(since=since, until=until, project_id=project_id or None, recipe_id=recipe_id or None)


@router.get("/recipes")
async def recipes(request: Request, since: float | None = None, until: float | None = None,
                  project_id: str | None = None) -> Response:
    return OrjsonResponse(await recipe_stats(_db(request), _filters(since, until, project_id)))


@router.get("/steps")
async def steps(request: Request, since: float | None = None, until: float | None = None,
                project_id: str | None = None, recipe_id: str | None = None,
                limit: int = Query(10, ge=1, le=100)) -> Response:
    f = _filters(since, until, project_id, recipe_id)
    return OrjsonResponse({"steps": await step_stats(_db(request), f, limit)})


@router.get("/daily")
async def daily(request: Request, days: int = Query(30, ge=1, le=MAX_DAYS), project_id: str | None = None,
                tz_offset_s: int | None = _TZ) -> Response:
    return OrjsonResponse(await daily_stats(_db(request), days, project_id or None, tz_offset_s=tz_offset_s))


@router.get("/overview")
async def overview(request: Request, since: float | None = None, until: float | None = None,
                   project_id: str | None = None, days: int | None = Query(None, ge=1, le=MAX_DAYS),
                   tz_offset_s: int | None = _TZ, steps_limit: int = Query(8, ge=1, le=100)) -> Response:
    """Everything the dashboard section needs in one round trip; the chart spans the period (7 days to a year).
    A given `since` is moved back to the local midnight the chart starts at, so the totals equal the sum of bars."""
    db = _db(request)
    f = _filters(since, until, project_id)
    if days is None:
        end = until if until is not None else time.time()
        start = since if since is not None else await first_run_at(db, f.project_id)
        days = 30 if start is None else max(7, min(MAX_DAYS, math.ceil((end - start) / 86400)))
    daily = await daily_stats(db, days, f.project_id, until=until, tz_offset_s=tz_offset_s)
    if f.since is not None:
        f = replace(f, since=daily["since"])
    body = await recipe_stats(db, f)
    body["steps"] = await step_stats(db, f, steps_limit)
    body["daily"] = daily
    return OrjsonResponse(body)
