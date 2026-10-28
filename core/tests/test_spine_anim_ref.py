import asyncio
import json
import math
import shutil
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from studio_core.adapters.claude import ClaudeReply, ClaudeTaskExecutor, named_schema
from studio_core.adapters.spine_review import SpineReviewLoopExecutor
from studio_core.config import Settings
from studio_core.contracts import BlobRef, StepError
from studio_core.engine.recipe import RecipeError, parse_recipe
from studio_core.ops import spine_anim as sa
from studio_core.ops import spine_runtime as rt
from studio_core.ops.spine_ref import SpineAddAnim, SpineRenderStrip, VideoFrames, parse_window
from studio_core.spine_atlas import atlas_page_files, parse_atlas
from studio_core.store.blobs import sniff_mime

from test_ops_executors import FakeCtx

ATLAS = """
rig.png
size:64,64
filter:Linear,Linear
pma:true
box
bounds:0,0,16,8
rot
bounds:20,0,8,16
offsets:1,1,10,18
rotate:90
"""

SPEC = {"duration": 2.0, "ease": 0.4, "sway_signal": [[0, 0], [0.5, 1], [1.0, -1], [1.5, 0]],
        "hip": {"shift_x": 10, "dip_y": 2, "tilt": 3},
        "upper_body": {"spine": 2, "chest": 1, "neck": 1, "head": 3, "lag": 0.05},
        "arm_keys": {"arm": [[0, 0], [0.5, -30], [1.5, 0]]},
        "secondary": [{"bones": ["tail1", "tail2"], "gain": -2, "falloff": 0.3, "lag": 0.05}],
        "blinks": [0.8], "blink_closed_scale": 0.2}


def skeleton() -> dict[str, Any]:
    bones = [
        {"name": "root"}, {"name": "hip", "parent": "root", "y": 100},
        {"name": "spine", "parent": "hip", "rotation": 90, "length": 50},
        {"name": "chest", "parent": "spine", "x": 50, "length": 30},
        {"name": "neck", "parent": "chest", "x": 30, "length": 10},
        {"name": "head", "parent": "neck", "x": 10, "length": 20},
        {"name": "eye_L", "parent": "head", "x": 10}, {"name": "eye_R", "parent": "head", "x": 10, "y": 5},
        {"name": "arm", "parent": "chest", "x": 25, "rotation": 180, "length": 40},
        {"name": "tail1", "parent": "hip", "rotation": -120, "length": 20},
        {"name": "tail2", "parent": "tail1", "x": 20, "length": 20},
        {"name": "thigh", "parent": "hip", "rotation": -90, "length": 50},
        {"name": "shin", "parent": "thigh", "x": 50, "length": 50},
        {"name": "foot_ik", "parent": "root", "x": 10, "y": 5},
    ]
    return {
        "skeleton": {"spine": "4.2.43"},
        "bones": bones,
        "slots": [{"name": "body", "bone": "spine", "attachment": "box"},
                  {"name": "arm", "bone": "arm", "attachment": "rot"}],
        "ik": [{"name": "leg", "bones": ["thigh", "shin"], "target": "foot_ik", "stretch": True}],
        "skins": [{"name": "default", "attachments": {}},
                  {"name": "top/top_0", "attachments": {
                      "body": {"box": {"x": 25, "width": 16, "height": 8}},
                      "arm": {"rot": {"x": 20, "rotation": 0, "width": 10, "height": 18}}}}],
        "animations": {"idle": {"bones": {"head": {"rotate": [{"value": 0}, {"time": 1, "value": 5}]}}}},
    }


def write_rig(tmp: Path) -> tuple[Path, Path, Path]:
    page = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    page.paste((255, 0, 0, 255), (0, 0, 16, 8))
    page.paste((0, 0, 255, 255), (20, 0, 28, 8))
    page.paste((0, 255, 0, 255), (28, 0, 36, 8))
    (tmp / "rig.png").parent.mkdir(parents=True, exist_ok=True)
    page.save(tmp / "rig.png")
    (tmp / "rig.atlas").write_text(ATLAS, encoding="utf-8")
    (tmp / "rig.json").write_text(json.dumps(skeleton(), indent=1), encoding="utf-8")
    return tmp / "rig.json", tmp / "rig.atlas", tmp / "rig.png"


def test_atlas_parse_and_region_unrotate(tmp_path: Path) -> None:
    _, atlas, page = write_rig(tmp_path)
    a = parse_atlas(atlas.read_text())
    assert [p.name for p in a.pages] == ["rig.png"] and a.pages[0].pma
    r = a.regions["rot"]
    assert (r.degrees, r.width, r.height, r.offset_x, r.orig_w, r.orig_h) == (90, 8, 16, 1, 10, 18)
    assert atlas_page_files(atlas) == [page]
    img = rt.RegionImages.from_files(atlas, [page]).get("rot")
    assert img.size == (10, 18)
    px = np.asarray(img.convert("RGBA"))
    # page area 16x8 (blue left, green right) turned clockwise: blue on top, green below; trimmed at offset (1, 1)
    assert px[3, 4, 2] == 255 and px[14, 4, 1] == 255
    assert px[0, 0, 3] == 0  # outside the trimmed area stays transparent


