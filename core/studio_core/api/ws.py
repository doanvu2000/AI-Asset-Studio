from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

import orjson
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from studio_core.api.auth import token_matches
from studio_core.contracts import EventBus

router = APIRouter()
log = logging.getLogger(__name__)

PING_INTERVAL_S = 20.0


async def _send(ws: WebSocket, lock: asyncio.Lock, payload: dict[str, Any]) -> None:
    async with lock:
        await ws.send_text(orjson.dumps(payload).decode())


class _Subs:
    """Per-connection run filter for step.log; `runs is None` means the legacy client that wants everything."""

    def __init__(self) -> None:
        self.runs: set[str] | None = None

    def wants(self, ev: Any) -> bool:
        return self.runs is None or ev.type != "step.log" or ev.run_id in self.runs


async def _stream(ws: WebSocket, lock: asyncio.Lock, bus: EventBus, after_seq: int, subs: _Subs) -> None:
    # Replayed events stay unfiltered: the client's resume only accepts a gap-free sequence.
    live_from = getattr(bus, "last_seq", 0)
    async for ev in bus.subscribe(after_seq):
        if ev.seq > live_from and not subs.wants(ev):
            continue
        await _send(ws, lock, ev.model_dump(mode="json"))


async def _ping(ws: WebSocket, lock: asyncio.Lock, bus: EventBus) -> None:
    while True:
        await asyncio.sleep(PING_INTERVAL_S)
        await _send(ws, lock, {"type": "ping", "seq": getattr(bus, "last_seq", 0)})


async def _cancel(task: asyncio.Task[None] | None) -> None:
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task


@router.websocket("/ws")
async def events_ws(ws: WebSocket) -> None:
    if not token_matches(ws.app.state.token, ws.query_params.get("t")):
        await ws.close(code=4401)
        return
    bus: EventBus = ws.app.state.bus
    await ws.accept()
    lock = asyncio.Lock()
    seq = getattr(bus, "last_seq", 0)
    await _send(ws, lock, {"type": "hello", "seq": seq})
    subs = _Subs()
    streamer: asyncio.Task[None] | None = asyncio.create_task(_stream(ws, lock, bus, seq, subs))
    pinger = asyncio.create_task(_ping(ws, lock, bus))
    try:
        while True:
            msg = await ws.receive_text()
            try:
                cmd = orjson.loads(msg)
            except orjson.JSONDecodeError:
                continue
            if not isinstance(cmd, dict):
                continue
            if cmd.get("op") == "subscribe_runs":
                ids = cmd.get("run_ids")
                subs.runs = {i for i in ids if isinstance(i, str)} if isinstance(ids, list) else None
            elif cmd.get("op") == "resume":
                after = cmd.get("after_seq")
                await _cancel(streamer)
                streamer = asyncio.create_task(
                    _stream(ws, lock, bus, int(after) if isinstance(after, int | float) else 0, subs))
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("ws handler failed")
    finally:
        await _cancel(streamer)
        await _cancel(pinger)
