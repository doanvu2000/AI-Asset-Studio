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

from studio_core.app import build_app
from studio_core.config import load_settings

READY_PREFIX = "STUDIO_CORE_READY"


def _parse(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="studio_core")
    sub = p.add_subparsers(dest="cmd", required=True)
    serve = sub.add_parser("serve", help="run the HTTP/WS core")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=0)
    serve.add_argument("--data-dir", type=Path, default=None)
    serve.add_argument("--log-level", default="info")
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


async def _announce_when_started(server: uvicorn.Server, port: int) -> None:
    while not server.started:
        if server.should_exit:
            return
        await asyncio.sleep(0.02)
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

    app = build_app(settings, token)
    sock = _bind(args.host, args.port)
    port = sock.getsockname()[1]
    config = uvicorn.Config(app, log_config=None, log_level=args.log_level, access_log=False,
                            ws_ping_interval=20.0, timeout_graceful_shutdown=10)
    server = uvicorn.Server(config)

    def request_shutdown() -> None:
        server.should_exit = True

    app.state.request_shutdown = request_shutdown
    announcer = asyncio.create_task(_announce_when_started(server, port))
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


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    logging.basicConfig(stream=sys.stderr, level=args.log_level.upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.cmd == "serve":
        try:
            asyncio.run(_serve(args))
        except KeyboardInterrupt:
            pass
    return 0
