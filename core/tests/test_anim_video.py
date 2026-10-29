from __future__ import annotations

import asyncio
import io
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image, ImageDraw

from studio_core.adapters.anim_motion import SUFFIX, ClaudeAnimMotionExecutor, build_prompt, finalize, manual
from studio_core.adapters.claude import ClaudeReply
from studio_core.bootstrap import plan
from studio_core.contracts import RunStatus, StepError, StepResult
from studio_core.engine.kinds import CATALOG
from studio_core.engine.library import validate
from studio_core.engine.recipe import load_recipe_file
from studio_core.export.sheet import layout
from studio_core.ops.anim_video import (ANIM_VIDEO_OPS, AnimLoop, action_span, anchor, anim_loop, crossfade_seam,
                                        descriptors, find_cycle, mask_of, prep_canvas, resolve_loop, sample,
                                        similarity_matrix, to_rgba, union_box)
from test_engine_runner import Env, FakeExec
from test_ops_executors import FakeCtx, put_img

RECIPES = Path(__file__).resolve().parents[1] / "studio_core" / "recipes"
GREY = (128, 128, 128)
BODY = (200, 60, 50)
SKIN = (240, 200, 160)


def runner(i: int, period: int = 12, w: int = 160, h: int = 160, jx: int = 0, jy: int = 0,
           bg: tuple[int, int, int] | None = None) -> np.ndarray:
    """A little figure running in place: wide red torso, thin legs and a small fist circling with `period`; (jx, jy)
    shifts the whole frame like camera shake / matting jitter."""
    im = Image.new("RGBA", (w, h), (*bg, 255) if bg else (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    t = 2 * math.pi * i / period
    cx, ground, bob = w // 2 + jx, round(h * 0.875) + jy, abs(math.sin(t)) * 3
    d.rectangle((cx - 18, 46 - bob + jy, cx + 18, 92 - bob + jy), fill=(*BODY, 255))
    d.ellipse((cx - 10, 24 - bob + jy, cx + 10, 46 - bob + jy), fill=(*SKIN, 255))
    for ph in (0, math.pi):
        fx = cx + 12 * math.sin(t + ph)
        fy = ground - max(0.0, 8 * math.cos(t + ph))
        d.line((cx, 92 - bob + jy, fx, fy), fill=(40, 60, 180, 255), width=3)
    ax, ay = cx + 22 * math.cos(t), 66 + 10 * math.sin(t) + jy
    d.ellipse((ax - 4, ay - 4, ax + 4, ay + 4), fill=(250, 220, 40, 255))
    return np.asarray(im)


def slasher(i: int, n: int = 40, w: int = 200, h: int = 160) -> np.ndarray:
    """Stands still for 8 frames, swings a blade over 16 frames, then holds the starting pose again."""
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle((90, 50, 110, 140), fill=(*BODY, 255))
    k = min(max(i - 8, 0), 16)
    ang = math.pi * math.sin(math.pi * k / 16)
    d.line((110, 80, 110 + 60 * math.cos(-ang / 2), 80 + 60 * math.sin(-ang / 2)), fill=(220, 220, 230, 255), width=5)
    return np.asarray(im)


def jitter(n: int, seed: int = 3) -> list[tuple[int, int]]:
    rng = np.random.default_rng(seed)
    return [(int(rng.integers(-4, 5)), int(rng.integers(-3, 4))) for _ in range(n)]


def body_x(cell: Image.Image) -> float:
    """Centre of the head: nothing ever overlaps it, unlike the torso the fist passes over."""
    a = np.asarray(cell.convert("RGBA")).astype(int)
    skin = (np.abs(a[..., :3] - SKIN).sum(-1) < 40) & (a[..., 3] > 200)
    return float(np.nonzero(skin)[1].mean())


def ground_y(cell: Image.Image) -> int:
    return int(np.nonzero(np.asarray(cell.convert("RGBA"))[..., 3] > 32)[0].max())


@pytest.mark.parametrize("period", [10, 12, 16])
def test_find_cycle_recovers_the_period(period: int) -> None:
    frames = [runner(i, period, jx=dx, jy=dy) for i, (dx, dy) in enumerate(jitter(49))]
    cells, rep = anim_loop(frames, loop=True, n_out=12, lock="feet", crossfade=1, size=128)
    assert rep["period"] == period and rep["seam_similarity"] > 0.95
    assert len(cells) == 12 and all(c.size == (128, 128) for c in cells)


def test_loop_locks_pivot_within_one_pixel() -> None:
    shake = jitter(49)
    frames = [runner(i, jx=dx, jy=dy) for i, (dx, dy) in enumerate(shake)]
    cells, rep = anim_loop(frames, loop=True, n_out=12, lock="feet", crossfade=0, size=160)
    px, py = rep["pivot"]
    assert rep["pivot_error_px"] <= 1.0
    # Measured independently of the op: the torso stays put and the lowest foot sits on the pivot line.
    xs = [body_x(c) for c in cells]
    assert max(abs(x - px) for x in xs) <= 1.0, xs
    assert all(abs(ground_y(c) - py) <= 1 for c in cells)
    raw_spread = max(dx for dx, _ in shake) - min(dx for dx, _ in shake)
    assert raw_spread >= 6 and max(xs) - min(xs) <= 1.5


def test_free_axis_keeps_the_jump() -> None:
    frames = []
    for i in range(24):
        lift = round(30 * math.sin(math.pi * i / 23))
        frames.append(runner(0, jy=-lift))
    cells, rep = anim_loop(frames, loop=False, n_out=8, lock="x", crossfade=0, size=160, start=0, end=24)
    ys = [ground_y(c) for c in cells]
    assert max(ys) - min(ys) >= 20 and abs(ys[0] - rep["pivot"][1]) <= 1


def test_oneshot_span_and_crossfade() -> None:
    frames = [slasher(i) for i in range(40)]
    box = union_box(frames)
    sim = similarity_matrix(descriptors(frames, box))
    s, e, peak = action_span(sim)
    assert 6 <= s <= 8 and 22 <= e <= 27 and peak > 0.05
    cells, rep = anim_loop(frames, loop=False, n_out=8, lock="feet", crossfade=1, size=128)
    assert rep["indices"][0] == rep["start"] and rep["indices"][-1] == rep["end"] - 1 and len(cells) == 8

    seq = [runner(i, jx=i % 5) for i in range(30)]
    seg, k = crossfade_seam(seq, 3, 12, 2)
    assert k == 2 and len(seg) == 12 and np.array_equal(seg[0], seq[3]) and not np.array_equal(seg[-1], seq[14])
    head, k0 = crossfade_seam(seq, 0, 12, 2)
    assert k0 == 2 and not np.array_equal(head[0], seq[0]) and np.array_equal(head[-1], seq[11])
    assert sample(12, 12, True) == list(range(12)) and sample(13, 5, False) == [0, 3, 6, 9, 12]


def test_find_cycle_prefers_fundamental_and_short_clips() -> None:
    frames = [runner(i, 8) for i in range(40)]
    sim = similarity_matrix(descriptors(frames, union_box(frames)))
    assert find_cycle(sim)[1] == 8
    assert find_cycle(sim[:4, :4]) == (0, 4, 0.0)


def test_prep_and_backdrop_key() -> None:
    img = Image.new("RGBA", (300, 500), (0, 0, 0, 0))
    ImageDraw.Draw(img).rectangle((100, 100, 199, 399), fill=(*BODY, 255))
    canvas, info = prep_canvas(img, 640 * 640, GREY)
    assert canvas.size == (640, 640) and info["width"] == 640
    x0, y0, x1, y1 = info["bbox"]
    assert y1 == round(0.86 * 640) and abs((x0 + x1) / 2 - 320) <= 1 and y1 - y0 == round(0.6 * 640)
    assert canvas.getpixel((5, 5)) == GREY and canvas.getpixel((320, 400)) == BODY
    keyed = to_rgba(canvas, GREY)
    assert not mask_of(keyed)[5, 5] and mask_of(keyed)[400, 320]
    assert anchor(mask_of(keyed))[1] == y1 - 1
    assert resolve_loop("idle", "auto") and not resolve_loop("slash", "auto") and resolve_loop("slash", "loop")


REPLY = {"character": "A chibi knight with a sword, painterly style.", "beats": ["wind-up", "slash", "recover"],
         "prompt": "A chibi knight raises his sword, slashes forward in one fast arc and returns to his stance."}


def test_motion_prompt_rules() -> None:
    doc = finalize(REPLY, "slash", "", False)
    assert doc["prompt"].endswith(SUFFIX) and "static" in doc["prompt"] and doc["beats"][1] == "slash"
    with pytest.raises(StepError):
        finalize({**REPLY, "prompt": " "}, "slash", "", False)
    assert manual("run", "", True)["source"] == "preset" and "runs in place" in manual("run", "", True)["prompt"]
    assert manual("custom", "spins around", False)["prompt"].startswith("The character spins around")
    p = build_prompt("idle", "tail wags", True, "topdown", "left")
    assert "loop as a game cycle" in p and "top-down" in p and "faces left" in p and "tail wags" in p


def test_recipe_lints_and_pack() -> None:
    r = load_recipe_file(RECIPES / "sprite-anim-video.yaml")
    assert [s.id for s in r.steps] == ["prep", "motion", "gen", "candidates", "pick", "take", "matte", "loop", "fx",
                                       "sheet", "approve"]
    text = (RECIPES / "sprite-anim-video.yaml").read_text(encoding="utf-8")
    assert validate(text, None)[1] == []
    assert "keyart_video" in plan.recipe_packs(text)
    for kind in ("op.anim_prep", "op.anim_candidates", "op.anim_pick", "op.anim_loop", "claude.anim_motion"):
        assert CATALOG[kind]["label"]


async def test_anim_loop_executor_outputs(tmp_path: Path) -> None:
    ctx = FakeCtx(tmp_path)
    ctx.job_dir.mkdir(parents=True)
    refs = [await put_img(ctx, Image.fromarray(runner(i, bg=GREY), "RGBA").convert("RGB"), f"f{i}.png")
            for i in range(36)]
    info = await ctx.blobs.put_bytes(json.dumps({"backdrop": "#808080"}).encode(), name="p.json",
                                     mime="application/json")
    res = await AnimLoop().run(ctx, {"action": "run", "frames": 8, "size": 96, "facing": "left"},
                               {"images": refs, "json": [info]})
    out = res.outputs
    assert len(out["images"]) == 8 and out["gif"][0].mime == "image/gif" and out["gif"][0].name == "run_loop.gif"
    piv = json.loads(ctx.blobs.path(out["pivot"][0].hash).read_bytes())
    assert piv["clips"] == [{"name": "run", "frames": 8, "fps": 12.0, "loop": True}] and piv["angles"] == [90]
    assert res.meta["period"] == 12
    with pytest.raises(StepError):
        await AnimLoop().run(ctx, {"action": "dance"}, {"images": refs})


class FakeQuery:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def __call__(self, content: list[dict[str, Any]], options: Any) -> ClaudeReply:
        self.calls.append((content, options))
        return ClaudeReply(REPLY, "", {"cost_usd": 0.01, "model": options.model})


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", compress_level=1)
    return buf.getvalue()


class FakeWan(FakeExec):
    """Wan 2.2 TI2V stand-in: `55.length` frames of the runner on the grey backdrop, a different phase per call."""

    def __init__(self) -> None:
        super().__init__("comfy.keyart_wan22_ti2v")
        self.params: list[dict[str, Any]] = []

    async def run(self, ctx: Any, params: dict[str, Any], inputs: Any) -> StepResult:
        self.params.append(params)
        start = Image.open(ctx.blobs.path(inputs["image"][0].hash))
        w, h, n = params["55.width"], params["55.height"], params["55.length"]
        assert start.size == (w, h) and start.getpixel((2, 2)) == GREY
        out = []
        for i, (dx, dy) in enumerate(jitter(n, len(self.params))):
            fig = Image.fromarray(runner(i + 3 * len(self.params), 12, jx=dx, jy=dy), "RGBA").resize((480, 480))
            frame = Image.new("RGBA", (w, h), (*GREY, 255))
            frame.alpha_composite(fig, ((w - 480) // 2, h - 520))
            out.append(await ctx.blobs.put_bytes(_png(frame.convert("RGB")), name=f"frame_{i:05d}_.png",
                                                 mime="image/png"))
        return StepResult(outputs={"images": out})


class FakeMatte(FakeExec):
    """BiRefNet stand-in: keys the grey backdrop out."""

    def __init__(self) -> None:
        super().__init__("comfy.remove_bg_birefnet")
        self.count = 0

    async def run(self, ctx: Any, params: dict[str, Any], inputs: Any) -> StepResult:
        self.count += 1
        a = to_rgba(Image.open(ctx.blobs.path(inputs["image"][0].hash)), GREY)
        ref = await ctx.blobs.put_bytes(_png(Image.fromarray(a, "RGBA")), name="rgba.png", mime="image/png")
        return StepResult(outputs={"images": [ref]})


async def test_recipe_runs_with_fakes_and_exports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from studio_core.adapters import claude as cl
    from studio_core.export import aseprite, godot, unity
    from studio_core.ops.executors import Sheet
    from studio_core.ops.fx import SpriteFx
    from studio_core.store.bundle import engine_source

    monkeypatch.setattr(cl, "_image_block", lambda d, m: {"type": "image", "source": {"type": "base64"}})
    q, wan, matte = FakeQuery(), FakeWan(), FakeMatte()
    env = Env(tmp_path, [wan, matte],
              {"sprite-anim-video": (RECIPES / "sprite-anim-video.yaml").read_text(encoding="utf-8")})
    env.registry.register(ClaudeAnimMotionExecutor(env.settings, q))
    for cls in (*ANIM_VIDEO_OPS, SpriteFx):
        env.registry.register(cls())
    env.registry.register(Sheet())
    img = Image.new("RGBA", (200, 300), (0, 0, 0, 0))
    ImageDraw.Draw(img).rectangle((60, 40, 140, 280), fill=(*BODY, 255))
    ref = await env.blobs.put_bytes(_png(img), name="hero.png", mime="image/png")
    run_id = await env.engine.start_run("sprite-anim-video", {"image": ref.hash, "action": "run", "frames": 8,
                                                              "candidates": 2, "size": 128, "review": False})
    await asyncio.wait_for(env.engine.wait_idle(run_id), 120)
    steps = env.steps(run_id)
    assert env.status(run_id) == RunStatus.SUCCEEDED, {k: s.error for k, s in steps.items() if s.error}
    assert len(wan.params) == 2 and wan.params[0]["55.length"] == 49 and wan.params[0]["55.width"] == 640
    assert wan.params[0]["6.text"].endswith(SUFFIX) and q.calls[0][1].model == "claude-opus-5-5"
    assert len(steps["candidates"].outputs["previews"]) == 2 and matte.count == 49
    approve = steps["approve"]
    meta = json.loads(env.blobs.path(approve.outputs["meta"][0].hash).read_bytes())
    assert meta["clips"][0]["name"] == "run" and meta["clips"][0]["frames"] == 8 and meta["row_angles"] == [270]
    report = json.loads(env.blobs.path(approve.outputs["report"][0].hash).read_bytes())
    assert report["period"] == 12 and report["pivot_error_px"] <= 1.0
    lay = layout(meta, "sheet")
    assert [t.name for t in lay.tracks] == ["run_E"] and len(lay.tracks[0].cells) == 8 and lay.tracks[0].loop

    src = engine_source(steps["sheet"], list(steps.values()), env.blobs, "Hero")
    u = dict(unity.export(src))
    clip = u["Animations/run_E.anim"].decode()
    assert clip.count("    - time:") == 8 and "m_SampleRate: 12" in clip and "m_LoopTime: 1" in clip
    g = dict(godot.export(src))
    tres = g["Hero.tres"].decode()
    assert '"run_E"' in tres and tres.count("ExtResource") + tres.count("SubResource(") >= 8
    a, _ = aseprite.export_files(src)
    doc = json.loads(dict(a)["aseprite/Hero.json"])
    assert [t["name"] for t in doc["meta"]["frameTags"]] == ["run_E"] and len(doc["frames"]) == 8
