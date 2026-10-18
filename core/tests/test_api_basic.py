import io
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from studio_core.app import build_app
from studio_core.config import Settings
from studio_core.contracts import (
    BlobRef,
    GateResolution,
    Lane,
    RecipeInfo,
    RunRow,
    StepRow,
    StepStatus,
    VersionRow,
)

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class StubEngine:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.recovered = False
        self.stopped = False

    def list_recipes(self) -> list[RecipeInfo]:
        return [RecipeInfo(id="concept-2d", name="Concept", hash="h")]

    async def start_run(self, recipe_id: str, inputs: dict[str, Any], params: dict[str, Any] | None = None,
                        project_id: str = "default", title: str = "") -> str:
        if recipe_id != "concept-2d":
            raise KeyError(f"unknown recipe: {recipe_id}")
        self.calls.append(("start", (recipe_id, inputs)))
        return "run-1"

    async def cancel_run(self, run_id: str) -> None:
        self.calls.append(("cancel", run_id))

    async def retry_step(self, step_id: str) -> None:
        self.calls.append(("retry", step_id))

    async def resolve_gate(self, step_id: str, res: GateResolution) -> None:
        self.calls.append(("gate", (step_id, res)))

    async def recover(self) -> None:
        self.recovered = True

    async def shutdown(self) -> None:
        self.stopped = True


def _settings(tmp_path: Path) -> Settings:
    s = Settings(data_dir=tmp_path)
    for d in (s.blobs_dir, s.thumbs_dir, s.jobs_dir):
        d.mkdir(parents=True, exist_ok=True)
    return s


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 6), (0, 128, 255)).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
async def app_ctx(tmp_path: Path) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient, StubEngine]]:
    engine = StubEngine()
    app = build_app(_settings(tmp_path), TOKEN, engine=engine, load_plugins=False)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield app, client, engine
    assert engine.stopped


async def test_health_is_public(app_ctx) -> None:
    _, client, engine = app_ctx
    r = await client.get("/api/health")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert engine.recovered


async def test_auth_required(app_ctx) -> None:
    _, client, _ = app_ctx
    r = await client.get("/api/runs")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"
    r = await client.get("/api/runs", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401
    r = await client.get(f"/api/runs?t={TOKEN}")
    assert r.status_code == 401
    assert (await client.get("/api/runs", headers=AUTH)).json() == []


async def test_origin_check(app_ctx) -> None:
    _, client, _ = app_ctx
    r = await client.get("/api/health", headers={"Origin": "http://evil.example"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden_origin"
    r = await client.get("/api/runs", headers={**AUTH, "Origin": "app://studio"})
    assert r.status_code == 200


async def test_runs_gates_and_engine_errors(app_ctx) -> None:
    app, client, engine = app_ctx
    r = await client.post("/api/runs", json={"recipe_id": "concept-2d", "inputs": {"prompt": "cat"}}, headers=AUTH)
    assert r.json() == {"run_id": "run-1"}
    r = await client.post("/api/runs", json={"recipe_id": "nope", "inputs": {}}, headers=AUTH)
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    r = await client.post("/api/runs", json={"inputs": {}}, headers=AUTH)
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid"

    repo = app.state.repo
    await repo.create_run(RunRow(id="r1", recipe_id="concept-2d", recipe_hash="h", created_at=1.0))
    await repo.create_steps([StepRow(id="s1", run_id="r1", key="pick", kind="gate.pick", lane=Lane.NONE,
                                     status=StepStatus.AWAITING_HUMAN)])
    listed = (await client.get("/api/runs", headers=AUTH)).json()
    assert listed[0]["id"] == "r1" and listed[0]["preview_hash"] is None
    detail = (await client.get("/api/runs/r1", headers=AUTH)).json()
    assert detail["run"]["id"] == "r1" and detail["steps"][0]["key"] == "pick"
    assert (await client.get("/api/runs/zzz", headers=AUTH)).status_code == 404
    assert [s["id"] for s in (await client.get("/api/gates", headers=AUTH)).json()] == ["s1"]

    assert (await client.post("/api/gates/s1", json={"picked": ["a" * 64]}, headers=AUTH)).json() == {"ok": True}
    assert (await client.post("/api/runs/r1/cancel", headers=AUTH)).json() == {"ok": True}
    assert (await client.post("/api/steps/s1/retry", headers=AUTH)).json() == {"ok": True}
    assert [c[0] for c in engine.calls] == ["start", "gate", "cancel", "retry"]
    assert (await client.get("/api/recipes", headers=AUTH)).json()[0]["id"] == "concept-2d"
    assert (await client.get("/api/kinds", headers=AUTH)).json() == []
    assert (await client.get("/api/resources", headers=AUTH)).json() == []
    assert (await client.post("/api/resources/comfyui/start", headers=AUTH)).status_code == 404


async def test_blob_upload_download_and_versions(app_ctx) -> None:
    app, client, _ = app_ctx
    data = _png()
    r = await client.post("/api/blobs", files={"file": ("hero.png", data, "application/octet-stream")}, headers=AUTH)
    assert r.status_code == 201
    ref = BlobRef.model_validate(r.json())
    assert ref.mime == "image/png" and (ref.width, ref.height) == (8, 6) and ref.name == "hero.png"

    r = await client.get(f"/api/blobs/{ref.hash}?t={TOKEN}")
    assert r.status_code == 200 and r.content == data
    assert r.headers["content-type"] == "image/png" and "immutable" in r.headers["cache-control"]
    assert (await client.get(f"/api/blobs/{ref.hash}")).status_code == 401
    assert (await client.get(f"/api/blobs/{'0' * 64}", headers=AUTH)).status_code == 404
    assert (await client.get("/api/blobs/not-a-hash", headers=AUTH)).status_code == 400

    await app.state.repo.add_version(VersionRow(id="v1", asset_name="hero", run_id="r1", step_id="s1",
                                                blob=ref, created_at=1.0))
    r = await client.post("/api/versions/v1/approve", headers=AUTH)
    assert r.json()["status"] == "approved"
    assert (await client.get("/api/versions?status=approved", headers=AUTH)).json()[0]["id"] == "v1"
    assert (await client.post("/api/versions/zz/reject", headers=AUTH)).status_code == 404


def test_websocket_hello_and_resume(tmp_path: Path) -> None:
    app = build_app(_settings(tmp_path), TOKEN, engine=StubEngine(), load_plugins=False)
    with TestClient(app) as client:
        bus = app.state.bus
        bus.publish("run.created", {"id": "r1"}, run_id="r1")
        with client.websocket_connect(f"/ws?t={TOKEN}") as ws:
            assert ws.receive_json() == {"type": "hello", "seq": 1}
            ws.send_json({"op": "resume", "after_seq": 0})
            ev = ws.receive_json()
            assert ev["seq"] == 1 and ev["type"] == "run.created" and ev["run_id"] == "r1"
            client.portal.call(_publish, bus)
            ev = ws.receive_json()
            assert ev["seq"] == 2 and ev["type"] == "step.log"


async def _publish(bus: Any) -> None:
    bus.publish("step.log", {"msg": "hi"})


def test_websocket_rejects_bad_token(tmp_path: Path) -> None:
    from starlette.websockets import WebSocketDisconnect

    app = build_app(_settings(tmp_path), TOKEN, engine=StubEngine(), load_plugins=False)
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws?t=bad") as ws:
                ws.receive_json()
