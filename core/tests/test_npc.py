from __future__ import annotations

import asyncio
import io
import json
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image, ImageDraw

from studio_core.adapters import tts_text as tt
from studio_core.adapters.claude import ClaudeReply
from studio_core.adapters.npc import (ClaudeNpcDialogueExecutor, lines_csv, normalize_dialogue, parse_expressions,
                                      parse_languages, parse_lines_csv)
from studio_core.adapters.tts import NpcVoiceExecutor, Speech, missing, pick_voice
from studio_core.config import _default_ffmpeg_exe
from studio_core.contracts import RunStatus, StepError, StepResult
from studio_core.engine.library import validate
from studio_core.engine.recipe import load_recipe_file
from studio_core.ops.audio import AudioPost
from studio_core.ops.lut import LutPrep
from studio_core.ops.npc import NpcLines, NpcPack, NpcPortraits, align_portrait, similarity
from test_engine_runner import Env, FakeExec

RECIPES = Path(__file__).resolve().parents[1] / "studio_core" / "recipes"
HAS_FFMPEG = shutil.which(_default_ffmpeg_exe()) is not None or Path(_default_ffmpeg_exe()).exists()
EXPR = "happy, warm smile\nangry, furrowed brows\nsurprised, wide eyes"

REPLY = {"voice": {"gender": "female", "age": "old", "speed": 1.4, "style": "raspy, warm"},
         "lines": [{"category": "greeting", "expression": "Happy",
                    "texts": [{"lang": "vi", "text": "Chào cháu, uống chén trà đã."},
                              {"lang": "en", "text": "Hello dear, have some tea first."}]},
                   {"category": "idle", "expression": "bored",
                    "texts": [{"lang": "vi", "text": "Trời hôm nay  oi quá."}, {"lang": "en", "text": "So humid."}]},
                   {"category": "idle", "expression": "angry",
                    "texts": [{"lang": "vi", "text": "Lũ trẻ lại phá vườn rồi!"},
                              {"lang": "en", "text": "Those kids wrecked my garden again!"}]},
                   {"category": "quest_offer", "expression": "thinking",
                    "texts": [{"lang": "en", "text": "only english"}]},
                   {"category": "farewell", "expression": "surprised",
                    "texts": [{"lang": "vi", "text": "Đi cẩn thận nhé. Nhớ ghé lại!"},
                              {"lang": "en", "text": "Take care. Come back soon!"}]}]}


def portrait(w: int = 160, h: int = 200, smile: bool = False, dx: int = 0, dy: int = 0) -> Image.Image:
    img = Image.new("RGB", (w, h), (90, 120, 150))
    d = ImageDraw.Draw(img)
    d.ellipse((40 + dx, 30 + dy, 120 + dx, 130 + dy), fill=(230, 190, 160), outline=(40, 30, 20), width=3)
    d.rectangle((30 + dx, 130 + dy, 130 + dx, 199), fill=(120, 60, 40))
    d.ellipse((60 + dx, 60 + dy, 70 + dx, 70 + dy), fill=(20, 20, 20))
    d.ellipse((90 + dx, 60 + dy, 100 + dx, 70 + dy), fill=(20, 20, 20))
    d.arc((60 + dx, 85 + dy, 100 + dx, 115 + dy), 0 if smile else 180, 180 if smile else 360, fill=(120, 30, 30),
          width=3)
    return img


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
        return ClaudeReply(self.reply, "", {"cost_usd": 0.03, "model": options.model})


class FakeKlein(FakeExec):
    """Returns the input portrait shifted a few pixels and slightly rescaled, like a Klein edit drifting."""

    def __init__(self) -> None:
        super().__init__("comfy.edit_flux2_klein4b")
        self.texts: list[str] = []

    async def run(self, ctx: Any, params: dict[str, Any], inputs: Any) -> StepResult:
        self.texts.append(params["75:74.text"])
        img = portrait(smile=True, dx=6, dy=-4).resize((176, 220))
        return StepResult(outputs={"images": [await ctx.blobs.put_bytes(png(img), name="klein.png",
                                                                        mime="image/png")]})


