"""ComfyUI HTTP + shared WebSocket client: routes WS events per prompt_id, falls back to /history polling."""
from __future__ import annotations

import asyncio
import json
import struct
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import httpx
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidURI

from ...contracts import StepError
from .._util import log

PREVIEW_IMAGE = 1
PREVIEW_IMAGE_WITH_METADATA = 4
_FORMAT_MIME = {1: "image/jpeg", 2: "image/png"}

EventCallback = Callable[[str, dict[str, Any]], None]


def parse_preview_frame(frame: bytes) -> tuple[str, bytes, str | None] | None:
    """-> (mime, image bytes, prompt_id or None). Type 1 = 4B type + 4B format + image; type 4 carries JSON metadata."""
    if len(frame) < 8:
        return None
    (kind,) = struct.unpack(">I", frame[:4])
    if kind == PREVIEW_IMAGE:
        (fmt,) = struct.unpack(">I", frame[4:8])
        return _FORMAT_MIME.get(fmt, "image/jpeg"), frame[8:], None
    if kind == PREVIEW_IMAGE_WITH_METADATA:
        (n,) = struct.unpack(">I", frame[4:8])
        try:
            meta = json.loads(frame[8:8 + n])
        except ValueError:
            return None
        return meta.get("image_type", "image/jpeg"), frame[8 + n:], meta.get("prompt_id")
    return None


def history_files(entry: dict[str, Any], output_dir: Path, node_order: list[str] | None = None) -> list[Path]:
    """Output files (type == output only) of a /history entry, ordered by node_order then server order."""
    outputs: dict[str, Any] = entry.get("outputs") or {}
    nodes = list(outputs)
    if node_order:
        rank = {n: i for i, n in enumerate(node_order)}
        nodes.sort(key=lambda n: rank.get(n, len(rank)))
    files: list[Path] = []
    for node in nodes:
        for kind in ("images", "3d"):
            for f in outputs[node].get(kind) or []:
                if isinstance(f, dict) and f.get("type") == "output" and f.get("filename"):
                    files.append(output_dir / (f.get("subfolder") or "") / f["filename"])
    return files


def history_error(entry: dict[str, Any]) -> dict[str, Any] | None:
    for msg in (entry.get("status") or {}).get("messages") or []:
        if isinstance(msg, list) and len(msg) == 2 and msg[0] in ("execution_error", "execution_interrupted"):
            return {"type": msg[0], "data": msg[1]}
    return None


def execution_error(data: dict[str, Any]) -> StepError:
    exc_type = str(data.get("exception_type") or "")
    exc_msg = str(data.get("exception_message") or "").strip()
    # TRELLIS remesh near the VRAM ceiling fails with "CUDA error: unknown error"; a rerun after /free succeeds.
    transient = "OutOfMemory" in exc_type or "CUDA error" in exc_msg or "out of memory" in exc_msg
    return StepError("comfy_execution_error",
                     f"{data.get('node_type') or data.get('node_id')}: {exc_msg}",
                     retryable=transient,
                     detail={k: data.get(k) for k in ("node_id", "node_type", "exception_type", "exception_message")}
                     | {"traceback": "".join(data.get("traceback") or [])[-4000:]})


@dataclass
class _Waiter:
    queue: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)


