from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image
from test_engine_runner import Env
from test_ops_executors import FakeCtx

from studio_core.contracts import RunStatus, StepError
from studio_core.engine.kinds import CATALOG
from studio_core.engine.library import validate
from studio_core.ops import spine as sp
from studio_core.ops import spine_physics as ph
from studio_core.ops.spine_pose import Pose
from studio_core.ops.spine_runtime import mesh_world

ROOT = Path(__file__).resolve().parents[2]
RECIPES = ROOT / "core" / "studio_core" / "recipes"
SPINE_CORE = ROOT / "app" / "node_modules" / "@esotericsoftware" / "spine-core" / "dist" / "index.js"

JOINTS = {"head_top": (100, 20), "neck": (100, 60), "pelvis": (100, 150), "shoulder_l": (130, 65),
          "elbow_l": (160, 65), "wrist_l": (185, 65), "hand_tip_l": (200, 65), "shoulder_r": (70, 65),
          "elbow_r": (40, 65), "wrist_r": (15, 65), "hand_tip_r": (2, 65), "hip_l": (115, 150), "knee_l": (115, 200),
          "ankle_l": (115, 245), "foot_tip_l": (125, 250), "hip_r": (85, 150), "knee_r": (85, 200),
          "ankle_r": (85, 245), "foot_tip_r": (75, 250)}
BOXES = {"cape": (70, 60, 130, 230, (150, 40, 60)), "torso": (80, 55, 120, 155, (60, 90, 160)),
         "head": (82, 18, 118, 62, (240, 200, 160)), "upper_arm_l": (128, 58, 162, 72, (60, 90, 160)),
         "upper_arm_r": (38, 58, 72, 72, (60, 90, 160)), "thigh_l": (106, 148, 124, 202, (50, 50, 60)),
         "thigh_r": (76, 148, 94, 202, (50, 50, 60))}


def rig_layers() -> list[sp.Layer]:
    out = []
    for name, (x0, y0, x1, y1, rgb) in BOXES.items():
        out.append(sp.Layer(name, Image.new("RGBA", (x1 - x0, y1 - y0), (*rgb, 255)), (x0, y0)))
    return out


def auto_rig(with_anim: bool = True) -> dict[str, Any]:
    doc, _ = sp.build_skeleton(rig_layers(), JOINTS, (100, 255), "hero", with_anim)
    return doc


class Regions:
    def __init__(self) -> None:
        self.imgs = {layer.name: layer.image.convert("RGBa") for layer in rig_layers()}

    def get(self, name: str) -> Image.Image | None:
        return self.imgs.get(name)


def test_classify_and_bone_chains() -> None:
    assert ph.classify("hair_front3") == "hair" and ph.classify("Cape") == "cloth"
    assert ph.classify("tail2") == "tail" and ph.classify("earL") == "ear" and ph.classify("ponyTail1") == "hair"
    assert ph.classify("spear") is None and ph.classify("torso") is None and ph.classify("heart") is None
    doc = {"skeleton": {"spine": "4.2.43"}, "bones": [
        {"name": "root"}, {"name": "head", "parent": "root", "length": 40},
        {"name": "hair", "parent": "head"},
        {"name": "hair1", "parent": "hair", "length": 30}, {"name": "hair2", "parent": "hair1", "length": 30},
        {"name": "hair_b1", "parent": "hair", "length": 30},
        {"name": "tail1", "parent": "root", "length": 20}, {"name": "tail2", "parent": "tail1", "length": 20},
        {"name": "rope", "parent": "root", "length": 20}],
        "ik": [{"name": "k", "bones": ["rope"], "target": "head"}]}
    chains = {tuple(c): k for c, k in ph.bone_chains(doc)}
    assert chains == {("hair1", "hair2"): "hair", ("hair_b1",): "hair", ("tail1", "tail2"): "tail"}
    assert {tuple(c) for c, _ in ph.bone_chains(doc, ["tail"])} == {("tail1", "tail2")}


def test_cape_becomes_weighted_chain_with_physics() -> None:
    doc = auto_rig()
    assert ph.add_physics(doc)[1]["chains"][0]["bones"][-1] == "cape_phys4"
    new, rep = ph.add_physics(doc, segments=2)
    assert [c["bones"] for c in rep["chains"]] == [["cape_phys1", "cape_phys2"]]
    assert rep["meshed"] == [["default", "cape", "cape"]] and rep["constraints"] == 2
    names = [b["name"] for b in new["bones"]]
    assert names[:len(doc["bones"])] == [b["name"] for b in doc["bones"]]
    assert ph.validate_physics(new) == [] and ph._mesh_report(new, rep["meshed"]) == []
    c0 = new["physics"][0]
    assert c0["bone"] == "cape_phys1" and c0["rotate"] == 1 and 0 <= c0["inertia"] <= 1 and c0["mass"] > 0
    assert [c["order"] for c in new["physics"]] == [0, 1]
    # At rest the mesh sits exactly where the region was.
    pose = Pose(new)
    att = new["skins"][0]["attachments"]["cape"]["cape"]
    pts = mesh_world(att, pose, "torso")
    assert pts[:, 0].min() == pytest.approx(70 - 100, abs=0.05) and pts[:, 1].min() == pytest.approx(255 - 230,
                                                                                                        abs=0.05)
    # Swinging the chain moves the hem, not the attached top edge.
    swung = ph.bent(new, [["cape_phys1", "cape_phys2"]], 30)
    moved = mesh_world(att, Pose(swung), "torso") - pts
    top = pts[:, 1] > pts[:, 1].max() - 1
    assert np.abs(moved[top]).max() < 1e-6 and np.abs(moved).max() > 10


