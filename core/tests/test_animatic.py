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
from PIL import Image

from studio_core.adapters.claude import ClaudeReply
from studio_core.adapters.storyboard import (ClaudeStoryboardExecutor, StoryboardFramesExecutor,
                                             StoryboardScoreExecutor, cast_voices, frame_prompt, layout_canvas,
                                             normalize_plan, parse_characters, tts_doc)
from studio_core.adapters.tts import NpcVoiceExecutor, Speech
from studio_core.config import _default_ffmpeg_exe
from studio_core.contracts import GateResolution, RunStatus, StepError, StepResult
from studio_core.engine.library import validate
from studio_core.engine.recipe import load_recipe_file
from studio_core.ops.animatic import (Animatic, build_ffmpeg_args, cues_for, fit_timeline, ken_burns, srt,
                                      video_size)
from test_engine_runner import Env, FakeExec

RECIPES = Path(__file__).resolve().parents[1] / "studio_core" / "recipes"
FFMPEG = _default_ffmpeg_exe()
HAS_FFMPEG = shutil.which(FFMPEG) is not None or Path(FFMPEG).exists()

REPLY = {"language": "vi", "title": "Cổng làng", "music": {"prompt": "tense strings", "bpm": 300},
         "cast": [{"name": "Lão Tư", "gender": "male", "age": "old", "speed": 0.9},
                  {"name": "Minh", "gender": "male", "age": "young", "speed": 1.3}],
         "shots": [
             {"duration": 4, "framing": "wide", "camera": "push_in", "characters": [], "description": "A rainy village gate at night.",
              "speaker": "", "line": "", "expression": "neutral", "sfx": "heavy rain", "music_cue": "low drone"},
             {"duration": 3, "framing": "medium", "camera": "pan_left", "characters": ["lão tư"],
              "description": "An old gatekeeper waits under the eaves.", "speaker": "Lão Tư",
              "line": "Cháu về muộn thế?  Lũ sói đã xuống núi rồi.", "expression": "worried", "sfx": "", "music_cue": ""},
             {"duration": 3, "framing": "zoom", "camera": "whip", "characters": ["Minh", "Lão Tư"],
              "description": "A soaked young man runs in holding a sword.", "speaker": "Minh",
              "line": "Cháu tìm được thanh kiếm của cha.", "expression": "determined", "sfx": "", "music_cue": ""},
             {"duration": 2, "framing": "close", "camera": "static", "characters": ["Minh"], "description": "",
              "speaker": "", "line": "", "expression": "", "sfx": "", "music_cue": ""},
             {"duration": 2, "framing": "close", "camera": "static", "characters": ["Minh"],
              "description": "Close on the young man's determined face.", "speaker": "",
              "line": "Và đêm ấy, làng không ngủ.", "expression": "neutral", "sfx": "", "music_cue": ""}]}
CHARS = "Lão Tư, ông lão gác cổng\nMinh, chàng trai trẻ"


def png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class FakeQuery:
    def __init__(self, reply: dict[str, Any]):
        self.reply = reply
        self.calls: list[tuple[list[dict[str, Any]], Any]] = []

    async def __call__(self, content: list[dict[str, Any]], options: Any) -> ClaudeReply:
        self.calls.append((content, options))
        return ClaudeReply(self.reply, "", {"cost_usd": 0.05, "model": options.model})


class FakeGen(FakeExec):
    """Klein (one layout image in) or Z-Image (nothing in): a flat frame whose colour changes every call."""

    def __init__(self, kind: str, key: str):
        super().__init__(kind)
        self.key = key
        self.prompts: list[str] = []
        self.sizes: list[tuple[int, int]] = []

    async def run(self, ctx: Any, params: dict[str, Any], inputs: Any) -> StepResult:
        self.prompts.append(params[self.key])
        if inputs.get("image"):
            size = Image.open(ctx.blobs.path(inputs["image"][0].hash)).size
        else:
            size = (params["57:13.width"], params["57:13.height"])
        self.sizes.append(size)
        n = len(self.prompts)
        img = Image.new("RGB", (size[0] + 8, size[1]), (40 * n % 255, 90 if inputs.get("image") else 30, 160))
        return StepResult(outputs={"images": [await ctx.blobs.put_bytes(png(img), name="ComfyUI_0001_.png",
                                                                        mime="image/png")]})


class FakeTts:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, float]] = []

    def synth(self, text: str, lang: str, voice: str, speed: float) -> Speech:
        self.calls.append((text, lang, voice, speed))
        rate = 22050
        t = np.arange(int(rate * 0.25 * len(text.split()))) / rate
        return Speech((0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), rate, voice, "fake")


def plan() -> dict[str, Any]:
    return normalize_plan(REPLY, characters=parse_characters(CHARS, 2), target_seconds=20, max_shots=8,
                          aspect="16:9", style="painterly")


