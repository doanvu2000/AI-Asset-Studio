import json
import sys
from pathlib import Path

import numpy as np
import pytest

from studio_core.adapters.blender import lod_math as lm
from studio_core.adapters.blender.lod import finish_report, report_warnings
from studio_core.export import lod as lod_export
from studio_core.export.source import Source

BLENDER_DIR = Path(lod_export.__file__).parents[1] / "adapters" / "blender"


def _cfg(**kw):
    return lm.normalize_params(kw)


def _doc(mode="hull", impostor=False, tris=(8000, 4000, 2000, 800)):
    lods = [{"level": i, "tris": t, "target": t, "ratio": round(t / tris[0], 4), "glb": f"rock_LOD{i}.glb",
             "fbx": f"rock_LOD{i}.fbx", "iou": {"min": 0.98, "views": [0.98] * 4}} for i, t in enumerate(tris)]
    raw = {"asset": "rock", "preset": "pc", "source_tris": tris[0], "lods": lods, "skipped": [], "warnings": [],
           "size": 2.0, "bounds_min": [-1, -1, 0], "bounds_max": [1, 1, 2], "smooth_normals": True,
           "impostor": ({"views": 8, "cols": 4, "res": 256, "ortho_scale": 3.6, "center": [0, 0, 1],
                         "elevation": 15.0, "frames": [], "atlas": "rock_impostor.png"} if impostor else None),
           "collision": {"mode": mode, "box": {"center": [0, 0, 1], "size": [2, 4, 6]}, "mesh_volume": 5.0,
                         "mesh_closed": True, "single_hull_ratio": 1.1, "volume_ratio": 1.1,
                         "hulls": [{"name": "rock_col_00", "points": [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]],
                                    "volume": 0.1}] if mode in ("hull", "multi_hull") else []}}
    doc = finish_report(raw, _cfg())
    doc["collision_glb"], doc["collision_fbx"] = "rock_collision.glb", "rock_collision.fbx"
    return doc


def _source(tmp_path, doc, atlas=True):
    names = [e["fbx"] for e in doc["lods"]] + ["rock_collision.fbx"]
    glbs = [e["glb"] for e in doc["lods"]] + ["rock_collision.glb"]
    src = Source(asset="rock", fbx=[(n, tmp_path / n) for n in names], glb=[(n, tmp_path / n) for n in glbs])
    src.lod = doc
    png = tmp_path / "rock_impostor.png"
    png.write_bytes(b"png")
    src.lod_files = {"impostor": png} if atlas else {}
    return src


def test_presets_and_param_validation():
    pc, mobile = _cfg(), _cfg(preset="mobile")
    assert pc["ratios"] == [1.0, 0.5, 0.25, 0.1] and pc["lod0_max_tris"] == 0
    assert mobile["lod0_max_tris"] == 5000 and mobile["heights"][0] > pc["heights"][0]
    assert _cfg(ratios="1, 0.6, 0.3")["ratios"] == [1.0, 0.6, 0.3]
    for bad in ({"preset": "console"}, {"ratios": "1,1.5"}, {"ratios": "1,0.5,0.6"}, {"targets": "100,200"},
                {"collision": "sphere"}, {"hulls": 1}, {"impostor_res": 100}):
        with pytest.raises(ValueError):
            _cfg(**bad)


def test_lod0_cap_zero_means_preset():
    assert _cfg(preset="mobile", lod0_max_tris=0)["lod0_max_tris"] == 5000
    assert _cfg(preset="mobile", lod0_max_tris="")["lod0_max_tris"] == 5000
    assert _cfg(preset="mobile", lod0_max_tris=3000)["lod0_max_tris"] == 3000
    assert _cfg(lod0_max_tris=0)["lod0_max_tris"] == 0


def test_plan_ratios_targets_and_mobile_cap():
    kept, skipped = lm.plan_lods(10000, _cfg())
    assert [k["target"] for k in kept] == [10000, 5000, 2500, 1000] and not skipped
    kept, _ = lm.plan_lods(10000, _cfg(targets="8000, 3000, 900"))
    assert [k["target"] for k in kept] == [8000, 3000, 900]
    kept, _ = lm.plan_lods(20000, _cfg(preset="mobile"))
    assert [k["target"] for k in kept] == [5000, 2500, 1250, 500]


def test_plan_drops_lods_a_light_mesh_cannot_reach():
    kept, skipped = lm.plan_lods(100, _cfg())
    assert [k["target"] for k in kept] == [100, 50]
    assert {s["target"] for s in skipped} == {25, 10}
    kept, skipped = lm.plan_lods(40, _cfg())
    assert len(kept) == 1 and len(skipped) == 3


def test_keep_lod_rejects_barely_lighter_or_tiny():
    assert lm.keep_lod(1000, 500)
    assert not lm.keep_lod(1000, 900)
    assert not lm.keep_lod(1000, 20)


def test_transition_heights_decrease_for_any_count():
    for count in (2, 4, 5, 6):
        h = lm.transition_heights(count, _cfg())
        assert len(h) == count and all(a > b for a, b in zip(h, h[1:])) and 0 < h[-1] < h[0] < 1


def test_lod_distances_follow_screen_height():
    d = lm.lod_distances(2.0, [0.5, 0.25])
    assert d[0] == pytest.approx(3.464, abs=1e-3) and d[1] == pytest.approx(2 * d[0], rel=1e-3)


