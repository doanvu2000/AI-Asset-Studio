from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from test_api_basic import StubEngine

from studio_core import __version__
from studio_core.api.diagnostics import redact, tail_lines
from studio_core.app import build_app
from studio_core.config import Settings
from studio_core.contracts import Lane, ResourceHealth, RunRow, StepRow, StepStatus

TOKEN = "diag-secret-token-123"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class FakeResource:
    def __init__(self, name: str, fail: bool = False) -> None:
        self.name = name
        self.fail = fail

    async def health(self) -> ResourceHealth:
        if self.fail:
            raise RuntimeError("probe exploded")
        return ResourceHealth(name=self.name, status="down", detail={"managed": True})

    async def ensure_started(self) -> None:
        return None

    async def stop(self) -> None:
        return None


@pytest.fixture
async def ctx(tmp_path: Path) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient]]:
    s = Settings(data_dir=tmp_path, comfy_dir=tmp_path / "nope-comfy", blender_exe=tmp_path / "nope.exe")
    for d in (s.blobs_dir, s.thumbs_dir, s.jobs_dir):
        d.mkdir(parents=True, exist_ok=True)
    app = build_app(s, TOKEN, engine=StubEngine(), load_plugins=False)
    async with app.router.lifespan_context(app):
        app.state.resources = {"comfyui": FakeResource("comfyui"), "blender": FakeResource("blender", fail=True)}
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield app, client


async def test_requires_auth(ctx) -> None:
    _, client = ctx
    assert (await client.get("/api/diagnostics")).status_code == 401
    assert (await client.get(f"/api/diagnostics?t={TOKEN}")).status_code == 401


async def test_snapshot_shape(ctx, tmp_path: Path) -> None:
    app, client = ctx
    r = await client.get("/api/diagnostics", headers=AUTH)
    assert r.status_code == 200
    d = r.json()
    assert d["core"]["version"] == __version__
    assert d["core"]["frozen"] is False and d["core"]["python"]
    assert d["data_dir"] == str(tmp_path)
    comfy = next(t for t in d["tools"] if t["key"] == "comfy_dir")
    assert comfy["exists"] is False and comfy["path"].endswith("nope-comfy")
    by_name = {h["name"]: h for h in d["resources"]}
    assert by_name["comfyui"]["status"] == "down"
    assert by_name["blender"]["status"] == "error" and "probe exploded" in by_name["blender"]["detail"]["message"]
    assert d["counts"]["recipes"] == 1
    assert d["failed_steps"] == [] and d["logs"] == []


async def test_failed_steps_and_logs_are_redacted(ctx, tmp_path: Path) -> None:
    app, client = ctx
    repo = app.state.repo
    await repo.create_run(RunRow(id="r1", recipe_id="concept-2d", recipe_hash="h", created_at=1.0))
    await repo.create_steps([
        StepRow(id="s-ok", run_id="r1", key="a", kind="image.gen", lane=Lane.GPU, status=StepStatus.SUCCEEDED),
        StepRow(id="s-old", run_id="r1", key="b", kind="image.gen", lane=Lane.GPU, status=StepStatus.FAILED,
                ended_at=10.0, error={"code": "x", "message": "older"}),
        StepRow(id="s-new", run_id="r1", key="c", kind="comfy.flux", lane=Lane.GPU, status=StepStatus.FAILED,
                ended_at=20.0, error={"code": "comfy_error", "message": f"GET /x?t={TOKEN} failed"}),
    ])
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "comfyui.log").write_text(
        "\n".join([f"line {i}" for i in range(50)] + [f"auth Bearer {TOKEN}", "url http://h/a?t=abcdef&x=1",
                                                       'cfg {"token": "zzzzzzzzzz"}']), encoding="utf-8")
    (logs / "other.log").write_text("not exposed", encoding="utf-8")

    d = (await client.get("/api/diagnostics?lines=5&failed=10", headers=AUTH)).json()
    assert [f["step_id"] for f in d["failed_steps"]] == ["s-new", "s-old"]
    newest = d["failed_steps"][0]
    assert newest["code"] == "comfy_error" and newest["kind"] == "comfy.flux"
    assert TOKEN not in newest["message"] and "t=***" in newest["message"]

    assert [lg["name"] for lg in d["logs"]] == ["comfyui.log"]
    lines = d["logs"][0]["lines"]
    assert len(lines) == 5 and lines[0] == "line 48"
    text = "\n".join(lines)
    assert TOKEN not in text and "abcdef" not in text and "zzzzzzzzzz" not in text
    assert "Bearer ***" in text and "?t=***&x=1" in text


def test_redact_patterns() -> None:
    assert redact("STUDIO_TOKEN=abcdef123 rest") == "STUDIO_TOKEN=*** rest"
    assert redact("dev token: Zx9_abc-def") == "dev token: ***"
    assert redact("plain text 123") == "plain text 123"
    assert redact("mine secretvalue here", ("secretvalue",)) == "mine *** here"


def test_tail_lines_large_file(tmp_path: Path) -> None:
    p = tmp_path / "big.log"
    p.write_text("".join(f"{i:07d} {'x' * 90}\n" for i in range(5000)), encoding="utf-8")
    out = tail_lines(p, 3)
    assert [ln.split()[0] for ln in out] == ["0004997", "0004998", "0004999"]
    assert tail_lines(p, 0) == []
