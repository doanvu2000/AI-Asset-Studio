from __future__ import annotations

import io
import math
import re
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import yaml
from PIL import Image

from studio_core.engine.recipe import parse_recipe
from studio_core.export.spine_skeleton2d import Rig, godot_files
from studio_core.export.spine_unity2d import unity_files
from studio_core.ops.spine_pose import Pose
from studio_core.ops.spine_runtime import RegionImages

ATLAS = """page.png
size: 64,64
filter: Linear,Linear
pma: false
body
bounds: 0,0,16,16
cloth
bounds: 20,0,8,12
rotate: 90
cloth2
bounds: 40,0,10,10

page2.png
size: 16,16
filter: Linear,Linear
pma: false
hat
bounds: 0,0,6,6
"""
HIP_M = np.array([[0.0, -1.0], [1.0, 0.0]])
HIP_P, ARM_P = np.array([10.0, 20.0]), np.array([10.0, 50.0])
CLOTH_WORLD = [(0, 40), (16, 40), (16, 60), (0, 60)]


def weighted(points: list[tuple[float, float]]) -> list[float]:
    out: list[float] = []
    for x, y in points:
        pw = np.array([x, y], float)
        hip_local = np.linalg.inv(HIP_M) @ (pw - HIP_P)
        out += [2, 1, *hip_local, 0.5, 2, *(pw - ARM_P), 0.5]
    return [float(v) for v in out]


def cloth(path: str) -> dict[str, Any]:
    return {"type": "mesh", "path": path, "uvs": [0, 0, 1, 0, 1, 1, 0, 1], "triangles": [0, 1, 2, 0, 2, 3],
            "vertices": weighted(CLOTH_WORLD), "hull": 4}


def make_doc() -> dict[str, Any]:
    return {
        "skeleton": {"spine": "4.2.43"},
        "bones": [{"name": "root"},
                  {"name": "hip", "parent": "root", "x": 10, "y": 20, "rotation": 90, "length": 30},
                  {"name": "arm", "parent": "hip", "x": 30, "rotation": -90, "length": 20}],
        "slots": [{"name": "body", "bone": "hip", "attachment": "body"},
                  {"name": "cloth", "bone": "arm", "attachment": "cloth"},
                  {"name": "hat", "bone": "hip"}],
        "skins": [{"name": "default", "attachments": {
                      "body": {"body": {"x": 2, "y": 3, "width": 16, "height": 16}},
                      "cloth": {"cloth": cloth("cloth")},
                      "hat": {"hat": {"width": 6, "height": 6}}}},
                  {"name": "alt", "attachments": {"cloth": {"cloth": cloth("cloth2")}}}],
        "animations": {
            "idle": {"bones": {
                "hip": {"rotate": [{"time": 0, "value": 0}, {"time": 1, "value": 30, "curve": [0.25, 0, 0.75, 1]},
                                   {"time": 2, "value": 0}]},
                "arm": {"translate": [{"time": 0}, {"time": 1, "x": 5, "y": -2, "curve": "stepped"}, {"time": 2}]}}},
            "pop": {"slots": {"hat": {"attachment": [{"time": 0.5, "name": "hat"}, {"time": 1, "name": None}]}}},
            "empty": {}}}


@pytest.fixture()
def images(tmp_path: Path) -> RegionImages:
    page = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    page.paste((255, 0, 0, 255), (0, 0, 16, 16))
    page.paste((0, 255, 0, 255), (20, 0, 32, 8))
    page.paste((0, 0, 255, 255), (40, 0, 50, 10))
    page.save(tmp_path / "page.png")
    Image.new("RGBA", (16, 16), (255, 255, 0, 255)).save(tmp_path / "page2.png")
    atlas = tmp_path / "rig.atlas"
    atlas.write_text(ATLAS, encoding="utf-8")
    return RegionImages.from_files(atlas, [tmp_path / "page.png", tmp_path / "page2.png"])


