"""`<data_dir>/core.lock.json`: lets local clients (MCP proxy, app) find a running core and its token."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import IO, Any

import httpx

LOCK_NAME = "core.lock.json"
SPAWN_LOCK_NAME = "core.spawn.lock"
EXIT_ALREADY_RUNNING = 3
BUSY_PREFIX = "STUDIO_CORE_ALREADY_RUNNING"
SPAWN_LOCK_WAIT_S = 60.0


def lock_path(data_dir: Path) -> Path:
    return Path(data_dir) / LOCK_NAME


def write_lock(data_dir: Path, *, port: int, token: str, host: str = "127.0.0.1", version: str = "") -> Path:
    path = lock_path(data_dir)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"pid": os.getpid(), "host": host, "port": port, "token": token,
                               "version": version, "started_at": time.time()}), encoding="utf-8")
    os.replace(tmp, path)
    return path


def remove_lock(data_dir: Path) -> None:
    """Only removes the lock this process wrote; a second core on the same dir may own it now."""
    path = lock_path(data_dir)
    info = read_lock(data_dir)
    if info is not None and info.get("pid") == os.getpid():
        path.unlink(missing_ok=True)


def read_lock(data_dir: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(lock_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("port"), int) or not data.get("token"):
        return None
    return data


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import pywintypes
        import win32api
        import win32con
        import win32process

        try:
            handle = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        except pywintypes.error:
            return False
        try:
            return win32process.GetExitCodeProcess(handle) == 259  # STILL_ACTIVE
        finally:
            win32api.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def lock_host(info: dict[str, Any]) -> str:
    host = str(info.get("host") or "127.0.0.1")
    return "127.0.0.1" if host in ("0.0.0.0", "::") else host


def live_core(data_dir: Path, timeout_s: float = 3.0) -> dict[str, Any] | None:
    """The lock's core if its pid is alive and it accepts the lock's token (rules out pid reuse)."""
    info = read_lock(data_dir)
    if info is None or info.get("pid") == os.getpid() or not pid_alive(int(info.get("pid", 0))):
        return None
    try:
        r = httpx.get(f"http://{lock_host(info)}:{info['port']}/api/runs", params={"limit": 1},
                      headers={"Authorization": f"Bearer {info['token']}"}, timeout=timeout_s)
    except httpx.HTTPError:
        return None
    return info if r.status_code == 200 else None


class SpawnLock:
    """OS lock on `core.spawn.lock`, held from the duplicate check until core.lock.json is written.

    The OS drops it when the holder dies, so a crashed starter never leaves it stale; a holder stuck for
    longer than `wait_s` is ignored instead of blocking startup forever.
    """

    def __init__(self, data_dir: Path, wait_s: float = SPAWN_LOCK_WAIT_S) -> None:
        self.path = Path(data_dir) / SPAWN_LOCK_NAME
        self.wait_s = wait_s
        self._f: IO[bytes] | None = None

    def acquire(self) -> bool:
        f = self.path.open("a+b")
        deadline = time.monotonic() + self.wait_s
        while not _try_lock(f):
            if time.monotonic() >= deadline:
                f.close()
                return False
            time.sleep(0.1)
        self._f = f
        return True

    def release(self) -> None:
        f, self._f = self._f, None
        if f is None:
            return
        try:
            _unlock(f)
        finally:
            f.close()


def _try_lock(f: IO[bytes]) -> bool:
    try:
        if sys.platform == "win32":
            import msvcrt

            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(f: IO[bytes]) -> None:
    if sys.platform == "win32":
        import msvcrt

        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
