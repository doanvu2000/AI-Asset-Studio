from __future__ import annotations

import asyncio
import io
import json
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from studio_core.adapters.claude import ClaudeReply
from studio_core.adapters.keyart_motion import SUFFIX, ClaudeKeyartMotionExecutor, build_prompt, finalize, manual
from studio_core.bootstrap import plan
from studio_core.config import _default_ffmpeg_exe
from studio_core.contracts import RunStatus, StepError, StepResult
from studio_core.engine.kinds import CATALOG
from studio_core.engine.library import validate
from studio_core.engine.recipe import load_recipe_file
from studio_core.ops.keyart import (crossfade_loop, fit_cover, frame_count, grid_size, make_loop, motion_level,
                                    pingpong_loop, seam_ratio)
from test_engine_runner import Env, FakeExec

RECIPES = Path(__file__).resolve().parents[1] / "studio_core" / "recipes"
HAS_FFMPEG = shutil.which(_default_ffmpeg_exe()) is not None or Path(_default_ffmpeg_exe()).exists()

REPLY = {"scene": "A knight on a cliff at sunset, painterly fantasy style.",
         "motion": ["cape ripples in the wind", "embers drift upward", ""],
         "camera": "slow push-in",
         "prompt": "A knight stands on a cliff at sunset. His cape ripples, embers drift upward. Slow push-in."}


def drifting(n: int, w: int = 96, h: int = 64) -> list[np.ndarray]:
    """A gradient whose bright band slides right one step per frame: the last frame is far from the first."""
    x = np.arange(w, dtype=np.float32)
    out = []
    for i in range(n):
        row = 128 + 100 * np.sin((x + 3 * i) / 9)
        out.append(np.repeat(np.stack([row, row * 0.8, 255 - row], -1)[None], h, 0).clip(0, 255).astype(np.uint8))
    return out


def test_grid_and_frame_count() -> None:
    assert grid_size(1280, 704, 1280 * 704) == (1280, 704)
    w, h = grid_size(1920, 1080, 1280 * 704)
    assert w % 32 == 0 and h % 32 == 0 and abs(w / h - 16 / 9) < 0.05
    pw, ph = grid_size(1080, 1920, 960 * 544)
    assert pw % 32 == 0 and ph > pw
    assert frame_count(5) == 121 and frame_count(4) == 97 and frame_count(3) == 73
    assert frame_count(9) == 121 and (frame_count(0.1) - 1) % 4 == 0


def test_fit_cover_flattens_alpha_and_hits_size() -> None:
    img = Image.new("RGBA", (300, 100), (255, 0, 0, 0))
    out = fit_cover(img, (128, 96))
    assert out.size == (128, 96) and out.mode == "RGB" and out.getpixel((5, 5)) == (0, 0, 0)


def test_crossfade_loop_closes_the_seam() -> None:
    frames = drifting(49)
    raw = seam_ratio(frames)
    looped = crossfade_loop(frames, 12)
    assert len(looped) == 49 - 12
    # The wrap (last -> first) is no bigger than an ordinary step once the tail is blended into the head.
    assert raw > 5 and seam_ratio(looped) < 2.0, (raw, seam_ratio(looped))
    assert np.array_equal(looped[0], frames[12])
    assert len(crossfade_loop(frames, 100)) == 49 - 16
    assert make_loop(frames, "none", 12) == frames


def test_pingpong_loop_returns_to_start() -> None:
    frames = drifting(9)
    pp = pingpong_loop(frames)
    assert len(pp) == 16 and np.array_equal(pp[-1], frames[1])
    assert seam_ratio(pp) < 1.5
    assert motion_level(frames) > 5 and motion_level([frames[0]] * 4) == 0


def test_motion_prompt_rules() -> None:
    doc = finalize(REPLY, "embers", "crossfade")
    assert doc["camera"] == "static" and "camera stays still" in doc["prompt"] and doc["prompt"].endswith(SUFFIX)
    assert doc["motion"] == ["cape ripples in the wind", "embers drift upward"]
    assert finalize(REPLY, "", "pingpong")["camera"] == "slow push-in"
    assert finalize({**REPLY, "camera": "dolly zoom"}, "", "none")["camera"] == "static"
    with pytest.raises(StepError):
        finalize({**REPLY, "prompt": "  "}, "", "none")
    m = manual("  hair sways.  ", "none")
    assert m["source"] == "user" and m["prompt"] == "hair sways" + SUFFIX
    assert manual("", "crossfade")["source"] == "default"
    assert "camera must be static" in build_prompt("", "crossfade")
    assert "keep it): fire" in build_prompt("fire", "none")


def test_recipe_lints_and_pack() -> None:
    r = load_recipe_file(RECIPES / "key-art-motion.yaml")
    assert [s.id for s in r.steps] == ["prep", "motion", "gen", "post", "approve"]
    text = (RECIPES / "key-art-motion.yaml").read_text(encoding="utf-8")
    assert validate(text, None)[1] == []
    assert plan.recipe_packs(text) == {"keyart_video"}
    names = {m["name"] for m in plan.pack_models(["keyart_video"])}
    assert {"wan2.2_ti2v_5B_fp16.safetensors", "wan2.2_vae.safetensors",
            "umt5_xxl_fp8_e4m3fn_scaled.safetensors"} <= names
    for kind in ("op.keyart_prep", "op.keyart_video", "claude.keyart_motion"):
        assert kind in CATALOG


