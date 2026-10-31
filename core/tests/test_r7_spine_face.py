from __future__ import annotations

import asyncio
import io
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image, ImageDraw
from test_engine_runner import Env
from test_ops_executors import FakeCtx

from studio_core.contracts import Lane, RunStatus, StepError, StepResult
from studio_core.engine.kinds import CATALOG
from studio_core.engine.library import validate
from studio_core.ops import spine as sp
from studio_core.ops import spine_face as sf
from studio_core.ops import spine_physics as ph
from studio_core.ops import spine_runtime as rt

ROOT = Path(__file__).resolve().parents[2]
RECIPES = ROOT / "core" / "studio_core" / "recipes"
SPINE_CORE = ROOT / "app" / "node_modules" / "@esotericsoftware" / "spine-core" / "dist" / "index.js"
HAS_NODE = bool(shutil.which("node") and SPINE_CORE.is_file())

K = 3
JOINTS = {k: (x * K, y * K) for k, (x, y) in {
    "head_top": (100, 20), "neck": (100, 60), "pelvis": (100, 150), "shoulder_l": (130, 65), "elbow_l": (160, 65),
    "wrist_l": (185, 65), "hand_tip_l": (200, 65), "shoulder_r": (70, 65), "elbow_r": (40, 65), "wrist_r": (15, 65),
    "hand_tip_r": (2, 65), "hip_l": (115, 150), "knee_l": (115, 200), "ankle_l": (115, 245), "foot_tip_l": (125, 250),
    "hip_r": (85, 150), "knee_r": (85, 200), "ankle_r": (85, 245), "foot_tip_r": (75, 250)}.items()}
SKIN = (240, 200, 160, 255)
# Feature boxes on the 108 x 132 head image (texels).
FEATURES = {"eye_r": (24, 52, 42, 66), "eye_l": (66, 52, 84, 66), "brow_r": (22, 36, 44, 42),
            "brow_l": (64, 36, 86, 42), "mouth": (42, 96, 66, 104)}


