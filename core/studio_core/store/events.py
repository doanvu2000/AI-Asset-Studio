from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from collections.abc import AsyncIterator
from typing import Any

from studio_core.contracts import Event

STREAM_DROPPED = "stream.dropped"


class _Subscriber:
    __slots__ = ("queue", "dropped")

    def __init__(self, maxsize: int) -> None:
        self.queue: asyncio.Queue[Event] = asyncio.Queue(maxsize)
        self.dropped = False


class MemoryEventBus:
    """In-process bus with a replay ring buffer. publish() is safe from any thread."""

    def __init__(self, buffer_size: int = 1000, queue_size: int = 2000) -> None:
        self._buffer: deque[Event] = deque(maxlen=buffer_size)
        self._subs: set[_Subscriber] = set()
        self._queue_size = queue_size
        self._seq = 0
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def last_seq(self) -> int:
        return self._seq

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def publish(self, type: str, data: dict[str, Any] | None = None, run_id: str | None = None,
                step_id: str | None = None) -> Event:
        with self._lock:
            self._seq += 1
            ev = Event(seq=self._seq, ts=time.time(), type=type, run_id=run_id, step_id=step_id, data=data or {})
            self._buffer.append(ev)
        loop = self._loop
        if loop is None or _running_loop() is loop:
            self._fanout(ev)
        elif not loop.is_closed():
            loop.call_soon_threadsafe(self._fanout, ev)
        return ev

    def _fanout(self, ev: Event) -> None:
        for sub in list(self._subs):
            try:
                sub.queue.put_nowait(ev)
            except asyncio.QueueFull:
                sub.dropped = True
                self._subs.discard(sub)

    async def subscribe(self, after_seq: int | None = None) -> AsyncIterator[Event]:
        if self._loop is None:
            self._loop = asyncio.get_running_loop()
        sub = _Subscriber(self._queue_size)
        self._subs.add(sub)
        last = self._seq if after_seq is None else after_seq
        try:
            if after_seq is not None:
                with self._lock:
                    backlog = [e for e in self._buffer if e.seq > after_seq]
                for ev in backlog:
                    last = ev.seq
                    yield ev
            while True:
                if sub.dropped and sub.queue.empty():
                    yield Event(seq=last, ts=time.time(), type=STREAM_DROPPED, data={"after_seq": last})
                    return
                ev = await sub.queue.get()
                if ev.seq <= last:
                    continue
                last = ev.seq
                yield ev
        finally:
            self._subs.discard(sub)


def _running_loop() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None