def test_collision_pick_and_volume_ratio():
    assert lm.pick_collision("auto", 1.2) == "hull"
    assert lm.pick_collision("auto", 2.5) == "multi_hull"
    assert lm.pick_collision("auto", None) == "hull"
    assert lm.pick_collision("box", 9) == "box"
    assert lm.volume_ratio(7, 5) == 1.4 and lm.volume_ratio(1, 0) is None


def test_mask_iou():
    a = np.zeros((4, 4), bool)
    a[:2] = True
    b = np.zeros((4, 4), bool)
    b[1:3] = True
    assert lm.mask_iou(a, a) == 1.0 and lm.mask_iou(a, b) == pytest.approx(1 / 3, abs=1e-3)
    assert lm.mask_iou(np.zeros((2, 2)), np.zeros((2, 2))) == 1.0


def test_cluster_points_separates_blobs():
    rng = np.random.default_rng(1)
    pts = np.concatenate([rng.normal(0, 0.1, (60, 3)), rng.normal(5, 0.1, (60, 3))])
    lab = lm.cluster_points(pts, 2, 0.25)
    assert len(set(lab[:60])) == 1 and len(set(lab[60:])) == 1 and lab[0] != lab[-1]


def test_report_warnings_flag_iou_ratio_and_single_lod():
    doc = _doc()
    doc["lods"][2]["iou"]["min"] = 0.9
    doc["collision"]["volume_ratio"] = 1.9
    text = " | ".join(report_warnings(doc))
    assert "LOD2" in text and "1.90x" in text
    solo = _doc(tris=(30,))
    assert any("chỉ có LOD0" in w for w in report_warnings(solo))


def test_unity_json_has_decreasing_heights_and_models(tmp_path):
    files, notes = lod_export.unity_files(_source(tmp_path, _doc(impostor=True)))
    by = dict(files)
    doc = json.loads(by["rock_lod.json"])
    heights = [e["height"] for e in doc["lods"]]
    assert [e["model"] for e in doc["lods"]][0] == "Models/rock_LOD0.fbx"
    assert heights == sorted(heights, reverse=True) and doc["impostor"]["height"] < heights[-1]
    assert doc["collision"] == {"mode": "hull", "model": "Models/rock_collision.fbx"}
    assert "Lod/rock_impostor.png" in by and f"Lod/{lod_export.IMPOSTOR_FILE}" in by
    editor = by[f"Editor/{lod_export.EDITOR_FILE}"].decode()
    assert "LODGroup" in editor and "SetLODs" in editor and "convex = true" in editor
    assert lod_export.EDITOR_GUID in by[f"Editor/{lod_export.EDITOR_FILE}.meta"].decode()
    assert len(notes) == 1 and "4 LOD" in notes[0]


def test_unity_box_mode_and_no_impostor_file_without_atlas(tmp_path):
    files, _ = lod_export.unity_files(_source(tmp_path, _doc(mode="box"), atlas=False))
    names = [n for n, _ in files]
    doc = json.loads(dict(files)["rock_lod.json"])
    assert doc["collision"] == {"mode": "box", "model": ""} and "impostor" not in doc
    assert not any(n.endswith(".png") for n in names)


def test_unity_skips_when_a_lod_model_is_missing(tmp_path):
    src = _source(tmp_path, _doc())
    src.fbx = [f for f in src.fbx if "LOD2" not in f[0]]
    assert lod_export.unity_files(src) == ([], [])


def test_godot_scene_ranges_shapes_and_impostor(tmp_path):
    files, _ = lod_export.godot_files(_source(tmp_path, _doc(mode="multi_hull", impostor=True)))
    by = {n: (v.decode() if isinstance(v, bytes) else v) for n, v in files}
    scene = by["rock_lod.tscn"]
    assert 'instance=ExtResource("5_lod3")' in scene and 'path="Models/rock_LOD3.glb"' in scene
    assert "ConvexPolygonShape3D" in scene and "visibility_range_begin" in scene and 'type="Sprite3D"' in scene
    assert "ranges = PackedVector2Array(0," in scene
    assert "visibility_range_begin" in by[lod_export.GODOT_SCRIPT] and "GeometryInstance3D" in by[lod_export.GODOT_SCRIPT]
    doc = _doc(mode="multi_hull", impostor=True)
    dist = doc["distances_m"]
    assert f"visibility_range_begin = {dist[3]:.5g}" in scene


def test_godot_box_swaps_axes_and_none_has_no_collision(tmp_path):
    box = lod_export.godot_files(_source(tmp_path, _doc(mode="box")))[0]
    scene = dict(box)["rock_lod.tscn"].decode()
    assert "BoxShape3D" in scene and "size = Vector3(2, 6, 4)" in scene
    none = lod_export.godot_files(_source(tmp_path, _doc(mode="none")))[0]
    assert "CollisionShape3D" not in dict(none)["rock_lod.tscn"].decode()


def test_bpy_hull_of_cube_points():
    pytest.importorskip("bpy")
    sys.path.insert(0, str(BLENDER_DIR))
    import lod_bpy

    pts = [(x, y, z) for x in (0, 1) for y in (0, 1) for z in (0, 1)] + [(0.5, 0.5, 0.5)]
    bm, hull_pts, volume = lod_bpy._hull(pts)
    bm.free()
    assert len(hull_pts) == 8 and volume == pytest.approx(1.0)
