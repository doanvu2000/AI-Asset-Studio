import json
import random
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image, ImageDraw

from studio_core.adapters import style_metrics as sm
from studio_core.contracts import StepError
from studio_core.engine.kinds import CATALOG
from studio_core.engine.library import validate
from studio_core.engine.recipe import load_recipe_file
from studio_core.ops import style_audit as sa
from test_ops_executors import FakeCtx, load, put_img

RECIPES = Path(__file__).resolve().parents[1] / "studio_core" / "recipes"
WARM = [(214, 140, 60), (168, 96, 44), (240, 200, 110), (120, 70, 40)]
COOL = [(60, 120, 214), (44, 150, 168), (110, 90, 240), (40, 70, 120)]
SCHEMA = """
CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT);
CREATE TABLE runs (id TEXT PRIMARY KEY, project_id TEXT);
CREATE TABLE versions (id TEXT PRIMARY KEY, project_id TEXT, asset_name TEXT, run_id TEXT, step_id TEXT, blob TEXT,
                       status TEXT, created_at REAL, qa TEXT);
"""


def jitter(c, rng, amp=5):
    return tuple(int(min(255, max(0, v + rng.randint(-amp, amp)))) for v in c)


def asset(colors, outline, width, size=256, seed=0):
    rng = random.Random(seed)
    cols = [jitter(c, rng) for c in colors]
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    m = size // 10 + rng.randint(0, 6)
    d.ellipse((m, m, size - m, size - m), fill=cols[0], outline=outline, width=width)
    d.rectangle((size * 0.3, size * 0.3, size * 0.7, size * 0.55), fill=cols[1])
    d.ellipse((size * 0.35, size * 0.55, size * 0.65, size * 0.85), fill=cols[2])
    d.rectangle((size * 0.45, size * 0.2, size * 0.55, size * 0.3), fill=cols[3])
    return img


def warm_asset(seed, size=256):
    return asset(WARM, (50, 30, 20), 2, size, seed)


def cool_asset(seed, size=256):
    return asset(COOL, (10, 20, 40), 9, size, seed)


class Project:
    def __init__(self, tmp_path: Path):
        self.ctx = FakeCtx(tmp_path)
        self.db = tmp_path / "studio.db"
        con = sqlite3.connect(self.db)
        con.executescript(SCHEMA)
        con.execute("INSERT INTO projects VALUES ('p1', 'Demo')")
        con.execute("INSERT INTO runs VALUES ('r', 'p1')")
        con.commit()
        con.close()
        self.settings = SimpleNamespace(db_path=str(self.db))
        self.n = 0

    async def add(self, img, name, status="approved", mime="image/png", project="p1"):
        ref = await put_img(self.ctx, img, name) if img is not None else await self.ctx.blobs.put_bytes(
            b"glb" + name.encode(), name=name, mime=mime)
        blob = {"hash": ref.hash, "mime": mime, "size": ref.size, "name": name,
                "width": img.width if img else None, "height": img.height if img else None}
        self.n += 1
        con = sqlite3.connect(self.db)
        con.execute("INSERT INTO versions VALUES (?, ?, ?, ?, 's', ?, ?, ?, NULL)",
                    (f"v{self.n}", project, name, f"run{self.n}", json.dumps(blob), status, float(self.n)))
        con.commit()
        con.close()
        return f"v{self.n}"

    def rows(self):
        con = sqlite3.connect(self.db)
        try:
            return con.execute("SELECT id, status, blob FROM versions ORDER BY id").fetchall()
        finally:
            con.close()

    async def audit(self, anchor, **params):
        ref = await put_img(self.ctx, anchor, "anchor.png")
        op = sa.StyleAudit(self.settings)
        res = await op.run(self.ctx, {"clip": False, "project": "p1", **params}, {"anchors": [ref]})
        doc = json.loads(self.ctx.blobs.path(res.outputs["report"][0].hash).read_bytes())
        return res, doc


async def planted(tmp_path: Path, n_warm=12, n_cool=5):
    p = Project(tmp_path)
    for i in range(n_warm):
        await p.add(warm_asset(i + 1, 256 if i % 2 else 192), f"warm_{i:02d}.png")
    for i in range(n_cool):
        await p.add(cool_asset(100 + i), f"cool_{i:02d}.png")
    return p