def test_atlas_page_missing(tmp_path: Path) -> None:
    (tmp_path / "x.atlas").write_text(ATLAS, encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="rig.png"):
        atlas_page_files(tmp_path / "x.atlas")


def test_bezier_sample_and_stepped() -> None:
    keys = [{"time": 0, "value": 0, "curve": [0.25, 0, 0.75, 10]}, {"time": 1, "value": 10}]
    assert rt.sample(keys, 0.5, ("value",), 0)[0] == pytest.approx(5, abs=1e-3)
    assert rt.sample(keys, 0.2, ("value",), 0)[0] < 2.0
    assert rt.sample([{"curve": "stepped", "value": 1}, {"time": 1, "value": 9}], 0.9, ("value",), 0) == (1.0,)
    assert rt.sample(keys, 2, ("value",), 0) == (10.0,)


def test_two_bone_ik_reaches_target() -> None:
    doc = skeleton()
    pose = rt.Pose(doc)
    m, p = pose.world["shin"]
    tip = p + m @ np.array([50.0, 0])
    assert tip == pytest.approx(pose.world["foot_ik"][1], abs=1e-3)
    doc["bones"][-1]["y"] = 200  # unreachable above: stretch keeps the chain straight toward it
    far = rt.Pose(doc)
    ang = math.degrees(math.atan2(*(far.world["thigh"][0][:, 0][::-1])))
    want = math.degrees(math.atan2(200 - 100, 10))
    assert ang == pytest.approx(want, abs=0.5)


def test_build_add_and_check_animation() -> None:
    doc = skeleton()
    text = json.dumps(doc, indent=1)
    anim = sa.build_animation(SPEC)
    assert set(anim["bones"]) >= {"hip", "spine", "chest", "neck", "head", "arm", "tail1", "tail2", "eye_L"}
    for tl in anim["bones"].values():
        for track in tl.values():
            assert track[-1]["time"] == SPEC["duration"]
    out = sa.add_animation(text, "sway", anim)
    assert out.startswith(text[:text.index('"animations"')])
    new = json.loads(out)
    assert new["animations"]["idle"] == doc["animations"]["idle"] and new["animations"]["sway"] == anim
    assert sa.check_animation(new, "sway") == []
    assert sa.add_animation(out, "sway", anim) == out
    bad = sa.add_animation(text, "x", {"bones": {"nope": {"rotate": [{"value": 1}]}}})
    assert sa.check_animation(json.loads(bad), "x") == ["unknown bone: nope"]
    no_anims = sa.add_animation(json.dumps({"bones": []}), "a", {"bones": {}})
    assert json.loads(no_anims)["animations"] == {"a": {"bones": {}}}


def test_explicit_keys_are_pinned_to_setup() -> None:
    spec = {**SPEC, "arm_keys": {"arm": [[0.4, -20], [1.2, -10]]}}
    keys = sa.build_animation(spec)["bones"]["arm"]["rotate"]
    assert (keys[0]["time"], keys[0]["value"]) == (0, 0) and keys[-1]["value"] == 0


def test_rig_summary_and_diff() -> None:
    s = sa.rig_summary(skeleton())
    assert "IK: leg thigh>shin -> foot_ik" in s and "tail1>tail2" in s and "top:1" in s
    assert len(sa.rig_summary(skeleton(), limit=200)) <= 200
    d = sa.diff_spec({"a": 1, "b": {"c": [1]}}, {"a": 2, "b": {"c": [1]}, "e": 3})
    assert d == [{"path": "a", "old": 1, "new": 2}, {"path": "e", "old": None, "new": 3}]


def test_render_strip_not_cropped(tmp_path: Path) -> None:
    skel, atlas, page = write_rig(tmp_path)
    doc = json.loads(skel.read_text())
    doc["animations"]["sway"] = sa.build_animation(SPEC)
    from studio_core.ops.spine_ref import render_strip
    strip, meta = render_strip(doc, rt.RegionImages.from_files(atlas, [page]), "sway", [0, 0.5, 1, 2], ["top_0"],
                               height=200)
    assert meta["skins"] == ["top/top_0"] and strip.height == 200
    a = np.asarray(strip)
    assert (a[..., 0] > 200).any() and (a[..., 1] > 200).sum() > 0
    assert rt.resolve_skins(doc, ["top_0", "nope_1"]) == (["top/top_0"], ["nope_1"])


def _ref(ctx: FakeCtx, path: Path, name: str) -> BlobRef:
    return asyncio.run(ctx.blobs.put_bytes(path.read_bytes(), name=name))


