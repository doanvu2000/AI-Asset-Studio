from __future__ import annotations

import io
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest
from PIL import Image

from studio_core.adapters.claude import ClaudeReply
from studio_core.adapters.project_qa import ProjectQaReviewer, notes_by_index
from studio_core.app import build_app
from studio_core.config import Settings
from studio_core.contracts import BlobRef, VersionRow, VersionStatus
from studio_core.engine import project_qa as pq

TOKEN = "tq"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

FIX = """
id: variants-2d
inputs:
  image: {type: image}
  instructions: {type: text}
  size: {type: int, default: 512, options: [128, 256, 512, 1024]}
steps:
  - {id: ok, kind: gate.approve, params: {k: 0}}
"""


def sprite(color: tuple[int, int, int], radius: float = 0.4, outline: int = 4, light: str = "left",
           size: int = 256) -> Image.Image:
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float64)
    c = size / 2
    d = np.hypot(xx - c, yy - c)
    r = radius * size
    grad = (xx - c) / r if light == "right" else (c - xx) / r
    shade = np.clip(0.75 + 0.25 * grad, 0.4, 1.0)
    rgb = np.clip(np.array(color)[None, None, :] * shade[..., None], 0, 255)
    alpha = (d <= r) * 255
    if outline:
        rgb[(d <= r) & (d > r - outline)] = (16, 12, 20)
    arr = np.dstack([rgb, alpha]).astype(np.uint8)
    return Image.fromarray(arr, "RGBA")


def png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def ver(vid: str, run: str, status: VersionStatus = VersionStatus.DRAFT, name: str = "a.png", w: int = 256,
        h: int = 256, t: float = 0.0, mime: str = "image/png") -> VersionRow:
    return VersionRow(id=vid, asset_name=vid, run_id=run, step_id="s", status=status, created_at=t,
                      blob=BlobRef(hash=vid * 4, mime=mime, size=1, width=w, height=h, name=name))


def test_measure_outline_light_and_fill() -> None:
    m = pq.measure(sprite((60, 180, 70), outline=5, light="left"), "x")
    assert m.ok and m.outlined and m.outline and m.outline > 2
    assert m.light is not None and pq._dir(m.light[0]) == "trái"
    assert 0.75 < (m.fill_h or 0) < 0.85
    bare = pq.measure(sprite((60, 180, 70), outline=0, light="right"), "y")
    assert not bare.outlined and bare.light is not None and pq._dir(bare.light[0]) == "phải"
    assert not pq.measure(Image.new("RGBA", (64, 64)), "z").ok


def test_evaluate_flags_the_odd_asset() -> None:
    imgs = {f"g{i}": sprite((60, 170 + i * 5, 70)) for i in range(4)}
    imgs["odd"] = sprite((220, 40, 40), radius=0.22, outline=0, light="right")
    versions = [ver(k, f"r{k}") for k in imgs]
    measures = [pq.measure(im, k) for k, im in imgs.items()]
    palette, source = pq.reference_palette(None, lambda h: None, measures)
    report = pq.evaluate(versions, measures, palette, source)
    by_id = {a["version_id"]: a for a in report["assets"]}
    odd = by_id["odd"]
    assert report["assets"][0]["version_id"] == "odd"
    assert {i["key"] for i in odd["issues"]} >= {"scale", "outline", "light"}
    assert all(by_id[f"g{i}"]["grade"] == "ok" for i in range(4))
    assert odd["score"] < min(by_id[f"g{i}"]["score"] for i in range(4))
    assert odd["fix"]["recipe_id"] == "variants-2d" and odd["fix"]["inputs"]["image"] == "odd" * 4
    assert "outline" in odd["fix"]["inputs"]["instructions"] and odd["fix"]["inputs"]["size"] == 256
    assert report["reference"]["light_label"] == "trái" and report["reference"]["outlined"] is True
    assert report["summary"]["flagged"] >= 1 and source


def test_palette_drift_against_project_palette() -> None:
    style = pq.ProjectStyle(pixel={"palette": "pico8"})
    imgs = {"in": sprite((0, 135, 81), outline=0), "out": sprite((150, 200, 120), outline=0)}
    measures = [pq.measure(im, k) for k, im in imgs.items()]
    palette, source = pq.reference_palette(style, lambda h: None, measures)
    assert source.endswith("pico8") and len(palette) == 16
    report = pq.evaluate([ver(k, k) for k in imgs], measures, palette, source)
    by_id = {a["version_id"]: a for a in report["assets"]}
    assert by_id["in"]["parts"]["palette"]["drift"] < by_id["out"]["parts"]["palette"]["drift"]


