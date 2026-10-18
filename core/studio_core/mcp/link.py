"""HTTP link to a core: attach via the lock file, or spawn a private core and stop it when done."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, BinaryIO

import httpx

import studio_core
from studio_core.mcp.lockfile import pid_alive, read_lock

log = logging.getLogger(__name__)

READY_PREFIX = "STUDIO_CORE_READY"
CORE_DIR = Path(studio_core.__file__).resolve().parents[1]


class CoreError(RuntimeError):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.status = status
        self.code = code
        self.message = message


class CoreLink:
    def __init__(self, data_dir: Path, *, boot_timeout_s: float = 60.0, python: str | None = None) -> None:
        self.data_dir = Path(data_dir).resolve()
        self.boot_timeout_s = boot_timeout_s
        self.python = python or sys.executable
        self.base_url: str | None = None
        self.token: str | None = None
        self.spawned_pid: int | None = None
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports" / "mcp"

    async def client(self) -> httpx.AsyncClient:
        async with self._lock:
            if self._client is None or not await self._alive(self._client):
                if self._client is not None:
                    await self._client.aclose()
                    self._client = None
                self._client = await self._attach() or await self._spawn()
            return self._client

    async def _alive(self, c: httpx.AsyncClient) -> bool:
        try:
            return (await c.get("/api/runs", params={"limit": 1}, timeout=3)).status_code == 200
        except httpx.HTTPError:
            return False

    def _make_client(self, host: str, port: int, token: str) -> httpx.AsyncClient:
        self.base_url = f"http://{host}:{port}"
        self.token = token
        return httpx.AsyncClient(base_url=self.base_url, headers={"Authorization": f"Bearer {token}"},
                                 timeout=httpx.Timeout(60, connect=5))

    async def _attach(self) -> httpx.AsyncClient | None:
        info = await asyncio.to_thread(read_lock, self.data_dir)
        if info is None or not await asyncio.to_thread(pid_alive, int(info.get("pid", 0))):
            return None
        host = str(info.get("host") or "127.0.0.1")
        if host in ("0.0.0.0", "::"):
            host = "127.0.0.1"
        c = self._make_client(host, info["port"], str(info["token"]))
        if await self._alive(c):
            log.info("attached to core pid=%s port=%s", info.get("pid"), info["port"])
            return c
        await c.aclose()
        return None

    async def _spawn(self) -> httpx.AsyncClient:
        token = secrets.token_urlsafe(32)
        logs = self.data_dir / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "STUDIO_TOKEN": token, "STUDIO_PARENT_PID": str(os.getpid()),
               "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
        cmd = [self.python, "-m", "studio_core", "serve", "--port", "0", "--data-dir", str(self.data_dir)]
        with (logs / "mcp-core.log").open("ab") as err:
            proc = _popen_detached(cmd, env, err)
        port, pid = await asyncio.to_thread(_await_ready, proc, self.boot_timeout_s)
        self.spawned_pid = pid
        log.info("spawned core pid=%s port=%s", pid, port)
        return self._make_client("127.0.0.1", port, token)

    async def close(self) -> None:
        c, self._client = self._client, None
        pid, self.spawned_pid = self.spawned_pid, None
        stop = False
        if pid is not None and c is not None:
            try:
                busy = (await c.get("/api/runs", params={"status": "running", "limit": 1}, timeout=3)).json()
                if not busy:
                    await c.post("/api/shutdown", timeout=3)
                    log.info("requested shutdown of spawned core %s", pid)
                    stop = True
                else:
                    log.warning("core %s still has running runs; it will exit once idle", pid)
            except httpx.HTTPError:
                log.exception("core shutdown request failed")
        # Close first: uvicorn's graceful shutdown waits for our keep-alive connection otherwise.
        if c is not None:
            await c.aclose()
        if stop and pid is not None:
            exited = await asyncio.to_thread(_wait_pid, pid, 15)
            log.info("core %s %s", pid, "exited" if exited else "still running after shutdown request")

    async def request(self, method: str, path: str, **kw: Any) -> Any:
        c = await self.client()
        try:
            r = await c.request(method, path, **kw)
        except httpx.HTTPError as e:
            raise CoreError(0, "core_unreachable", f"{type(e).__name__}: {e}") from e
        if r.status_code >= 400:
            try:
                err = r.json().get("error", {})
            except ValueError:
                err = {}
            raise CoreError(r.status_code, err.get("code", "http_error"), err.get("message", r.text[:500]))
        return r.json() if r.content else None

    async def upload(self, path: Path) -> dict[str, Any]:
        def _open() -> BinaryIO:
            return path.open("rb")

        f = await asyncio.to_thread(_open)
        try:
            return await self.request("POST", "/api/blobs", files={"file": (path.name, f)}, timeout=300)
        finally:
            await asyncio.to_thread(f.close)

    async def download(self, url: str, dest: Path, params: dict[str, Any] | None = None) -> Path:
        c = await self.client()
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        try:
            async with c.stream("GET", url, params=params, timeout=300) as r:
                if r.status_code >= 400:
                    await r.aread()
                    raise CoreError(r.status_code, "download_failed", r.text[:300])
                out = await asyncio.to_thread(tmp.open, "wb")
                try:
                    async for chunk in r.aiter_bytes(1 << 20):
                        await asyncio.to_thread(out.write, chunk)
                finally:
                    await asyncio.to_thread(out.close)
        except httpx.HTTPError as e:
            raise CoreError(0, "core_unreachable", f"{type(e).__name__}: {e}") from e
        await asyncio.to_thread(os.replace, tmp, dest)
        return dest

    async def fetch_bytes(self, url: str, params: dict[str, Any] | None = None) -> bytes:
        c = await self.client()
        r = await c.get(url, params=params, timeout=120)
        if r.status_code >= 400:
            raise CoreError(r.status_code, "download_failed", r.text[:300])
        return r.content


def _popen_detached(cmd: list[str], env: dict[str, str], err: BinaryIO) -> subprocess.Popen[bytes]:
    kw: dict[str, Any] = {"cwd": CORE_DIR, "env": env, "stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE,
                          "stderr": err}
    if sys.platform != "win32":
        return subprocess.Popen(cmd, start_new_session=True, **kw)
    # Claude Code tree-kills its MCP servers on exit; a short-lived intermediate leaves the core parentless so
    # it finishes an idle-gated shutdown via STUDIO_PARENT_PID. Breakaway helps only where the job allows it
    # (Codex's kill-on-close job does not, so there the core still dies with the CLI).
    # The core inherits the intermediate's stdout pipe, so READY still reaches us.
    flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    launcher = _LAUNCHER.format(flags=flags, breakaway=subprocess.CREATE_BREAKAWAY_FROM_JOB)
    args = [cmd[0], "-c", launcher, json.dumps(cmd)]
    try:
        return subprocess.Popen(args, creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, **kw)
    except OSError:
        return subprocess.Popen(args, creationflags=flags, **kw)


_LAUNCHER = """import json, subprocess, sys
cmd = json.loads(sys.argv[1])
kw = dict(stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr)
try:
    subprocess.Popen(cmd, creationflags={flags} | {breakaway}, **kw)
except OSError:
    subprocess.Popen(cmd, creationflags={flags}, **kw)
"""


def _await_ready(proc: subprocess.Popen[bytes], timeout_s: float) -> tuple[int, int]:
    assert proc.stdout is not None
    found: list[tuple[int, int]] = []
    eof = threading.Event()

    def reader() -> None:
        # Drains until EOF so a stray print in the core never blocks on a full pipe.
        for raw in proc.stdout:  # type: ignore[union-attr]
            line = raw.decode("utf-8", "replace").strip()
            if not found and line.startswith(READY_PREFIX):
                info = json.loads(line.split(" ", 1)[1])
                found.append((int(info["port"]), int(info["pid"])))
        eof.set()

    threading.Thread(target=reader, name="core-stdout", daemon=True).start()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if found:
            return found[0]
        if eof.is_set():
            raise CoreError(0, "core_boot_failed", "core exited before READY; see logs/mcp-core.log")
        time.sleep(0.05)
    raise CoreError(0, "core_boot_timeout", f"core not ready after {timeout_s:.0f}s; see logs/mcp-core.log")


def _wait_pid(pid: int, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.1)
    return False

