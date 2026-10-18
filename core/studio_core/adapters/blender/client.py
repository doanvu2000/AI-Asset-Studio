"""Resident Blender worker supervision (Job Object) with a serialized async JSON-line RPC."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Callable

from ...config import Settings
from ...contracts import ResourceHealth, StepError
from ...paths import blender_scripts_dir
from .._util import log, publish_health
from .._winproc import JobObject, spawn_in_job

WORKER_SCRIPT = blender_scripts_dir() / "worker.py"
BOOT_TIMEOUT_S = 90.0
ProgressCallback = Callable[[dict[str, Any]], None]


class BlenderResource:
    name = "blender"

    def __init__(self, settings: Settings, bus: Any = None, warmup: bool = True):
        self.settings = settings
        self.bus = bus
        self.warmup = warmup
        self.status = "down"
        self.error: str | None = None
        self.version: str | None = None
        self.proc: asyncio.subprocess.Process | None = None
        self.job: JobObject | None = None
        self.boot_s: float | None = None
        self.warmup_s: float | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._start_lock = asyncio.Lock()
        self._call_lock = asyncio.Lock()
        self._busy = False
        self._n = 0
        self._stopping = False
        self._watchdog: asyncio.Task[None] | None = None
        self._drain: asyncio.Task[None] | None = None

    @property
    def log_path(self) -> Path:
        return self.settings.data_dir / "logs" / "blender.log"

    def _set(self, status: str, error: str | None = None) -> None:
        changed = (status, error) != (self.status, self.error)
        self.status, self.error = status, error
        if changed:
            publish_health(self.bus, self._health_now())

    def _health_now(self) -> ResourceHealth:
        detail: dict[str, Any] = {"managed": True, "version": self.version,
                                  "pid": self.proc.pid if self.proc and self.proc.returncode is None else None,
                                  "boot_s": self.boot_s, "warmup_s": self.warmup_s}
        if self.error:
            detail["error"] = self.error
        status = "busy" if self.status == "up" and self._busy else self.status
        return ResourceHealth(name=self.name, status=status, detail=detail)

    async def health(self) -> ResourceHealth:
        return self._health_now()

    def _alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None and self._writer is not None

    async def ensure_started(self) -> None:
        async with self._start_lock:
            if self.status == "up" and self._alive():
                return
            await self._kill()
            await self._spawn()

    async def _spawn(self) -> None:
        exe = self.settings.blender_exe
        if not exe.exists():
            self._set("error", f"missing {exe}")
            raise StepError("blender_missing", f"blender.exe not found at {exe}")
        self._set("starting")
        self._stopping = False
        t0 = time.perf_counter()
        args = [str(exe), "-b", "--factory-startup", "-noaudio", "-P", str(WORKER_SCRIPT), "--", "0"]
        self.proc, self.job = await spawn_in_job(args, stdin=asyncio.subprocess.DEVNULL,
                                                 stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        logf = open(self.log_path, "ab")
        logf.write(f"\n==== spawn {time.strftime('%Y-%m-%d %H:%M:%S')} pid={self.proc.pid}\n".encode())
        port = None
        try:
            async with asyncio.timeout(BOOT_TIMEOUT_S):
                while port is None:
                    line = await self.proc.stdout.readline()
                    if not line:
                        break
                    logf.write(line)
                    if line.startswith(b"WORKER_READY"):
                        port = int(line.split()[1])
        except TimeoutError:
            pass
        if port is None:
            logf.close()
            await self._kill()
            msg = f"blender worker failed to boot, see {self.log_path}"
            self._set("error", msg)
            raise StepError("blender_boot_failed", msg, retryable=True)
        self._drain = asyncio.create_task(self._drain_stdout(logf), name="blender-stdout")
        self._reader, self._writer = await asyncio.open_connection("127.0.0.1", port, limit=16 * 2**20)
        self._watchdog = asyncio.create_task(self._watch(), name="blender-watchdog")
        self.boot_s = time.perf_counter() - t0
        try:
            ping = await self._call_raw("ping", {}, None, 30)
            self.version = ping.get("blender")
            if self.warmup:
                w = await self._call_raw("warmup", {}, None, 120)
                self.warmup_s = w.get("s")
        except BaseException as e:
            await asyncio.shield(self._kill())
            self._set("error", f"worker init failed: {e!r}"[:300])
            if isinstance(e, (StepError, asyncio.CancelledError)):
                raise
            raise StepError("blender_boot_failed", repr(e), retryable=True) from e
        self._set("up")
        log.info("blender worker up pid=%s boot=%.1fs warmup=%ss", self.proc.pid, self.boot_s, self.warmup_s)

    async def _drain_stdout(self, logf: Any) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        try:
            while line := await self.proc.stdout.readline():
                logf.write(line)
                logf.flush()
        finally:
            logf.close()

    async def _watch(self) -> None:
        assert self.proc is not None
        rc = await self.proc.wait()
        if not self._stopping:
            msg = f"blender worker exited rc={rc}"
            log.error(msg)
            self._set("error", msg)

    async def _call_raw(self, cmd: str, params: dict[str, Any], on_event: ProgressCallback | None,
                        timeout: float) -> Any:
        if not self._alive():
            raise StepError("blender_crashed", "blender worker is not running", retryable=True)
        assert self._reader is not None and self._writer is not None
        self._n += 1
        rid = self._n
        self._writer.write((json.dumps({"id": rid, "cmd": cmd, "params": params}) + "\n").encode())
        await self._writer.drain()
        async with asyncio.timeout(timeout):
            while True:
                line = await self._reader.readline()
                if not line:
                    self._set("error", "blender worker connection lost")
                    raise StepError("blender_crashed", f"worker died during {cmd}", retryable=True)
                msg = json.loads(line)
                if msg.get("id") != rid:
                    continue
                if "event" in msg:
                    if on_event:
                        on_event(msg)
                    continue
                if not msg.get("ok"):
                    raise StepError(msg.get("code") or "blender_error", msg.get("error", "worker error"),
                                    detail={"trace": msg.get("trace")})
                return msg.get("result")

    async def call(self, cmd: str, params: dict[str, Any] | None = None, on_event: ProgressCallback | None = None,
                   timeout: float = 900) -> Any:
        """One request at a time; a cancelled or timed-out call kills the worker (it cannot abort a render)."""
        async with self._call_lock:
            await self.ensure_started()
            self._busy = True
            publish_health(self.bus, self._health_now())
            try:
                return await self._call_raw(cmd, params or {}, on_event, timeout)
            except (asyncio.CancelledError, TimeoutError) as e:
                await asyncio.shield(self._kill())
                self._set("down")
                if isinstance(e, TimeoutError):
                    raise StepError("blender_timeout", f"{cmd} exceeded {timeout:.0f}s", retryable=True) from e
                raise
            except StepError as e:
                if e.code == "blender_crashed":
                    await self._kill()
                raise
            finally:
                self._busy = False
                publish_health(self.bus, self._health_now())

    async def _kill(self) -> None:
        self._stopping = True
        if self._writer:
            self._writer.close()
            self._writer = self._reader = None
        if self.job:
            self.job.close()
            self.job = None
        if self.proc and self.proc.returncode is None:
            try:
                await asyncio.wait_for(self.proc.wait(), 10)
            except TimeoutError:
                log.warning("blender pid %s survived job termination", self.proc.pid)
        for t in (self._watchdog, self._drain):
            if t and not t.done() and t is not asyncio.current_task():
                t.cancel()
        self._watchdog = self._drain = None

    async def stop(self) -> None:
        async with self._start_lock:
            if self._alive() and not self._call_lock.locked():
                self._stopping = True
                try:
                    assert self._writer is not None
                    self._writer.write(b'{"cmd": "quit"}\n')
                    await self._writer.drain()
                    await asyncio.wait_for(self.proc.wait(), 5)
                except (OSError, TimeoutError):
                    pass
            await self._kill()
            self._set("down")