@pytest.mark.asyncio
async def test_off_style_assets_rank_on_top_and_get_flagged(tmp_path):
    p = await planted(tmp_path)
    res, doc = await p.audit(warm_asset(999, 160))
    top = [a["asset_name"] for a in doc["assets"][:5]]
    assert sum(n.startswith("cool_") for n in top) >= 4
    flagged = {a["asset_name"] for a in doc["assets"] if a["flagged"]}
    assert sum(n.startswith("cool_") for n in flagged) >= 4
    assert sum(n.startswith("warm_") for n in flagged) <= 1
    assert [a["rank"] for a in doc["assets"]] == list(range(1, 18))
    assert doc["type"] == sa.REPORT_TYPE and "palette" in doc["metrics_used"]
    assert "CLIP" not in " ".join(doc["warnings"])
    assert res.outputs["preview"][0].mime == "image/png"


@pytest.mark.asyncio
async def test_outline_metric_separates_thick_from_thin(tmp_path):
    p = await planted(tmp_path, 6, 3)
    _, doc = await p.audit(warm_asset(999))
    by = {a["asset_name"]: a["metrics"]["outline"] for a in doc["assets"]}
    thin = [v for k, v in by.items() if k.startswith("warm_") and v is not None]
    thick = [v for k, v in by.items() if k.startswith("cool_") and v is not None]
    assert thin and thick and np.median(thick) > np.median(thin)


@pytest.mark.asyncio
async def test_lut_recolour_cuts_palette_delta_e_by_40_percent(tmp_path):
    p = await planted(tmp_path)
    res, doc = await p.audit(warm_asset(999, 160), recolor=True)
    fixed = [a for a in doc["assets"] if a["asset_name"].startswith("cool_") and a["fix"]["recolored"]]
    assert len(fixed) >= 4
    for a in fixed:
        assert a["delta_e_after"] <= 0.6 * a["metrics"]["palette_de"], a["asset_name"]
    names = {r.name for r in res.outputs["recolored"]}
    assert names == {a["fix"]["file"] for a in doc["assets"] if a["fix"]["recolored"]}
    out = load(p.ctx, res.outputs["recolored"][0])
    assert out.getpixel((0, 0))[3] == 0 and out.getchannel("A").getextrema()[1] == 255
    assert not res.outputs["restyle_in"] and json.loads(
        p.ctx.blobs.path(res.outputs["layout"][0].hash).read_bytes())["prompts"] == []


@pytest.mark.asyncio
async def test_restyle_is_queued_only_when_enabled_and_only_for_flagged(tmp_path):
    p = await planted(tmp_path)
    off, _ = await p.audit(warm_asset(999))
    assert off.outputs["restyle_in"] == []
    res, doc = await p.audit(warm_asset(999), restyle=True, style="soft cel shading")
    queued = [a for a in doc["assets"] if a["fix"]["restyle_queued"]]
    assert queued and all(a["flagged"] and "outline" in a["flags"] or "clip" in a["flags"] for a in queued)
    layout = json.loads(p.ctx.blobs.path(res.outputs["layout"][0].hash).read_bytes())
    assert len(layout["prompts"]) == len(res.outputs["restyle_in"]) == len(res.outputs["sources"]) == len(queued)
    assert all("soft cel shading" in t for t in layout["prompts"])
    assert any("thinner" in t for t in layout["prompts"])


