import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from studio_core.adapters.blender.clips import expected_frames, parse_anims, plan_clips
from studio_core.adapters.blender.executor import BlenderAnimateDirsExecutor, BlenderRenderDirsExecutor
from studio_core.config import Settings
from studio_core.contracts import ExecutorRegistry, StepError
from studio_core.engine.library import validate
from studio_core.ops import register
from studio_core.ops.sheet_layout import clip_layout
from test_ops_executors import FakeCtx, load, put_img

ANGLES = [0, 45, 90, 135, 180, 225, 270, 315]
RECIPES = Path(__file__).resolve().parents[1] / "studio_core" / "recipes"


@pytest.fixture
def ctx(tmp_path: Path) -> FakeCtx:
    return FakeCtx(tmp_path)


@pytest.fixture
def reg() -> ExecutorRegistry:
    r = ExecutorRegistry()
    register(r, None, None, None)
    return r


def test_parse_anims_forms() -> None:
    assert parse_anims(None) == ["walk"] and parse_anims("") == ["walk"]
    assert parse_anims("idle, Walk run,attack") == ["idle", "walk", "run", "attack"]
    assert parse_anims(["run", "run", "idle"]) == ["run", "idle"]
    with pytest.raises(ValueError):
        parse_anims("dance")


def test_plan_clips_defaults_and_legacy_frames() -> None:
    assert plan_clips({"frames": 12}) == [{"name": "walk", "frames": 12, "fps": 10, "loop": True}]
    clips = plan_clips({"anims": "idle,walk,run,attack", "frames": 6, "attack_frames": 10})
    assert [(c["name"], c["frames"], c["loop"]) for c in clips] == [
        ("idle", 8, True), ("walk", 6, True), ("run", 8, True), ("attack", 10, False)]
    assert expected_frames(clips, 8) == (8 + 6 + 8 + 10) * 8
    assert plan_clips({"anims": "idle", "idle_frames": 99})[0]["frames"] == 24


def test_clip_layout_rows_and_padding() -> None:
    clips = [{"name": "walk", "frames": 8, "fps": 10, "loop": True},
             {"name": "attack", "frames": 6, "fps": 12, "loop": False}]
    slots, cols, meta, row_angles = clip_layout(clips, ANGLES, 14 * 8)
    assert cols == 8 and len(slots) == 16 * 8
    assert meta == [{"name": "walk", "row0": 0, "rows": 8, "frames": 8, "fps": 10, "loop": True},
                    {"name": "attack", "row0": 8, "rows": 8, "frames": 6, "fps": 12, "loop": False}]
    assert row_angles == ANGLES * 2
    assert slots[8 * 8:8 * 8 + 8] == [64, 65, 66, 67, 68, 69, None, None]
    with pytest.raises(ValueError):
        clip_layout(clips, ANGLES, 100)


async def _anim_meta(ctx: FakeCtx, clips: list[dict[str, Any]], size: int = 16):
    info = {"pivot": [size / 2, size - 1], "width": size, "height": size, "angles": ANGLES, "clips": clips}
    return await ctx.blobs.put_bytes(json.dumps(info).encode(), name="anim.json", mime="application/json")


async def test_sheet_lays_out_clip_blocks(ctx: FakeCtx, reg: ExecutorRegistry) -> None:
    clips = [{"name": "idle", "frames": 2, "fps": 6, "loop": True},
             {"name": "attack", "frames": 3, "fps": 12, "loop": False}]
    n = 5 * 8
    refs = [await put_img(ctx, Image.new("RGBA", (16, 16), (i * 6, 0, 0, 255)), f"f{i}.png") for i in range(n)]
    pivot = await _anim_meta(ctx, clips)
    for normal in (False, True):
        res = await reg.resolve("op.sheet").run(ctx, {"cols": 2, "normal": normal},
                                                {"images": refs, "pivot": [pivot]})
        meta = res.meta
        assert meta["cols"] == 3 and meta["rows"] == 16
        assert meta["clips"] == [{"name": "idle", "row0": 0, "rows": 8, "frames": 2, "fps": 6, "loop": True},
                                 {"name": "attack", "row0": 8, "rows": 8, "frames": 3, "fps": 12, "loop": False}]
        assert len(meta["row_angles"]) == meta["rows"] and meta["row_angles"][8] == 0
        assert meta.get("normal", False) is normal
        sheet = load(ctx, res.outputs["sheet"][0])
        assert sheet.getpixel((2 * 16 + 1, 1))[3] == 0  # idle row padded past its 2 frames
        assert sheet.getpixel((1, 8 * 16 + 1))[0] == 16 * 6  # attack 0°, frame 0 = source index 16
        assert meta["frames"][2] is None and meta["frames"][3] == refs[2].hash
    with pytest.raises(StepError):
        await reg.resolve("op.sheet").run(ctx, {}, {"images": refs[:-1], "pivot": [pivot]})


