from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from studio_core.adapters.blender import fracture_math as fm
from studio_core.adapters.blender.fracture import REPORT_FILE, BlenderFractureExecutor, asset_stem
from studio_core.config import Settings
from studio_core.contracts import StepError
from studio_core.engine.kinds import CATALOG
from studio_core.engine.library import validate
from studio_core.engine.recipe import load_recipe_file
from test_ops_executors import FakeCtx

RECIPE = Path(__file__).resolve().parents[1] / "studio_core" / "recipes" / "prop-destructible.yaml"
PIECES = 8


def test_params_defaults_and_errors() -> None:
    p = fm.normalize_params({})
    assert (p["pieces"], p["pattern"], p["material"], p["density"]) == (16, "uniform", "wood", 600.0)
    assert p["impact"] == [0.5, 0.5, 1.0]
    assert fm.normalize_params({"material": "metal"})["metallic"] == 1.0
    assert fm.normalize_params({"material": "stone", "density": 1000})["density"] == 1000
    assert fm.parse_impact("0.2, 0.3 ,0.9") == (0.2, 0.3, 0.9)
    assert fm.parse_impact([1, 0, 1]) == (1.0, 0.0, 1.0)
    for bad in ({"pieces": 7}, {"pieces": 41}, {"pattern": "shatter"}, {"material": "cheese"}, {"impact": "up"},
                {"impact": "0.5,0.5,2"}, {"grain": "w"}, {"max_tris": 100}, {"explode": 5}):
        with pytest.raises(ValueError):
            fm.normalize_params(bad)


def _box_samples(n: int = 3000, size=(1.0, 1.0, 1.0)) -> np.ndarray:
    return np.random.default_rng(1).uniform((0, 0, 0), size, size=(n, 3))


def test_seeds_uniform_impact_planks() -> None:
    inside = _box_samples()
    uni = fm.place_seeds(inside, 16, "uniform", 0)
    assert uni.shape == (16, 3) and len({tuple(s) for s in uni}) == 16
    # Snapped to interior samples, so a concave mesh never gets a seed (and an empty cell) outside it.
    assert all(any(np.allclose(s, q) for q in inside) for s in uni)
    corner = np.array([1.0, 0.0, 1.0])
    hit = fm.place_seeds(inside, 16, "impact", 0, corner)
    assert np.linalg.norm(hit - corner, axis=1).mean() < 0.75 * np.linalg.norm(uni - corner, axis=1).mean()
    assert fm.place_seeds(inside, 16, "uniform", 0).tolist() == uni.tolist()
    with pytest.raises(ValueError, match="interior samples"):
        fm.place_seeds(inside[:5], 8, "uniform", 0)


def test_planks_metric_stretches_cells_along_grain() -> None:
    size = np.array([2.0, 0.5, 0.5])
    axis = fm.grain_axis("auto", size)
    assert axis == 0 and fm.grain_axis("z", size) == 2
    scale = fm.metric("planks", axis)
    assert scale.tolist() == [1 / fm.PLANK_STRETCH, 1, 1] and fm.metric("uniform", axis).tolist() == [1, 1, 1]
    a, b = np.array([0.2, 0.1, 0.1]), np.array([1.0, 0.4, 0.3])
    co, no = fm.bisector(a, b, scale)
    assert abs(np.linalg.norm(no) - 1) < 1e-9 and np.dot(b - a, no) > 0
    # Points on the plane are equidistant from both seeds in the stretched metric.
    t = np.cross(no, [0, 0, 1.0])
    p = co + 0.3 * t / np.linalg.norm(t)
    assert abs(np.linalg.norm((p - a) * scale) - np.linalg.norm((p - b) * scale)) < 1e-9
    seeds = fm.place_seeds(_box_samples(size=size), 12, "planks", 0, scale=scale)
    spread = seeds.std(0)
    assert spread[0] > 2.5 * spread[1]
    assert fm.cut_order(seeds, 0, scale)[0] != 0 and len(fm.cut_order(seeds, 0, scale)) == 11


def test_budget_mass_offsets_hull() -> None:
    assert fm.pre_cut_target(30000, 20000) == 16000 and fm.pre_cut_target(1000, 20000) is None
    nxt = fm.next_target(16000, 24000, 8000, 20000)
    assert nxt is not None and 9000 < nxt < 16000
    assert fm.next_target(16000, 19000, 4000, 20000) is None
    assert fm.masses([0.1, -0.0, 0.25], 600) == [60.0, 0.0, 150.0]
    assert fm.volume_error(1.0, [0.5, 0.49]) == pytest.approx(0.01)
    off = fm.exploded_offset(np.array([1.0, 0, 0.5]), np.zeros(3), 1.0, 0.5)
    assert off[0] == 0.5 and off[2] > 0.25
    pts = np.random.default_rng(0).normal(size=(100, 3))
    assert len(fm.hull_subset(pts)) == fm.MAX_HULL_POINTS and len(fm.hull_subset(pts[:10])) == 10
    assert fm.chunk_name("crate", 3) == "crate_chunk03" and asset_stem("Thùng gỗ!") == "Th_ng_g"