def test_add_anim_and_render_ops(tmp_path: Path) -> None:
    skel, atlas, page = write_rig(tmp_path / "src")
    ctx = FakeCtx(tmp_path)
    sk, at, pg = _ref(ctx, skel, "rig.json"), _ref(ctx, atlas, "rig.atlas"), _ref(ctx, page, "rig.png")
    spec = asyncio.run(ctx.blobs.put_bytes(json.dumps({"score": 4, "critique": "", "spec": SPEC}).encode()))
    res = asyncio.run(SpineAddAnim().run(ctx, {"name": "sway"}, {"skeleton": [sk], "spec": [spec]}))
    out = res.outputs["skeleton"][0]
    assert out.name == "rig.json" and "sway" in json.loads(ctx.blobs.path(out.hash).read_text())["animations"]
    strip = asyncio.run(SpineRenderStrip().run(ctx, {"anim": "sway", "count": 3, "skin": "top_0", "height": 120},
                                               {"skeleton": [out], "atlas": [at, pg]}))
    assert strip.meta["times"] == [0.0, 1.0, 2.0]
    with pytest.raises(StepError, match="page images"):
        asyncio.run(SpineRenderStrip().run(ctx, {}, {"skeleton": [out], "atlas": [at]}))
    bad = asyncio.run(ctx.blobs.put_bytes(json.dumps({**SPEC, "arm_keys": {"ghost": [[0, 0], [1, 5]]}}).encode()))
    with pytest.raises(StepError, match="unknown bone: ghost"):
        asyncio.run(SpineAddAnim().run(ctx, {"name": "sway"}, {"skeleton": [sk], "spec": [bad]}))


class Scripted:
    def __init__(self, replies: list[dict[str, Any]]):
        self.replies = replies
        self.prompts: list[str] = []
        self.n_images: list[int] = []

    async def __call__(self, content: list[dict[str, Any]], options: Any) -> ClaudeReply:
        self.prompts.append(content[-1]["text"])
        self.n_images.append(sum(1 for c in content if c["type"] == "image"))
        return ClaudeReply(self.replies[len(self.prompts) - 1], "", {"cost_usd": 0.01})


def test_review_loop_keeps_best_and_stops_at_accept(tmp_path: Path) -> None:
    skel, atlas, page = write_rig(tmp_path / "src")
    ctx = FakeCtx(tmp_path)
    ref_img = tmp_path / "ref.png"
    Image.new("RGB", (64, 32), "white").save(ref_img)
    better = {**SPEC, "hip": {"shift_x": 12, "dip_y": 2, "tilt": 3}}
    q = Scripted([{"score": 5, "critique": "hips too small", "spec": better},
                  {"score": 8.5, "critique": "close", "spec": better}])
    inputs = {"skeleton": [_ref(ctx, skel, "rig.json")], "atlas": [_ref(ctx, atlas, "a.atlas"), _ref(ctx, page, "p")],
              "ref": [BlobRef(**{**_ref(ctx, ref_img, "ref.png").model_dump(), "mime": "image/png"})],
              "spec": [asyncio.run(ctx.blobs.put_bytes(json.dumps(SPEC).encode()))]}
    res = asyncio.run(SpineReviewLoopExecutor(Settings(data_dir=tmp_path), q).run(
        ctx, {"name": "sway", "rounds": 4, "accept_score": 8, "skin": "top_0", "count": 3, "height": 100}, inputs))
    assert len(q.prompts) == 2 and q.n_images == [2, 2]
    assert "hips too small" in q.prompts[1] and "Do not reverse" in q.prompts[1]
    review = json.loads(ctx.blobs.path(res.outputs["review"][0].hash).read_text())
    assert review["best_round"] == 2 and review["accepted"] and res.meta["score_history"] == [5, 8.5]
    assert review["rounds"][0]["diff"] == [{"path": "hip.shift_x", "old": 10, "new": 12}]
    assert json.loads(ctx.blobs.path(res.outputs["spec"][0].hash).read_text()) == better
    assert len(res.outputs["strips"]) == 2


def test_claude_task_named_schema_and_score_meta(tmp_path: Path) -> None:
    assert named_schema("motion_review")["required"] == ["score", "critique", "seam_visible", "spec"]
    with pytest.raises(StepError):
        named_schema("nope")
    q = Scripted([{"score": 6.5, "critique": "ok", "spec": {}}])
    res = asyncio.run(ClaudeTaskExecutor(Settings(data_dir=tmp_path), q).run(FakeCtx(tmp_path), {"prompt": "x"}, {}))
    assert res.meta["score"] == 6.5 and res.meta["critique"] == "ok"


def test_recipe_file_video_inputs() -> None:
    raw = (Path(__file__).parents[1] / "studio_core/recipes/spine-anim-ref.yaml").read_bytes()
    r = parse_recipe(raw)
    assert r.inputs["skeleton"]["ext"] == ["json"] and r.inputs["atlas"]["siblings"] == "atlas_pages"
    vals = r.coerce_inputs({"skeleton": "a" * 64, "atlas": ["b" * 64, "c" * 64], "video": "d" * 64, "brief": "x"})
    assert vals["atlas"] == ["b" * 64, "c" * 64] and vals["rounds"] == 3
    with pytest.raises(RecipeError, match="ext needs a file input"):
        parse_recipe(b"id: x\ninputs: {a: {type: text, ext: [json]}}\nsteps: [{id: s, kind: op.x}]\n")


