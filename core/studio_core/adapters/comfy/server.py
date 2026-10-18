"""ComfyUI server supervision: attach to a running server or spawn a managed one inside a Job Object."""
from __future__ import annotations

import asyncio
import time
from typing import Any, Callable

import httpx

from ...config import Settings
from ...contracts import ResourceHealth, StepError
from .._util import log, publish_health
from .._winproc import JobObject, spawn_in_job
from .client import ComfyClient

CrashListener = Callable[[str], None]


class ComfyResource:
    name = "comfyui"

    def __init__(self, settings: Settings, bus: Any = None):
        self.settings = settings
        self.bus = bus
        self.base = f"http://127.0.0.1:{settings.comfy_port}"
        self.status = "down"
        self.managed = False
        self.version: str | None = None
        self.argv: list[str] = []
        self.error: str | None = None
        self.client: ComfyClient | None = None
        self.proc: asyncio.subprocess.Process | None = None
        self.job: JobObject | None = None
        self.boot_s: float | None = None
        self._listeners: list[CrashListener] = []
        self._lock = asyncio.Lock()
        self._watchdog: asyncio.Task[None] | None = None
        self._stopping = False
        self._log_file: Any = None

    @property
    def log_path(self):
        return self.settings.data_dir / "logs" / "comfyui.log"

    def add_crash_listener(self, cb: CrashListener) -> None:
        self._listeners.append(cb)

    def remove_crash_listener(self, cb: CrashListener) -> None:
        if cb in self._listeners:
            self._listeners.remove(cb)

    async def _stats(self, timeout: float = 2.0) -> dict[str, Any] | None:
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.get(self.base + "/system_stats")
            return r.json() if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            return None

    def _set(self, status: str, error: str | None = None) -> None:
        changed = status != self.status or error != self.error
        self.status, self.error = status, error
        if changed:
            publish_health(self.bus, self._health_now())

    def _health_now(self, stats: dict[str, Any] | None = None) -> ResourceHealth:
        detail: dict[str, Any] = {"managed": self.managed, "version": self.version, "url": self.base,
                                  "pid": self.proc.pid if self.proc and self.managed else None}
        if self.error:
            detail["error"] = self.error
        if self.boot_s is not None:
            detail["boot_s"] = round(self.boot_s, 1)
        if stats:
            dev = (stats.get("devices") or [{}])[0]
            if "vram_free" in dev:
                detail["vram_free_mb"] = int(dev["vram_free"]) // 2**20
                detail["vram_total_mb"] = int(dev.get("vram_total", 0)) // 2**20
        if self.status == "up" and "--disable-pinned-memory" not in self.argv:
            detail["warning"] = "server runs without --disable-pinned-memory"
        status = "busy" if self.status == "up" and self.client and self.client.active else self.status
        return ResourceHealth(name=self.name, status=status, detail=detail)

    async def health(self) -> ResourceHealth:
        stats = await self._stats() if self.status == "up" else None
        return self._health_now(stats)

    async def ensure_started(self) -> None:
        async with self._lock:
            if self.status == "up" and self.client:
                return
            await self._teardown()
            stats = await self._stats()
            if stats is not None:
                self._adopt(stats, managed=False)
                return
            await self._spawn()

    def _adopt(self, stats: dict[str, Any], managed: bool) -> None:
        system = stats.get("system") or {}
        self.version = system.get("comfyui_version")
        self.argv = list(system.get("argv") or [])
        self.managed = managed
        self.client = ComfyClient(self.base, self.settings.comfy_dir / "output")
        self.client.start()
        self._stopping = False
        self._watchdog = asyncio.create_task(self._watch_proc() if managed else self._watch_remote(),
                                             name="comfy-watchdog")
        self._set("up")

    async def _spawn(self) -> None:
        s = self.settings
        self.managed = True
        self._set("starting")
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        prev = self.log_path.with_suffix(".prev.log")
        if self.log_path.exists():
            self.log_path.replace(prev)
        self._log_file = open(self.log_path, "wb")
        args = [str(s.comfy_python), "main.py", "--listen", "127.0.0.1", "--port", str(s.comfy_port),
                "--disable-auto-launch", "--disable-pinned-memory", "--models-directory", str(s.comfy_models_dir),
                "--preview-method", "auto"]
        t0 = time.perf_counter()
        try:
            self.proc, self.job = await spawn_in_job(args, cwd=s.comfy_dir, stdin=asyncio.subprocess.DEVNULL,
                                                     stdout=self._log_file, stderr=asyncio.subprocess.STDOUT)
        except OSError as e:
            self._fail_boot(f"spawn failed: {e!r}")
            raise StepError("comfy_boot_failed", f"cannot spawn ComfyUI: {e!r}") from e
        while True:
            if self.proc.returncode is not None:
                msg = f"ComfyUI exited rc={self.proc.returncode} during boot, see {self.log_path}"
                self._fail_boot(msg)
                raise StepError("comfy_boot_failed", msg, retryable=True)
            stats = await self._stats()
            if stats is not None:
                argv = (stats.get("system") or {}).get("argv") or []
                if "--disable-pinned-memory" not in argv:
                    self._fail_boot("server argv lacks --disable-pinned-memory")
                    raise StepError("comfy_boot_failed", "pinned memory not disabled", detail={"argv": argv})
                self.boot_s = time.perf_counter() - t0
                self._adopt(stats, managed=True)
                log.info("comfyui up in %.1fs pid=%s", self.boot_s, self.proc.pid)
                return
            if time.perf_counter() - t0 > s.comfy_boot_timeout_s:
                self._fail_boot(f"not ready after {s.comfy_boot_timeout_s:.0f}s")
                raise StepError("comfy_boot_timeout", f"ComfyUI not ready after {s.comfy_boot_timeout_s:.0f}s",
                                retryable=True)
            await asyncio.sleep(0.5)

    def _fail_boot(self, msg: str) -> None:
        self._kill()
        self._set("error", msg)

    async def _watch_proc(self) -> None:
        assert self.proc is not None
        rc = await self.proc.wait()
        if not self._stopping:
            await self._crashed(f"ComfyUI process exited rc={rc}")

    async def _watch_remote(self) -> None:
        misses = 0
        while True:
            await asyncio.sleep(3)
            misses = 0 if await self._stats() is not None else misses + 1
            if misses >= 3:
                await self._crashed("attached ComfyUI stopped answering")
                return

    async def _crashed(self, msg: str) -> None:
        log.error(msg)
        if self.client:
            self.client.fail_all("comfy_crashed", msg)
        for cb in list(self._listeners):
            try:
                cb(msg)
            except Exception:
                log.exception("crash listener")
        self._watchdog = None
        self._kill()
        if self.client:
            client, self.client = self.client, None
            await client.close()
        self._set("error", msg)

    def _kill(self) -> None:
        if self.job:
            self.job.close()
            self.job = None
        if self._log_file:
            self._log_file.close()
            self._log_file = None

    async def _teardown(self) -> None:
        self._stopping = True
        wd, self._watchdog = self._watchdog, None
        if wd and wd is not asyncio.current_task():
            wd.cancel()
            try:
                await wd
            except (asyncio.CancelledError, Exception):
                pass
        if self.client:
            client, self.client = self.client, None
            await client.close()
        if self.managed and self.proc and self.proc.returncode is None:
            self._kill()
            try:
                await asyncio.wait_for(self.proc.wait(), 15)
            except TimeoutError:
                log.warning("comfyui pid %s did not exit after job termination", self.proc.pid)
        self._kill()

    async def stop(self) -> None:
        """Kills a managed server; an attached (external) server is only detached, never killed."""
        async with self._lock:
            await self._teardown()
            self.proc = None
            self._set("down")

    async def free(self) -> None:
        if self.status == "up" and self.client:
            try:
                await self.client.free()
            except httpx.HTTPError as e:
                log.warning("comfy /free failed: %r", e)