def _chunks(n: int = PIECES) -> list[dict[str, Any]]:
    cube = [[x, y, z] for x in (-0.1, 0.1) for y in (-0.1, 0.1) for z in (-0.1, 0.1)]
    return [{"name": f"crate_chunk{i:02d}", "volume": 0.125, "mass": 75.0, "tris": 40 + i, "cap_tris": 12,
             "closed": True, "method": "bisect", "center": [0.25 * (i % 2), 0.25 * (i // 2 % 2), 0.25 + 0.5 * (i // 4)],
             "hull": cube} for i in range(n)]


def fake_report(**kw: Any) -> dict[str, Any]:
    return {"asset": "crate", "params": fm.normalize_params({}), "source_tris": 2000, "pre_cut_tris": 2000,
            "decimated_to": None, "budget_attempts": [{"target": None, "tris": 404}], "remeshed": None,
            "intact_tris": 12, "intact_hull": [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], "total_volume": 1.0,
            "chunks_volume": 1.0, "volume_error": 0.0, "all_closed": True, "cap_tris": 96, "tris_total": 404,
            "grain_axis": "x", "impact_point": [0.5, 0.5, 1], "bbox_min": [0, 0, 0], "bbox_max": [1, 1, 1],
            "total_mass": 600.0, "chunks": _chunks(), "timing": {"total_s": 0.1}, **kw}


class FakeBlender:
    version = "fake"

    def __init__(self, report: dict[str, Any] | None = None) -> None:
        self.req: dict[str, Any] = {}
        self.report = report or fake_report()

    async def call(self, cmd: str, params: dict[str, Any], on_event=None, timeout: float = 0) -> dict[str, Any]:
        assert cmd == "script" and params["unrestricted"] is True
        self.req = params
        p = params["params"]
        for key in ("out_intact", "out_exploded"):
            Path(p[key]).write_bytes(b"glTF" + key.encode())
        for key in ("out_intact_fbx", "out_fractured_fbx"):
            Path(p[key]).write_bytes(b"Kaydara FBX Binary  \x00" + key.encode())
        Path(p["report"]).write_text(json.dumps(self.report), encoding="utf-8")
        Path(params["out"]).write_bytes(b"glTFfractured")
        return {"out": params["out"], "objects": [c["name"] for c in self.report["chunks"]], "stdout": "ok"}


async def _fracture(ctx: FakeCtx, tmp_path: Path, fake: FakeBlender | None = None, **params: Any):
    mesh = await ctx.blobs.put_bytes(b"glTFsrc", name="crate_clean.glb", mime="model/gltf-binary")
    tex = await ctx.blobs.put_bytes(b"png", name="cut.png", mime="image/png")
    fake = fake or FakeBlender()
    ex = BlenderFractureExecutor(Settings(data_dir=tmp_path / "d"), fake)  # type: ignore[arg-type]
    res = await ex.run(ctx, {"pieces": PIECES, "pattern": "planks", **params}, {"mesh": [mesh], "texture": [tex]})
    return fake, res


async def test_executor_stages_script_and_collects_outputs(tmp_path: Path) -> None:
    ctx = FakeCtx(tmp_path)
    fake, res = await _fracture(ctx, tmp_path, asset="Crate")
    p = fake.req["params"]
    assert "fracture_bpy.run(params)" in fake.req["code"] and Path(fake.req["glb_in"]).read_bytes() == b"glTFsrc"
    assert p["asset"] == "Crate" and p["pattern"] == "planks" and p["pieces"] == PIECES
    assert Path(p["texture"]).read_bytes() == b"png" and "_timeout_s" not in p
    assert [r.name for r in res.outputs["meshes"]] == ["Crate_fractured.glb", "Crate_intact.glb"]
    assert ctx.blobs.path(res.outputs["meshes"][0].hash).read_bytes() == b"glTFfractured"
    assert [r.name for r in res.outputs["exploded"]] == ["Crate_exploded.glb"]
    assert [r.name for r in res.outputs["fbx"]] == ["Crate_fractured.fbx", "Crate_intact.fbx"]
    rep = json.loads(ctx.blobs.path(res.outputs["report"][0].hash).read_bytes())
    assert res.outputs["report"][0].name == REPORT_FILE and rep["type"] == "asset_studio.destructible"
    assert rep["fractured_fbx"] == "Crate_fractured.fbx" and len(rep["chunks"]) == PIECES
    assert res.meta["pieces"] == PIECES and res.meta["warnings"] == [] and res.meta["total_mass"] == 600.0


async def test_executor_flags_volume_drift_and_rejects_params(tmp_path: Path) -> None:
    ctx = FakeCtx(tmp_path)
    bad = fake_report(volume_error=0.05, all_closed=False, tris_total=50000)
    bad["chunks"][2]["closed"] = False
    _, res = await _fracture(ctx, tmp_path, FakeBlender(bad))
    w = " ".join(res.meta["warnings"])
    assert "5.0%" in w and "crate_chunk02" in w and "50000" in w
    with pytest.raises(StepError, match="pieces"):
        await _fracture(ctx, tmp_path, pieces=3)
    with pytest.raises(StepError, match="exactly 1 mesh"):
        ex = BlenderFractureExecutor(Settings(data_dir=tmp_path / "d"), FakeBlender())  # type: ignore[arg-type]
        await ex.run(ctx, {}, {"mesh": []})


async def test_export_unity_and_godot(tmp_path: Path) -> None:
    from test_export_engines import _row

    from studio_core.export import godot, unity
    from studio_core.store.bundle import engine_ready, engine_source

    ctx = FakeCtx(tmp_path)
    _, res = await _fracture(ctx, tmp_path, asset="crate")
    step = _row("approve", "gate.approve", {"meshes": res.outputs["meshes"], "fbx": res.outputs["fbx"],
                                            "report": res.outputs["report"]})
    assert engine_ready(step, ctx.blobs)
    src = engine_source(step, [], ctx.blobs, "crate")
    assert src.destructible and len(src.destructible["chunks"]) == PIECES
    u = dict(unity.export(src))
    assert {"Models/crate_fractured.fbx", "Models/crate_fractured.fbx.meta", "Models/crate_intact.fbx",
            "crate_destructible.json", "Editor/AssetStudioDestructible.cs",
            "Destructible/AssetStudioSwapOnHit.cs", "Destructible/AssetStudioSwapOnHit.cs.meta"} <= set(u)
    assert not any(k.endswith(".glb") for k in u)
    doc = json.loads(u["crate_destructible.json"])
    assert doc["fractured_model"] == "Models/crate_fractured.fbx" and doc["total_mass"] == 600.0
    assert [c["name"] for c in doc["chunks"]] == [f"crate_chunk{i:02d}" for i in range(PIECES)]
    editor = u["Editor/AssetStudioDestructible.cs"].decode()
    assert "namespace AssetStudio" in editor and "convex = true" in editor and "_Fractured.prefab" in editor
    assert "AddComponent<T>" in editor and "??" not in editor
    assert "linearVelocity" in u["Destructible/AssetStudioSwapOnHit.cs"].decode()
    assert "IgnoreCollision" in u["Destructible/AssetStudioFracturedPieces.cs"].decode()
    assert "Ensure<AssetStudioFracturedPieces>(frac)" in editor
    metas = [b.decode() for k, b in u.items() if k.endswith(".cs.meta")]
    assert len({m.split("guid: ")[1][:32] for m in metas}) == len(metas) == 3
    readme = u["README.txt"].decode()
    assert "crate_Intact.prefab" in readme and "404 tam giác" in readme and "mô hình tĩnh" in readme

    g = dict(godot.export(src))
    assert {"Models/crate_fractured.glb", "Models/crate_intact.glb", "crate_fractured.tscn", "crate_intact.tscn",
            "asset_studio_fractured.gd", "asset_studio_destructible.gd"} <= set(g)
    assert not any(k.endswith(".fbx") for k in g)
    frac = g["crate_fractured.tscn"].decode()
    assert frac.count('type="RigidBody3D"') == PIECES and frac.count('type="CollisionShape3D"') == PIECES
    assert frac.count('type="ConvexPolygonShape3D"') == PIECES and 'path="Models/crate_fractured.glb"' in frac
    # Blender (x, y, z) -> Godot (x, z, -y): chunk 3 sits at x 0.25, y 0.25, z 0.25.
    assert re.search(r'name="crate_chunk03".*\ntransform = Transform3D\(1, 0, 0, 0, 1, 0, 0, 0, 1, 0\.25, 0\.25, -0\.25\)',
                     frac)
    assert "mass = 75" in frac
    intact = g["crate_intact.tscn"].decode()
    assert 'path="crate_fractured.tscn"' in intact and "fractured = ExtResource" in intact and "mass = 600" in intact
    assert "PackedVector3Array(0, 0, -0, 1, 0, -0, 0, 0, -1, 0, 1, -0)" in intact
    frac_gd = g["asset_studio_fractured.gd"].decode()
    assert "reparent(body, true)" in frac_gd and "add_collision_exception_with" in frac_gd
    assert "call_deferred" in g["asset_studio_destructible.gd"].decode()


def test_recipe_loads_and_lints() -> None:
    r = load_recipe_file(RECIPE)
    assert [s.id for s in r.steps] == ["cut_tex", "cut_seam", "fracture", "exploded", "approve"]
    _, issues = validate(RECIPE.read_text(encoding="utf-8"), None)
    assert issues == []
    spec = CATALOG["blender.fracture"]
    assert spec["in"] == {"mesh": "mesh", "texture": "image[]"} and spec["params"]["pieces"]["max"] == 40
