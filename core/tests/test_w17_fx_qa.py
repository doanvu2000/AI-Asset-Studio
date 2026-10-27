import asyncio
import io
import json
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path

import blake3
import httpx
import numpy as np
import pytest
from PIL import Image, ImageDraw

from studio_core.app import build_app
from studio_core.config import Settings
from studio_core.contracts import BlobRef, Lane, StepRow, StepStatus, VersionRow
from studio_core.engine.qa import QaContext, score_image
from studio_core.engine.recipe import load_recipe_file
from studio_core.ops.fx import FLAT_NORMAL, FxParams, SpriteFx, blob_geometry, sprite_fx
from studio_core.store.db import Database
from studio_core.store.repo import SqliteRepo

TOKEN = "t17"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
RECIPES = Path(__file__).resolve().parents[1] / "studio_core" / "recipes"


def _figure(size: int = 64, dx: int = 0, h: int = 40, color=(200, 60, 60)) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    x0 = size // 2 - 8 + dx
    d.rectangle((x0, size - 8 - h, x0 + 16, size - 8), fill=(*color, 255))
    return img


class FakeBlobs:
    def __init__(self, root: Path):
        self.root = root

    async def put_bytes(self, data: bytes, name: str | None = None, mime: str | None = None) -> BlobRef:
        h = blake3.blake3(data).hexdigest()
        (self.root / h).write_bytes(data)
        return BlobRef(hash=h, mime=mime or "application/octet-stream", size=len(data), name=name)

    def path(self, hash: str) -> Path:
        return self.root / hash


class FakeCtx:
    def __init__(self, root: Path):
        self.blobs = FakeBlobs(root)
        self.cancelled = asyncio.Event()

    def progress(self, *a, **k) -> None:
        pass


async def _png_ref(ctx: FakeCtx, img: Image.Image, name: str) -> BlobRef:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return await ctx.blobs.put_bytes(buf.getvalue(), name=name, mime="image/png")


def test_outline_keeps_canvas_and_grows_silhouette() -> None:
    frames = [_figure(), _figure(dx=3)]
    out = sprite_fx(frames, FxParams(outline=(0, 0, 0), width=2))
    for src, res in zip(frames, out):
        assert res.size == src.size
        a0, a1 = np.array(src)[..., 3], np.array(res)[..., 3]
        assert (a1 >= a0).all() and (a1 > 0).sum() > (a0 > 0).sum()
        ring = (a1 == 255) & (a0 == 0)
        assert (np.array(res)[ring][:, :3] == 0).all()


def test_blob_shadow_identical_across_frames_and_uses_pivot() -> None:
    frames = [_figure(h=40), _figure(h=30, dx=4)]
    fx = FxParams(shadow="blob")
    assert blob_geometry(frames, (32, 56), fx)[:2] == (32, 56)
    out = sprite_fx(frames, fx, (32, 56))
    # Below the feet only the shadow exists, and it must be the same in every frame.
    s0, s1 = np.array(out[0])[57:, :, 3], np.array(out[1])[57:, :, 3]
    assert s0.any() and np.array_equal(s0, s1)


def test_normal_frames_get_flat_outline_and_no_shadow() -> None:
    n = _figure(color=(100, 140, 230))
    res = np.array(sprite_fx([n], FxParams(shadow="drop", outline=(0, 0, 0), normal=True))[0])
    src = np.array(n)
    ring = (res[..., 3] == 255) & (src[..., 3] == 0)
    assert ring.any() and (res[ring][:, :3] == FLAT_NORMAL).all()
    assert ((res[..., 3] > 0) & (res[..., 3] < 255)).sum() == 0


def test_pixel_mode_has_hard_edges() -> None:
    res = np.array(sprite_fx([_figure()], FxParams(shadow="drop", shadow_opacity=0.5, outline=(255, 255, 255),
                                                   pixel=True, blur=3))[0])
    assert set(np.unique(res[..., 3]).tolist()) == {0, 255}
    shadow = (res[..., 3] == 255) & (res[..., :3] == 0).all(-1)
    assert 0 < shadow.sum()


def test_bad_params() -> None:
    with pytest.raises(ValueError):
        FxParams.from_params({"width": 9})
    with pytest.raises(ValueError):
        FxParams.from_params({"shadow": "giant"})
    with pytest.raises(ValueError):
        FxParams.from_params({"width": 0})
    assert FxParams.from_params({"width": None}).width == 1