def test_bodice_and_constraint_names_and_version() -> None:
    doc = auto_rig(False)
    doc["slots"].append({"name": "dress", "bone": "torso", "attachment": "dress"})
    doc["skins"][0]["attachments"]["dress"] = {"dress": {"x": 40, "y": 0, "rotation": -90, "width": 40,
                                                         "height": 80}}
    new, rep = ph.add_physics(doc, parts=["dress"])
    assert rep["chains"] == [] and "does not hang" in rep["skipped"][0]
    doc["physics"] = [{"name": "phys_cape_phys1", "bone": "torso", "rotate": 1}]
    new, rep = ph.add_physics(doc, segments=2)
    assert [c["name"] for c in new["physics"]] == ["phys_cape_phys1", "phys_cape_phys1_2", "phys_cape_phys2"]
    assert ph.validate_physics({"skeleton": {"spine": "4.1.20"}, "bones": [{"name": "a"}],
                                "physics": [{"name": "p", "bone": "a", "rotate": 1}]})
    bad = ph.validate_physics({"skeleton": {"spine": "4.2.1"}, "bones": [{"name": "a"}],
                               "physics": [{"name": "p", "bone": "b", "inertia": 2, "rotate": 0}]})
    assert any("unknown bone" in b for b in bad) and any("inertia" in b for b in bad)
    assert any("drives nothing" in b for b in bad)


def test_physics_text_keeps_the_rest_of_the_file() -> None:
    doc = auto_rig()
    text = json.dumps(doc, indent=2)
    new, _ = ph.add_physics(doc)
    out = ph.physics_text(text, new)
    assert json.loads(out) == new
    assert out.startswith(text[:text.index('"bones"')])


def test_qa_render_setup_matches() -> None:
    doc = auto_rig()
    new, rep = ph.add_physics(doc)
    img, match = ph.qa_render(doc, new, [c["bones"] for c in rep["chains"]], Regions(), height=200)
    assert match["iou"] > 0.99 and img.width > 3 * img.height / 2


