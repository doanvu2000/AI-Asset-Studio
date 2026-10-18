import json
import os
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path

from test_p4b_mcp import make_data_dir, spawn_core, stop_core

from studio_core.mcp.link import CORE_DIR, CoreLink
from studio_core.mcp.lockfile import (BUSY_PREFIX, EXIT_ALREADY_RUNNING, SpawnLock, live_core, lock_path,
                                      pid_alive, read_lock, write_lock)


def serve(data: Path) -> subprocess.Popen[bytes]:
    return subprocess.Popen([sys.executable, "-m", "studio_core", "serve", "--port", "0", "--data-dir", str(data)],
                            cwd=CORE_DIR, env={**os.environ, "STUDIO_TOKEN": secrets.token_hex(16)},
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def first_line(proc: subprocess.Popen[bytes], timeout_s: float = 60) -> tuple[str, float]:
    """First READY/BUSY line and when it arrived; the reader keeps draining so the core never blocks."""
    got: list[tuple[str, float]] = []
    done = threading.Event()

    def reader() -> None:
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if not got and line.startswith(("STUDIO_CORE_READY", BUSY_PREFIX)):
                got.append((line, time.monotonic()))
                done.set()
        done.set()

    threading.Thread(target=reader, daemon=True).start()
    assert done.wait(timeout_s), "core produced no READY/BUSY line"
    assert got, f"core exited without READY/BUSY: {proc.wait(5)} {proc.stderr.read() if proc.stderr else b''!r}"
    return got[0]


def test_second_serve_is_refused(tmp_path: Path) -> None:
    data = make_data_dir(tmp_path)
    proc, pid = spawn_core(data)
    try:
        second = serve(data)
        out, err = second.communicate(timeout=60)
        assert second.returncode == EXIT_ALREADY_RUNNING
        assert out.decode().startswith(BUSY_PREFIX) and b"already running" in err
        assert pid_alive(pid) and read_lock(data)["pid"] == pid
    finally:
        stop_core(proc, data)
    assert not lock_path(data).exists()


def test_concurrent_starts_yield_one_core(tmp_path: Path) -> None:
    data = make_data_dir(tmp_path)
    procs = [serve(data), serve(data)]
    try:
        lines = [first_line(p)[0].split(" ", 1) for p in procs]
        assert sorted(k for k, _ in lines) == sorted([BUSY_PREFIX, "STUDIO_CORE_READY"])
        w = 0 if lines[0][0] != BUSY_PREFIX else 1
        assert procs[1 - w].wait(30) == EXIT_ALREADY_RUNNING
        assert procs[w].poll() is None and read_lock(data)["pid"] == json.loads(lines[w][1])["pid"]
    finally:
        for p in procs:
            if p.poll() is None:
                stop_core(p, data)


def test_stale_lock_does_not_block_start(tmp_path: Path) -> None:
    data = make_data_dir(tmp_path)
    write_lock(data, port=1, token="x")
    info = json.loads(lock_path(data).read_text())
    info["pid"] = 0x7FFFFFF0
    lock_path(data).write_text(json.dumps(info))
    proc, pid = spawn_core(data)
    try:
        assert read_lock(data)["pid"] == pid
    finally:
        stop_core(proc, data)


def test_reused_pid_without_core_is_not_live(tmp_path: Path) -> None:
    data = make_data_dir(tmp_path)
    write_lock(data, port=1, token="x")
    info = json.loads(lock_path(data).read_text())
    info["pid"] = os.getppid() if pid_alive(os.getppid()) else 4
    lock_path(data).write_text(json.dumps(info))
    assert live_core(data, timeout_s=1) is None
    proc, pid = spawn_core(data)
    try:
        assert read_lock(data)["pid"] == pid
        assert live_core(data)["pid"] == pid
    finally:
        stop_core(proc, data)


def test_spawn_lock_serializes_starters(tmp_path: Path) -> None:
    data = make_data_dir(tmp_path)
    held = SpawnLock(data)
    assert held.acquire()
    assert not SpawnLock(data, wait_s=0.2).acquire()
    proc = serve(data)
    try:
        time.sleep(1.5)
        assert proc.poll() is None and read_lock(data) is None
        released = time.monotonic()
        held.release()
        line, at = first_line(proc)
        assert line.startswith("STUDIO_CORE_READY") and at >= released
    finally:
        held.release()
        stop_core(proc, data)


async def test_link_spawn_attaches_when_core_already_runs(tmp_path: Path) -> None:
    data = make_data_dir(tmp_path)
    proc, pid = spawn_core(data)
    link = CoreLink(data)
    try:
        c = await link._spawn()
        assert link.spawned_pid is None and link.base_url == f"http://127.0.0.1:{read_lock(data)['port']}"
        assert (await c.get("/api/runs", params={"limit": 1})).status_code == 200
        await c.aclose()
        assert pid_alive(pid)
    finally:
        stop_core(proc, data)