async def test_op_passthrough_when_off_and_fx_when_on(tmp_path: Path) -> None:
    ctx = FakeCtx(tmp_path)
    refs = [await _png_ref(ctx, _figure(dx=i), f"dir_{i:03d}_down.png") for i in range(2)]
    op = SpriteFx()
    off = await op.run(ctx, {"shadow": "none", "outline": "none", "width": 1}, {"images": refs})
    assert off.outputs["images"] == refs
    pivot = await ctx.blobs.put_bytes(json.dumps({"pivot": [64, 112], "width": 128, "height": 128}).encode(),
                                      name="render.json", mime="application/json")
    on = await op.run(ctx, {"shadow": "blob", "outline": "#000000", "width": 1},
                      {"images": refs, "pivot": [pivot]})
    assert [r.name for r in on.outputs["images"]] == ["dir_000_down_fx.png", "dir_001_down_fx.png"]
    assert on.meta["fx"]["shadow"] == "blob"
    nrm = await op.run(ctx, {"outline": "#000000", "normal": True}, {"images": refs})
    assert nrm.outputs["images"][0].name.endswith("_fx_n.png") and nrm.meta["normal"]
    empty = await op.run(ctx, {"outline": "#000000", "optional": True}, {"images": []})
    assert empty.outputs["images"] == []


def test_sprite_recipes_have_default_off_fx_step() -> None:
    files = sorted(RECIPES.glob("sprite-8dir-*.yaml"))
    assert len(files) >= 10
    for p in files:
        r = load_recipe_file(p)
        assert r.inputs["fx_shadow"]["default"] == "none" and r.inputs["fx_outline"]["default"] == "none", p.name
        fx = [s for s in r.steps if s.kind == "op.sprite_fx"]
        assert fx, p.name
        for s in r.steps:
            if s.kind == "op.sheet":
                src = s.bindings["images"].parts[0].source
                assert r.step(src).kind == "op.sprite_fx", (p.name, s.id)
        if "pixel" in p.name:
            assert all(s.params.get("pixel") for s in fx)


def _sheet(frames: list[Image.Image]) -> tuple[Image.Image, dict]:
    fw, fh = frames[0].size
    sheet = Image.new("RGBA", (fw * len(frames), fh), (0, 0, 0, 0))
    for i, f in enumerate(frames):
        sheet.alpha_composite(f, (i * fw, 0))
    return sheet, {"frame_w": fw, "frame_h": fh, "cols": len(frames), "rows": 1, "count": len(frames),
                   "padding": 0, "pivot": [32, 56]}


def test_qa_clean_sheet_scores_high() -> None:
    sheet, meta = _sheet([_figure() for _ in range(8)])
    r = score_image(sheet, QaContext(meta=meta))
    assert r["score"] >= 95 and r["issues"] == []
    assert {p["key"] for p in r["parts"]} == {"empty", "alpha", "framing", "consistency"}


def test_qa_penalizes_empty_clipped_specks_and_drift() -> None:
    frames = [_figure() for _ in range(8)]
    frames[2] = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    frames[3] = _figure(h=60)
    noisy = np.array(frames[5])
    noisy[2:5:2, 2:60:6] = (255, 255, 255, 255)
    frames[5] = Image.fromarray(noisy)
    frames[6] = _figure(h=20)
    sheet, meta = _sheet(frames)
    r = score_image(sheet, QaContext(meta=meta))
    parts = {p["key"]: p for p in r["parts"]}
    assert r["score"] <= 70
    assert parts["empty"]["count"] == 1 and parts["framing"]["clipped"] >= 1
    assert parts["alpha"]["stray"] > 0 and parts["consistency"]["score"] < 90
    assert any("trống" in i for i in r["issues"])


def test_qa_blank_clip_slots_are_not_empty_frames() -> None:
    frames = [_figure() for _ in range(4)] + [Image.new("RGBA", (64, 64)) for _ in range(4)]
    sheet, meta = _sheet(frames)
    meta["frames"] = ["h"] * 4 + [None] * 4
    r = score_image(sheet, QaContext(meta=meta))
    assert r["cells"] == 4 and next(p for p in r["parts"] if p["key"] == "empty")["count"] == 0