class FakeQuery:
    def __init__(self) -> None:
        self.calls: list[tuple[list[dict[str, Any]], Any]] = []

    async def __call__(self, content: list[dict[str, Any]], options: Any) -> ClaudeReply:
        self.calls.append((content, options))
        return ClaudeReply(REPLY, "", {"cost_usd": 0.01, "model": options.model})


class FakeWan(FakeExec):
    """Stands in for Wan 2.2 TI2V: `55.length` frames at the requested size, drifting from the start image."""

    def __init__(self) -> None:
        super().__init__("comfy.keyart_wan22_ti2v")
        self.params: list[dict[str, Any]] = []
        self.staged: list[Any] = []

    async def run(self, ctx: Any, params: dict[str, Any], inputs: Any) -> StepResult:
        self.params.append(params)
        start = Image.open(ctx.blobs.path(inputs["image"][0].hash)).convert("RGB")
        self.staged.append(start.size)
        w, h, n = params["55.width"], params["55.height"], params["55.length"]
        assert start.size == (w, h)
        out = []
        for i, a in enumerate(drifting(n, w, h)):
            buf = io.BytesIO()
            Image.fromarray(a).save(buf, "PNG", compress_level=1)
            out.append(await ctx.blobs.put_bytes(buf.getvalue(), name=f"frame_{i:05d}_.png", mime="image/png"))
        return StepResult(outputs={"images": out})


def _env(tmp_path: Path) -> tuple[Env, FakeQuery, FakeWan]:
    from studio_core.ops.keyart import KeyartPrep, KeyartVideo

    q, wan = FakeQuery(), FakeWan()
    env = Env(tmp_path, [wan], {"key-art-motion": (RECIPES / "key-art-motion.yaml").read_text(encoding="utf-8")})
    env.registry.register(ClaudeKeyartMotionExecutor(env.settings, q))
    env.registry.register(KeyartVideo(env.settings))
    env.registry.register(KeyartPrep())
    return env, q, wan


async def _run(env: Env, inputs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    buf = io.BytesIO()
    Image.new("RGB", (400, 225), (40, 60, 90)).save(buf, "PNG")
    img = await env.blobs.put_bytes(buf.getvalue(), name="keyart.png", mime="image/png")
    run_id = await env.engine.start_run("key-art-motion", {"image": img.hash, **inputs},
                                        params={"approve": {"enabled": False}})
    await asyncio.wait_for(env.engine.wait_idle(run_id), 120)
    return run_id, env.steps(run_id)


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
async def test_recipe_runs_with_fakes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from studio_core.adapters import claude as cl

    monkeypatch.setattr(cl, "_image_block", lambda d, m: {"type": "image", "source": {"type": "base64"}})
    env, q, wan = _env(tmp_path)
    run_id, steps = await _run(env, {"seconds": 3, "resolution": "480p", "sampler_steps": 12})
    assert env.status(run_id) == RunStatus.SUCCEEDED, {k: s.error for k, s in steps.items() if s.error}
    assert q.calls and q.calls[0][1].model == "claude-opus-5-5"
    p = wan.params[0]
    assert p["55.length"] == 73 and p["3.steps"] == 12 and p["55.width"] % 32 == 0
    assert "camera stays still" in p["6.text"] and p["6.text"].endswith(SUFFIX)
    out = steps["approve"].outputs
    names = sorted(r.name for r in out["video"])
    assert names == ["keyart_teaser_loop.mp4", "keyart_teaser_loop.webm"]
    assert {r.mime for r in out["video"]} == {"video/mp4", "video/webm"}
    assert out["gif"][0].mime == "image/gif" and out["poster"][0].name == "keyart_teaser_loop_poster.png"
    meta = json.loads(env.blobs.path(out["meta"][0].hash).read_bytes())
    assert meta["loop"] == "crossfade" and meta["frames"] == 73 - 18 and meta["seam_ratio"] < 2.0
    assert env.blobs.path(out["video"][0].hash).stat().st_size > 1000


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
async def test_recipe_manual_prompt_skips_claude(tmp_path: Path) -> None:
    env, q, wan = _env(tmp_path)
    run_id, steps = await _run(env, {"seconds": 3, "resolution": "480p", "suggest": False, "loop": "pingpong",
                                     "motion": "flag waves"})
    assert env.status(run_id) == RunStatus.SUCCEEDED, {k: s.error for k, s in steps.items() if s.error}
    assert not q.calls and wan.params[0]["6.text"] == "flag waves" + SUFFIX
    meta = json.loads(env.blobs.path(steps["post"].outputs["json"][0].hash).read_bytes())
    assert meta["loop"] == "pingpong" and meta["frames"] == 2 * 73 - 2