def scene_of(rig: Rig, asset: str = "rig") -> tuple[str, dict[str, bytes | Path]]:
    files = dict(godot_files(rig, asset)[0])
    return files[f"{asset}.tscn"].decode("utf-8"), files  # type: ignore[union-attr]


def track(scene: str, path: str) -> tuple[list[float], list[str]]:
    m = re.search(r'tracks/(\d+)/path = NodePath\("%s"\)' % re.escape(path), scene)
    assert m, path
    k = re.search(r'tracks/%s/keys = \{\n"times": PackedFloat32Array\(([^)]*)\),\n"transitions": [^\n]*\n'
                  r'"update": \d,\n"values": \[(.*?)\]\n\}' % m[1], scene, re.S)
    assert k, path
    times = [float(x) for x in k[1].split(",")]
    vals = re.findall(r"Vector2\([^)]*\)|[-\w.]+", k[2])
    assert len(vals) == len(times)
    return times, vals


def floats(text: str) -> list[float]:
    return [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?(?:e[+-]?\d+)?", text)]


def unity_docs(text: str) -> dict[int, tuple[int, dict[str, Any]]]:
    parts = re.split(r"^--- !u!(\d+) &(-?\d+)\n", text, flags=re.M)
    return {int(parts[i + 1]): (int(parts[i]), yaml.safe_load(parts[i + 2])) for i in range(1, len(parts), 3)}


def test_setup_world_matches_hand_calc_and_the_spine_solver(images: RegionImages) -> None:
    doc = make_doc()
    rig = Rig(doc, images)
    hip, arm = rig.bones[1], rig.bones[2]
    assert np.allclose(hip.m, HIP_M, atol=1e-9) and np.allclose(hip.p, HIP_P)
    assert np.allclose(arm.m, np.eye(2), atol=1e-9) and np.allclose(arm.p, ARM_P)
    solved = Pose(doc).world
    for b in rig.bones:
        assert np.allclose(b.m, solved[b.name][0], atol=1e-9) and np.allclose(b.p, solved[b.name][1], atol=1e-9)


def test_region_corners_and_weighted_mesh_bind_to_the_setup_pose(images: RegionImages) -> None:
    rig = Rig(make_doc(), images)
    body, mesh = rig.parts[0], rig.parts[1]
    assert np.allclose(rig.geometry(body).world, [(-1, 14), (-1, 30), (15, 30), (15, 14)], atol=1e-9)
    assert np.allclose(rig.geometry(body).uv, [(0, 0), (16, 0), (16, 16), (0, 16)])
    g = rig.geometry(mesh)
    assert np.allclose(g.world, CLOTH_WORLD, atol=1e-9)
    assert np.allclose(g.uv, [(0, 0), (8, 0), (8, 12), (0, 12)])
    assert [sorted(b for b, _ in row) for row in g.weights] == [[1, 2]] * 4


def test_godot_scene_is_a_consistent_format3_tscn(images: RegionImages) -> None:
    scene, files = scene_of(Rig(make_doc(), images))
    assert scene.startswith("[gd_scene load_steps=") and "format=3]" in scene
    ext = set(re.findall(r'\[ext_resource [^\]]*id="([^"]+)"', scene))
    sub = set(re.findall(r'\[sub_resource [^\]]*id="([^"]+)"', scene))
    assert all(r in ext for r in re.findall(r'ExtResource\("([^"]+)"\)', scene))
    assert all(r in sub for r in re.findall(r'SubResource\("([^"]+)"\)', scene))
    assert int(re.match(r"\[gd_scene load_steps=(\d+)", scene)[1]) == len(ext) + len(sub) + 1
    paths = {"."}
    for name, parent in re.findall(r'\[node name="([^"]+)" type="\w+"(?: parent="([^"]*)")?\]', scene):
        assert parent == "" or parent in paths, (name, parent)
        paths.add(name if parent in ("", ".") else f"{parent}/{name}")
    assert {"Skeleton2D/root", "Skeleton2D/root/hip", "Skeleton2D/root/hip/arm", "body_body", "AnimationPlayer"} <= paths
    assert scene.count('type="Bone2D"') == 3 and scene.count('type="Polygon2D"') == 3
    for rel in re.findall(r'path="(images/[^"]+)"', scene):
        assert rel in files
    assert Image.open(io.BytesIO(files["images/cloth.png"])).size == (8, 12)  # type: ignore[arg-type]


def test_godot_bones_use_y_down_rest_transforms(images: RegionImages) -> None:
    scene, _ = scene_of(Rig(make_doc(), images))
    hip = scene.split('[node name="hip"')[1].split("\n\n")[0]
    assert "position = Vector2(10, -20)" in hip
    assert math.isclose(floats(re.search(r"rotation = (\S+)", hip)[1])[0], -math.pi / 2, abs_tol=1e-4)
    assert "rest = Transform2D(0, -1, 1, 0, 10, -20)" in hip
    assert "length = 30" in hip


def test_godot_polygons_carry_uv_bones_and_weights(images: RegionImages) -> None:
    scene, _ = scene_of(Rig(make_doc(), images))
    body = scene.split('[node name="body_body"')[1].split("\n\n")[0]
    assert floats(re.search(r"polygon = PackedVector2Array\(([^)]*)\)", body)[1]) == [-1, -14, -1, -30, 15, -30, 15, -14]
    assert floats(re.search(r"uv = PackedVector2Array\(([^)]*)\)", body)[1]) == [0, 0, 16, 0, 16, 16, 0, 16]
    assert 'skeleton = NodePath("../Skeleton2D")' in body
    assert 'bones = [NodePath("root/hip"), PackedFloat32Array(1, 1, 1, 1)]' in body
    mesh = scene.split('[node name="cloth_cloth"')[1].split("\n\n")[0]
    assert floats(re.search(r"polygon = PackedVector2Array\(([^)]*)\)", mesh)[1]) == [0, -40, 16, -40, 16, -60, 0, -60]
    assert "polygons = [PackedInt32Array(0, 1, 2), PackedInt32Array(0, 2, 3)]" in mesh
    assert 'NodePath("root/hip"), PackedFloat32Array(0.5, 0.5, 0.5, 0.5)' in mesh
    assert 'NodePath("root/hip/arm"), PackedFloat32Array(0.5, 0.5, 0.5, 0.5)' in mesh
    hat = scene.split('[node name="hat_hat"')[1].split("\n\n")[0]
    assert "visible = false" in hat


def test_godot_animation_rotation_keys_and_stepped_hold(images: RegionImages) -> None:
    scene, _ = scene_of(Rig(make_doc(), images))
    times, vals = track(scene, "Skeleton2D/root/hip:rotation")
    assert times[0] == 0 and times[-1] == 2 and len(times) > 30  # bezier segment is densified
    assert math.isclose(float(vals[0]), -math.radians(90), abs_tol=1e-4)
    assert math.isclose(float(vals[times.index(1.0)]), -math.radians(120), abs_tol=1e-4)
    ptimes, pvals = track(scene, "Skeleton2D/root/hip/arm:position")
    assert ptimes == [0, 1, 1.999, 2]
    assert pvals == ["Vector2(30, 0)", "Vector2(35, 2)", "Vector2(35, 2)", "Vector2(30, 0)"]


def test_godot_attachment_timeline_toggles_visibility(images: RegionImages) -> None:
    scene, _ = scene_of(Rig(make_doc(), images))
    pop = scene.split('resource_name = "pop"')[1].split("[sub_resource")[0]
    times, vals = track(pop, "hat_hat:visible")
    assert times == [0, 0.5, 1] and vals == ["false", "true", "false"]
    assert "Skeleton2D/" not in pop


def test_empty_animation_and_negative_scale_bone(images: RegionImages) -> None:
    scene, _ = scene_of(Rig(make_doc(), images))
    block = scene.split('resource_name = "empty"')[1].split("[sub_resource")[0]
    assert "length = 0.01" in block and "Skeleton2D/" not in block
    doc = make_doc()
    doc["bones"][0] |= {"x": 3, "y": 4, "scaleX": -1}
    doc["animations"] = {}
    neg, _ = scene_of(Rig(doc, images))
    assert "scale = Vector2(-1, 1)" in neg and "rest = Transform2D(-1, 0, 0, 1, 3, -4)" in neg
    assert "AnimationPlayer" not in neg


def test_skin_param_swaps_attachments_and_multi_page_regions_are_cut(images: RegionImages) -> None:
    base, alt = Rig(make_doc(), images), Rig(make_doc(), images, skin="alt")
    assert [p.image for p in base.parts] == ["body", "cloth", "hat"]
    assert [p.image for p in alt.parts] == ["body", "cloth2", "hat"]
    _, files = scene_of(alt)
    assert Image.open(io.BytesIO(files["images/cloth2.png"])).size == (10, 10)  # type: ignore[arg-type]
    hat = Image.open(io.BytesIO(files["images/hat.png"])).convert("RGBA")  # type: ignore[arg-type]
    assert hat.getpixel((0, 0)) == (255, 255, 0, 255)
    missing = Rig(make_doc(), images, skin="nope")
    assert any("nope" in w for w in missing.warnings)


def test_rotated_atlas_region_is_unrotated_in_the_png(images: RegionImages) -> None:
    img = images.get("cloth")
    assert img is not None and img.size == (8, 12)
    _, files = scene_of(Rig(make_doc(), images))
    assert Image.open(io.BytesIO(files["images/cloth.png"])).getpixel((4, 6)) == (0, 255, 0, 255)  # type: ignore[arg-type]


def test_bake_samples_every_frame_from_the_solver(images: RegionImages) -> None:
    doc = make_doc()
    del doc["animations"]["idle"]["bones"]["arm"]
    rig = Rig(doc, images, bake="on", fps=30)
    idle = rig.clips()[0]
    assert set(idle.bones) == {1}
    keys = idle.bones[1]["rotation"]
    assert len(keys) == 61
    assert math.isclose(keys[30][1][0], 120.0, abs_tol=1e-6) and math.isclose(keys[0][1][0], 90.0, abs_tol=1e-6)
    assert rig.bake
    doc["ik"] = [{"name": "x", "bones": ["hip", "arm"], "target": "root"}]
    assert any("not solved" in w for w in Rig(doc, images, bake="off").warnings)
    assert any("baked" in w for w in Rig(doc, images).warnings)


def test_unsupported_features_are_reported(images: RegionImages) -> None:
    doc = make_doc()
    doc["physics"] = [{"name": "p", "bone": "arm"}]
    doc["animations"]["idle"]["deform"] = {"default": {}}
    doc["bones"][2]["shearX"] = 10
    warns = " | ".join(Rig(doc, images).warnings)
    assert "physics" in warns and "deform" in warns and "shear" in warns


def test_unity_prefab_hierarchy_and_references(images: RegionImages) -> None:
    rig = Rig(make_doc(), images)
    files = dict(unity_files(rig, "rig", "uid1", 100.0)[0])
    docs = unity_docs(files["rig.prefab"].decode())  # type: ignore[union-attr]
    kinds = [c for c, _ in docs.values()]
    assert kinds.count(1) == 7 and kinds.count(4) == 7 and kinds.count(212) == 3 and kinds.count(95) == 1
    names = {d["GameObject"]["m_Name"] for c, d in docs.values() if c == 1}
    assert names == {"rig", "root", "hip", "arm", "body_body", "cloth_cloth", "hat_hat"}
    for c, d in docs.values():
        if c == 4:
            t = d["Transform"]
            assert t["m_Father"]["fileID"] in (0, *docs) and t["m_GameObject"]["fileID"] in docs
            assert all(k["fileID"] in docs for k in t["m_Children"])
        if c == 1:
            assert all(k["component"]["fileID"] in docs for k in d["GameObject"]["m_Component"])
    hip = next(d["GameObject"] for c, d in docs.values() if c == 1 and d["GameObject"]["m_Name"] == "hip")
    t = docs[hip["m_Component"][0]["component"]["fileID"]][1]["Transform"]
    assert math.isclose(t["m_LocalRotation"]["z"], math.sin(math.radians(45)), abs_tol=1e-4)
    assert math.isclose(t["m_LocalPosition"]["x"], 0.1) and math.isclose(t["m_LocalPosition"]["y"], 0.2)
    hat = next(d["GameObject"] for c, d in docs.values() if c == 1 and d["GameObject"]["m_Name"] == "hat_hat")
    assert hat["m_IsActive"] == 0
    for rel in [p for p in files if p.endswith((".png", ".anim", ".controller", ".prefab"))]:
        assert f"{rel}.meta" in files
    assert any("baked to a static sprite" in w for w in rig.warnings)
    mesh_png = next(v for k, v in files.items() if k.startswith("Textures/cloth_"))
    assert Image.open(io.BytesIO(mesh_png)).size == (16, 20)  # type: ignore[arg-type]


def test_unity_anim_curves_use_bone_paths_and_controller_links_clips(images: RegionImages) -> None:
    rig = Rig(make_doc(), images)
    files = dict(unity_files(rig, "rig", "uid1", 100.0)[0])
    clip = unity_docs(files["Animations/rig_idle.anim"].decode())[7400000][1]["AnimationClip"]  # type: ignore[union-attr]
    assert clip["m_Name"] == "idle" and clip["m_AnimationClipSettings"]["m_LoopTime"] == 1
    eul = {c["path"]: c["curve"]["m_Curve"] for c in clip["m_EulerCurves"]}
    assert set(eul) == {"root/hip"}
    by_time = {k["time"]: k["value"]["z"] for k in eul["root/hip"]}
    assert math.isclose(by_time[0], 90) and math.isclose(by_time[1], 120) and len(by_time) > 30
    pos = {c["path"]: c["curve"]["m_Curve"] for c in clip["m_PositionCurves"]}
    arm = {k["time"]: k for k in pos["root/hip/arm"]}
    assert math.isclose(arm[1]["value"]["x"], 0.35) and math.isclose(arm[1]["value"]["y"], 0.02 * -1)
    assert arm[1]["outSlope"]["x"] == "Infinity"
    active = [c for c in clip["m_FloatCurves"] if c["attribute"] == "m_IsActive"]
    assert [c["path"] for c in active] == ["root/hip/hat_hat"] and active[0]["classID"] == 1
    assert clip["m_AnimationClipSettings"]["m_StopTime"] == 2
    empty = unity_docs(files["Animations/rig_empty.anim"].decode())[7400000][1]["AnimationClip"]  # type: ignore[union-attr]
    assert empty["m_EulerCurves"] == [] and empty["m_AnimationClipSettings"]["m_StopTime"] == 0.01
    ctrl = unity_docs(files["rig.controller"].decode())  # type: ignore[union-attr]
    clip_guids = {re.search(r"guid: ([0-9a-f]{32})", files[f"{p}.meta"].decode())[1]  # type: ignore[union-attr]
                  for p in files if p.endswith(".anim")}
    used = {d["AnimatorState"]["m_Motion"]["guid"] for c, d in ctrl.values() if c == 1102}
    assert used == clip_guids and len(used) == 3


def test_recipe_yaml_parses() -> None:
    path = Path(__file__).parents[1] / "studio_core" / "recipes" / "spine-to-engine.yaml"
    r = parse_recipe(path.read_bytes(), path)
    assert r.id == "spine-to-engine" and [s.kind for s in r.steps] == ["op.spine_convert", "gate.approve"]
    assert {"godot", "unity", "report", "final"} <= set(r.outputs)
    assert r.inputs["atlas"]["siblings"] == "atlas_pages"
    assert r.inputs["bake"]["options"] == ["auto", "on", "off"]
