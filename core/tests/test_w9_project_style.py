from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from studio_core.app import build_app
from studio_core.config import Settings
from studio_core.contracts import Lane, RunRow, RunStatus, StepRow, StepStatus, VersionRow, VersionStatus
from studio_core.ops.style_audit import REPORT_TYPE
from tests.test_project_qa import png, sprite

TOKEN = "tw9"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

STYLED = """
id: styled
inputs:
  image: {type: image, label: Ảnh style tham chiếu, default: null}
  prompt: {type: text}
  negative: {type: text, default: ""}
steps:
  - {id: ok, kind: gate.approve, params: {k: 0}}
"""


@pytest.fixture
async def app_client(tmp_path: Path) -> AsyncIterator[tuple[Any, httpx.AsyncClient]]:
    rdir = tmp_path / "builtin"
    rdir.mkdir()
    (rdir / "styled.yaml").write_text(STYLED, encoding="utf-8")
    settings = Settings(data_dir=tmp_path / "data", recipes_dir=rdir, comfy_workflows_dir=tmp_path / "none")
    for d in (settings.blobs_dir, settings.thumbs_dir, settings.jobs_dir):
        d.mkdir(parents=True, exist_ok=True)
    app = build_app(settings, TOKEN)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            yield app, c


async def _lock_run(app: Any, pid: str, anchor: str, status: StepStatus = StepStatus.SUCCEEDED) -> str:
    repo = app.state.repo
    report = {"type": REPORT_TYPE, "anchors": {"palette": ["#3caa46", "#2a6e30"]},
              "options": {"style": "soft painted forest, thin dark outline"}}
    ref = await app.state.blobs.put_bytes(json.dumps(report).encode(), name="style_lock.json", mime="application/json")
    now = time.time()
    run = RunRow(id=f"lock-{status.value}", project_id=pid, recipe_id="style-lock", recipe_hash="h",
                 inputs={"anchor": anchor}, status=RunStatus.SUCCEEDED, created_at=now)
    await repo.create_run(run)
    await repo.create_steps([StepRow(id=f"{run.id}-approve", run_id=run.id, key="approve", kind="gate.approve",
                                     lane=Lane.NONE, status=status, outputs={"report": [ref]})])
    return run.id