def test_sniff_video_and_window() -> None:
    assert sniff_mime(b"\x00\x00\x00\x18ftypmp42" + b"\0" * 8) == "video/mp4"
    assert sniff_mime(bytes.fromhex("1a45dfa3") + b"\0" * 8) == "video/webm"
    assert parse_window("1.6-5.6") == (1.6, 5.6) and parse_window("auto") == (None, None)
    with pytest.raises(StepError):
        parse_window("5-1")


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not on PATH")
def test_video_frames_with_ffmpeg(tmp_path: Path) -> None:
    video = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=white:s=160x120:d=2",
                    "-f", "lavfi", "-i", "color=red:s=20x40:d=2", "-filter_complex",
                    "[0][1]overlay=x='60+20*sin(t*3)':y=40", "-pix_fmt", "yuv420p", str(video)], check=True)
    ctx = FakeCtx(tmp_path)
    ref = _ref(ctx, video, "v.mp4")
    res = asyncio.run(VideoFrames(Settings(data_dir=tmp_path)).run(
        ctx, {"fps": 4, "window": "0.5-1.5", "tile": "2x2", "cell": 60}, {"video": [ref]}))
    assert res.meta["count"] == 4 and res.meta["times"][0] == 0.5
    assert len(res.outputs["sheets"]) == 1 and len(res.outputs["frames"]) == 4
    x0, y0, x1, y1 = res.meta["crop"]
    assert x0 >= 30 and x1 <= 110 and y0 >= 30


def limb(mesh_span: bool) -> dict[str, Any]:
    doc = skeleton()
    doc["bones"] += [{"name": "upper", "parent": "chest", "rotation": 180, "length": 40},
                     {"name": "lower", "parent": "upper", "x": 40, "rotation": -8, "length": 30},
                     {"name": "hand", "parent": "lower", "x": 30, "rotation": -30, "length": 10}]
    n = len(doc["bones"])
    doc["slots"] += [{"name": "upper", "bone": "upper", "attachment": "box"},
                     {"name": "lower", "bone": "lower", "attachment": "box"},
                     {"name": "hand", "bone": "hand", "attachment": "box"}]
    atts = doc["skins"][1]["attachments"]
    atts["upper"] = {"box": {"width": 16, "height": 8}}
    atts["hand"] = {"box": {"width": 16, "height": 8}}
    if mesh_span:
        w = [1, n - 3, 0, 0, 1, 2, n - 3, 40, 0, 0.5, n - 2, 0, 0, 0.5, 1, n - 2, 30, 0, 1]
        atts["lower"] = {"box": {"type": "mesh", "uvs": [0, 0, 1, 0, 1, 1], "triangles": [0, 1, 2], "vertices": w,
                                 "width": 16, "height": 8}}
    else:
        atts["lower"] = {"box": {"width": 16, "height": 8}}
    return doc


def test_bend_range() -> None:
    assert sa.bend_range(-8.36, 20) == pytest.approx((-11.64, 11.36))
    assert sa.bend_range(8.76, 20) == pytest.approx((-11.76, 11.24))
    assert sa.bend_range(-30, 20) == (-20, 20)
    assert sa.bend_range(28.39, 20) == (-20, 20)
    lo, hi = sa.bend_range(0, 20)
    assert lo == 0 and hi == 20 or (lo == -3 and hi == 20)


def test_hard_seam_joints_and_clamp() -> None:
    hard = sa.joint_limits(limb(False))
    assert set(hard) == {"lower", "hand"} and hard["lower"]["limit"] == 20
    soft = sa.joint_limits(limb(True))
    assert "lower" not in soft and "hand" in soft
    assert "joint lower: hard seam" in sa.rig_summary(limb(False))
    assert "Weighted (bend freely): " in sa.rig_summary(limb(True))
    # a spec may tighten a hard seam but not loosen it
    assert sa.joint_limits(limb(False), {"elbow_max": 45, "joint_limits": {"lower": 12}})["lower"]["limit"] == 12
    assert sa.joint_limits(limb(False), {"joint_limits": {"lower": 60}})["lower"]["limit"] == 20

    doc = limb(False)
    spec = {**SPEC, "arm_keys": {"lower": [[0, 0], [0.5, -47], [1.0, 10], [1.5, 0]]}, "arm_lag": {"lower": 0.1}}
    anim = sa.build_animation(spec, limits=sa.joint_limits(doc, spec))
    keys = anim["bones"]["lower"]["rotate"]
    assert [k["time"] for k in keys[:3]] == [0, 0.6, 1.1]
    doc["animations"]["sway"] = anim
    for i in range(41):
        total = -8 + rt.Pose(doc, "sway", i * 0.05).local["lower"].rotation + 8
        assert -20 - 1e-6 <= total <= 3 + 1e-6
    bone, t, v = sa.deepest_bend(doc, "sway", sa.joint_limits(doc, spec))
    assert bone == "lower" and t == pytest.approx(0.6) and v == pytest.approx(-12)


def test_joint_zoom_render(tmp_path: Path) -> None:
    from studio_core.ops.spine_ref import render_joint_zoom
    _, atlas, page = write_rig(tmp_path)
    img = render_joint_zoom(limb(False), rt.RegionImages.from_files(atlas, [page]), "idle", 0.5, "lower", ["top_0"],
                            size=200)
    assert img.size == (200, 200) and (np.asarray(img)[..., 0] > 200).any()


def covered_limb(order_above: bool = True) -> dict[str, Any]:
    doc = limb(False)
    sleeve = {"name": "sleeve", "bone": "upper", "attachment": "box"}
    if order_above:
        doc["slots"].append(sleeve)
    else:
        doc["slots"].insert(0, sleeve)
    doc["skins"].append({"name": "outerwear/coat_0",
                         "attachments": {"sleeve": {"box": {"x": 40, "width": 30, "height": 12}}}})
    return doc