async def test_optional_steps_pass_empty(ctx: FakeCtx, reg: ExecutorRegistry) -> None:
    for kind in ("op.sheet", "op.downscale", "op.mirror_dirs"):
        res = await reg.resolve(kind).run(ctx, {"optional": True}, {"images": []})
        assert all(v == [] for v in res.outputs.values())
        with pytest.raises(StepError):
            await reg.resolve(kind).run(ctx, {}, {"images": []})


async def test_mirror_flips_normal_x(ctx: FakeCtx, reg: ExecutorRegistry) -> None:
    refs = [await put_img(ctx, Image.new("RGBA", (4, 4), (40, 128, 200, 255)), f"n{i}.png") for i in range(5)]
    res = await reg.resolve("op.mirror_dirs").run(ctx, {"count": 8, "normal": True}, {"images": refs})
    out = res.outputs["images"]
    assert out[:5] == refs and out[5].name == "dir_225_n.png"
    assert load(ctx, out[5]).getpixel((0, 0)) == (215, 128, 200, 255)
    plain = await reg.resolve("op.mirror_dirs").run(ctx, {"count": 8}, {"images": refs})
    assert load(ctx, plain.outputs["images"][5]).getpixel((0, 0))[0] == 40


class FakeBlender:
    version = "fake"

    def __init__(self, drop: int = 0):
        self.drop = drop
        self.req: dict[str, Any] = {}

    async def call(self, cmd: str, params: dict[str, Any], on_event=None, timeout: float = 0) -> dict[str, Any]:
        self.req = params
        out = Path(params["out_dir"])
        out.mkdir(parents=True, exist_ok=True)
        frames = []
        clips = params.get("clips") or [{"name": "walk", "frames": 2}]
        for c in clips:
            for fi in range(c["frames"]):
                for a in ANGLES:
                    f = out / f"{c['name']}_{a}_{fi}.png"
                    Image.new("RGBA", (8, 8), (255, 0, 0, 255 if a else 128)).save(f)
                    item = {"clip": c["name"], "angle": float(a), "frame": fi, "file": str(f)}
                    if params.get("normals"):
                        n = out / f"{c['name']}_{a}_{fi}_n.png"
                        Image.new("RGBA", (8, 8), (128, 128, 255, 255)).save(n)
                        item["normal"] = str(n)
                    frames.append(item)
        frames = frames[self.drop:]
        # Reversed so the executor has to restore clip-major order itself.
        return {"frames": frames[::-1], "pivot": [4, 7], "res": 8, "frames_per_dir": 3, "elevation": 30,
                "ortho_scale": 1.0, "weights": "distance", "engine": "BLENDER_EEVEE", "total_s": 0.1}


async def _anim_inputs(ctx: FakeCtx) -> dict[str, Any]:
    joints = {k: [0, 0] for k in ("head_top neck pelvis shoulder_l elbow_l wrist_l hand_tip_l shoulder_r elbow_r "
                                   "wrist_r hand_tip_r hip_l knee_l ankle_l foot_tip_l hip_r knee_r ankle_r "
                                   "foot_tip_r").split()}
    mesh = await ctx.blobs.put_bytes(b"glb", name="m.glb")
    j = await ctx.blobs.put_bytes(json.dumps({"joints": joints}).encode())
    front = await ctx.blobs.put_bytes(json.dumps({"width": 512, "height": 512, "pivot": [256, 500],
                                                  "ortho_scale": 1.0}).encode())
    return {"mesh": [mesh], "joints": [j], "front": [front]}