def test_plan_normalize() -> None:
    p = plan()
    assert [s["id"] for s in p["shots"]] == ["shot_01", "shot_02", "shot_03", "shot_04"]
    s1, s2, s3, s4 = p["shots"]
    assert s1["refs"] == [] and s2["characters"] == ["Lão Tư"] and s2["refs"] == [0]
    assert s3["framing"] == "medium" and s3["camera"] == "static" and s3["refs"] == [0, 1]
    assert s2["line"] == "Cháu về muộn thế? Lũ sói đã xuống núi rồi." and s2["expression"] == "neutral"
    assert s3["expression"] == "determined" and s4["speaker"] == "Narrator"
    assert abs(sum(s["duration"] for s in p["shots"]) - 20) < 0.5
    assert p["music"]["bpm"] == 160 and p["language"] == "vi"
    names = {c["name"]: c for c in p["cast"]}
    assert set(names) == {"Lão Tư", "Minh", "Narrator"} and names["Minh"]["speed"] == 1.15
    assert len({c["kokoro"] for c in p["cast"]}) == 3 and len({c["pitch"] for c in p["cast"]}) == 3
    doc = tts_doc(p)
    assert [ln["id"] for ln in doc["lines"]] == ["shot_02", "shot_03", "shot_04"] and doc["languages"] == ["vi"]
    assert doc["lines"][0]["voice"]["name"] == names["Lão Tư"]["kokoro"] and doc["allow_empty"]
    with pytest.raises(StepError):
        normalize_plan({"shots": [REPLY["shots"][0]]}, characters=[], target_seconds=20, max_shots=8,
                       aspect="16:9", style="")
    assert cast_voices([{"name": "A", "gender": "f", "age": "?"}])[0]["kokoro"] == "af_heart"


def test_layout_and_prompts() -> None:
    tall = Image.new("RGBA", (200, 400), (200, 50, 50, 255))
    canvas = layout_canvas([tall, tall], (1360, 768), "wide")
    assert canvas.size == (1360, 768) and canvas.getpixel((5, 5)) == (128, 128, 128)
    assert canvas.getpixel((340, 760)) == (200, 50, 50)
    close = layout_canvas([tall], (1360, 768), "close")
    assert close.getpixel((680, 384)) == (200, 50, 50)
    p = plan()
    assert "reference" in frame_prompt(p["shots"][1], "painterly", True)
    assert "reference" not in frame_prompt(p["shots"][0], "painterly", False)


def test_fit_timeline_extends_for_voice() -> None:
    shots = [{"id": "a", "duration": 2.0}, {"id": "b", "duration": 2.0}, {"id": "c", "duration": 3.0}]
    tl, total = fit_timeline(shots, {"b": 3.0}, fps=24, xfade=0.5)
    a, b, c = tl
    assert not a["extended"] and b["extended"] and not c["extended"]
    assert b["line_start"] == pytest.approx(b["start"] + 0.75) and b["line_end"] <= b["start"] + b["duration"] - 0.5
    assert b["start"] == pytest.approx(a["duration"] - 0.5)
    assert all(abs(x["duration"] * 24 - x["frames"]) < 1e-2 for x in tl)
    assert total == pytest.approx(sum(x["duration"] for x in tl) - 1.0, abs=1e-3)
    tl2, _ = fit_timeline([{"id": "z", "duration": 1.0}], {"z": 2.0})
    assert tl2[0]["duration"] >= 0.25 + 2.0 + 0.45 + 0.6


def test_filtergraph_build(tmp_path: Path) -> None:
    shots = [{"id": "s1", "duration": 2.0}, {"id": "s2", "duration": 2.5}]
    tl, total = fit_timeline(shots, {"s2": 1.0})
    args = build_ffmpeg_args(tl, total, [tmp_path / "a.png", tmp_path / "b.png"], ["push_in", "pan_right"],
                             (1280, 720), tmp_path / "o.mp4", subs=[(tmp_path / "s.png", 10, 600, 2.0, 3.0)],
                             voices=[(tmp_path / "v.wav", tl[1]["line_start"], 0.8)], music=tmp_path / "m.wav",
                             sfx=[(tmp_path / "x.wav", 0.1)])
    graph = args[args.index("-filter_complex") + 1]
    assert f"xfade=transition=fade:duration=0.5:offset={tl[1]['start']:.4f}" in graph
    assert "overlay=10:600:enable='between(t,2.000,3.000)'" in graph
    assert "asetrate=38400" in graph and "atempo=1.2500" in graph
    assert "amix=inputs=4:duration=first:normalize=0" in graph and "volume=-20.0dB" in graph
    assert args[args.index("-stream_loop") + 2: args.index("-stream_loop") + 4] == ["-i", str(tmp_path / "m.wav")]
    assert "libx264" in args and "aac" in args and args[-1] == str(tmp_path / "o.mp4")
    assert "z='1+0.12*on/" in ken_burns("push_in", 48, (1280, 720))
    assert "x='(iw-iw/zoom)*(1-on/47)'" in ken_burns("pan_left", 48, (1280, 720))
    assert video_size("9:16") == (720, 1280) and video_size("4:3", 1080) == (1440, 1080)