def test_joint_covers_and_limits() -> None:
    doc = covered_limb()
    assert sa.joint_covers(doc, ["top_0", "coat_0"]) == {"lower": "outerwear/coat_0"}
    assert sa.joint_covers(doc, ["top_0"]) == {}
    assert sa.joint_covers(doc, ["top_0", "coat_0"], "none") == {}
    assert sa.joint_covers(doc, [], "hand, nope") == {"hand": "joint_cover"}
    assert sa.joint_covers(covered_limb(order_above=False), ["top_0", "coat_0"]) == {}
    covers = {"lower": "outerwear/coat_0"}
    lims = sa.joint_limits(doc, None, covers)
    assert lims["lower"]["limit"] == 40 and lims["lower"]["cover"] == "outerwear/coat_0"
    assert lims["hand"]["limit"] == 20
    assert sa.joint_limits(doc, {"joint_limits": {"lower": 60}}, covers)["lower"]["limit"] == 40
    assert sa.joint_limits(doc, {"joint_limits": {"lower": 25}}, covers)["lower"]["limit"] == 25
    assert "joint lower: hard seam covered by outerwear/coat_0" in sa.rig_summary(doc, covers=covers)


def two_legs() -> dict[str, Any]:
    doc = skeleton()
    doc["bones"] += [{"name": "thigh2", "parent": "hip", "x": 30, "rotation": -90, "length": 50},
                     {"name": "shin2", "parent": "thigh2", "x": 50, "length": 50},
                     {"name": "foot_ik2", "parent": "root", "x": 40, "y": 5}]
    doc["ik"].append({"name": "leg2", "bones": ["thigh2", "shin2"], "target": "foot_ik2", "bendPositive": False})
    return doc


def test_bounce_hip_and_knee_gap() -> None:
    doc = two_legs()
    assert [c["knee"] for c in sa.leg_chains(doc)] == ["shin", "shin2"] and sa.hip_bone(doc) == "hip"
    assert "Hip for bounce: hip" in sa.rig_summary(doc)
    assert sa.hip_bone(skeleton()) is None

    spec = {**SPEC, "hip": {**SPEC["hip"], "shift_x": 4}, "bounce": [[0, 0], [1.0, 6], [0.5, 6], [1.8, 0]]}
    anim = sa.build_animation(spec)
    hip = anim["bones"]["hip"]
    assert "translate" not in hip and {"translatex", "translatey", "rotate"} <= set(hip)
    ys = [(k["time"], k["value"]) for k in hip["translatey"]]
    assert ys[:5] == [(0, 0), (0.5, -6), (0.75, 0.9), (1.0, -6), (1.5, 0)] and ys[-1] == (2.0, 0)
    with pytest.raises(sa.SpecError):
        sa.build_animation({**spec, "bounce": [[2.5, 6]]})

    doc["animations"]["sway"] = anim
    gap = sa.knee_gap(doc, "sway")
    assert gap is not None and not gap["overlap"] and gap["knees"] == ["shin", "shin2"]
    doc["animations"]["cross"] = {"bones": {"foot_ik2": {"translate": [{"x": 0}, {"time": 1, "x": -80}]}}}
    cross = sa.knee_gap(doc, "cross")
    assert cross is not None and cross["overlap"] and cross["time"] == pytest.approx(1.0)


def test_weld_joints_bends_as_one_piece() -> None:
    from studio_core.ops import spine_weld as sw

    doc = limb(False)
    doc["skins"][1]["attachments"]["lower"]["box"] = {"x": 15, "width": 30, "height": 8}
    doc["skins"][1]["attachments"]["upper"]["box"] = {"x": 28, "width": 24, "height": 8}
    assert sw.resolve_joints(doc, "auto") == ["lower", "hand"]
    assert sw.resolve_joints(doc, "auto", {"arm_keys": {"lower": [[0, 0]]}}) == ["lower"]
    assert sw.resolve_joints(doc, "hand, ghost") == ["hand"] and sw.resolve_joints(doc, "none") == []

    text = json.dumps(doc, indent=1)
    out, report = sw.weld_text(text, ["lower"])
    assert report["changed"] == 2 and report["skipped"] == []
    welded = json.loads(out)
    assert {k: v for k, v in welded.items() if k != "skins"} == {k: v for k, v in doc.items() if k != "skins"}
    assert out[:out.index('"skins"')] == text[:text.index('"skins"')]
    atts = welded["skins"][1]["attachments"]
    upper, lower = atts["upper"]["box"], atts["lower"]["box"]
    assert upper["type"] == lower["type"] == "mesh" and upper["hull"] == len(upper["uvs"]) // 2
    assert atts["hand"]["box"] == doc["skins"][1]["attachments"]["hand"]["box"]
    assert "lower" not in [j["bone"] for j in sa._tip_joints(welded) if j["hard"]]

    names = [b["name"] for b in doc["bones"]]
    bone_of = {s["name"]: s["bone"] for s in doc["slots"]}
    setup_w, setup_o = rt.Pose(welded), rt.Pose(doc)
    for slot in ("upper", "lower"):
        pts = rt.mesh_world(atts[slot]["box"], setup_w, bone_of[slot])
        o = rt._region_corners(doc["skins"][1]["attachments"][slot]["box"], setup_o, bone_of[slot])
        assert pts.min(0) == pytest.approx(o.min(0), abs=0.02) and pts.max(0) == pytest.approx(o.max(0), abs=0.02)
    # bent 40 deg: the far ends follow their own bone, the two pieces meet at the elbow without a gap
    welded["animations"]["bend"] = {"bones": {"lower": {"rotate": [{"value": 0}, {"time": 1, "value": -40}]}}}
    p = rt.Pose(welded, "bend", 1)
    up, lo = rt.mesh_world(upper, p, "upper"), rt.mesh_world(lower, p, "lower")
    elbow = p.world["lower"][1]
    assert np.linalg.norm(up - elbow, axis=1).min() < 5 and np.linalg.norm(lo - elbow, axis=1).min() < 5
    weights = np.array(lower["vertices"], dtype=float)
    assert names.index("upper") in weights.astype(int).tolist()

    deformed = limb(False)
    deformed["animations"]["d"] = {"attachments": {"top/top_0": {"upper": {"box": {"deform": [{"time": 0}]}}}}}
    _, rep = sw.weld_text(json.dumps(deformed), ["lower"])
    assert rep["skipped"] == ["top/top_0/upper/box"]


