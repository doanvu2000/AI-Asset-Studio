from __future__ import annotations

import asyncio
import itertools
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ..contracts import Lane


@dataclass
class Waiter:
    ready_at: float
    seq: int
    future: asyncio.Future[None]
    hint: dict[str, Any] = field(default_factory=dict)  # e.g. {"model": ...} for GPU affinity (P2)


class LaneScheduler:
    def __init__(self, limits: dict[str, int]):
        self._limits = dict(limits)
        self._used: dict[Lane, int] = {}
        self._waiters: dict[Lane, list[Waiter]] = {}
        self._seq = itertools.count()

    def limit(self, lane: Lane) -> int:
        return max(1, int(self._limits.get(lane.value, 1)))

    def in_use(self, lane: Lane) -> int:
        return self._used.get(lane, 0)

    def queued(self, lane: Lane) -> int:
        return len(self._waiters.get(lane, ()))

    def has_capacity(self, lane: Lane) -> bool:
        return lane == Lane.NONE or (self.in_use(lane) < self.limit(lane) and not self._waiters.get(lane))

    async def acquire(self, lane: Lane, ready_at: float, on_wait: Callable[[], Awaitable[None]] | None = None,
                      hint: dict[str, Any] | None = None) -> None:
        if lane == Lane.NONE:
            return
        if self.has_capacity(lane):
            self._used[lane] = self.in_use(lane) + 1
            return
        w = Waiter(ready_at, next(self._seq), asyncio.get_running_loop().create_future(), hint or {})
        self._waiters.setdefault(lane, []).append(w)
        try:
            if on_wait is not None:
                await on_wait()
            await w.future
        except BaseException:
            if w.future.done() and not w.future.cancelled():
                self.release(lane)
            else:
                w.future.cancel()
                self._remove(lane, w)
            raise

    def release(self, lane: Lane) -> None:
        if lane == Lane.NONE:
            return
        self._used[lane] = max(0, self.in_use(lane) - 1)
        self._dispatch(lane)

    def _remove(self, lane: Lane, w: Waiter) -> None:
        q = self._waiters.get(lane)
        if q and w in q:
            q.remove(w)

    def _dispatch(self, lane: Lane) -> None:
        q = self._waiters.get(lane)
        while q and self.in_use(lane) < self.limit(lane):
            w = self._select(lane, q)
            q.remove(w)
            if w.future.done():
                continue
            self._used[lane] = self.in_use(lane) + 1
            w.future.set_result(None)

    def _select(self, lane: Lane, waiters: list[Waiter]) -> Waiter:
        """FIFO by ready time. P2 hook: on Lane.GPU prefer waiters whose hint['model'] matches the loaded model."""
        return min(waiters, key=lambda w: (w.ready_at, w.seq))