class ComfyClient:
    def __init__(self, base: str, output_dir: Path, poll_s: float = 1.5, safety_poll_s: float = 10.0):
        self.base = base.rstrip("/")
        self.output_dir = output_dir
        self.client_id = str(uuid.uuid4())
        self.poll_s = poll_s
        self.safety_poll_s = safety_poll_s
        self.http = httpx.AsyncClient(base_url=self.base, timeout=30)
        self.ws_connected = asyncio.Event()
        self._waiters: dict[str, _Waiter] = {}
        self._executing: str | None = None
        self._ws_task: asyncio.Task[None] | None = None
        self._closed = False

    @property
    def active(self) -> int:
        return len(self._waiters)

    def start(self) -> None:
        if self._ws_task is None:
            self._ws_task = asyncio.create_task(self._ws_loop(), name="comfy-ws")

    async def close(self) -> None:
        self._closed = True
        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except (asyncio.CancelledError, Exception):
                pass
        await self.http.aclose()

    async def _ws_loop(self) -> None:
        url = self.base.replace("http", "ws", 1) + f"/ws?clientId={self.client_id}"
        backoff = 0.5
        while not self._closed:
            try:
                async with ws_connect(url, max_size=None, open_timeout=5, ping_interval=20) as ws:
                    self.ws_connected.set()
                    backoff = 0.5
                    async for frame in ws:
                        self.dispatch(frame)
            except asyncio.CancelledError:
                raise
            except (OSError, ConnectionClosed, InvalidHandshake, InvalidURI, TimeoutError) as e:
                log.debug("comfy ws: %r", e)
            except Exception:
                log.exception("comfy ws loop")
            self.ws_connected.clear()
            if self._closed:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 5.0)

    def dispatch(self, frame: str | bytes) -> None:
        if isinstance(frame, bytes):
            parsed = parse_preview_frame(frame)
            if parsed:
                mime, data, pid = parsed
                self._route(pid or self._executing, {"type": "_preview", "data": {"mime": mime, "bytes": data}})
            return
        try:
            msg = json.loads(frame)
        except ValueError:
            return
        t, data = msg.get("type"), msg.get("data") or {}
        pid = data.get("prompt_id")
        if t == "execution_start" or (t == "executing" and data.get("node") is not None):
            self._executing = pid
        elif t in ("execution_success", "execution_error", "execution_interrupted") and pid == self._executing:
            self._executing = None
        if pid:
            self._route(pid, msg)

    def _route(self, pid: str | None, msg: dict[str, Any]) -> None:
        w = self._waiters.get(pid) if pid else None
        if w:
            w.queue.put_nowait(msg)

    def fail_all(self, code: str, message: str) -> None:
        for w in self._waiters.values():
            w.queue.put_nowait({"type": "_failed", "data": {"code": code, "message": message}})

    async def submit(self, prompt: dict[str, Any], prompt_id: str) -> None:
        try:
            r = await self.http.post("/prompt", json={"prompt": prompt, "client_id": self.client_id,
                                                      "prompt_id": prompt_id})
        except httpx.TransportError as e:
            raise StepError("comfy_unreachable", f"POST /prompt failed: {e!r}", retryable=True) from e
        try:
            body = r.json()
        except ValueError:
            body = {"raw": r.text[:2000]}
        if r.status_code == 400 or (r.status_code == 200 and body.get("node_errors")):
            err = body.get("error") or {}
            raise StepError("comfy_node_errors", err.get("message") or "workflow validation failed",
                            detail={"error": err, "node_errors": body.get("node_errors")})
        if r.status_code != 200:
            raise StepError("comfy_submit_failed", f"HTTP {r.status_code}", retryable=True, detail=body)

    async def history(self, prompt_id: str) -> dict[str, Any] | None:
        r = await self.http.get(f"/history/{prompt_id}")
        r.raise_for_status()
        return r.json().get(prompt_id)

    async def run(self, prompt: dict[str, Any], prompt_id: str | None = None, on_event: EventCallback | None = None,
                  cancelled: asyncio.Event | None = None, timeout: float = 1800) -> tuple[str, dict[str, Any]]:
        """Submit and wait; returns (prompt_id, history entry). Raises StepError / CancelledError."""
        pid = prompt_id or str(uuid.uuid4())
        w = self._waiters[pid] = _Waiter()
        try:
            await self.submit(prompt, pid)
            return pid, await self._wait(pid, w, on_event, cancelled, timeout)
        except asyncio.CancelledError:
            await asyncio.shield(self.cancel(pid))
            raise
        finally:
            self._waiters.pop(pid, None)

    async def _wait(self, pid: str, w: _Waiter, on_event: EventCallback | None, cancelled: asyncio.Event | None,
                    timeout: float) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        last_poll = loop.time()
        while True:
            if cancelled is not None and cancelled.is_set():
                raise asyncio.CancelledError()
            now = loop.time()
            if now > deadline:
                await self.cancel(pid)
                raise StepError("comfy_timeout", f"no result after {timeout:.0f}s", retryable=True)
            interval = self.safety_poll_s if self.ws_connected.is_set() else self.poll_s
            if now - last_poll >= interval:
                last_poll = now
                entry = await self._poll(pid)
                if entry is not None:
                    return entry
            try:
                msg = await asyncio.wait_for(w.queue.get(), timeout=min(self.poll_s, max(deadline - now, 0.01)))
            except TimeoutError:
                continue
            t, data = msg.get("type"), msg.get("data") or {}
            if t == "_failed":
                raise StepError(data["code"], data["message"], retryable=True)
            if on_event:
                on_event(t, data)
            if t == "execution_error":
                raise execution_error(data)
            if t == "execution_interrupted":
                raise _interrupted(cancelled)
            if t == "execution_success" or (t == "executing" and data.get("node") is None):
                return await self._final_history(pid)

    async def _poll(self, pid: str) -> dict[str, Any] | None:
        try:
            entry = await self.history(pid)
        except (httpx.HTTPError, ValueError):
            return None
        if not entry or (entry.get("status") or {}).get("completed") is None:
            return None
        return self._check_entry(entry, None)

    def _check_entry(self, entry: dict[str, Any], cancelled: asyncio.Event | None) -> dict[str, Any]:
        status = entry.get("status") or {}
        if status.get("status_str", "success") != "success":
            err = history_error(entry)
            if err and err["type"] == "execution_interrupted":
                raise _interrupted(cancelled)
            raise execution_error((err or {}).get("data") or {"exception_message": str(status)[:500]})
        return entry

    async def _final_history(self, pid: str) -> dict[str, Any]:
        # execution_success is sent before the queue records history, so the entry may lag briefly.
        for _ in range(40):
            try:
                entry = await self.history(pid)
            except (httpx.HTTPError, ValueError):
                entry = None
            if entry and (entry.get("status") or {}).get("completed") is not None:
                return self._check_entry(entry, None)
            await asyncio.sleep(0.25)
        raise StepError("comfy_no_history", f"history for {pid} missing after success", retryable=True)

    async def cancel(self, pid: str) -> None:
        for path, body in ((f"/api/jobs/{pid}/cancel", None), ("/interrupt", {"prompt_id": pid})):
            try:
                await self.http.post(path, json=body, timeout=5)
            except httpx.HTTPError:
                pass

    async def free(self) -> None:
        await self.http.post("/free", json={"unload_models": True, "free_memory": True}, timeout=30)


def _interrupted(cancelled: asyncio.Event | None) -> BaseException:
    if cancelled is not None and cancelled.is_set():
        return asyncio.CancelledError()
    return StepError("comfy_interrupted", "prompt was interrupted outside the studio", retryable=True)