def test_welded_joint_limits_and_summary() -> None:
    doc = limb(False)
    lims = sa.joint_limits(doc, {"joint_limits": {"lower": 60, "hand": 50}}, None, ["lower"])
    assert lims["lower"]["limit"] == 40 and lims["lower"]["welded"] and lims["hand"]["limit"] == 20
    assert sa.joint_limits(doc, {"joint_limits": {"lower": 30}}, None, ["lower"])["lower"]["limit"] == 30
    assert "joint lower: welded" in sa.rig_summary(doc, welded=["lower"])


def test_weld_op_feeds_add_anim(tmp_path: Path) -> None:
    from studio_core.ops.spine_ref import SpineWeldJoints

    skel, _, _ = write_rig(tmp_path / "src")
    (tmp_path / "src" / "rig.json").write_text(json.dumps(limb(False)), encoding="utf-8")
    ctx = FakeCtx(tmp_path)
    sk = _ref(ctx, skel, "rig.json")
    res = asyncio.run(SpineWeldJoints().run(ctx, {"joints": "auto"}, {"skeleton": [sk]}))
    assert res.meta["joints"] == ["lower", "hand"] and res.outputs["original"] == [sk]
    spec = asyncio.run(ctx.blobs.put_bytes(json.dumps(
        {**SPEC, "arm_keys": {"lower": [[0, 0], [0.5, -60], [1.5, 0]]}}).encode()))
    added = asyncio.run(SpineAddAnim().run(ctx, {"name": "sway"}, {
        "skeleton": res.outputs["skeleton"], "spec": [spec], "welded": res.outputs["joints"]}))
    keys = json.loads(ctx.blobs.path(added.outputs["skeleton"][0].hash).read_text())["animations"]["sway"]
    assert min(k["value"] for k in keys["bones"]["lower"]["rotate"]) == pytest.approx(-32)
    none = asyncio.run(SpineWeldJoints().run(ctx, {"joints": "none"}, {"skeleton": [sk]}))
    assert none.outputs["skeleton"] == [sk] and none.meta["joints"] == []


def test_review_loop_rejects_visible_seam(tmp_path: Path) -> None:
    skel, atlas, page = write_rig(tmp_path / "src")
    ctx = FakeCtx(tmp_path)
    ref_img = tmp_path / "ref.png"
    Image.new("RGB", (64, 32), "white").save(ref_img)
    q = Scripted([{"score": 9, "critique": "seam at elbow", "seam_visible": True, "spec": SPEC},
                  {"score": 8.2, "critique": "clean", "seam_visible": False, "spec": SPEC}])
    welded = asyncio.run(ctx.blobs.put_bytes(json.dumps({"joints": ["shin"]}).encode()))
    inputs = {"skeleton": [_ref(ctx, skel, "rig.json")], "atlas": [_ref(ctx, atlas, "a.atlas"), _ref(ctx, page, "p")],
              "ref": [BlobRef(**{**_ref(ctx, ref_img, "ref.png").model_dump(), "mime": "image/png"})],
              "spec": [asyncio.run(ctx.blobs.put_bytes(json.dumps(SPEC).encode()))], "welded": [welded]}
    res = asyncio.run(SpineReviewLoopExecutor(Settings(data_dir=tmp_path), q).run(
        ctx, {"name": "sway", "rounds": 3, "accept_score": 8, "skin": "top_0", "count": 3, "height": 100}, inputs))
    review = json.loads(ctx.blobs.path(res.outputs["review"][0].hash).read_text(encoding="utf-8"))
    assert len(q.prompts) == 2 and review["best_round"] == 2 and review["accepted"]
    assert review["rounds"][0]["seam_visible"] and review["welded"] == ["shin"]
    assert "mesh weighted" in res.meta["warning"] and "Lưu ý" in res.meta["critique"]


