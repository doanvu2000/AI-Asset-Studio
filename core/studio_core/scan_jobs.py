"""Live progress + cancel for long read-only scans (audit, restyle scan, QA recompute) over the event bus."""
from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

EVENT = "scan.progress"
MIN_GAP_S = 0.1

Progress = Any  # Callable[[int, int, str], None]


class ScanCancelled(Exception):
    pass


class ScanJob:
    def __init__(self, bus: Any, kind: str, key: str) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.kind, self.key = kind, key
        self._bus = bus
        self._stop = threading.Event()
        self._last = 0.0
        self._phase = ""
        self._total = 0

    def cancel(self) -> None:
        self._stop.set()

    @property
    def cancelled(self) -> bool:
        return self._stop.is_set()

    def emit(self, done: int, total: int, phase: str) -> None:
        self._bus.publish(EVENT, {"job": self.id, "kind": self.kind, "key": self.key, "done": done,
                                  "total": total, "phase": phase})

    def __call__(self, done: int, total: int, phase: str = "files") -> None:
        """Raises ScanCancelled between units so callers never stop mid-write; emits at most ~10 events/s."""
        if self._stop.is_set():
            raise ScanCancelled()
        now = time.monotonic()
        if phase == self._phase and now - self._last < MIN_GAP_S and done != total:
            self._total = total
            return
        # Loops tick before each unit, so a phase never reports its own last unit; close it out on hand-over.
        if phase != self._phase and self._phase and self._total:
            self.emit(self._total, self._total, self._phase)
        self._phase, self._last, self._total = phase, now, total
        self.emit(done, total, phase)


class ScanJobs:
    def __init__(self, bus: Any) -> None:
        self._bus = bus
        self._jobs: dict[str, ScanJob] = {}
        self._lock = threading.Lock()

    @contextmanager
    def open(self, kind: str, key: str = "") -> Iterator[ScanJob]:
        job = ScanJob(self._bus, kind, key)
        with self._lock:
            self._jobs[job.id] = job
        job.emit(0, 0, "start")
        end = "done"
        try:
            yield job
        except ScanCancelled:
            end = "cancelled"
            raise
        except BaseException:
            end = "error"
            raise
        finally:
            if end == "done" and job.cancelled:
                end = "cancelled"
            with self._lock:
                self._jobs.pop(job.id, None)
            job.emit(0, 0, end)

    def cancel(self, job: str | None = None, kind: str | None = None) -> int:
        with self._lock:
            hit = [j for j in self._jobs.values() if (job and j.id == job) or (not job and kind and j.kind == kind)]
        for j in hit:
            j.cancel()
        return len(hit)


def jobs_of(request: Any) -> ScanJobs:
    st = request.app.state
    jobs = getattr(st, "scan_jobs", None)
    if jobs is None:
        jobs = st.scan_jobs = ScanJobs(st.bus)
    return jobs