class FakeTts:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, float]] = []

    def synth(self, text: str, lang: str, voice: str, speed: float) -> Speech:
        self.calls.append((text, lang, voice, speed))
        rate = 22050
        t = np.arange(int(rate * 0.08 * len(text.split()))) / rate
        tone = 0.4 * np.sin(2 * np.pi * 220 * t).astype(np.float32)
        pad = np.zeros(int(rate * 0.3), np.float32)
        return Speech(np.concatenate([pad, tone, pad]), rate, voice, "fake")


def _env(tmp_path: Path) -> tuple[Env, FakeQuery, FakeKlein, FakeTts]:
    q, klein, tts = FakeQuery(REPLY), FakeKlein(), FakeTts()
    recipes = {n: (RECIPES / f"{n}.yaml").read_text(encoding="utf-8") for n in ("npc-pack", "npc-voice-csv")}
    env = Env(tmp_path, [klein], recipes)
    for ex in (LutPrep(), NpcPortraits(), NpcPack(), NpcLines(), AudioPost(env.settings),
               ClaudeNpcDialogueExecutor(env.settings, q), NpcVoiceExecutor(env.settings, engine=tts)):
        env.registry.register(ex)
    return env, q, klein, tts


def test_parse_helpers() -> None:
    assert parse_expressions(EXPR) == ["neutral", "happy", "angry", "surprised"]
    assert parse_expressions("neutral, calm\nHappy Face, x") == ["neutral", "happy_face"]
    assert parse_languages("VI, en;en ja") == ["vi", "en", "ja"] and parse_languages("") == ["vi", "en"]
    doc = normalize_dialogue(REPLY, name="Bà Trà", persona="tea seller", langs=["vi", "en"],
                             expressions=parse_expressions(EXPR), count=6)
    assert [ln["id"] for ln in doc["lines"]] == ["greeting_01", "idle_01", "idle_02", "farewell_01"]
    assert [ln["expression"] for ln in doc["lines"]] == ["happy", "neutral", "angry", "surprised"]
    assert doc["lines"][1]["text"]["vi"] == "Trời hôm nay oi quá."
    assert doc["voice"] == {"gender": "female", "age": "old", "speed": 1.15, "style": "raspy, warm"}
    with pytest.raises(StepError):
        normalize_dialogue({"lines": [REPLY["lines"][3]]}, name="x", persona="", langs=["vi", "en"],
                           expressions=["neutral"], count=6)


def test_csv_parse_and_roundtrip() -> None:
    raw = ("﻿ID;Lang;Text;Expression\n"
           "greeting_01;vi;Chào ngươi!;happy\n"
           "greeting_01;en;Hello there!;happy\n"
           "Bark 2;en;  Nice   day. ;\n"
           ";en;skipped;\n").encode("utf-8")
    doc = parse_lines_csv(raw, name="Guard")
    assert doc["languages"] == ["vi", "en"] and doc["expressions"] == ["neutral", "happy"]
    assert [(ln["id"], ln["category"], ln["expression"]) for ln in doc["lines"]] == [
        ("greeting_01", "greeting", "happy"), ("bark_2", "line", "neutral")]
    assert doc["lines"][1]["text"] == {"en": "Nice day."}
    back = parse_lines_csv(lines_csv(doc))
    assert [ln["text"] for ln in back["lines"]] == [ln["text"] for ln in doc["lines"]]
    loose = parse_lines_csv("id,lang,text,expression\nidle_01,en,Well, hello, friend.,smug\n".encode())
    assert loose["lines"][0]["text"] == {"en": "Well, hello, friend."} and loose["lines"][0]["expression"] == "smug"
    with pytest.raises(StepError):
        parse_lines_csv(b"a,b\n1,2\n")


