from __future__ import annotations

import asyncio
import itertools
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ..contracts import Lane

# A waiter older than this wins over model affinity, so a long same-model batch cannot starve other work.
AFFINITY_MAX_WAIT_S = 120.0
AFFINITY_LANES = frozenset({Lane.GPU})


@dataclass
class Waiter:
    ready_at: float
    seq: int
    future: asyncio.Future[None]
    hint: dict[str, Any] = field(default_factory=dict)  # {"group": <model group>} for GPU affinity
    enqueued_at: float = 0.0

    @property
    def group(self) -> str | None:
        return self.hint.get("group")


class LaneScheduler:
    def __init__(self, limits: dict[str, int], clock: Callable[[], float] = time.monotonic,
                 max_wait_s: float = AFFINITY_MAX_WAIT_S):
        self._limits = dict(limits)
        self._used: dict[Lane, int] = {}
        self._waiters: dict[Lane, list[Waiter]] = {}
        self._last_group: dict[Lane, str | None] = {}
        self._seq = itertools.count()
        self._clock = clock
        self._max_wait_s = max_wait_s

    def limit(self, lane: Lane) -> int:
        return max(1, int(self._limits.get(lane.value, 1)))

    def in_use(self, lane: Lane) -> int:
        return self._used.get(lane, 0)

    def queued(self, lane: Lane) -> int:
        return len(self._waiters.get(lane, ()))

    def last_group(self, lane: Lane) -> str | None:
        return self._last_group.get(lane)

    def has_capacity(self, lane: Lane) -> bool:
        return lane == Lane.NONE or (self.in_use(lane) < self.limit(lane) and not self._waiters.get(lane))

    async def acquire(self, lane: Lane, ready_at: float, on_wait: Callable[[], Awaitable[None]] | None = None,
                      hint: dict[str, Any] | None = None) -> None:
        if lane == Lane.NONE:
            return
        hint = hint or {}
        if self.has_capacity(lane):
            self._grant(lane, hint.get("group"))
            return
        w = Waiter(ready_at, next(self._seq), asyncio.get_running_loop().create_future(), hint, self._clock())
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

    def _grant(self, lane: Lane, group: str | None) -> None:
        self._used[lane] = self.in_use(lane) + 1
        self._last_group[lane] = group

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
            self._grant(lane, w.group)
            w.future.set_result(None)

    def _select(self, lane: Lane, waiters: list[Waiter]) -> Waiter:
        """FIFO by ready time; on affinity lanes prefer the group that ran last unless someone waited too long."""
        def first(ws: list[Waiter]) -> Waiter:
            return min(ws, key=lambda w: (w.ready_at, w.seq))

        last = self._last_group.get(lane)
        if lane not in AFFINITY_LANES or last is None:
            return first(waiters)
        now = self._clock()
        starving = [w for w in waiters if now - w.enqueued_at >= self._max_wait_s]
        if starving:
            return first(starving)
        same = [w for w in waiters if w.group == last]
        return first(same or waiters)