def head_image() -> Image.Image:
    img = Image.new("RGBA", (108, 132), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((2, 2, 105, 129), fill=SKIN, outline=(60, 40, 30, 255), width=2)
    for k in ("eye_l", "eye_r"):
        d.ellipse(FEATURES[k], fill=(255, 255, 255, 255), outline=(20, 20, 30, 255), width=2)
        x0, y0, x1, y1 = FEATURES[k]
        d.ellipse((x0 + 5, y0 + 3, x1 - 5, y1 - 3), fill=(30, 60, 120, 255))
    for k in ("brow_l", "brow_r"):
        d.rectangle(FEATURES[k], fill=(70, 40, 20, 255))
    d.arc(FEATURES["mouth"], 10, 170, fill=(150, 50, 40, 255), width=2)
    return img


def layers() -> list[sp.Layer]:
    return [sp.Layer("torso", Image.new("RGBA", (40 * K, 100 * K), (60, 90, 160, 255)), (80 * K, 55 * K)),
            sp.Layer("head", head_image(), (82 * K - 0, 18 * K - 0))]


def rig() -> tuple[dict[str, Any], str, Image.Image]:
    doc, _ = sp.build_skeleton(layers(), JOINTS, (100 * K, 255 * K), "hero", True)
    ls = layers()
    pos, pw, phh = sp.pack([layer.image.size for layer in ls], 2)
    page = Image.new("RGBA", (pw, phh), (0, 0, 0, 0))
    regions = []
    for layer, (x, y) in zip(ls, pos):
        page.paste(layer.image, (x, y))
        regions.append((layer.name, x, y, layer.image.width, layer.image.height))
    return doc, sp.atlas_text("hero.png", pw, phh, regions), page


def located(scale: float) -> dict[str, Any]:
    return {k: [v * scale for v in b] for k, b in FEATURES.items()} | {"face_visible": True, "brows_visible": True}


def fake_edit(hole: Image.Image) -> Image.Image:
    """Paints the hole a flat colour per call so each expression is recognisable in its attachment."""
    arr = np.asarray(hole.convert("RGBA")).copy()
    arr[arr[..., 3] == 0, :3] = (200, 30, 160)
    arr[..., 3] = 255
    return Image.fromarray(arr, "RGBA").convert("RGB")


async def _put(ctx: Any, data: bytes, name: str, mime: str) -> Any:
    return await ctx.blobs.put_bytes(data, name=name, mime=mime)


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def test_find_head_affine_and_region_placement() -> None:
    doc, _, _ = rig()
    slot, att_name, att, skin = sf.find_head(doc, [])
    assert (slot["name"], att_name, skin) == ("head", "head", "default")
    with pytest.raises(StepError, match="no visible head"):
        sf.find_head({"slots": [{"name": "torso", "bone": "torso", "attachment": "torso"}], "skins": []}, [])
    a, b, resid = sf.texel_affine(att, doc, slot["bone"])
    assert resid == 0
    box = (24, 52, 42, 66)
    reg = sf.region_from_box(a, b, box)
    pose = rt.Pose(doc)
    got = rt._region_corners(reg, pose, slot["bone"])
    m, o = pose.world[slot["bone"]]
    want = np.array([[24, 52], [42, 52], [42, 66], [24, 66]], float)
    expect = (m @ (a @ want.T + b[:, None])).T + o
    assert np.abs(got - expect).max() < 1e-6
    # The same image as a flat weighted mesh gives the same texel map.
    mesh = {"type": "mesh", "width": att["width"], "height": att["height"], "uvs": [0, 0, 1, 0, 1, 1, 0, 1],
            "triangles": [0, 1, 2, 0, 2, 3], "hull": 4,
            "vertices": [float(v) for p in (a @ np.array([[0, 0], [108, 0], [108, 132], [0, 132]]).T).T + b
                         for v in p]}
    a2, b2, r2 = sf.texel_affine(mesh, doc, slot["bone"])
    assert r2 < 1e-6 and np.allclose(a2, a) and np.allclose(b2, b)


def test_blink_talk_and_variants() -> None:
    keys, end = sf.blink_keys("eyes", 7, True)
    starts = [k["time"] for k in keys if k["name"] == "eyes_half"][::2]
    assert all(3 <= g <= 5 for g in np.diff(starts)) and 3 <= end - starts[-1] <= 5
    assert keys == sf.blink_keys("eyes", 7, True)[0] and keys[-1] == {"time": end, "name": "eyes_open"}
    talk = sf.talk_keys(3)
    names = [k["name"] for k in talk]
    assert names[-1] == "mouth_neutral" and all(a != b for a, b in zip(names, names[1:-2]))
    assert set(names[:-2]) == {"mouth_a", "mouth_o", "mouth_m"}
    assert "brow_sad" not in sf.wanted_variants(["sad"], {"eyes", "mouth"})
    assert sf.wanted_variants([], {"eyes", "mouth", "brow"}) == list(sf.BASE)
    with pytest.raises(StepError, match="unknown emotion"):
        sf.parse_emotions("joy")


def test_extend_atlas_keeps_other_pages() -> None:
    text = "a.png\nsize:64,64\nfilter:Linear,Linear\nhead\nbounds:0,0,10,10\n\nb.png\nsize:32,32\ntorso\nbounds:0,0,4,4\n"
    out = sf.extend_atlas(text, 0, "a_face.png", (64, 96), [("head_eyes_open", 2, 66, 8, 6)])
    from studio_core.spine_atlas import parse_atlas

    at = parse_atlas(out)
    assert [p.name for p in at.pages] == ["a_face.png", "b.png"] and at.pages[0].props["size"] == "64,96"
    assert at.regions["head_eyes_open"].page == 0 and at.regions["torso"].page == 1


async def _pipeline(tmp: Path) -> tuple[FakeCtx, dict[str, Any], Any, dict[str, Any]]:
    doc, atlas_text, page = rig()
    ctx = FakeCtx(tmp)
    ctx.job_dir.mkdir(parents=True)
    sk = await _put(ctx, json.dumps(doc).encode(), "hero.json", "application/json")
    atlas = [await _put(ctx, atlas_text.encode(), "hero.atlas", "text/plain"),
             await _put(ctx, _png(page), "hero.png", "image/png")]
    prep = await sf.SpineFacePrep().run(ctx, {}, {"skeleton": [sk], "atlas": atlas})
    info = json.loads(ctx.blobs.path(prep.outputs["info"][0].hash).read_bytes())
    assert prep.meta["size"] == info["work"] and all(v % 16 == 0 for v in info["work"])
    boxes = await _put(ctx, json.dumps(located(info["scale"])).encode(), "b.json", "application/json")
    plan = await sf.SpineFacePlan().run(ctx, {}, {"info": prep.outputs["info"], "boxes": [boxes], "atlas": atlas})
    layout = json.loads(ctx.blobs.path(plan.outputs["layout"][0].hash).read_bytes())
    assert len(layout["prompts"]) == len(plan.outputs["holes"]) == len(plan.meta["variants"])
    edits = []
    for ref in plan.outputs["holes"]:
        hole = Image.open(ctx.blobs.path(ref.hash))
        assert hole.mode == "RGBA" and (np.asarray(hole)[..., 3] == 0).any()
        edits.append(await _put(ctx, _png(fake_edit(hole)), "e.png", "image/png"))
    res = await sf.SpineFaceBuild().run(ctx, {}, {"skeleton": [sk], "atlas": atlas, "plan": plan.outputs["plan"],
                                                  "edits": edits})
    report = json.loads(ctx.blobs.path(res.outputs["report"][0].hash).read_bytes())
    return ctx, report, res, doc


async def test_executors_build_face_slots_and_animations(tmp_path: Path) -> None:
    ctx, report, res, doc = await _pipeline(tmp_path)
    assert report["placement_error_px"] < 0.01 and report["neutral_match"]["mae"] < 1.5
    new = json.loads(ctx.blobs.path(res.outputs["skeleton"][0].hash).read_bytes())
    names = [s["name"] for s in new["slots"]]
    assert names[names.index("head") + 1:names.index("head") + 4] == ["eyes", "brow", "mouth"]
    assert {"blink", "talk", "happy", "angry", "sad"} <= set(new["animations"])
    assert new["animations"]["hurt"]["slots"]["eyes"] == doc["animations"]["hurt"]["slots"]["head"]
    atts = new["skins"][0]["attachments"]
    assert set(atts["eyes"]) == {"eyes_open", "eyes_half", "eyes_closed", "eyes_happy"}
    assert set(atts["mouth"]) >= {"mouth_neutral", "mouth_a", "mouth_o", "mouth_m"}
    atlas = (ctx.blobs.path(res.outputs["atlas"][0].hash)).read_text(encoding="utf-8")
    assert sp.validate_skeleton(new, atlas) == [] and atlas.splitlines()[0] == "hero_face.png"
    page = Image.open(ctx.blobs.path(res.outputs["texture"][0].hash))
    from studio_core.spine_atlas import parse_atlas

    regions = parse_atlas(atlas).regions
    for att in atts["mouth"].values():
        r = regions[att["path"]]
        assert r.x + r.width <= page.width and r.y + r.height <= page.height
        assert (r.width, r.height) == (att["width"], att["height"])
    # The open-mouth attachment carries the edit (magenta) where the neutral one has skin.
    a_reg = regions[atts["mouth"]["mouth_a"]["path"]]
    crop = np.asarray(page.convert("RGBA").crop((a_reg.x, a_reg.y, a_reg.x + a_reg.width, a_reg.y + a_reg.height)))
    assert ((crop[..., 0] > 180) & (crop[..., 1] < 60) & (crop[..., 3] > 200)).sum() > 20
    assert b"spine-player@" in ctx.blobs.path(res.outputs["html"][0].hash).read_bytes()
    assert Image.open(ctx.blobs.path(res.outputs["preview"][0].hash)).width > 400
    return None


@pytest.mark.skipif(not HAS_NODE, reason="needs node + spine-core 4.2")
async def test_spine_core_plays_face_animations(tmp_path: Path) -> None:
    ctx, _, res, _ = await _pipeline(tmp_path / "p")
    sk = tmp_path / "hero.json"
    sk.write_bytes(ctx.blobs.path(res.outputs["skeleton"][0].hash).read_bytes())
    at = tmp_path / "hero.atlas"
    at.write_bytes(ctx.blobs.path(res.outputs["atlas"][0].hash).read_bytes())
    script = tmp_path / "check.mjs"
    script.write_text(FACE_CHECK, encoding="utf-8")
    out = subprocess.run(["node", str(script), SPINE_CORE.as_uri(), str(sk), str(at)], capture_output=True, text=True,
                         timeout=60, check=False)
    assert out.returncode == 0, out.stderr
    seen = json.loads(out.stdout)
    assert set(seen["blink"]["eyes"]) == {"eyes_open", "eyes_half", "eyes_closed"}
    assert {"mouth_a", "mouth_o", "mouth_m"} <= set(seen["talk"]["mouth"])
    assert seen["angry"]["brow"] == ["brow_angry"] and seen["happy"]["eyes"] == ["eyes_happy"]


FACE_CHECK = """
import { readFileSync } from "node:fs";
const [coreUrl, skelPath, atlasPath] = process.argv.slice(2);
const spine = await import(coreUrl);
const atlas = new spine.TextureAtlas(readFileSync(atlasPath, "utf8"));
const data = new spine.SkeletonJson(new spine.AtlasAttachmentLoader(atlas)).readSkeletonData(
  JSON.parse(readFileSync(skelPath, "utf8")));
const out = {};
for (const name of ["blink", "talk", "happy", "angry", "sad"]) {
  const anim = data.findAnimation(name);
  const sk = new spine.Skeleton(data), st = new spine.AnimationState(new spine.AnimationStateData(data));
  st.setAnimation(0, "walk", true); st.setAnimation(1, name, true);
  const seen = {};
  for (let i = 0; i <= Math.ceil(anim.duration * 60); i++) {
    st.update(1 / 60); st.apply(sk); sk.updateWorldTransform(spine.Physics.update);
    for (const s of ["eyes", "brow", "mouth"]) {
      const a = sk.findSlot(s).getAttachment();
      if (a) (seen[s] ||= new Set()).add(a.name);
    }
  }
  out[name] = Object.fromEntries(Object.entries(seen).map(([k, v]) => [k, [...v]]));
}
console.log(JSON.stringify(out));
"""


async def test_covered_face_is_refused(tmp_path: Path) -> None:
    work = Image.new("RGBA", (64, 64), SKIN)
    with pytest.raises(StepError, match="no face"):
        sf.feature_holes(work, {"face_visible": False, "eye_l": [1, 1, 5, 5], "mouth": [1, 1, 5, 5]})
    with pytest.raises(StepError, match="no eyes"):
        sf.feature_holes(work, {"face_visible": True})


class FakeClaude:
    kind = "claude.task"
    lane = Lane.CLOUD
    version = "1"

    def __init__(self, scale: float) -> None:
        self.scale = scale

    def cacheable(self, params: dict[str, Any]) -> bool:
        return False

    def cache_material(self, params: dict[str, Any]) -> str:
        return ""

    async def run(self, ctx: Any, params: dict[str, Any], inputs: Any) -> StepResult:
        assert len(inputs["images"]) == 2 and "eye_l" in params["schema"]["properties"]
        ref = await ctx.blobs.put_bytes(json.dumps(located(self.scale)).encode(), name="result.json",
                                        mime="application/json")
        return StepResult(outputs={"json": [ref]})


class FakeInpaint:
    kind = "comfy.inpaint_flux2_klein4b"
    lane = Lane.GPU
    version = "1"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def cacheable(self, params: dict[str, Any]) -> bool:
        return False

    def cache_material(self, params: dict[str, Any]) -> str:
        return ""

    async def run(self, ctx: Any, params: dict[str, Any], inputs: Any) -> StepResult:
        self.prompts.append(params["14.text"])
        out = []
        for ref in inputs["image"]:
            with Image.open(ctx.blobs.path(ref.hash)) as im:
                out.append(await ctx.blobs.put_bytes(_png(fake_edit(im)), name="fill.png", mime="image/png"))
        return StepResult(outputs={"images": out})


async def test_recipe_runs_with_fake_claude_and_klein(tmp_path: Path) -> None:
    doc, atlas_text, page = rig()
    s, _ = sf.work_size(108, 132)
    inpaint = FakeInpaint()
    env = Env(tmp_path, [], {"spine-face": (RECIPES / "spine-face.yaml").read_text(encoding="utf-8")})
    env.registry.register(FakeClaude(s))
    env.registry.register(inpaint)
    for cls in sf.FACE_OPS:
        env.registry.register(cls())
    sk = await env.blobs.put_bytes(json.dumps(doc).encode(), name="hero.json", mime="application/json")
    at = await env.blobs.put_bytes(atlas_text.encode(), name="hero.atlas", mime="text/plain")
    pg = await env.blobs.put_bytes(_png(page), name="hero.png", mime="image/png")
    run_id = await env.engine.start_run("spine-face", {"skeleton": sk.hash, "atlas": [at.hash, pg.hash],
                                                       "emotions": "happy"})
    await asyncio.wait_for(env.engine.wait_idle(run_id), 60)
    steps = env.steps(run_id)
    assert env.status(run_id) in (RunStatus.AWAITING_HUMAN, RunStatus.SUCCEEDED), \
        {k: st.error for k, st in steps.items() if st.error}
    assert len(inpaint.prompts) == 7 and any("closed" in p for p in inpaint.prompts)
    out = steps["build"].outputs
    new = json.loads(env.blobs.path(out["skeleton"][0].hash).read_bytes())
    assert "happy" in new["animations"] and "angry" not in new["animations"]
    for kind in ("op.spine_face_prep", "op.spine_face_plan", "op.spine_face_build"):
        assert CATALOG[kind]["label"]
    assert validate((RECIPES / "spine-face.yaml").read_text(encoding="utf-8"), None)[1] == []


def test_export_readme_mentions_face_tracks(tmp_path: Path) -> None:
    from studio_core.export import godot, unity
    from studio_core.export.source import Source

    doc, _, _ = rig()
    doc["slots"].append({"name": "eyes", "bone": "head"})
    doc["animations"]["blink"] = {"slots": {"eyes": {"attachment": [{"time": 0, "name": None}]}}}
    sk, at = tmp_path / "hero.json", tmp_path / "hero.atlas"
    sk.write_text(json.dumps(doc), encoding="utf-8")
    at.write_text("hero.png\nsize:8,8\n", encoding="utf-8")
    src = Source(asset="Hero", spine_json=("hero.json", sk), spine_atlas=("hero.atlas", at))
    assert 'SetAnimation(1, "blink", true)' in dict(unity.export(src))["README.txt"].decode()
    assert 'set_animation("blink", true, 1)' in dict(godot.export(src))["README.txt"].decode()


@pytest.mark.skipif(not HAS_NODE, reason="needs node + spine-core 4.2")
def test_cloth_preset_swings_more_and_long_chains_stay_bounded(tmp_path: Path) -> None:
    """#51: the cloth preset used to swing a 2-bone cape ~5° on walk; a 4-bone one must not spin round."""
    from test_r6_spine_physics import BOXES, NODE_CHECK, auto_rig

    regions = "\n".join(f"{n}\nbounds:0,0,{x1 - x0},{y1 - y0}" for n, (x0, y0, x1, y1, _) in BOXES.items())
    atlas = tmp_path / "hero.atlas"
    atlas.write_text(f"hero.png\nsize:512,512\nfilter:Linear,Linear\n{regions}\n", encoding="utf-8")
    script = tmp_path / "check.mjs"
    script.write_text(NODE_CHECK, encoding="utf-8")
    tips = {}
    for seg in (2, 4):
        new, _ = ph.add_physics(auto_rig(), segments=seg)
        assert [c["damping"] for c in new["physics"]][:2] == [0.95, 0.935]
        sk = tmp_path / f"hero{seg}.json"
        sk.write_text(json.dumps(new), encoding="utf-8")
        out = subprocess.run(["node", str(script), SPINE_CORE.as_uri(), str(sk), str(atlas)], capture_output=True,
                             text=True, timeout=60, check=False)
        assert out.returncode == 0, out.stderr
        tips[seg] = max(json.loads(out.stdout)["swing"].values())
    assert tips[2] > 8 and tips[4] < 90, tips
