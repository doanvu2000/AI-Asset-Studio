"""What external agents (Claude Code / Codex via MCP) are doing, for the app's live "agent" panel, plus one
system snapshot an agent can read before starting GPU work."""
from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from starlette.responses import Response

from studio_core import __version__
from studio_core.api.auth import require_token
from studio_core.api.errors import OrjsonResponse
from studio_core.contracts import Resource, StepStatus

router = APIRouter(prefix="/api/agent", dependencies=[Depends(require_token)])

RING = 300
# A session that has not called a tool for this long is shown as idle, not as "controlling" the studio.
ACTIVE_S = 600.0
HEALTH_TIMEOUT_S = 3.0

Phase = Literal["connect", "disconnect", "start", "end", "error", "wait"]


class Activity(BaseModel):
    session: str = Field(min_length=1, max_length=64)
    client: str = Field("agent", max_length=80)
    client_version: str = Field("", max_length=40)
    phase: Phase
    tool: str | None = Field(None, max_length=80)
    summary: str = Field("", max_length=400)
    detail: str = Field("", max_length=400)
    run_id: str | None = Field(None, max_length=64)
    step_id: str | None = Field(None, max_length=64)
    seconds: float | None = None


class AgentLog:
    def __init__(self) -> None:
        self.events: deque[dict[str, Any]] = deque(maxlen=RING)
        self.sessions: dict[str, dict[str, Any]] = {}

    def add(self, a: Activity) -> dict[str, Any]:
        now = time.time()
        ev = {**a.model_dump(), "ts": now}
        self.events.append(ev)
        s = self.sessions.setdefault(a.session, {"session": a.session, "connected_at": now, "calls": 0})
        s.update(client=a.client, client_version=a.client_version, last_seen=now,
                 connected=a.phase != "disconnect")
        if a.phase == "start":
            s["calls"] += 1
            s["current"] = {"tool": a.tool, "summary": a.summary, "run_id": a.run_id, "since": now}
        elif a.phase == "wait" and s.get("current"):
            s["current"] = {**s["current"], "detail": a.detail, "run_id": a.run_id or s["current"].get("run_id")}
        elif a.phase in ("end", "error", "disconnect"):
            s.pop("current", None)
        if a.run_id:
            s["last_run_id"] = a.run_id
        return ev

    def snapshot(self, limit: int) -> dict[str, Any]:
        now = time.time()
        sessions = [{**s, "active": s.get("connected", False) and now - s["last_seen"] < ACTIVE_S}
                    for s in self.sessions.values()]
        sessions.sort(key=lambda s: s["last_seen"], reverse=True)
        return {"sessions": sessions, "events": list(self.events)[-limit:] if limit > 0 else []}


def agent_log(request: Request) -> AgentLog:
    state = request.app.state
    if getattr(state, "agent_log", None) is None:
        state.agent_log = AgentLog()
    return state.agent_log


@router.post("/activity")
async def post_activity(request: Request, body: Activity) -> Response:
    ev = agent_log(request).add(body)
    request.app.state.bus.publish("agent.activity", ev, run_id=body.run_id, step_id=body.step_id)
    return OrjsonResponse({"ok": True})


@router.get("/activity")
async def get_activity(request: Request, limit: int = Query(100, ge=1, le=RING)) -> Response:
    return OrjsonResponse(agent_log(request).snapshot(limit))


async def _health(r: Resource) -> dict[str, Any]:
    try:
        return (await asyncio.wait_for(r.health(), HEALTH_TIMEOUT_S)).model_dump(mode="json")
    except Exception as e:
        return {"name": getattr(r, "name", "?"), "status": "error", "detail": {"message": f"{type(e).__name__}: {e}"}}


def _setup_plan(settings: Any) -> dict[str, Any] | None:
    from studio_core.bootstrap.plan import describe

    try:
        return describe(settings)
    except Exception:
        return None


@router.get("/status")
async def system_status(request: Request) -> Response:
    """Core, ComfyUI/Blender health, what holds each lane (GPU/Blender), open gates, missing model packs,
    GPU, and which engines (Unity/Godot) can preview."""
    from studio_core.export.preview import availability

    state = request.app.state
    resources: dict[str, Resource] = getattr(state, "resources", {})
    repo = state.repo
    health, plan, engines = await asyncio.gather(
        asyncio.gather(*(_health(r) for r in resources.values())),
        asyncio.to_thread(_setup_plan, state.settings),
        asyncio.to_thread(availability, state.settings))
    active = await repo.list_steps_by_status([StepStatus.RUNNING, StepStatus.WAITING_RESOURCE])
    lanes: dict[str, list[dict[str, Any]]] = {}
    for s in active:
        lanes.setdefault(str(getattr(s.lane, "value", s.lane)), []).append(
            {"run_id": s.run_id, "step_id": s.id, "key": s.key, "kind": s.kind,
             "status": str(getattr(s.status, "value", s.status)), "waiting_for": s.meta.get("waiting_for")})
    gates = await repo.list_steps_by_status([StepStatus.AWAITING_HUMAN])
    out: dict[str, Any] = {
        "core": {"version": __version__, "data_dir": str(state.settings.data_dir)},
        "resources": list(health),
        "lanes": lanes,
        "open_gates": [{"run_id": g.run_id, "step_id": g.id, "key": g.key} for g in gates],
        "engines": engines,
        "agents": agent_log(request).snapshot(0)["sessions"],
    }
    if plan:
        out["gpu"] = plan.get("gpu")
        out["missing_packs"] = [{"id": p["id"], "label": p["label"], "required": p["required"],
                                 "missing_size": p["missing_size"]} for p in plan.get("packs", []) if not p["installed"]]
        out["components"] = {k: {"installed": v.get("installed")} for k, v in (plan.get("components") or {}).items()}
    return OrjsonResponse(out)