@pytest.mark.asyncio
async def test_finish_makes_new_images_and_keeps_alpha(tmp_path):
    p = await planted(tmp_path)
    res, doc = await p.audit(warm_asset(999), restyle=True)
    assert res.outputs["sources"]
    before = p.rows()
    edits = []
    for i, src in enumerate(res.outputs["sources"]):
        edits.append(await put_img(p.ctx, Image.new("RGB", (64, 64), (90, 60, 30)), f"edit{i}.png"))
    fin = await sa.StyleLockFinish().run(p.ctx, {}, {
        "recolored": res.outputs["recolored"], "sources": res.outputs["sources"], "edits": edits,
        "report": res.outputs["report"], "layout": res.outputs["layout"]})
    assert len(fin.outputs["images"]) == len(res.outputs["recolored"]) + len(edits)
    restyled = load(p.ctx, fin.outputs["images"][-1])
    src = load(p.ctx, res.outputs["sources"][-1])
    assert restyled.size == src.size and restyled.getchannel("A").tobytes() == src.getchannel("A").tobytes()
    report = json.loads(p.ctx.blobs.path(fin.outputs["report"][0].hash).read_bytes())
    assert report["summary"]["restyled"] == len(edits)
    assert p.rows() == before


@pytest.mark.asyncio
async def test_empty_unknown_and_single_asset_projects(tmp_path):
    p = Project(tmp_path)
    anchor = warm_asset(1)
    res, doc = await p.audit(anchor)
    assert doc["assets"] == [] and doc["summary"]["total"] == 0 and doc["summary"]["median_palette_de"] is None
    assert any("no approved image versions" in w for w in doc["warnings"])
    assert res.outputs["recolored"] == res.outputs["restyle_in"] == [] and res.outputs["preview"]
    await p.add(cool_asset(2), "draft.png", status="draft")
    _, doc = await p.audit(anchor)
    assert doc["assets"] == [] and any("no approved image versions" in w for w in doc["warnings"])
    fin = await sa.StyleLockFinish().run(p.ctx, {}, {"report": res.outputs["report"], "layout": res.outputs["layout"]})
    assert fin.outputs["images"] == []
    with pytest.raises(StepError) as e:
        await p.audit(anchor, project="nope")
    assert e.value.code == "op_bad_params"
    await p.add(cool_asset(3), "only.png")
    _, doc = await p.audit(anchor)
    assert len(doc["assets"]) == 1 and not doc["assets"][0]["flagged"]
    assert any("need 3" in w for w in doc["warnings"])


@pytest.mark.asyncio
async def test_skips_transparent_mesh_rejected_sheet_and_other_projects(tmp_path):
    p = await planted(tmp_path, 4, 1)
    await p.add(Image.new("RGBA", (64, 64), (0, 0, 0, 0)), "ghost.png")
    await p.add(None, "model.glb", mime="model/gltf-binary")
    await p.add(cool_asset(7), "rejected.png", status="rejected")
    await p.add(cool_asset(8), "draft.png", status="draft")
    await p.add(warm_asset(9, 256), "hero_sheet.png")
    await p.add(cool_asset(10), "elsewhere.png", project="p2")
    before = p.rows()
    _, doc = await p.audit(warm_asset(999))
    names = {a["asset_name"] for a in doc["assets"]}
    assert len(names) == 5 and not names & {"ghost.png", "model.glb", "rejected.png", "draft.png", "elsewhere.png"}
    reasons = {s["asset_name"]: s["reason"] for s in doc["skipped"]}
    assert reasons["ghost.png"] == "empty image" and reasons["hero_sheet.png"] == "sprite sheet"
    assert doc["summary"]["non_image"] == 1
    assert p.rows() == before


@pytest.mark.asyncio
async def test_transparent_anchor_is_rejected_and_anchor_sizes_may_differ(tmp_path):
    p = await planted(tmp_path, 4, 1)
    with pytest.raises(StepError) as e:
        await p.audit(Image.new("RGBA", (32, 32), (0, 0, 0, 0)))
    assert e.value.code == "op_bad_input"
    _, doc = await p.audit(warm_asset(5, 64))
    assert doc["anchors"]["count"] == 1 and len(doc["anchors"]["palette"]) >= 3