async def test_animate_dirs_orders_clips_and_checks_count(ctx: FakeCtx, tmp_path: Path) -> None:
    fake = FakeBlender()
    ex = BlenderAnimateDirsExecutor(Settings(data_dir=tmp_path / "d"), fake)  # type: ignore[arg-type]
    inputs = await _anim_inputs(ctx)
    res = await ex.run(ctx, {"anims": "idle,attack", "idle_frames": 2, "attack_frames": 3, "normals": True}, inputs)
    assert [c["name"] for c in fake.req["clips"]] == ["idle", "attack"] and fake.req["normals"] is True
    names = [r.name for r in res.outputs["images"]]
    assert len(names) == 40 and names[0] == "idle_dir_000_f00.png" and names[1] == "idle_dir_000_f01.png"
    assert names[2] == "idle_dir_045_f00.png" and names[16] == "attack_dir_000_f00.png"
    normals = res.outputs["normal_frames"]
    assert len(normals) == 40 and normals[0].name == "idle_dir_000_f00_n.png"
    assert load(ctx, normals[0]).getpixel((0, 0)) == (128, 128, 255, 128)  # alpha copied from the colour frame
    info = json.loads(ctx.blobs.path(res.outputs["meta"][0].hash).read_bytes())
    assert info["clips"] == [{"name": "idle", "frames": 2, "fps": 6, "loop": True},
                             {"name": "attack", "frames": 3, "fps": 12, "loop": False}]
    assert info["angles"] == [float(a) for a in ANGLES]
    with pytest.raises(StepError) as e:
        await BlenderAnimateDirsExecutor(Settings(data_dir=tmp_path / "d"), FakeBlender(drop=1)).run(  # type: ignore[arg-type]
            ctx, {"anims": "idle", "idle_frames": 2}, inputs)
    assert e.value.code == "blender_bad_output"
    legacy = await ex.run(ctx, {"frames": 2}, inputs)
    assert fake.req["clips"] == [{"name": "walk", "frames": 2, "fps": 10, "loop": True}]
    assert legacy.outputs["normal_frames"] == [] and len(legacy.outputs["images"]) == 16


async def test_render_dirs_normal_frames(ctx: FakeCtx, tmp_path: Path) -> None:
    class Fake:
        version = "fake"

        async def call(self, cmd, params, on_event=None, timeout=0):
            out = Path(params["out_dir"])
            out.mkdir(parents=True, exist_ok=True)
            frames = []
            for a in (0, 90):
                f, n = out / f"{a}.png", out / f"{a}_n.png"
                Image.new("RGBA", (4, 4), (9, 9, 9, 200)).save(f)
                Image.new("RGBA", (4, 4), (10, 128, 250, 255)).save(n)
                frames.append({"angle": float(a), "file": str(f), "normal": str(n)})
            return {"frames": frames, "pivot": [2, 3], "res": 4, "elevation": 30, "ortho_scale": 1.0,
                    "engine": "BLENDER_EEVEE", "supersample": 1, "radius": 1, "height": 1, "verts": 3, "total_s": 0}

    mesh = await ctx.blobs.put_bytes(b"glb", name="m.glb")
    ex = BlenderRenderDirsExecutor(Settings(data_dir=tmp_path / "d"), Fake())  # type: ignore[arg-type]
    res = await ex.run(ctx, {"normals": True}, {"mesh": [mesh]})
    assert [r.name for r in res.outputs["normal_frames"]] == ["dir_000_n.png", "dir_090_n.png"]
    assert load(ctx, res.outputs["normal_frames"][1]).getpixel((0, 0)) == (10, 128, 250, 200)
    plain = await ex.run(ctx, {}, {"mesh": [mesh]})
    assert plain.outputs["normal_frames"] == []


def test_anim_recipes_lint_clean() -> None:
    for name in ("sprite-8dir-anim", "sprite-8dir-walk", "sprite-8dir-3d-mv"):
        _, issues = validate((RECIPES / f"{name}.yaml").read_text(encoding="utf-8"), None)
        assert issues == [], name
