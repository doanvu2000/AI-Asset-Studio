from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import secrets
import socket
import sys
from pathlib import Path

import uvicorn

from studio_core import __version__
from studio_core.app import build_app
from studio_core.config import load_settings
from studio_core.mcp.lockfile import (BUSY_PREFIX, EXIT_ALREADY_RUNNING, SpawnLock, live_core, remove_lock,
                                      write_lock)

READY_PREFIX = "STUDIO_CORE_READY"


class AlreadyRunning(Exception):
    def __init__(self, info: dict) -> None:
        super().__init__(f"core already running: pid={info.get('pid')} port={info.get('port')}")
        self.info = info


def _parse(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="studio_core")
    sub = p.add_subparsers(dest="cmd", required=True)
    serve = sub.add_parser("serve", help="run the HTTP/WS core")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=0)
    serve.add_argument("--data-dir", type=Path, default=None)
    serve.add_argument("--log-level", default="info")
    mcp = sub.add_parser("mcp", help="stdio MCP server proxying to a running (or auto-spawned) core")
    mcp.add_argument("--data-dir", type=Path, default=None)
    mcp.add_argument("--log-level", default="warning")
    return p.parse_args(argv)


def _bind(host: str, port: int) -> socket.socket:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    if sys.platform == "win32":
        # Windows SO_REUSEADDR allows port hijacking; exclusive use is the safe equivalent.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    else:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    return sock


async def _announce_when_started(server: uvicorn.Server, host: str, port: int, data_dir: Path, token: str,
                                 spawn_lock: SpawnLock) -> None:
    while not server.started:
        if server.should_exit:
            return
        await asyncio.sleep(0.02)
    try:
        write_lock(data_dir, host=host, port=port, token=token, version=__version__)
    except OSError:
        logging.getLogger(__name__).exception("could not write core lock file")
    spawn_lock.release()
    sys.stdout.write(f"{READY_PREFIX} {json.dumps({'port': port, 'pid': os.getpid()})}\n")
    sys.stdout.flush()


async def _watch_parent(app, pid: int, request_shutdown) -> None:
    """Exit once the launching app is gone and nothing is running, so a crashed shell leaves no orphan core."""
    import win32api
    import win32con
    import win32event

    from studio_core.contracts import StepStatus

    handle = win32api.OpenProcess(win32con.SYNCHRONIZE, False, pid)
    while win32event.WaitForSingleObject(handle, 0) != win32event.WAIT_OBJECT_0:
        await asyncio.sleep(2)
    logging.getLogger(__name__).warning("parent %s exited; shutting down when idle", pid)
    while True:
        repo = getattr(app.state, "repo", None)
        active = await repo.list_steps_by_status(
            [StepStatus.RUNNING, StepStatus.WAITING_RESOURCE, StepStatus.READY]) if repo else []
        if not active:
            request_shutdown()
            return
        await asyncio.sleep(5)


async def _serve(args: argparse.Namespace) -> None:
    settings = load_settings(args.data_dir)
    token = os.environ.get("STUDIO_TOKEN")
    if not token:
        token = secrets.token_urlsafe(32)
        print(f"STUDIO_TOKEN not set; dev token: {token}", file=sys.stderr, flush=True)

    spawn_lock = SpawnLock(settings.data_dir)
    if not await asyncio.to_thread(spawn_lock.acquire):
        logging.getLogger(__name__).warning("spawn lock held for >%.0fs; starting anyway", spawn_lock.wait_s)
    try:
        other = await asyncio.to_thread(live_core, settings.data_dir)
        if other is not None:
            raise AlreadyRunning(other)
        app = build_app(settings, token)
        sock = _bind(args.host, args.port)
    except BaseException:
        spawn_lock.release()
        raise
    port = sock.getsockname()[1]
    config = uvicorn.Config(app, log_config=None, log_level=args.log_level, access_log=False,
                            ws_ping_interval=20.0, timeout_graceful_shutdown=10)
    server = uvicorn.Server(config)

    def request_shutdown() -> None:
        server.should_exit = True

    app.state.request_shutdown = request_shutdown
    announcer = asyncio.create_task(_announce_when_started(server, args.host, port, settings.data_dir, token,
                                                         spawn_lock))
    parent = os.environ.get("STUDIO_PARENT_PID")
    watcher = None
    if parent and sys.platform == "win32":
        watcher = asyncio.create_task(_watch_parent(app, int(parent), request_shutdown))
    try:
        await server.serve(sockets=[sock])
    finally:
        announcer.cancel()
        if watcher:
            watcher.cancel()
        sock.close()
        spawn_lock.release()
        remove_lock(settings.data_dir)


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    logging.basicConfig(stream=sys.stderr, level=args.log_level.upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.cmd == "serve":
        try:
            asyncio.run(_serve(args))
        except KeyboardInterrupt:
            pass
        except AlreadyRunning as e:
            print(f"{BUSY_PREFIX} {json.dumps({'port': e.info['port'], 'pid': e.info['pid']})}", flush=True)
            print(f"studio_core: {e} for this data dir; attach via core.lock.json", file=sys.stderr, flush=True)
            return EXIT_ALREADY_RUNNING
    elif args.cmd == "mcp":
        from studio_core.mcp.server import run_stdio

        run_stdio(args.data_dir)
    return 0