def test_qa_palette_adherence_and_opaque_skip() -> None:
    sheet, meta = _sheet([_figure() for _ in range(4)])
    ok = score_image(sheet, QaContext(meta=meta, palette=[(200, 60, 60)]))
    bad = score_image(sheet, QaContext(meta=meta, palette=[(10, 10, 10)]))
    pal = lambda r: next(p for p in r["parts"] if p["key"] == "palette")["score"]
    assert pal(ok) == 100 and pal(bad) == 0 and bad["score"] < ok["score"]
    assert score_image(Image.new("RGB", (32, 32), (255, 255, 255)))["score"] is None


async def test_db_migration_adds_qa_column(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE versions (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, asset_name TEXT NOT NULL, "
                "run_id TEXT NOT NULL, step_id TEXT NOT NULL, blob TEXT NOT NULL, status TEXT NOT NULL, "
                "created_at REAL NOT NULL)")
    con.execute("INSERT INTO versions VALUES ('v0','default','a','r','s','{\"hash\":\"h\",\"mime\":\"image/png\","
                "\"size\":1}','draft',1.0)")
    con.commit()
    con.close()
    db = Database(path)
    await db.open()
    repo = SqliteRepo(db)
    try:
        assert (await repo.list_versions())[0].qa is None
        v = await repo.set_version_qa("v0", {"version": 1, "score": 77})
        assert v is not None and v.qa == {"version": 1, "score": 77}
    finally:
        await db.close()


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[tuple[object, httpx.AsyncClient]]:
    settings = Settings(data_dir=tmp_path / "data", comfy_workflows_dir=tmp_path / "no-comfy")
    for d in (settings.blobs_dir, settings.thumbs_dir, settings.jobs_dir):
        d.mkdir(parents=True, exist_ok=True)
    app = build_app(settings, TOKEN)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            yield app, c


async def test_recompute_endpoints(client) -> None:
    app, c = client
    sheet, meta = _sheet([_figure() for _ in range(8)])
    buf = io.BytesIO()
    sheet.save(buf, "PNG")
    sref = await app.state.blobs.put_bytes(buf.getvalue(), name="sheet.png", mime="image/png")
    mref = await app.state.blobs.put_bytes(json.dumps(meta).encode(), name="sheet.json", mime="application/json")
    repo = app.state.repo
    await repo.create_steps([StepRow(id="s1", run_id="r1", key="sheet", kind="op.sheet", lane=Lane.CPU,
                                     status=StepStatus.SUCCEEDED, outputs={"sheet": [sref], "meta": [mref]})])
    await repo.add_version(VersionRow(id="v1", asset_name="hero", run_id="r1", step_id="s1", blob=sref,
                                      created_at=1.0))
    await repo.add_version(VersionRow(id="v2", asset_name="hero", run_id="r1", step_id="s1", blob=mref,
                                      created_at=2.0))
    r = await c.post("/api/qa/recompute", json={"run_id": "r1"}, headers=AUTH)
    assert r.status_code == 200 and r.json() == {"updated": 1, "skipped": 0, "total": 1}
    listed = {v["id"]: v for v in (await c.get("/api/versions?run_id=r1", headers=AUTH)).json()}
    assert listed["v1"]["qa"]["score"] >= 95 and listed["v2"]["qa"] is None
    assert (await c.post("/api/qa/recompute", json={"run_id": "r1"}, headers=AUTH)).json()["updated"] == 0
    one = await c.post("/api/qa/versions/v1", headers=AUTH)
    assert one.status_code == 200 and one.json()["qa"]["cells"] == 8
    assert (await c.post("/api/qa/versions/nope", headers=AUTH)).status_code == 404


async def test_version_qa_builds_context_from_run_steps(tmp_path: Path) -> None:
    from studio_core.engine.qa import version_qa

    blobs = FakeBlobs(tmp_path)
    frames = sprite_fx([_figure() for _ in range(8)], FxParams(outline=(255, 255, 255), pixel=True))
    sheet, meta = _sheet(frames)
    buf = io.BytesIO()
    sheet.save(buf, "PNG")
    sref = await blobs.put_bytes(buf.getvalue(), name="sprite_sheet.png", mime="image/png")
    mref = await blobs.put_bytes(json.dumps(meta).encode(), name="sheet.json", mime="application/json")
    pref = await blobs.put_bytes(json.dumps({"colors": ["#c83c3c"], "outline": []}).encode(), name="palette.json",
                                 mime="application/json")
    steps = [StepRow(id="p", run_id="r", key="pixel", kind="op.pixel_art", lane=Lane.CPU, outputs={"palette": [pref]}),
             StepRow(id="f", run_id="r", key="fx", kind="op.sprite_fx", lane=Lane.CPU,
                     params={"shadow": "none", "outline": "#ffffff", "pixel": True}, deps=["pixel"]),
             StepRow(id="s", run_id="r", key="sheet", kind="op.sheet", lane=Lane.CPU, deps=["fx"],
                     outputs={"sheet": [sref], "meta": [mref]})]
    qa = await version_qa(blobs, steps, steps[2], sref)
    assert qa is not None and qa["pixel"] and qa["cells"] == 8
    assert next(p for p in qa["parts"] if p["key"] == "palette")["score"] == 100
    assert await version_qa(blobs, steps, steps[2], mref) is None


def test_qa_grid_layout_is_one_direction_per_cell() -> None:
    frames = [_figure(h=40) for _ in range(4)] + [_figure(h=22) for _ in range(4)]
    fw = 64
    sheet = Image.new("RGBA", (fw * 4, fw * 2))
    for i, f in enumerate(frames):
        sheet.alpha_composite(f, ((i % 4) * fw, (i // 4) * fw))
    meta = {"frame_w": fw, "frame_h": fw, "cols": 4, "rows": 2, "count": 8, "padding": 0, "pivot": [32, 56]}
    grid = next(p for p in score_image(sheet, QaContext(meta=meta))["parts"] if p["key"] == "consistency")
    assert grid["directions"] == 8 and grid["height_cv"] > 0.2
    rows = score_image(sheet, QaContext(meta={**meta, "row_angles": [0, 45]}))
    assert next(p for p in rows["parts"] if p["key"] == "consistency")["directions"] == 2


async def test_qa_follows_lineage_not_sibling_fx(tmp_path: Path) -> None:
    from studio_core.engine.qa import build_context

    blobs = FakeBlobs(tmp_path)
    ref = BlobRef(hash="h", mime="image/png", size=1, name="sheet.png")
    steps = [StepRow(id="a", run_id="r", key="fx", kind="op.sprite_fx", lane=Lane.CPU, params={"shadow": "blob"}),
             StepRow(id="b", run_id="r", key="anim_fx", kind="op.sprite_fx", lane=Lane.CPU,
                     params={"shadow": "none", "outline": "#ffffff"}),
             StepRow(id="c", run_id="r", key="sheet", kind="op.sheet", lane=Lane.CPU, deps=["fx"])]
    ctx = await build_context(blobs, steps, steps[2], ref)
    assert ctx.shadow and (255, 255, 255) not in ctx.extra_colors


async def test_recompute_reaches_old_unscored_and_keeps_scores(client) -> None:
    app, c = client
    repo = app.state.repo
    sheet, meta = _sheet([_figure() for _ in range(8)])
    buf = io.BytesIO()
    sheet.save(buf, "PNG")
    sref = await app.state.blobs.put_bytes(buf.getvalue(), name="sheet.png", mime="image/png")
    await repo.create_steps([StepRow(id="s1", run_id="r1", key="sheet", kind="op.sheet", lane=Lane.CPU,
                                     outputs={"sheet": [sref]})])
    for i in range(3):
        await repo.add_version(VersionRow(id=f"new{i}", asset_name="a", run_id="r1", step_id="s1", blob=sref,
                                          created_at=10.0 + i, qa={"version": 1, "score": 90}))
    await repo.add_version(VersionRow(id="old", asset_name="a", run_id="r1", step_id="s1", blob=sref, created_at=1.0))
    r = (await c.post("/api/qa/recompute", json={"limit": 1}, headers=AUTH)).json()
    assert r == {"updated": 1, "skipped": 0, "total": 1}
    broken = BlobRef(hash="0" * 64, mime="image/png", size=1, name="gone.png")
    await repo.add_version(VersionRow(id="gone", asset_name="a", run_id="r1", step_id="s1", blob=broken,
                                      created_at=2.0, qa={"version": 1, "score": 88}))
    r = (await c.post("/api/qa/recompute", json={"missing_only": False}, headers=AUTH)).json()
    assert r["skipped"] == 1 and r["updated"] == 4
    assert (await repo.get_version("gone")).qa == {"version": 1, "score": 88}