def test_text_frontend() -> None:
    assert tt.vi_syllable("chào") == "tʃˈaː2w" and tt.vi_syllable("Việt") == "vˈiə6t"
    assert tt.vi_syllable("người") == "ŋˈɯə2j" and tt.vi_syllable("quyển") == "kwˈiə4n"
    assert tt.vi_syllable("gì") == "zˈi2" and tt.vi_syllable("này") == "nˈa2j" and tt.vi_syllable("brr") is None
    assert tt.vi_number(25) == "hai mươi lăm" and tt.vi_number(101) == "một trăm lẻ một"
    assert tt.vi_number(21) == "hai mươi mốt" and tt.en_number(342) == "three hundred forty-two"
    assert tt.vi_phonemes("Chào! Có 2 con.") == ["tʃˈaː2w!", "kˈɔ5 hˈaːj kˈɔn."]
    known = {"welcome", "to", "oak", "vale", "coins", "three"}.__contains__
    assert tt.en_respell("Welcome to Oakvale! 3 coins", known) == "Welcome to oak vale! three coins"
    cues = tt.subtitle_cues("Xin chào. Ngươi từ đâu tới, hỡi lữ khách phương xa, mà trông mệt mỏi thế?", 4.0)
    assert [c["text"] for c in cues][0] == "Xin chào." and len(cues) == 3
    assert cues[0]["start"] == 0 and cues[-1]["end"] == 4.0
    assert all(a["end"] == b["start"] for a, b in zip(cues, cues[1:]))
    assert tt.subtitle_cues("hi", 0) == []


def test_align_portrait_recovers_crop() -> None:
    base = portrait()
    edit = portrait(smile=True, dx=6, dy=-4).resize((176, 220))
    out, shift = align_portrait(base, edit, 0.1)
    assert out.size == base.size and abs(shift[0] + 6) <= 1 and abs(shift[1] - 4) <= 1
    assert similarity(base, out) > similarity(base, edit.resize(base.size))
    rgba = base.convert("RGBA")
    rgba.putpixel((0, 0), (0, 0, 0, 0))
    out2, _ = align_portrait(rgba, edit, 0.1)
    assert out2.mode == "RGBA" and out2.getpixel((0, 0))[3] == 0


def test_voice_pick_and_missing(tmp_path: Path) -> None:
    assert pick_voice("en", "auto", {"gender": "female", "age": "old"}) == "bf_emma"
    assert pick_voice("en", "am_adam", {}) == "am_adam" and pick_voice("vi", "x", {}) == "vais1000"
    assert set(missing(tmp_path, ["vi", "en"])) >= {"vits-piper-vi_VN-vais1000-medium", "kokoro-int8-multi-lang-v1_0"}


async def test_tts_reports_install_hint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STUDIO_TTS_MODELS", str(tmp_path / "none"))
    ex = NpcVoiceExecutor(None)
    with pytest.raises(StepError) as e:
        ex._engine(["vi"])
    assert e.value.code == "tts_missing" and "uv add sherpa-onnx" in e.value.message