def test_srt_and_cues() -> None:
    tl, _ = fit_timeline([{"id": "a", "duration": 2}, {"id": "b", "duration": 2}], {"a": 2.0, "b": 1.0})
    cues = cues_for(tl, {"a": "Xin chào. Ngươi từ đâu tới?", "b": "Đi thôi."})
    assert [c["shot"] for c in cues] == ["a", "a", "b"]
    assert all(x["end"] <= y["start"] for x, y in zip(cues, cues[1:]))
    text = srt(cues)
    assert text.startswith("1\n00:00:00,250 --> ") and "\n3\n" in text and "Đi thôi." in text


def test_recipe_lints() -> None:
    r = load_recipe_file(RECIPES / "storyboard-animatic.yaml")
    assert [s.id for s in r.steps][:2] == ["plan", "frames"]
    _, issues = validate((RECIPES / "storyboard-animatic.yaml").read_text(encoding="utf-8"), None)
    assert issues == [], issues


def _env(tmp_path: Path, reply: dict[str, Any] = REPLY) -> tuple[Env, FakeQuery, FakeGen, FakeGen, FakeTts]:
    q, tts = FakeQuery(reply), FakeTts()
    klein = FakeGen("comfy.edit_flux2_klein4b", "75:74.text")
    zimg = FakeGen("comfy.t2i_zimage_turbo", "57:27.text")
    recipes = {"storyboard-animatic": (RECIPES / "storyboard-animatic.yaml").read_text(encoding="utf-8")}
    env = Env(tmp_path, [klein, zimg], recipes)
    for ex in (ClaudeStoryboardExecutor(env.settings, q), StoryboardFramesExecutor(env.registry.resolve),
               StoryboardScoreExecutor(env.registry.resolve), NpcVoiceExecutor(env.settings, engine=tts),
               Animatic(env.settings)):
        env.registry.register(ex)
    return env, q, klein, zimg, tts


async def _idle(env: Env, run_id: str) -> None:
    await asyncio.wait_for(env.engine.wait_idle(run_id), 120)


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
async def test_recipe_end_to_end_with_per_shot_regenerate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from studio_core.adapters import claude as cl

    monkeypatch.setattr(cl, "_image_block", lambda d, m: {"type": "image", "source": {"type": "base64"}})
    env, q, klein, zimg, tts = _env(tmp_path)
    refs = [await env.blobs.put_bytes(png(Image.new("RGB", (200, 400), c)), name=f"c{i}.png", mime="image/png")
            for i, c in enumerate([(200, 60, 60), (60, 200, 60)])]
    run_id = await env.engine.start_run("storyboard-animatic", {
        "script": "Đêm mưa. LÃO TƯ: Cháu về muộn thế?", "characters": CHARS, "character_1": refs[0].hash,
        "character_2": refs[1].hash, "seconds": 20})
    await _idle(env, run_id)
    steps = env.steps(run_id)
    assert steps["board"].status.value == "awaiting_human", {k: s.error for k, s in steps.items() if s.error}
    frames = steps["frames"].outputs["images"]
    assert [f.name for f in frames] == ["shot_01.png", "shot_02.png", "shot_03.png", "shot_04.png"]
    assert len(zimg.prompts) == 1 and len(klein.prompts) == 3 and klein.sizes[0] == (1360, 768)
    assert {c[2] for c in tts.calls} == {"vais1000"} and steps["score"].meta.get("skipped") is True

    keep = [frames[0].hash, frames[2].hash]
    await env.engine.retry_step(steps["frames"].id, keep=keep)
    await _idle(env, run_id)
    steps = env.steps(run_id)
    again = steps["frames"].outputs["images"]
    assert len(klein.prompts) == 5 and len(zimg.prompts) == 1
    assert again[0].hash == frames[0].hash and again[2].hash == frames[2].hash and again[1].hash != frames[1].hash

    await env.engine.resolve_gate(steps["board"].id, GateResolution(picked=[f.hash for f in again[:3]]))
    await _idle(env, run_id)
    steps = env.steps(run_id)
    assert steps["approve"].status.value == "awaiting_human", {k: s.error for k, s in steps.items() if s.error}
    out = steps["assemble"].outputs
    doc = json.loads(env.blobs.path(out["json"][0].hash).read_bytes())
    assert doc["dropped_shots"] == ["shot_04"] and len(doc["shots"]) == 3 and doc["subtitles"]
    assert steps["assemble"].meta["voiced"] == 2
    video = env.blobs.path(out["video"][0].hash)
    probe = subprocess.run([FFMPEG, "-hide_banner", "-i", str(video)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace").stderr
    assert "h264" in probe and "aac" in probe and "1280x720" in probe
    srt_text = env.blobs.path(out["srt"][0].hash).read_text(encoding="utf-8")
    assert "Cháu về muộn thế?" in srt_text
    assert env.blobs.path(out["pdf"][0].hash).read_bytes().startswith(b"%PDF")
    await env.engine.resolve_gate(steps["approve"].id, GateResolution(approved=True))
    await _idle(env, run_id)
    assert env.status(run_id) == RunStatus.SUCCEEDED
