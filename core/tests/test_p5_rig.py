from __future__ import annotations

import asyncio
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from studio_core.config import Settings
from studio_core.contracts import ExecutorRegistry
from studio_core.engine.recipe import load_recipes
from studio_core.ops import parts as pt
from studio_core.ops import register
from studio_core.ops import rig
from studio_core.ops import spine as sp
from test_ops_executors import FakeCtx

W, H = 512, 512
RECIPES_DIR = Settings.model_fields["recipes_dir"].default
WORKFLOWS_DIR = Settings.model_fields["comfy_workflows_dir"].default


def figure() -> tuple[Image.Image, list[Image.Image]]:
    """T-pose stick figure with textured parts; SAM-like seeds deliberately cover only part of each limb."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle((216, 140, 296, 300), fill=(120, 70, 40, 255))  # torso
    d.rectangle((60, 150, 216, 180), fill=(230, 180, 140, 255))  # arm r (image left)
    d.rectangle((296, 150, 452, 180), fill=(230, 180, 140, 255))  # arm l
    d.rectangle((40, 146, 60, 184), fill=(90, 50, 30, 255))
    d.rectangle((452, 146, 472, 184), fill=(90, 50, 30, 255))
    d.rectangle((222, 300, 252, 470), fill=(60, 60, 120, 255))
    d.rectangle((260, 300, 290, 470), fill=(60, 60, 120, 255))
    d.rectangle((216, 470, 256, 490), fill=(30, 30, 30, 255))
    d.rectangle((256, 470, 296, 490), fill=(30, 30, 30, 255))
    d.ellipse((221, 60, 291, 140), fill=(250, 210, 170, 255))
    rng = np.random.default_rng(1)
    a = np.asarray(img).copy()
    noise = rng.integers(-20, 20, a[..., :3].shape)
    a[..., :3] = np.clip(a[..., :3].astype(int) + noise, 0, 255).astype(np.uint8) * (a[..., 3:4] > 0)
    img = Image.fromarray(a, "RGBA")

    def m(*boxes: tuple[int, int, int, int], ellipse: bool = False) -> Image.Image:
        mk = Image.new("L", (W, H), 0)
        md = ImageDraw.Draw(mk)
        for b in boxes:
            (md.ellipse if ellipse else md.rectangle)(b, fill=255)
        return mk

    masks = [m((225, 64, 287, 136), ellipse=True),  # head
             m((220, 160, 292, 280)),  # torso core only
             m((90, 152, 200, 178), (312, 152, 420, 178)),  # arms, partial
             m((42, 148, 58, 182)),  # one hand only
             m((224, 320, 250, 488), (262, 320, 288, 488)),  # legs
             m()]  # no cape
    return img, masks


@pytest.fixture(scope="module")
def built() -> dict:
    img, masks = figure()
    meta, labels, preview = rig.run_parts(img, masks, rig.DEFAULT_ROLES)
    layers_meta, crops, holes, lprev = rig.run_layers(img, labels, meta, None, {})
    return {"img": img, "meta": meta, "labels": labels, "layers": layers_meta, "crops": crops, "holes": holes}


def test_partition_covers_silhouette_and_splits_sides(built: dict) -> None:
    labels = np.asarray(built["labels"])
    alpha = np.asarray(built["img"])[..., 3]
    assert ((alpha > 0) == (labels > 0)).all()
    names = built["meta"]["names"]
    for req in pt.REQUIRED:
        assert req in names
    ys, xs = np.nonzero(labels == names.index("arm_l") + 1)
    assert xs.min() > 296 - 2  # character's left arm is on the image right
    assert "cape" not in names or built["meta"]["areas"]["cape"] == 0


def test_joints_are_plausible(built: dict) -> None:
    j = built["meta"]["joints"]
    assert j["shoulder_l"][0] < j["elbow_l"][0] < j["wrist_l"][0] < j["hand_tip_l"][0]
    assert j["shoulder_r"][0] > j["elbow_r"][0] > j["wrist_r"][0] > j["hand_tip_r"][0]
    assert j["hip_l"][1] < j["knee_l"][1] < j["ankle_l"][1] <= j["foot_tip_l"][1]
    assert j["head_top"][1] < j["neck"][1] < j["pelvis"][1]
    assert abs((j["shoulder_l"][0] - j["neck"][0]) - (j["neck"][0] - j["shoulder_r"][0])) < 12
    assert built["meta"]["hints"]["wrist_r"] == "sam_hand"


def test_layers_split_limbs_and_request_fills(built: dict) -> None:
    lm = built["layers"]
    names = [p["name"] for p in lm["parts"]]
    for n in ("head", "torso", "upper_arm_l", "lower_arm_l", "hand_l", "thigh_r", "shin_r", "foot_r"):
        assert n in names
    assert names.index("torso") > names.index("thigh_l")  # torso draws over the thighs
    assert names.index("hand_l") > names.index("lower_arm_l") > names.index("upper_arm_l")
    assert {h["part"] for h in lm["holes"]} >= {"torso", "upper_arm_l", "thigh_l"}
    for req, h in zip(built["holes"], lm["holes"]):
        x0, y0, x1, y1 = h["box"]
        assert req.size == (x1 - x0, y1 - y0) and req.width % 16 == 0 and req.height % 16 == 0
        assert (np.asarray(req)[..., 3] == 0).sum() > 0
    torso = next(p for p in lm["parts"] if p["name"] == "torso")
    assert torso["fill_px"] > 0


def test_spine_export_is_valid_and_reassembles(built: dict) -> None:
    layers = [sp.Layer(e["name"], c, tuple(e["offset"])) for c, e in zip(built["crops"], built["layers"]["parts"])]
    res = rig.run_export(built["img"], layers, built["layers"], "hero", True, 2)
    doc = res["doc"]
    assert res["report"]["problems"] == []
    assert doc["bones"][0] == {"name": "root"}
    names = [b["name"] for b in doc["bones"]]
    parent = {b["name"]: b.get("parent") for b in doc["bones"]}
    assert parent["head"] == "neck" and parent["neck"] == "torso" and parent["torso"] == "hip"
    assert parent["hand_l"] == "lower_arm_l" and parent["upper_arm_l"] == "shoulder_l"
    assert parent["foot_r"] == "shin_r" and parent["thigh_r"] == "hip"
    assert all(names.index(p) < names.index(n) for n, p in parent.items() if p)
    slots = [s["name"] for s in doc["slots"]]
    assert slots == [p["name"] for p in built["layers"]["parts"]]
    assert set(doc["skins"][0]["attachments"]) == set(slots)
    assert set(doc["animations"]) == {"idle", "relax", "walk", "attack", "hurt", "jump"}
    assert set(doc["animations"]["hurt"]["slots"]) == set(slots)
    lines = res["atlas"].splitlines()
    assert lines[0] == "hero.png" and lines[1].startswith("size:") and lines[3] == "pma:false"
    regions = dict(zip(lines[4::2], lines[5::2]))
    assert set(regions) == set(slots) and all(v.startswith("bounds:") for v in regions.values())
    cmp = res["report"]["setup_vs_original"]
    assert cmp["mae"] < 0.5 and cmp["iou"] > 0.999
    assert b"spine-player@" in res["html"].encode() and b"data:image/png;base64," in res["html"].encode()
    json.loads(res["skeleton"])


def test_animation_moves_arms_but_not_feet(built: dict) -> None:
    layers = [sp.Layer(e["name"], c, tuple(e["offset"])) for c, e in zip(built["crops"], built["layers"]["parts"])]
    doc, _ = sp.build_skeleton(layers, {k: tuple(v) for k, v in built["layers"]["joints"].items()},
                               tuple(built["layers"]["origin"]), "x")
    regions = {l.name: l.image for l in layers}
    origin = tuple(built["layers"]["origin"])
    setup = np.asarray(sp.compose(doc, regions, origin, (W, H)))
    down = np.asarray(sp.compose(doc, regions, origin, (W, H), "relax", 1.2))
    assert (down[150:185, 400:470, 3] > 0).mean() < (setup[150:185, 400:470, 3] > 0).mean() * 0.5
    assert np.abs(down[475:490].astype(int) - setup[475:490].astype(int)).mean() < 1


def test_walk_lifts_one_leg_and_attack_swings_right_arm(built: dict) -> None:
    layers = [sp.Layer(e["name"], c, tuple(e["offset"])) for c, e in zip(built["crops"], built["layers"]["parts"])]
    doc, _ = sp.build_skeleton(layers, {k: tuple(v) for k, v in built["layers"]["joints"].items()},
                               tuple(built["layers"]["origin"]), "x")
    def tip(anim: str | None, t: float, bone: str) -> np.ndarray:
        m, p = sp.world_transforms(doc, anim, t)[bone]
        length = next(b.get("length", 0) for b in doc["bones"] if b["name"] == bone)
        return p + m @ np.array([length, 0.0])
    rest = {s: tip(None, 0, f"foot_{s}") for s in "lr"}
    l_up = tip("walk", 0.25, "foot_l")[1] - rest["l"][1]
    r_up = tip("walk", 0.25, "foot_r")[1] - rest["r"][1]
    assert l_up > 5 and l_up > r_up + 5
    swing_r = np.linalg.norm(tip("attack", 0.3, "hand_r") - tip("attack", 0.0, "hand_r"))
    swing_l = np.linalg.norm(tip("attack", 0.3, "hand_l") - tip("attack", 0.0, "hand_l"))
    assert swing_r > 3 * swing_l
    hip_y = [sp.world_transforms(doc, "jump", t)["hip"][1][1] for t in (0.0, 0.5)]
    assert hip_y[1] - hip_y[0] > 20


def test_merge_joints_validates_and_unswaps(built: dict) -> None:
    meta = built["meta"]
    labels = np.asarray(built["labels"]).astype(np.int16)
    auto = {k: tuple(v) for k, v in meta["joints"].items()}
    swapped = {k[:-1] + ("r" if k.endswith("l") else "l") if k.endswith(("_l", "_r")) else k: list(v)
               for k, v in auto.items()}
    swapped["elbow_l"] = [auto["elbow_l"][0] + 4, auto["elbow_l"][1]]
    swapped["elbow_r"], swapped["elbow_l"] = swapped["elbow_l"], swapped["elbow_r"]
    swapped["neck"] = [5, 5]
    out, src = pt.merge_joints(auto, swapped, labels, meta["names"], meta["center_x"], 30)
    assert src["neck"] == "auto" and out["neck"] == auto["neck"]
    assert out["elbow_l"] == (auto["elbow_l"][0] + 4, auto["elbow_l"][1]) and src["elbow_l"] == "claude"


def test_merge_fills_writes_only_hole_pixels(built: dict) -> None:
    layers = [sp.Layer(e["name"], c.copy(), tuple(e["offset"])) for c, e in zip(built["crops"], built["layers"]["parts"])]
    before = {l.name: np.asarray(l.image).copy() for l in layers}
    fills = [Image.new("RGB", h.size, (0, 255, 0)) for h in built["holes"]]
    merged = rig.merge_fills(layers, built["layers"]["holes"], built["holes"], fills)
    assert set(merged) == {h["part"] for h in built["layers"]["holes"]}
    torso = next(l for l in layers if l.name == "torso")
    a, b = np.asarray(torso.image), before["torso"]
    changed = (a != b).any(axis=-1)
    assert changed.any() and (a[changed][:, :3] == [0, 255, 0]).all()
    assert (a[..., 3] == b[..., 3]).all()


def test_pack_fits_power_of_two() -> None:
    pos, w, h = sp.pack([(100, 300), (300, 100), (50, 50), (500, 20)], 2)
    assert w & (w - 1) == 0 and h & (h - 1) == 0
    rects = [(x, y, x + s[0], y + s[1]) for (x, y), s in zip(pos, [(100, 300), (300, 100), (50, 50), (500, 20)])]
    for i, a in enumerate(rects):
        assert a[2] <= w and a[3] <= h
        for b in rects[i + 1:]:
            assert a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]


def test_parts_missing_roles_is_a_step_error() -> None:
    img, masks = figure()
    masks[2] = Image.new("L", (W, H), 0)
    with pytest.raises(pt.PartsError) as e:
        rig.run_parts(img, masks, rig.DEFAULT_ROLES)
    assert e.value.code == "parts_missing"


def _png(img: Image.Image) -> bytes:
    import io
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def test_executors_chain(tmp_path: Path) -> None:
    reg = ExecutorRegistry()
    register(reg)
    ctx = FakeCtx(tmp_path)
    img, masks = figure()

    async def go() -> None:
        image = await ctx.blobs.put_bytes(_png(img), "char.png", "image/png")
        mrefs = [await ctx.blobs.put_bytes(_png(m), f"m{i}.png", "image/png") for i, m in enumerate(masks)]
        p = await reg.resolve("op.parts").run(ctx, {}, {"image": [image], "masks": mrefs})
        bones = await ctx.blobs.put_bytes(json.dumps({"joints": {"neck": [1, 1]}, "notes": ""}).encode())
        lay = await reg.resolve("op.layers").run(ctx, {"rig": "claude"}, {
            "image": [image], "labels": p.outputs["labels"], "parts": p.outputs["meta"], "bones": [bones]})
        assert lay.meta["joint_sources"]["neck"] == "auto"
        assert len(lay.outputs["holes"]) == 5
        fills = [await ctx.blobs.put_bytes(_png(Image.new("RGB", (64, 64), "red")), "f.png", "image/png")
                 for _ in lay.outputs["holes"]]
        ex = await reg.resolve("op.spine_export").run(ctx, {"name": "hero"}, {
            "layers": lay.outputs["layers"], "meta": lay.outputs["meta"], "holes": lay.outputs["holes"],
            "fills": fills, "image": [image]})
        assert ex.meta["setup_vs_original"]["iou"] > 0.999
        assert ex.outputs["skeleton"][0].name == "hero.json" and ex.outputs["atlas"][0].name == "hero.atlas"
        assert len(ex.meta["inpainted"]) == 5
        off = await reg.resolve("op.layers").run(ctx, {"fill": False}, {
            "image": [image], "labels": p.outputs["labels"], "parts": p.outputs["meta"]})
        assert "holes" not in off.outputs

    asyncio.run(go())


def test_recipe_loads_and_kinds_resolve() -> None:
    recipes = load_recipes(RECIPES_DIR)
    r = recipes["spine-character"]
    kinds = {s.id: s.kind for s in r.steps}
    assert kinds["segment"] == "comfy.segment_sam3" and kinds["fill"] == "comfy.inpaint_flux2_klein4b"
    assert r.step("segment").map_param == "3.text" and r.step("fill").map == "image"
    assert set(r.step("bones").refs) == {"parts"}
    reg = ExecutorRegistry()
    register(reg)
    for k in ("op.parts", "op.layers", "op.spine_export"):
        reg.resolve(k)
    roles = r.step("parts").params["roles"]
    assert len(r.step("segment").params["3.text"]) == len(roles)
    assert set(r.outputs) >= {"skeleton", "atlas", "texture", "html", "preview"}


@pytest.mark.skipif(not (WORKFLOWS_DIR / "index.json").exists(), reason="ComfyUI workflows not installed")
def test_workflows_registered() -> None:
    index = json.loads((WORKFLOWS_DIR / "index.json").read_text(encoding="utf-8"))
    for name, img_key, out in (("segment_sam3", "2.image", "6"), ("inpaint_flux2_klein4b", "10.image", "9")):
        e = index[name]
        wf = json.loads((WORKFLOWS_DIR / e["file"]).read_text(encoding="utf-8"))
        assert e["images"][0]["key"] == img_key and e["outputs"][0]["node"] == out
        for key in [t["key"] for t in e["texts"]] + e["seeds"] + [i["key"] for i in e["images"]]:
            node, _, inp = key.rpartition(".")
            assert inp in wf[node]["inputs"]