async def test_from_run_drift_restyle_and_apply(app_client: tuple[Any, httpx.AsyncClient]) -> None:
    app, c = app_client
    pid = (await c.post("/api/projects", json={"name": "Forest"}, headers=AUTH)).json()["id"]
    url = f"/api/projects/{pid}/style"
    anchor = await app.state.blobs.put_bytes(png(sprite((60, 170, 70))), name="anchor.png", mime="image/png")

    assert (await c.get(f"{url}/drift", headers=AUTH)).status_code == 409
    assert (await c.post(f"{url}/from-run", json={"run_id": "nope"}, headers=AUTH)).status_code == 404
    assert (await c.post("/api/projects/default/style/from-run", json={"run_id": "x"}, headers=AUTH)).status_code == 400

    other = (await c.post("/api/runs", json={"recipe_id": "styled", "inputs": {"prompt": "x"}, "project_id": pid},
                          headers=AUTH)).json()["run_id"]
    assert (await c.post(f"{url}/from-run", json={"run_id": other}, headers=AUTH)).status_code == 422

    pending = await _lock_run(app, pid, anchor.hash, StepStatus.PENDING)
    assert (await c.post(f"{url}/from-run", json={"run_id": pending}, headers=AUTH)).status_code == 409

    done = await _lock_run(app, pid, anchor.hash)
    r = await c.post(f"{url}/from-run", json={"run_id": done}, headers=AUTH)
    assert r.status_code == 200, r.text
    bible = r.json()["bible"]
    assert bible["refs"][0]["hash"] == anchor.hash and bible["palette"] == ["#3caa46", "#2a6e30"]
    assert bible["suffix"] == "soft painted forest, thin dark outline" and bible["active"] is True
    assert bible["lock"]["run_id"] == done and bible["lock"]["locked_at"] > 0

    run = (await c.post("/api/runs", json={"recipe_id": "styled", "inputs": {"prompt": "a chest"}, "project_id": pid},
                        headers=AUTH)).json()["run_id"]
    detail = (await c.get(f"/api/runs/{run}", headers=AUTH)).json()["run"]
    assert detail["inputs"]["image"] == anchor.hash
    assert detail["inputs"]["prompt"] == "a chest, soft painted forest, thin dark outline"
    assert detail["params"]["$meta"]["style_bible"]["version"] == bible["version"]

    for i, color in enumerate([(60, 170, 70), (62, 168, 72), (58, 172, 68), (220, 40, 200)]):
        ref = await app.state.blobs.put_bytes(png(sprite(color)), name=f"a{i}.png", mime="image/png")
        await app.state.repo.add_version(VersionRow(
            id=f"v{i}", project_id=pid, asset_name=f"asset {i}", run_id=run if i == 3 else f"r{i}", step_id="s",
            blob=ref, created_at=time.time() + i, status=VersionStatus.APPROVED))
    d = (await c.get(f"{url}/drift", params={"limit": 10}, headers=AUTH)).json()
    assert d["reference"]["run_id"] == done and len(d["assets"]) == 4
    assert d["assets"][0]["version_id"] == "v3" and d["assets"][0]["score"] < d["assets"][-1]["score"]
    again = (await c.get(f"{url}/drift", headers=AUTH)).json()
    assert [a["score"] for a in again["assets"]] == [a["score"] for a in d["assets"]]

    r = await c.post(f"{url}/restyle", json={"version_ids": ["v3", "v0", "ghost"], "run_ids": [done]}, headers=AUTH)
    assert r.status_code == 201, r.text
    out = r.json()
    assert [s["source_run_id"] for s in out["started"]] == [run]
    reasons = {s["id"]: s["reason"] for s in out["skipped"]}
    assert set(reasons) == {"ghost", "r0", done}
    new = (await c.get(f"/api/runs/{out['started'][0]['run_id']}", headers=AUTH)).json()["run"]
    assert new["project_id"] == pid and new["params"]["$meta"]["restyled_from"] == run
    assert new["inputs"]["image"] == anchor.hash and new["inputs"]["negative"] == ""

    assert new["inputs"]["prompt"] == "a chest, soft painted forest, thin dark outline"
    assert new["params"]["$meta"]["style_base"] == {"prompt": "a chest"}

    assert (await c.post(f"{url}/restyle", json={}, headers=AUTH)).status_code == 400


async def test_restyle_swaps_style_and_is_idempotent(app_client: tuple[Any, httpx.AsyncClient]) -> None:
    app, c = app_client
    pid = (await c.post("/api/projects", json={"name": "Swap", "style": {"prompt_prefix": "OLD", "prompt_suffix": "oldsfx"}},
                        headers=AUTH)).json()["id"]
    await app.state.repo.save_style_bible(pid, {"version": 1, "suffix": "bible sfx"})
    run = (await c.post("/api/runs", json={"recipe_id": "styled", "inputs": {"prompt": "a chest"}, "project_id": pid},
                        headers=AUTH)).json()["run_id"]
    got = (await c.get(f"/api/runs/{run}", headers=AUTH)).json()["run"]["inputs"]["prompt"]
    assert got == "OLD, a chest, oldsfx, bible sfx"

    async def restyle(source: str) -> dict[str, Any]:
        r = await c.post(f"/api/projects/{pid}/style/restyle", json={"run_ids": [source]}, headers=AUTH)
        assert r.status_code == 201, r.text
        rid = r.json()["started"][0]["run_id"]
        return (await c.get(f"/api/runs/{rid}", headers=AUTH)).json()["run"]

    # Old run recorded no base: the current style strings are cut off exactly, so an unchanged style does not double.
    first = await restyle(run)
    assert first["inputs"]["prompt"] == got
    assert first["params"]["$meta"]["style_base"] == {"prompt": "a chest"}

    await c.patch(f"/api/projects/{pid}", json={"style": {"prompt_prefix": "NEW", "prompt_suffix": "newsfx"}}, headers=AUTH)
    second = await restyle(first["id"])
    assert second["inputs"]["prompt"] == "NEW, a chest, newsfx, bible sfx"
    third = await restyle(second["id"])
    assert third["inputs"]["prompt"] == second["inputs"]["prompt"]