class FakeClip:
    def available(self):
        return True

    def embed_images(self, images):
        v = np.array([[1.0, 0.0] if im.getpixel((im.width // 2, im.height // 3))[2] < 100 else [0.0, 1.0]
                      for im in images], dtype=np.float32)
        return v


@pytest.mark.asyncio
async def test_clip_metric_is_used_when_available_and_skipped_with_warning_otherwise(tmp_path):
    p = await planted(tmp_path, 6, 3)
    ref = await put_img(p.ctx, warm_asset(999), "anchor.png")
    op = sa.StyleAudit(p.settings, embedder=FakeClip())
    res = await op.run(p.ctx, {"project": "p1"}, {"anchors": [ref]})
    doc = json.loads(p.ctx.blobs.path(res.outputs["report"][0].hash).read_bytes())
    assert "clip" in doc["metrics_used"] and doc["assets"][0]["metrics"]["clip_dist"] is not None
    off = await sa.StyleAudit(p.settings).run(p.ctx, {"project": "p1"}, {"anchors": [ref]})
    doc = json.loads(p.ctx.blobs.path(off.outputs["report"][0].hash).read_bytes())
    assert "clip" not in doc["metrics_used"] and any("CLIP" in w for w in doc["warnings"])


def test_threshold_is_median_plus_k_mad():
    base = [1.0 + 0.1 * (i % 5) for i in range(20)]
    rows = [{"palette": v} for v in base] + [{"palette": 14.0}]
    ranked, limits = sm.rank(rows, k=3.0)
    spread = max(sm.MAD_SCALE * float(np.median(np.abs(np.array(base + [14.0]) - np.median(base + [14.0])))),
                 sm.SPREAD_FLOOR["palette"])
    med = float(np.median([r["palette"] for r in rows]))
    assert limits["palette"]["limit"] == pytest.approx(med + 3 * spread, abs=1e-3)
    assert ranked[-1]["flags"] == ["palette"] and not any(r["flags"] for r in ranked[:-1])
    assert ranked[-1]["score"] > max(r["score"] for r in ranked[:-1])


def test_uniform_set_flags_nothing_and_small_outliers_stay_below_the_absolute_floor():
    ranked, _ = sm.rank([{"palette": 2.0}] * 10 + [{"palette": 5.0}], k=3.0)
    assert not any(r["flags"] for r in ranked)
    ranked, limits = sm.rank([{"palette": 1.0}] * 2)
    assert limits == {} and ranked[0]["flags"] == []


def test_ciede2000_matches_published_pairs():
    pairs = [((50.0, 2.6772, -79.7751), (50.0, 0.0, -82.7485), 2.0425),
             ((50.0, 3.1571, -77.2803), (50.0, 0.0, -82.7485), 2.8615),
             ((60.2574, -34.0099, 36.2677), (60.4626, -34.1751, 39.4387), 1.2644)]
    for a, b, want in pairs:
        assert float(sm.ciede2000(np.array(a), np.array(b))) == pytest.approx(want, abs=1e-3)


def test_lab_round_trip_and_palette_distance_of_identical_sets_is_zero():
    rgb = np.random.default_rng(1).random((50, 3))
    assert np.abs(sm.lab_to_rgb(sm.rgb_to_lab(rgb)) - rgb).max() < 1e-3
    colors = np.array([[0.8, 0.5, 0.2], [0.1, 0.2, 0.3]])
    assert sm.palette_distance(colors, np.array([0.5, 0.5]), sm.rgb_to_lab(colors)) == pytest.approx(0, abs=1e-6)


def test_recipe_loads_and_lints():
    r = load_recipe_file(RECIPES / "style-lock.yaml")
    assert [s.id for s in r.steps] == ["audit", "edit", "finish", "approve"]
    assert r.inputs["restyle"]["default"] is False and r.inputs["anchor"]["type"] == "image"
    _, issues = validate((RECIPES / "style-lock.yaml").read_text(encoding="utf-8"), None)
    assert issues == []


@pytest.mark.skipif("op.style_audit" not in CATALOG or "op.style_lock_finish" not in CATALOG,
                    reason="catalog entries are added by the integrator")
def test_catalog_declares_the_ports_the_recipe_uses():
    audit, fin = CATALOG["op.style_audit"], CATALOG["op.style_lock_finish"]
    assert {"report", "preview", "recolored", "sources", "restyle_in", "layout"} <= set(audit["out"])
    assert "anchors" in audit["in"] and {"recolored", "sources", "edits", "report", "layout"} <= set(fin["in"])
    assert {"images", "report", "preview"} <= set(fin["out"])