@pytest.mark.skipif(not (shutil.which("node") and SPINE_CORE.is_file()), reason="needs node + spine-core 4.2")
def test_spine_core_runtime_swings_the_cape(tmp_path: Path) -> None:
    """The real spine-ts 4.2 runtime parses the file and its physics moves the chain during walk."""
    new, _ = ph.add_physics(auto_rig(), segments=2)
    sk = tmp_path / "hero.json"
    sk.write_text(json.dumps(new), encoding="utf-8")
    regions = "\n".join(f"{n}\nbounds:0,0,{x1 - x0},{y1 - y0}" for n, (x0, y0, x1, y1, _) in BOXES.items())
    atlas = tmp_path / "hero.atlas"
    atlas.write_text(f"hero.png\nsize:512,512\nfilter:Linear,Linear\n{regions}\n", encoding="utf-8")
    script = tmp_path / "check.mjs"
    script.write_text(NODE_CHECK, encoding="utf-8")
    out = subprocess.run(["node", str(script), SPINE_CORE.as_uri(), str(sk), str(atlas)], check=False, capture_output=True,
                         text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    res = json.loads(out.stdout)
    assert res["physics"] == 2 and res["anim"] == "walk" and max(res["swing"].values()) > 0.5


NODE_CHECK = """
import { readFileSync } from "node:fs";
const [coreUrl, skelPath, atlasPath] = process.argv.slice(2);
const spine = await import(coreUrl);
const atlas = new spine.TextureAtlas(readFileSync(atlasPath, "utf8"));
const data = new spine.SkeletonJson(new spine.AtlasAttachmentLoader(atlas)).readSkeletonData(
  JSON.parse(readFileSync(skelPath, "utf8")));
function sim(mode) {
  const sk = new spine.Skeleton(data), st = new spine.AnimationState(new spine.AnimationStateData(data));
  st.setAnimation(0, "walk", true);
  sk.updateWorldTransform(spine.Physics.reset);
  const rec = {};
  for (let i = 0; i < 120; i++) {
    st.update(1 / 60); st.apply(sk); sk.update(1 / 60); sk.updateWorldTransform(mode);
    for (const c of data.physicsConstraints) (rec[c.bone.name] ||= []).push(sk.findBone(c.bone.name).getWorldRotationX());
  }
  return rec;
}
const on = sim(spine.Physics.update), off = sim(spine.Physics.none), swing = {};
for (const k in on) swing[k] = Math.max(...on[k].map((v, i) => Math.abs(v - off[k][i])));
console.log(JSON.stringify({ physics: data.physicsConstraints.length, anim: "walk", swing }));
"""


async def test_executor_outputs_and_recipe(tmp_path: Path) -> None:
    doc = auto_rig()
    layers = rig_layers()
    positions, pw, ph_ = sp.pack([layer.image.size for layer in layers], 2)
    page = Image.new("RGBA", (pw, ph_), (0, 0, 0, 0))
    regions = []
    for layer, (x, y) in zip(layers, positions):
        page.paste(layer.image, (x, y))
        regions.append((layer.name, x, y, layer.image.width, layer.image.height))
    atlas_text = sp.atlas_text("hero.png", pw, ph_, regions)

    env = Env(tmp_path, [], {"spine-physics": (RECIPES / "spine-physics.yaml").read_text(encoding="utf-8")})
    for cls in ph.SPINE_PHYSICS_OPS:
        env.registry.register(cls())
    import io

    buf = io.BytesIO()
    page.save(buf, "PNG")
    sk = await env.blobs.put_bytes(json.dumps(doc).encode(), name="hero.json", mime="application/json")
    at = await env.blobs.put_bytes(atlas_text.encode(), name="hero.atlas", mime="text/plain")
    pg = await env.blobs.put_bytes(buf.getvalue(), name="hero.png", mime="image/png")
    run_id = await env.engine.start_run("spine-physics", {"skeleton": sk.hash, "atlas": [at.hash, pg.hash]})
    await asyncio.wait_for(env.engine.wait_idle(run_id), 60)
    steps = env.steps(run_id)
    if env.status(run_id) == RunStatus.AWAITING_HUMAN:
        step = steps["physics"]
    else:
        assert env.status(run_id) == RunStatus.SUCCEEDED, {k: s.error for k, s in steps.items() if s.error}
        step = steps["physics"]
    out = step.outputs
    new = json.loads(env.blobs.path(out["skeleton"][0].hash).read_bytes())
    assert len(new["physics"]) == len([b for b in new["bones"] if "_phys" in b["name"]]) >= 2
    assert out["skeleton"][0].name == "hero.json"
    assert out["atlas"][0].hash == at.hash and out["texture"][0].hash == pg.hash
    report = json.loads(env.blobs.path(out["report"][0].hash).read_bytes())
    assert report["setup_match"]["iou"] > 0.99 and "4.2" in report["runtime"]
    assert b"spine-player@" in env.blobs.path(out["html"][0].hash).read_bytes()
    assert CATALOG["op.spine_physics"]["label"]
    assert validate((RECIPES / "spine-physics.yaml").read_text(encoding="utf-8"), None)[1] == []

    ctx = FakeCtx(tmp_path / "c")
    ctx.job_dir.mkdir(parents=True)
    plain = auto_rig(False)
    plain["slots"] = [s for s in plain["slots"] if s["name"] != "cape"]
    ref = await ctx.blobs.put_bytes(json.dumps(plain).encode(), name="x.json", mime="application/json")
    with pytest.raises(StepError, match="no hair"):
        await ph.SpinePhysics().run(ctx, {}, {"skeleton": [ref]})
    old = await ctx.blobs.put_bytes(json.dumps({**plain, "skeleton": {"spine": "4.1.24"}}).encode(), name="o.json",
                                    mime="application/json")
    with pytest.raises(StepError, match="4.2"):
        await ph.SpinePhysics().run(ctx, {}, {"skeleton": [old]})


def test_export_readme_names_the_runtime(tmp_path: Path) -> None:
    from studio_core.export import godot, unity
    from studio_core.export.source import Source

    new, _ = ph.add_physics(auto_rig(), segments=2)
    sk, at = tmp_path / "hero.json", tmp_path / "hero.atlas"
    sk.write_text(json.dumps(new), encoding="utf-8")
    at.write_text("hero.png\nsize:8,8\n", encoding="utf-8")
    src = Source(asset="Hero", spine_json=("hero.json", sk), spine_atlas=("hero.atlas", at))
    u = dict(unity.export(src))["README.txt"].decode()
    g = dict(godot.export(src))["README.txt"].decode()
    assert "Physics Spine 4.2: 2 constraint" in u and "spine-unity 4.2" in u
    assert "spine-godot 4.2 trở lên" in g
    sk.write_text(json.dumps(auto_rig(False)), encoding="utf-8")
    assert "Physics" not in dict(unity.export(src))["README.txt"].decode()


def test_cloth_tip_is_calmer_than_root_and_finite() -> None:
    new, _ = ph.add_physics(auto_rig())
    cs = new["physics"]
    assert len(cs) == 4 and ph.validate_physics(new) == []
    assert cs[0]["inertia"] > cs[-1]["inertia"] >= ph.CLOTH_INERTIA_MIN
    assert cs[0]["strength"] < cs[-1]["strength"] and cs[0]["damping"] > cs[-1]["damping"] >= 0.5
    assert 0 < cs[-1]["limit"] < cs[0]["limit"]