def test_pick_versions_prefers_approved_and_skips_sheets() -> None:
    vs = [ver("aa", "r1", t=1), ver("ab", "r1", VersionStatus.APPROVED, t=2),
          ver("ba", "r2", t=3), ver("bb", "r2", VersionStatus.REJECTED, t=4),
          ver("ca", "r3", name="sheet.png", t=5), ver("cb", "r3", w=2048, h=256, t=6),
          ver("da", "r4", mime="application/json", t=7)]
    picked = [v.id for v in pq.pick_versions(vs)]
    assert picked == ["ab", "ba"]
    assert [v.id for v in pq.pick_versions(vs, only_approved=True)] == ["ab"]


def test_contact_sheet_and_notes() -> None:
    data = pq.contact_sheet([sprite((1, 2, 3))] * 7, cell=64, cols=4)
    with Image.open(io.BytesIO(data)) as im:
        assert im.size == (256, 128)
    assert notes_by_index([{"i": 1, "note": "x"}, {"i": 9, "note": "y"}, {"i": 2, "note": " "}, "j"], 3) == {0: "x"}


@pytest.fixture
async def app_client(tmp_path: Path) -> AsyncIterator[tuple[Any, httpx.AsyncClient]]:
    rdir = tmp_path / "builtin"
    rdir.mkdir()
    (rdir / "variants-2d.yaml").write_text(FIX, encoding="utf-8")
    settings = Settings(data_dir=tmp_path / "data", recipes_dir=rdir, comfy_workflows_dir=tmp_path / "none")
    for d in (settings.blobs_dir, settings.thumbs_dir, settings.jobs_dir):
        d.mkdir(parents=True, exist_ok=True)
    app = build_app(settings, TOKEN)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            yield app, c


async def test_api_report_vision_and_fix(app_client: tuple[Any, httpx.AsyncClient], tmp_path: Path) -> None:
    app, c = app_client
    pid = (await c.post("/api/projects", json={"name": "Forest"}, headers=AUTH)).json()["id"]
    url = f"/api/projects/{pid}/qa"
    assert (await c.get(url, headers=AUTH)).json()["assets"] == []
    assert (await c.get("/api/projects/nope/qa", headers=AUTH)).status_code == 404

    imgs = [sprite((60, 170 + i * 5, 70)) for i in range(4)] + [sprite((220, 40, 40), 0.22, 0, "right")]
    ids = []
    for i, im in enumerate(imgs):
        ref = await app.state.blobs.put_bytes(png(im), name=f"a{i}.png", mime="image/png")
        vid = f"v{i}"
        ids.append(vid)
        await app.state.repo.add_version(VersionRow(id=vid, project_id=pid, asset_name=f"asset {i}", run_id=f"r{i}",
                                                    step_id="s", blob=ref, created_at=time.time() + i))

    seen: list[Any] = []

    async def query(content: list[dict[str, Any]], opts: Any) -> ClaudeReply:
        seen.append((content, opts))
        return ClaudeReply({"summary": "Bộ asset khá đồng bộ.", "assets": [{"i": 1, "note": "đỏ lạc tông"}]}, "",
                           {"cost_usd": 0.01, "model": opts.model})

    app.state.project_qa_reviewer = ProjectQaReviewer(tmp_path / "pqa", query)
    r = await c.post(url, json={"vision": True}, headers=AUTH)
    body = r.json()
    assert r.status_code == 200 and body["summary"]["total"] == 5
    assert body["assets"][0]["version_id"] == "v4" and body["assets"][0]["vision_note"] == "đỏ lạc tông"
    assert body["vision"]["summary"].startswith("Bộ asset") and seen[0][1].model == "claude-opus-5-5"
    assert seen[0][0][0]["type"] == "image"
    assert (await c.get(url, headers=AUTH)).json()["created_at"] == body["created_at"]

    r = await c.post(f"{url}/fix", json={"version_id": "v4"}, headers=AUTH)
    assert r.status_code == 201, r.text
    fix = r.json()
    assert fix["recipe_id"] == "variants-2d" and fix["inputs"]["image"] == body["assets"][0]["blob"]["hash"]
    assert "outline" in fix["inputs"]["instructions"] and fix["inputs"]["size"] == 256
    run = (await c.get(f"/api/runs/{fix['run_id']}", headers=AUTH)).json()["run"]
    assert run["project_id"] == pid and run["params"]["$meta"]["qa_version"] == "v4"

    ok_one = next(a["version_id"] for a in body["assets"] if not a["issues"])
    assert (await c.post(f"{url}/fix", json={"version_id": ok_one}, headers=AUTH)).status_code == 400
    r = await c.post(f"{url}/fix", json={"version_id": ok_one, "instructions": "warmer colors"}, headers=AUTH)
    assert r.status_code == 201 and r.json()["inputs"]["instructions"] == "warmer colors"
    assert (await c.post(f"{url}/fix", json={"version_id": "nope"}, headers=AUTH)).status_code == 404
