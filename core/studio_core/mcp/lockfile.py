"""`<data_dir>/core.lock.json`: lets local clients (MCP proxy) find a running core and its token."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

LOCK_NAME = "core.lock.json"


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
