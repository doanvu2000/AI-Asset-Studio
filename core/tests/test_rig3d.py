import asyncio
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from studio_core.adapters.blender import rig3d_math as rm
from studio_core.adapters.blender.rig3d import BlenderRigExportExecutor, file_stem, take_name
from studio_core.config import Settings
from studio_core.contracts import StepError
from studio_core.engine.library import validate
from studio_core.engine.recipe import load_recipes
from studio_core.export import godot, unity
from studio_core.export.rig3d import unity_model_meta
from studio_core.export.source import Source
from studio_core.store.bundle import engine_source
from test_export_engines import _Blobs, _row
from test_ops_executors import FakeCtx

RECIPES = Path(__file__).resolve().parents[1] / "studio_core" / "recipes"
JOINTS = ("head_top neck pelvis shoulder_l elbow_l wrist_l hand_tip_l shoulder_r elbow_r wrist_r hand_tip_r "
          "hip_l knee_l ankle_l foot_tip_l hip_r knee_r ankle_r foot_tip_r").split()


def test_rig_is_one_humanoid_tree() -> None:
    names = [b[0] for b in rm.RIG]
    roots = [b for b in rm.RIG if b[1] is None]
    assert roots == [("hips", None, "pelvis", "spine_a")]
    for i, (_, parent, _, _) in enumerate(rm.RIG):
        assert parent is None or parent in names[:i]
    assert set(rm.HUMANOID) == set(names)
    mapped = rm.renamed()
    assert set(rm.UNITY_REQUIRED) <= {b[0] for b in mapped}
    assert dict((b[0], b[1]) for b in mapped)["LeftUpperArm"] == "LeftShoulder"
    assert dict((b[0], b[1]) for b in mapped)["RightUpperLeg"] == "Hips"
    used = {j for b in rm.RIG for j in b[2:]}
    assert used - set(JOINTS) == {"spine_a", "spine_b", "neck_top"}


def test_humanoid_points_and_pose_rename() -> None:
    pts = {j: np.array([0.0, 0.0, float(i)]) for i, j in enumerate(JOINTS)}
    pts["pelvis"], pts["neck"], pts["head_top"] = np.zeros(3), np.array([0, 0, 1.0]), np.array([0, 0, 2.0])
    out = rm.humanoid_points(pts)
    assert out["spine_a"][2] == pytest.approx(0.12) and out["spine_b"][2] == pytest.approx(0.5)
    assert out["neck_top"][2] == pytest.approx(1.3) and "spine_a" not in pts
    pose = {"thigh_l": ("X", 10.0), "spine": {"X": 3.0}, "bogus": ("X", 1.0)}
    assert rm.rename_pose(pose) == {"LeftUpperLeg": ("X", 10.0), "Spine": {"X": 3.0}}


def test_clip_samples_close_loops_and_span_one_shots() -> None:
    walk = rm.clip_samples({"name": "walk", "frames": 8, "fps": 10, "loop": True}, 30)
    assert len(walk) == 25 and walk[0] == 0.0 and walk[-1] == 1.0
    attack = rm.clip_samples({"name": "attack", "frames": 8, "fps": 12, "loop": False}, 30)
    assert len(attack) == round(7 / 12 * 30) + 1 and attack[-1] == 1.0
    assert rm.preview_keys(25, True) == [0, 6, 12, 18]
    assert rm.preview_keys(19, False) == [0, 6, 12, 18]


def test_take_name_and_stem() -> None:
    assert take_name("walk", ["Armature|idle", "Armature|walk"]) == "walk"
    assert take_name("walk", ["Armature|Armature|walk"]) == "Armature|walk"
    assert take_name("run", []) == "run"
    assert file_stem("My Hero!") == "My_Hero" and file_stem("") == "character"


class FakeRigBlender:
    version = "5.2"

    def __init__(self, bones: list[str] | None = None):
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.bones = bones

    async def call(self, cmd: str, params: dict[str, Any], on_event: Any = None, timeout: float = 0) -> Any:
        self.calls.append((cmd, params))
        Path(params["out_glb"]).write_bytes(b"glTF")
        Path(params["out_fbx"]).write_bytes(b"Kaydara FBX Binary")
        previews = []
        for i, label in enumerate(["rest 0°", "rest 90°", *(f"{c['name']} · khung {k}" for c in params["clips"]
                                                           for k in (0, 4))]):
            f = Path(params["out_dir"]) / f"p{i}.png"
            Image.new("RGBA", (16, 16), (i * 20, 0, 0, 255)).save(f)
            previews.append({"file": str(f), "label": label})
        clips = [{**c, "first": 0, "last": 24, "keys": 25} for c in params["clips"]]
        bones = self.bones if self.bones is not None else [b[0] for b in rm.renamed()]
        return {"total_s": 1.0, "weights": "auto", "height": 1.8, "fps": params["fps"], "clips": clips,
                "bones": bones, "faces": 100, "previews": previews,
                "check": {"glb_bones": sorted(bones), "glb_animations": [c["name"] for c in clips],
                          "fbx_actions": [f"Armature|{c['name']}" for c in clips]}}


async def _inputs(ctx: FakeCtx) -> dict[str, Any]:
    mesh = await ctx.blobs.put_bytes(b"glb", name="m.glb", mime="model/gltf-binary")
    joints = await ctx.blobs.put_bytes(json.dumps({"joints": {j: [10, 20] for j in JOINTS}}).encode())
    front = await ctx.blobs.put_bytes(json.dumps({"pivot": [256, 480], "width": 512, "height": 512,
                                                  "ortho_scale": 1.3}).encode())
    return {"mesh": [mesh], "joints": [joints], "front": [front]}


