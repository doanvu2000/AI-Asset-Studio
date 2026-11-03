"""Short-lived cache of the fix plan a dry run just made, so the Ghi click right after Xem trước does not plan again
(Flutter plans re-encode every image to WebP, 40-57 s on a large project). Plans hold metadata and importer text only,
never image bytes; any file a plan touches that changed since planning drops the entry."""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Any

TTL_SECONDS = 600.0
MAX_ENTRIES = 8

Stamp = tuple[int, int] | None


def _stamp(p: Path) -> Stamp:
    try:
        st = os.stat(p)
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


def actions_key(actions: list[dict[str, Any]]) -> str:
    return json.dumps(actions, sort_keys=True, default=str)


class _Entry:
    __slots__ = ("project", "key", "plan", "stamps", "expires")

    def __init__(self, project: str, key: str, plan: list[dict[str, Any]], stamps: dict[Path, Stamp], expires: float):
        self.project, self.key, self.plan, self.stamps, self.expires = project, key, plan, stamps, expires


class PlanCache:
    def __init__(self, ttl: float = TTL_SECONDS, max_entries: int = MAX_ENTRIES,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl, self._max, self._clock = ttl, max_entries, clock
        self._items: OrderedDict[str, _Entry] = OrderedDict()
        self._lock = threading.Lock()

    def put(self, project: str, actions: list[dict[str, Any]], plan: list[dict[str, Any]], paths: list[Path]) -> str:
        pid = uuid.uuid4().hex
        entry = _Entry(project, actions_key(actions), plan, {p: _stamp(p) for p in paths}, self._clock() + self._ttl)
        with self._lock:
            self._drop_expired()
            self._items[pid] = entry
            while len(self._items) > self._max:
                self._items.popitem(last=False)
        return pid

    def get(self, plan_id: str | None, project: str, actions: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
        """None for an unknown / expired id, another project or action set, or a touched file that changed."""
        if not plan_id:
            return None
        with self._lock:
            self._drop_expired()
            entry = self._items.get(plan_id)
        if entry is None or entry.project != project or entry.key != actions_key(actions):
            return None
        if any(_stamp(p) != s for p, s in entry.stamps.items()):
            self.discard(plan_id)
            return None
        return entry.plan

    def discard(self, plan_id: str | None) -> None:
        with self._lock:
            self._items.pop(plan_id or "", None)

    def drop_project(self, project: str) -> None:
        with self._lock:
            for k in [k for k, e in self._items.items() if e.project == project]:
                del self._items[k]

    def _drop_expired(self) -> None:
        now = self._clock()
        for k in [k for k, e in self._items.items() if e.expires <= now]:
            del self._items[k]

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)