def test_weld_rule_is_per_piece_and_includes_ik_knees() -> None:
    from studio_core.ops import spine_weld as sw

    doc = limb(True)
    doc["skins"].append({"name": "solo/solo_0", "attachments": {"upper": {"box": {"x": 30, "width": 24, "height": 8}}}})
    doc["skins"][1]["attachments"]["upper"]["box"] = {"x": 30, "width": 24, "height": 8}
    skins, changed, _ = sw.weld(doc, ["lower"])
    by = {s["name"]: s["attachments"] for s in skins}
    assert by["top/top_0"]["lower"]["box"] == doc["skins"][1]["attachments"]["lower"]["box"]
    assert by["top/top_0"]["upper"]["box"]["type"] == "mesh"
    assert by["solo/solo_0"]["upper"]["box"] == {"x": 30, "width": 24, "height": 8} and changed == 1

    legs = two_legs()
    legs["slots"] += [{"name": s, "bone": s, "attachment": "box"} for s in ("thigh", "shin", "thigh2", "shin2")]
    legs["skins"][1]["attachments"].update({s: {"box": {"width": 16, "height": 8}} for s in ("thigh", "shin", "thigh2",
                                                                                            "shin2")})
    welds = ["thigh", "shin", "thigh2", "shin2"]
    assert sw.ik_joints(legs) == ["shin", "shin2"] and sw.resolve_joints(legs, "auto") == welds
    assert sw.resolve_joints(legs, "auto", {"arm_keys": {}}) == welds
    # socket (thigh hangs off a length-less hip): a torso piece on the hip is never dragged, the thigh piece blends
    legs["slots"].append({"name": "pelvis", "bone": "hip", "attachment": "box"})
    legs["skins"][1]["attachments"]["pelvis"] = {"box": {"y": -5, "width": 60, "height": 20}}
    skins, _, _ = sw.weld(legs, ["thigh"])
    welded_legs = {s["name"]: s["attachments"] for s in skins}["top/top_0"]
    assert welded_legs["pelvis"] == legs["skins"][1]["attachments"]["pelvis"]
    assert welded_legs["thigh"]["box"]["type"] == "mesh"
    # two hips side by side: the waistband piece lying over a thigh bends with it (wrap), the rest stays on the hip
    legs["skins"][1]["attachments"]["thigh"]["box"] = {"x": 20, "width": 40, "height": 30}
    skins, _, _ = sw.weld(legs, ["thigh", "thigh2"])
    pelvis = {s["name"]: s["attachments"] for s in skins}["top/top_0"]["pelvis"]["box"]
    assert pelvis["type"] == "mesh" and len(pelvis["uvs"]) // 2 > pelvis["hull"]
    bones = [b["name"] for b in legs["bones"]]
    v, k, moved, kept = pelvis["vertices"], 0, 0, 0
    for _ in range(len(pelvis["uvs"]) // 2):
        n = int(v[k])
        names = {bones[int(v[k + 1 + 4 * i])] for i in range(n)}
        moved += "thigh" in names
        kept += names == {"hip"}
        k += 1 + 4 * n
    assert moved and kept
    assert not sa.joint_limits(legs, None, None, ["shin"])["shin"].get("welded")


def arm_rig(mesh_span: bool = False) -> dict[str, Any]:
    """limb() with the upper arm hung off a length-less shoulder bone, the shape of a real arm chain."""
    doc = limb(mesh_span)
    i = next(i for i, b in enumerate(doc["bones"]) if b["name"] == "upper")
    doc["bones"].insert(i, {"name": "shoulder", "parent": "chest", "x": 25})
    doc["bones"][i + 1]["parent"] = "shoulder"
    return doc


def test_held_accessory_hangs_by_gravity() -> None:
    doc = arm_rig()
    doc["bones"].append({"name": "bag", "parent": "hand", "x": 10})
    doc["slots"].append({"name": "bag", "bone": "bag", "attachment": "box"})
    doc["skins"].append({"name": "bag/bag_1", "attachments": {"bag": {"box": {"y": -10, "width": 12, "height": 16}}}})
    assert sa.held_bones(doc) == ["bag"]
    assert sa.hang_plan(doc, {}, "none") == [] and sa.hang_plan(doc, {"hang": []}) == []
    plan = sa.hang_plan(doc, {})
    assert plan[0]["ancestors"][:4] == ["hand", "lower", "upper", "shoulder"] and plan[0]["lag"] == 0.05
    spec = {**SPEC, "arm_keys": {"upper": [[0, 0], [0.5, -40], [1.5, 0]]}}
    doc["animations"]["sway"] = sa.build_animation(spec, None, None, plan)
    keys = doc["animations"]["sway"]["bones"]["bag"]["rotate"]
    assert keys[0]["value"] == 0 and keys[-1]["value"] == 0 and keys[-1]["time"] == 2.0
    angle = lambda t: math.degrees(math.atan2(*rt.Pose(doc, "sway", t).world["bag"][0][::-1, 0]))  # noqa: E731
    arm = lambda t: math.degrees(math.atan2(*rt.Pose(doc, "sway", t).world["hand"][0][::-1, 0]))  # noqa: E731
    assert abs(arm(0.55) - arm(0)) > 30 and abs(angle(0.55) - angle(0)) < 5


def test_smooth_graph_offset_and_holds() -> None:
    keys = [(0.0, 0.0), (1.0, 10.0), (2.0, 20.0), (3.0, 0.0)]
    track = sa._rotate_track(keys, 0.4, smooth=True)
    vals = [rt.sample(track, i / 100, ("value",), 0.0)[0] for i in range(301)]
    assert max(vals) <= 20 + 1e-6 and min(vals) >= -1e-6
    assert vals[101] - vals[99] > 0.15  # keeps moving through the inner key
    eased = sa._rotate_track(keys, 0.4)
    assert abs(rt.sample(eased, 1.01, ("value",), 0.0)[0] - rt.sample(eased, 0.99, ("value",), 0.0)[0]) < 0.05
    assert sa._drift_holds([(0, 0), (1, -8), (2, -8), (3, 0)]) == [(0, 0), (1, -8), (2, -5.2), (3, 0)]

    spec = {**SPEC, "offset": {"arms": 0.04, "hip_tilt": 0.03}}
    anim = sa.build_animation(spec)["bones"]
    assert [k["time"] for k in anim["arm"]["rotate"][:2]] == [0, 0.54]
    assert anim["hip"]["rotate"][1]["time"] == pytest.approx(0.53)
    assert anim["arm"]["rotate"][1]["curve"] != sa.build_animation({**SPEC, "graph": "ease"})["bones"]["arm"][
        "rotate"][1]["curve"]


def skirt_rig() -> dict[str, Any]:
    doc = two_legs()
    doc["bones"] += [{"name": "sk1", "parent": "spine", "rotation": -180, "length": 15},
                     *({"name": f"sk{i}", "parent": f"sk{i - 1}", "x": 15, "length": 15} for i in range(2, 5))]
    doc["slots"].append({"name": "skirt", "bone": "hip", "attachment": "skirt"})
    doc["skins"].append({"name": "skirt/skirt_1", "attachments": {"skirt": {"skirt": {"y": -40, "width": 40,
                                                                                       "height": 80}}}})
    return doc


def test_skirt_mesh_and_spring() -> None:
    from studio_core.ops import spine_skirt as skm

    doc = skirt_rig()
    chains = skm.skirt_chains(doc)
    assert chains == [["sk1", "sk2", "sk3", "sk4"]] and skm.skirt_chains(doc, "none") == []
    assert skm.skirt_chains(doc, "sk1>sk2;ghost") == [["sk1", "sk2"]]
    text = json.dumps(doc)
    out, rep = skm.skirt_text(text, chains)
    assert rep["changed"] == ["skirt/skirt_1/skirt"]
    mesh = json.loads(out)["skins"][-1]["attachments"]["skirt"]["skirt"]
    assert mesh["type"] == "mesh" and len(mesh["uvs"]) // 2 > mesh["hull"]
    setup = rt.mesh_world(mesh, rt.Pose(json.loads(out)), "hip")
    assert setup.min(0) == pytest.approx([-20, 20], abs=0.05) and setup.max(0) == pytest.approx([20, 100], abs=0.05)

    plan = skm.skirt_plan(doc, {})
    assert plan["counter"] == ["spine"] and plan["chains"] == chains
    doc["animations"]["idle"]["bones"]["sk2"] = {"rotate": [{"value": 0}, {"time": 1, "value": -1.7}]}
    assert skm.keyed_chains(doc, chains) == {"idle": 1.7} and skm.keyed_chains(doc, chains, "idle") == {}
    assert skm.skirt_plan(doc, {"skirt": {"enabled": False}}) is None
    spec = {**SPEC, "secondary": [{"bones": ["sk1", "sk2"], "gain": 2, "falloff": 0, "lag": 0}]}
    bones = sa.build_animation(spec, None, None, None, plan)["bones"]
    keys = bones["sk2"]["rotate"]
    assert keys[0]["value"] == 0 and keys[-1]["value"] == 0 and keys[-1]["time"] == 2.0
    assert max(abs(k["value"]) for k in keys) > 0.1
    # the old secondary chain on the skirt bones is dropped: sk2 is keyed by the spring, not by gain*s
    assert len(keys) > len(SPEC["sway_signal"]) + 2


def test_fingers_follow_and_forced_weld() -> None:
    from studio_core.ops import spine_weld as sw

    doc = arm_rig()
    doc["bones"].append({"name": "finger", "parent": "hand", "x": 10, "length": 6})
    doc["slots"].append({"name": "finger", "bone": "finger", "attachment": "box"})
    doc["skins"][1]["attachments"]["finger"] = {"box": {"width": 6, "height": 4}}
    assert sa._hands(doc) == {"hand"} and sw.finger_joints(doc) == ["finger"]
    assert "finger!" in sw.resolve_joints(doc, "auto")
    plan = sa.follow_plan(doc, {})
    assert plan[0]["bone"] == "finger" and plan[0]["src"] == ["upper", "lower", "hand"]
    spec = {**SPEC, "arm_keys": {"upper": [[0, 0], [0.4, -40], [1.2, 30], [1.6, 0]]}}
    keys = sa.build_animation(spec, None, None, None, None, plan)["bones"]["finger"]["rotate"]
    assert keys[0]["value"] == 0 and keys[-1]["value"] == 0 and max(abs(k["value"]) for k in keys) <= 10
    skins, changed, _ = sw.weld(doc, ["finger!"])
    hand = {s["name"]: s["attachments"] for s in skins}["top/top_0"]["hand"]["box"]
    assert changed >= 1 and hand["type"] == "mesh"