def test_rig_export_executor_outputs(tmp_path: Path) -> None:
    async def go() -> None:
        ctx = FakeCtx(tmp_path)
        fake = FakeRigBlender()
        ex = BlenderRigExportExecutor(Settings(data_dir=tmp_path / "d"), fake)  # type: ignore[arg-type]
        inputs = await _inputs(ctx)
        res = await ex.run(ctx, {"anims": "idle, attack", "name": "Hero Knight", "fps": 24}, inputs)
        cmd, p = fake.calls[0]
        assert cmd == "rig_export" and p["fps"] == 24 and p["image_w"] == 512 and p["ortho_scale"] == 1.3
        assert [c["name"] for c in p["clips"]] == ["idle", "attack"] and p["res"] == 384
        assert res.outputs["meshes"][0].name == "Hero_Knight.glb"
        assert res.outputs["fbx"][0].name == "Hero_Knight.fbx"
        assert len(res.outputs["images"]) == 6
        with Image.open(ctx.blobs.path(res.outputs["preview"][0].hash)) as im:
            assert im.size == (64, 48)  # rest row + one row per clip, 4 columns
        rep = json.loads(ctx.blobs.path(res.outputs["report"][0].hash).read_bytes())
        assert rep["type"] == "asset_studio.rig3d" and rep["humanoid"] is True and rep["fps"] == 24
        assert rep["clips"][0] == {"name": "idle", "take": "idle", "first": 0, "last": 24, "loop": True,
                                   "sprite_fps": 6}
        assert rep["clips"][1]["loop"] is False
        default = await ex.run(ctx, {}, inputs)
        assert [c["name"] for c in fake.calls[-1][1]["clips"]] == ["idle", "walk", "run", "attack"]
        assert default.outputs["meshes"][0].name == "character.glb"
        bad = await ctx.blobs.put_bytes(b'{"joints": {"neck": [1, 2]}}')
        with pytest.raises(StepError, match="19-joint"):
            await ex.run(ctx, {}, {**inputs, "joints": [bad]})
        with pytest.raises(StepError) as e:
            await BlenderRigExportExecutor(Settings(data_dir=tmp_path / "d"), FakeRigBlender(["Hips"])).run(  # type: ignore[arg-type]
                ctx, {}, inputs)
        assert e.value.code == "blender_bad_output"

    asyncio.run(go())


def test_rig_recipes_load_and_lint() -> None:
    recipes = load_recipes(Settings().recipes_dir)
    for rid in ("character-3d-rigged", "character-3d-rigged-image"):
        _, issues = validate((RECIPES / f"{rid}.yaml").read_text(encoding="utf-8"), None)
        assert issues == [], rid
        steps = [s.kind for s in recipes[rid].steps]
        assert steps[-4:] == ["blender.render_dirs", "claude.task", "blender.rig_export", "gate.approve"]
    assert recipes["character-3d-rigged-image"].steps[0].kind == "comfy.img2mesh_trellis2_textured"


def _rig_report(b: _Blobs) -> Any:
    doc = {"type": "asset_studio.rig3d", "humanoid": True, "fps": 30, "glb": "hero.glb", "fbx": "hero.fbx",
           "clips": [{"name": "idle", "take": "idle", "first": 0, "last": 24, "loop": True},
                     {"name": "attack", "take": "attack", "first": 0, "last": 18, "loop": False}]}
    return b.put(json.dumps(doc).encode(), "rig3d.json", "application/json")


def test_engine_export_of_rigged_character(tmp_path: Path) -> None:
    b = _Blobs(tmp_path)
    outs = {"meshes": [b.put(b"glTF", "hero.glb", "model/gltf-binary")],
            "fbx": [b.put(b"FBX", "hero.fbx", "application/octet-stream")],
            "preview": [b.put(b"png", "hero_preview.png", "image/png")], "report": [_rig_report(b)]}
    src = engine_source(_row("approve", "gate.approve", outs), [], b, "Hero")
    assert [n for n, _ in src.fbx] == ["hero.fbx"] and src.rig and src.rig["humanoid"]

    u = dict(unity.export(src))
    assert "Models/hero.fbx" in u and "Models/hero.glb" not in u
    meta = u["Models/hero.fbx.meta"].decode()  # type: ignore[union-attr]
    assert "ModelImporter:" in meta and "animationType: 3" in meta and "avatarSetup: 1" in meta
    assert "takeName: idle" in meta and "lastFrame: 24" in meta
    assert meta.count("loopTime: 1") == 1 and meta.count("loopTime: 0") == 1
    assert "Humanoid" in u["README.txt"].decode()  # type: ignore[union-attr]
    again = dict(unity.export(src))["Models/hero.fbx.meta"]
    assert again == u["Models/hero.fbx.meta"]  # stable guid across re-exports

    g = dict(godot.export(src))
    assert "Models/hero.glb" in g and not any(k.endswith(".fbx") for k in g)
    assert "SkeletonProfileHumanoid" in g["README.txt"].decode()  # type: ignore[union-attr]


def test_unity_meta_generic_without_report() -> None:
    meta = unity_model_meta("0" * 32, None)
    assert "animationType: 2" in meta and "clipAnimations: []" in meta
    src = Source(asset="X", fbx=[("x.fbx", Path("x.fbx"))], glb=[("y.glb", Path("y.glb"))])
    files = dict(unity.export(src))
    assert "Models/x.fbx" in files and "Models/y.glb" in files
    assert "animationType: 2" in files["Models/x.fbx.meta"].decode()  # type: ignore[union-attr]
