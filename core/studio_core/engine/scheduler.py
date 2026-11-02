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
# Waiters this old beat any run priority; a GPU job takes minutes, so a short bound would make "move to front" moot.
PRIORITY_MAX_WAIT_S = 15 * 60.0


@dataclass
class Waiter:
    ready_at: float
    seq: int
    future: asyncio.Future[None]
    hint: dict[str, Any] = field(default_factory=dict)  # {"group": <model group>, "run_id": ...} for GPU affinity
    enqueued_at: float = 0.0

    @property
    def group(self) -> str | None:
        return self.hint.get("group")

    @property
    def run_id(self) -> str | None:
        return self.hint.get("run_id")


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
        self._held: set[Lane] = set()
        self._idle: dict[Lane, list[asyncio.Future[None]]] = {}
        self._priority: dict[str, float] = {}

    def limit(self, lane: Lane) -> int:
        return max(1, int(self._limits.get(lane.value, 1)))

    def in_use(self, lane: Lane) -> int:
        return self._used.get(lane, 0)

    def queued(self, lane: Lane) -> int:
        return len(self._waiters.get(lane, ()))

    def last_group(self, lane: Lane) -> str | None:
        return self._last_group.get(lane)

    def has_capacity(self, lane: Lane) -> bool:
        return lane == Lane.NONE or (lane not in self._held and self.in_use(lane) < self.limit(lane)
                                     and not self._waiters.get(lane))

    def held(self, lane: Lane) -> bool:
        return lane in self._held

    def hold(self, lane: Lane) -> None:
        """Stop granting the lane; holders keep it until they release."""
        if lane != Lane.NONE:
            self._held.add(lane)

    def unhold(self, lane: Lane) -> None:
        self._held.discard(lane)
        self._dispatch(lane)

    async def wait_idle(self, lane: Lane) -> None:
        if self.in_use(lane) == 0:
            return
        fut = asyncio.get_running_loop().create_future()
        self._idle.setdefault(lane, []).append(fut)
        try:
            await fut
        finally:
            q = self._idle.get(lane)
            if q and fut in q:
                q.remove(fut)

    def now(self) -> float:
        return self._clock()

    def priority(self, run_id: str) -> float:
        return self._priority.get(run_id, 0)

    def waiters(self, lane: Lane) -> list[Waiter]:
        """Queued waiters in the order they would be granted if nothing else arrived."""
        rest = list(self._waiters.get(lane, ()))
        last = self._last_group.get(lane)
        out: list[Waiter] = []
        while rest:
            w = self._select(lane, rest, last)
            rest.remove(w)
            out.append(w)
            last = w.group
        return out

    def queued_runs(self, lane: Lane) -> list[str]:
        return list(dict.fromkeys(w.run_id for w in self.waiters(lane) if w.run_id))

    def set_priority(self, lane: Lane, run_id: str, *, delta: int | None = None, position: int | None = None) -> None:
        """Only the moved run's priority changes, so the others keep their order and model-affinity grouping.
        `position` > 0 lands between two runs of different priority; inside one priority tier it falls back to FIFO."""
        runs = self.queued_runs(lane)
        if run_id not in runs:
            raise LookupError(f"run {run_id} is not waiting for the {lane.value} lane")
        if position is None:
            self._priority[run_id] = self.priority(run_id) + int(delta or 0)
        else:
            runs.remove(run_id)
            pos = min(position, len(runs))
            above = self.priority(runs[pos - 1]) if pos > 0 else None
            below = self.priority(runs[pos]) if pos < len(runs) else None
            if above is None:
                p = (below if below is not None else 0) + 1
            elif below is None:
                p = above - 1
            else:
                p = (above + below) / 2 if above > below else above
            self._priority[run_id] = p
        if self.in_use(lane) < self.limit(lane):
            self._dispatch(lane)

    def forget(self, run_id: str) -> None:
        self._priority.pop(run_id, None)

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
        if self.in_use(lane) == 0:
            for fut in self._idle.pop(lane, []):
                if not fut.done():
                    fut.set_result(None)

    def _grant(self, lane: Lane, group: str | None) -> None:
        self._used[lane] = self.in_use(lane) + 1
        self._last_group[lane] = group

    def _remove(self, lane: Lane, w: Waiter) -> None:
        q = self._waiters.get(lane)
        if q and w in q:
            q.remove(w)

    def _dispatch(self, lane: Lane) -> None:
        q = self._waiters.get(lane)
        while q and lane not in self._held and self.in_use(lane) < self.limit(lane):
            w = self._select(lane, q, self._last_group.get(lane))
            q.remove(w)
            if w.future.done():
                continue
            self._grant(lane, w.group)
            w.future.set_result(None)

    def _select(self, lane: Lane, waiters: list[Waiter], last: str | None) -> Waiter:
        """Highest run priority first; within it FIFO by ready time, and on affinity lanes the group that ran
        last unless someone waited too long."""
        def first(ws: list[Waiter]) -> Waiter:
            return min(ws, key=lambda w: (w.ready_at, w.seq))

        now = self._clock()
        if self._priority:
            starving = [w for w in waiters if now - w.enqueued_at >= PRIORITY_MAX_WAIT_S]
            if starving:
                return first(starving)
            top = max(self.priority(w.run_id or "") for w in waiters)
            waiters = [w for w in waiters if self.priority(w.run_id or "") == top]
        if lane not in AFFINITY_LANES or last is None:
            return first(waiters)
        starving = [w for w in waiters if now - w.enqueued_at >= self._max_wait_s]
        if starving:
            return first(starving)
        same = [w for w in waiters if w.group == last]
        return first(same or waiters)
