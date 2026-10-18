from __future__ import annotations

import secrets

import orjson
from fastapi import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from studio_core.api.errors import ApiError


def token_matches(expected: str, given: str | None) -> bool:
    return given is not None and secrets.compare_digest(expected.encode(), given.encode())


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    return value.strip() if scheme.lower() == "bearer" else None


def require_token(request: Request) -> None:
    if not token_matches(request.app.state.token, _bearer(request)):
        raise ApiError(401, "unauthorized", "missing or invalid token")


def require_token_or_query(request: Request) -> None:
    given = _bearer(request) or request.query_params.get("t")
    if not token_matches(request.app.state.token, given):
        raise ApiError(401, "unauthorized", "missing or invalid token")


class OriginCheckMiddleware:
    """Blocks browser pages from other origins (DNS rebinding / drive-by fetches against localhost)."""

    def __init__(self, app: ASGIApp, allowed_origins: list[str]) -> None:
        self.app = app
        self.allowed = set(allowed_origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            origin = next((v.decode("latin-1") for k, v in scope["headers"] if k == b"origin"), None)
            if origin is not None and origin not in self.allowed:
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008, "reason": "origin not allowed"})
                    return
                body = orjson.dumps({"error": {"code": "forbidden_origin", "message": f"origin not allowed: {origin}"}})
                await send({"type": "http.response.start", "status": 403,
                            "headers": [(b"content-type", b"application/json"),
                                        (b"content-length", str(len(body)).encode())]})
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)