async def _start(env: Env, recipe: str, inputs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    run_id = await env.engine.start_run(recipe, inputs, params={"approve": {"enabled": False}})
    await asyncio.wait_for(env.engine.wait_idle(run_id), 60)
    return run_id, env.steps(run_id)


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
async def test_npc_pack_recipe_and_export(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from studio_core.adapters import claude as cl

    monkeypatch.setattr(cl, "_image_block", lambda d, m: {"type": "image", "source": {"type": "base64"}})
    env, q, klein, tts = _env(tmp_path)
    img = await env.blobs.put_bytes(png(portrait()), name="granny.png", mime="image/png")
    run_id, steps = await _start(env, "npc-pack", {"image": img.hash, "name": "Bà Trà", "persona": "tea seller",
                                                   "expressions": EXPR, "line_count": 6})
    assert env.status(run_id) == RunStatus.SUCCEEDED, {k: s.error for k, s in steps.items() if s.error}
    assert len(klein.texts) == 3 and "warm smile" in klein.texts[0] and "identity" in klein.texts[0]
    assert q.calls and q.calls[0][1].model == "claude-opus-5-5"
    assert {c[1] for c in tts.calls} == {"vi", "en"} and len(tts.calls) == 8
    assert {c[2] for c in tts.calls} == {"vais1000", "bf_emma"} and all(c[3] == 1.15 for c in tts.calls)
    names = sorted(r.name for r in steps["approve"].outputs["images"])
    assert names == ["portrait_angry.png", "portrait_happy.png", "portrait_neutral.png", "portrait_surprised.png"]
    assert "flagged" not in steps["portraits"].meta, steps["portraits"].meta
    pack = json.loads(env.blobs.path(steps["approve"].outputs["npc"][0].hash).read_bytes())
    assert pack["type"] == "npc_pack" and not pack["missing_audio"]
    greet = pack["lines"][0]
    assert greet["audio"]["vi"]["stem"] == "vi_greeting_01_1" and 0.3 < greet["audio"]["vi"]["seconds"] < 1.0
    cues = greet["subtitles"]["en"]
    assert cues[-1]["end"] == greet["audio"]["en"]["seconds"] and cues[0]["text"] == "Hello dear, have some tea first."
    assert pack["lines"][1]["expression"] == "neutral"

    from test_export_engines import _row

    from studio_core.export import godot, unity
    from studio_core.store.bundle import engine_ready, engine_source

    step = _row("approve", "gate.approve", steps["approve"].outputs)
    assert engine_ready(step, env.blobs)
    src = engine_source(step, [], env.blobs, "Granny")
    assert src.npc is not None and [e for e, _ in src.npc_portraits][0] == "neutral" and len(src.audio) == 8
    u = dict(unity.export(src))
    asset = u["NPC/Granny_npc.asset"].decode()
    assert "m_Script: {fileID: 11500000, guid: 5a17c0de4e9d4c1fa3b2c0ffee000001" in asset
    assert asset.count("clip: {fileID: 8300000") == 8 and asset.count("sprite: {fileID: 21300000") == 4
    assert '"B\\u00e0 Tr\\u00e0"' in asset and "Scripts/NpcData.cs" in u and "Audio/vi_greeting_01_1.ogg.meta" in u
    assert "NPC/Granny_happy.png.meta" in u and "class NpcData : ScriptableObject" in u["Scripts/NpcData.cs"].decode()
    doc = json.loads(u["NPC/Granny_npc.json"])
    assert doc["portraits"]["happy"] == "NPC/Granny_happy.png" and doc["lines"][0]["audio"].startswith("Audio/")
    assert "NpcData" in u["README.txt"].decode()
    g = dict(godot.export(src))
    tres = g["Granny_npc.tres"].decode()
    assert tres.startswith('[gd_resource type="Resource" script_class="NpcData"')
    assert 'path="Audio/en_farewell_01_1.ogg"' in tres and '"happy": ExtResource(' in tres
    assert 'npc_name = "Bà Trà"' in tres and "class_name NpcData" in g["npc_data.gd"].decode()


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
async def test_csv_only_recipe(tmp_path: Path) -> None:
    env, q, _, tts = _env(tmp_path)
    csv_ref = await env.blobs.put_bytes("id,lang,text,expression\nidle_01,en,Move along.,angry\n"
                                        "idle_01,vi,Đi chỗ khác.,angry\nfarewell_01,en,Bye.,\n".encode(),
                                        name="lines.csv", mime="text/csv")
    run_id, steps = await _start(env, "npc-voice-csv", {"lines_csv": csv_ref.hash, "name": "Guard",
                                                        "gender": "male", "age": "old"})
    assert env.status(run_id) == RunStatus.SUCCEEDED, {k: s.error for k, s in steps.items() if s.error}
    assert not q.calls and len(tts.calls) == 3 and {c[2] for c in tts.calls} == {"bm_george", "vais1000"}
    pack = json.loads(env.blobs.path(steps["approve"].outputs["npc"][0].hash).read_bytes())
    assert pack["portraits"] == [] and [ln["id"] for ln in pack["lines"]] == ["idle_01", "farewell_01"]
    subs = json.loads(env.blobs.path(steps["approve"].outputs["subtitles"][0].hash).read_bytes())
    assert subs["vi"]["idle_01"][0]["text"] == "Đi chỗ khác."

    from test_export_engines import _row

    from studio_core.export import godot
    from studio_core.store.bundle import engine_source

    src = engine_source(_row("approve", "gate.approve", steps["approve"].outputs), [], env.blobs, "Guard")
    tres = dict(godot.export(src))["Guard_npc.tres"].decode()
    assert "portraits = {}" in tres and tres.count("AudioStream") == 3


@pytest.mark.parametrize("name", ["npc-pack", "npc-pack-prompt", "npc-voice-csv"])
def test_recipes_lint(name: str) -> None:
    r = load_recipe_file(RECIPES / f"{name}.yaml")
    assert "tts" in [s.id for s in r.steps]
    _, issues = validate((RECIPES / f"{name}.yaml").read_text(encoding="utf-8"), None)
    assert issues == []
