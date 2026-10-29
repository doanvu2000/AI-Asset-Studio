from __future__ import annotations

import io
import json
import shutil
import wave
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from studio_core.adapters.claude import ClaudeReply
from studio_core.adapters.soundscape import KEYSCALES, ClaudeSoundscapeExecutor, build_prompt, normalize_plan
from studio_core.config import _default_ffmpeg_exe
from studio_core.contracts import RunStatus, StepError, StepResult
from studio_core.engine.library import validate
from studio_core.engine.recipe import load_recipe_file
from studio_core.ops.audio import AudioPost
from studio_core.ops.soundscape import SoundscapeMix, bpm_check, estimate_bpm, tempo_error
from test_engine_runner import Env, FakeExec

RECIPES = Path(__file__).resolve().parents[1] / "studio_core" / "recipes"
RATE = 48000
HAS_FFMPEG = shutil.which(_default_ffmpeg_exe()) is not None or Path(_default_ffmpeg_exe()).exists()

REPLY = {"scene": "A misty harbour town at dusk.", "bpm": 96, "keyscale": "D minor",
         "music": {"label": "Harbour Dusk", "prompt": "folk, accordion, soft strings, calm", "volume_db": -9},
         "tense": {"label": "harbour dusk", "prompt": "folk, taiko drums, urgent strings", "volume_db": -8},
         "ambience": {"label": "harbour", "prompt": "gentle waves against wooden docks, gulls far away",
                      "volume_db": -15},
         "sfx": [{"label": "bell", "prompt": "distant ship bell", "volume_db": -6},
                 {"label": "rope", "prompt": "creaking mooring rope", "volume_db": -7},
                 {"label": "Bell", "prompt": "small brass bell", "volume_db": -50},
                 {"label": "gull", "prompt": "seagull call", "volume_db": "loud"},
                 {"label": "extra", "prompt": "never used", "volume_db": -5}]}


def clicks(bpm: float, seconds: float, rate: int = RATE, seed: int = 0) -> np.ndarray:
    """Decaying noise bursts on every beat (louder on the downbeat) over a little noise floor."""
    rng = np.random.default_rng(seed)
    a = 0.01 * rng.standard_normal(int(seconds * rate)).astype(np.float32)
    burst = (rng.standard_normal(int(0.05 * rate)) * np.exp(-np.linspace(0, 8, int(0.05 * rate)))).astype(np.float32)
    for k, t in enumerate(np.arange(0, seconds - 0.06, 60.0 / bpm)):
        i = int(t * rate)
        a[i:i + len(burst)] += burst * (0.8 if k % 4 == 0 else 0.5)
    return a


def wav_bytes(mono: np.ndarray, rate: int = RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        s = (np.clip(mono, -1, 1) * 32767).astype("<i2")
        w.writeframes(np.stack([s, s], 1).tobytes())
    return buf.getvalue()


def test_normalize_plan_clamps_and_dedupes() -> None:
    plan = normalize_plan(REPLY, n_sfx=4, tense=True, mood="calm")
    assert plan["bpm"] == 96 and plan["keyscale"] == "D minor"
    assert [m["label"] for m in plan["music"]] == ["harbour_dusk", "harbour_dusk_2"]
    assert [m["role"] for m in plan["music"]] == ["music", "music_tense"]
    assert plan["music_prompts"][0].endswith("instrumental, loopable game background music")
    assert plan["music_labels"] == "music_harbour_dusk\nmusic_harbour_dusk_2"
    assert [s["label"] for s in plan["sfx"]] == ["bell", "rope", "bell_2", "gull"]
    assert plan["sfx_labels"].splitlines() == ["sfx_bell", "sfx_rope", "sfx_bell_2", "sfx_gull"]
    assert [s["volume_db"] for s in plan["sfx"]] == [-6, -7, -40, -6.0]
    assert plan["ambience"]["loop"] and not plan["sfx"][0]["loop"]
    assert "no music" in plan["ambience_prompt"] and plan["ambience_label"] == "ambience_harbour"

    off = normalize_plan({**REPLY, "bpm": 400, "keyscale": "H major"}, n_sfx=3, tense=False)
    assert off["bpm"] == 200 and off["keyscale"] == "C major" and len(off["music"]) == 1 and len(off["sfx"]) == 3
    with pytest.raises(StepError):
        normalize_plan({**REPLY, "sfx": []}, n_sfx=3, tense=True)
    assert "exactly 5" in build_prompt("", 5) and "Mood" in build_prompt("rainy", 3)
    assert "D minor" in KEYSCALES and len(KEYSCALES) == 34


@pytest.mark.parametrize("bpm", [72, 96, 128, 150])
def test_estimate_bpm_click_track(bpm: int) -> None:
    est = estimate_bpm(clicks(bpm, 20), RATE)
    assert est is not None and tempo_error(est, bpm) < 0.02, est


def test_bpm_check_flags_mismatch() -> None:
    assert tempo_error(48, 96) == 0 and tempo_error(192, 96) == 0
    ok = bpm_check({"a": 96.4, "b": 191.0}, 96, 0.04)
    assert ok["ok"] and ok["stems_agree"]
    bad = bpm_check({"a": 96.0, "b": 120.0}, 96, 0.04)
    assert not bad["ok"] and "b ~120.0" in bad["message"]
    assert not bpm_check({"a": None}, 96, 0.04)["ok"]
    assert estimate_bpm(np.zeros(RATE * 5, np.float32), RATE) is None


class FakeQuery:
    def __init__(self, reply: dict[str, Any]):
        self.reply = reply
        self.calls: list[tuple[list[dict[str, Any]], Any]] = []

    async def __call__(self, content: list[dict[str, Any]], options: Any) -> ClaudeReply:
        self.calls.append((content, options))
        return ClaudeReply(self.reply, "", {"cost_usd": 0.02, "model": options.model})


class FakeAudioGen(FakeExec):
    """Stands in for ACE-Step / Stable Audio: music at the planned BPM (or `music_bpm`), noise bursts otherwise."""

    def __init__(self, kind: str, music_bpm: list[float] | None = None):
        super().__init__(kind)
        self.music_bpm = music_bpm
        self.params: list[dict[str, Any]] = []

    async def run(self, ctx: Any, params: dict[str, Any], inputs: Any) -> StepResult:
        self.params.append(params)
        if "94.tags" in params:
            bpm = (self.music_bpm or [])[len(self.params) - 1] if self.music_bpm else params["94.bpm"]
            data = [wav_bytes(clicks(float(bpm), 12, seed=len(self.params)))]
        else:
            data = [wav_bytes(clicks(70, 2.5, seed=i) * 0.5) for i in range(int(params.get("11.batch_size", 1)))]
        return StepResult(outputs={"audio": [await ctx.blobs.put_bytes(d, name=f"gen_{i}.wav", mime="audio/wav")
                                             for i, d in enumerate(data)]})


def _env(tmp_path: Path, music_bpm: list[float] | None = None) -> tuple[Env, FakeQuery, FakeAudioGen, FakeAudioGen]:
    q = FakeQuery(REPLY)
    music, sfx = FakeAudioGen("comfy.music_ace_step15", music_bpm), FakeAudioGen("comfy.sfx_stable_audio")
    text = (RECIPES / "scene-soundscape.yaml").read_text(encoding="utf-8")
    env = Env(tmp_path, [music, sfx], {"scene-soundscape": text})
    plan = ClaudeSoundscapeExecutor(env.settings, q)
    for ex in (plan, AudioPost(env.settings), SoundscapeMix()):
        env.registry.register(ex)
    return env, q, music, sfx


async def _run(env: Env, music_seconds: float = 15) -> tuple[str, dict[str, Any]]:
    import asyncio

    img = await env.blobs.put_bytes(b"\x89PNG fake scene", name="scene.png", mime="image/png")
    run_id = await env.engine.start_run("scene-soundscape", {"image": img.hash, "mood": "calm", "takes": 1,
                                                             "music_seconds": music_seconds},
                                        params={"approve": {"enabled": False}})
    await asyncio.wait_for(env.engine.wait_idle(run_id), 60)
    return run_id, env.steps(run_id)


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
async def test_recipe_runs_with_mocked_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from studio_core.adapters import claude as cl

    monkeypatch.setattr(cl, "_image_block", lambda d, m: {"type": "image", "source": {"type": "base64"}})
    env, q, music, sfx = _env(tmp_path)
    run_id, steps = await _run(env)
    assert env.status(run_id) == RunStatus.SUCCEEDED, {k: s.error for k, s in steps.items() if s.error}
    assert q.calls and q.calls[0][1].model == "claude-opus-5-5"
    assert [p["94.tags"] for p in music.params] == normalize_plan(REPLY, n_sfx=4, tense=True)["music_prompts"]
    assert all(p["94.bpm"] == 96 and p["94.keyscale"] == "D minor" for p in music.params)
    # One ambience call plus one call per SFX prompt (the map over the plan's list).
    texts = sorted(p["6.text"].split(",")[0] for p in sfx.params)
    assert texts == ["creaking mooring rope", "distant ship bell", "gentle waves against wooden docks",
                     "seagull call", "small brass bell"]
    names = sorted(r.name for r in steps["approve"].outputs["audio"] if r.name.endswith(".ogg"))
    assert names == ["ambience_harbour_1_loop.ogg", "music_harbour_dusk_1_loop.ogg",
                     "music_harbour_dusk_2_1_loop.ogg", "sfx_bell_1.ogg", "sfx_bell_2_1.ogg", "sfx_gull_1.ogg",
                     "sfx_rope_1.ogg"]
    doc = json.loads(env.blobs.path(steps["approve"].outputs["soundscape"][0].hash).read_bytes())
    assert doc["type"] == "soundscape" and doc["bpm"] == 96 and doc["checks"]["bpm"]["ok"], doc["checks"]
    roles = {i["stem"]: (i["role"], i["loop"], i["volume_db"]) for i in doc["items"]}
    assert roles["music_harbour_dusk_2_1_loop"] == ("music_tense", True, -8)
    assert roles["sfx_bell_2_1"] == ("sfx", False, -40)
    assert roles["ambience_harbour_1_loop"] == ("ambience", True, -15)
    assert "flagged" not in steps["mix"].meta

    from test_export_engines import _row

    from studio_core.export import godot, unity
    from studio_core.store.bundle import engine_ready, engine_source

    step = _row("approve", "gate.approve", steps["approve"].outputs)
    assert engine_ready(step, env.blobs)
    src = engine_source(step, [], env.blobs, "Harbour")
    assert src.soundscape is not None and len(src.audio) == 7
    u = dict(unity.export(src))
    sc = json.loads(u["Audio/Harbour_soundscape.json"])
    assert {i["file"] for i in sc["items"]} == {f"Audio/{n}" for n in names}
    readme = u["README.txt"].decode()
    assert "AudioSource" in readme and "PlayScheduled" in readme and "Volume 0.35 (-9" in readme
    g = dict(godot.export(src))
    assert "Audio/Harbour_soundscape.json" in g and "Audio/sfx_rope_1.ogg" in g
    greadme = g["README.txt"].decode()
    assert "volume_db = -15" in greadme and "AudioStreamSynchronized" in greadme


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
async def test_recipe_flags_bpm_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from studio_core.adapters import claude as cl

    monkeypatch.setattr(cl, "_image_block", lambda d, m: {"type": "image", "source": {"type": "base64"}})
    env, *_ = _env(tmp_path, music_bpm=[96, 123])
    run_id, steps = await _run(env)
    assert env.status(run_id) == RunStatus.SUCCEEDED
    doc = json.loads(env.blobs.path(steps["mix"].outputs["soundscape"][0].hash).read_bytes())
    check = doc["checks"]["bpm"]
    assert not check["ok"] and not check["stems_agree"]
    assert steps["mix"].meta["flagged"] == [check["message"]]


def test_recipe_loads_and_lints() -> None:
    r = load_recipe_file(RECIPES / "scene-soundscape.yaml")
    assert [s.id for s in r.steps] == ["plan", "music", "ambience", "sfx", "music_post", "ambience_post",
                                       "sfx_post", "mix", "approve"]
    assert r.step("music").map_param == "94.tags" and r.step("sfx").map_param == "6.text"
    _, issues = validate((RECIPES / "scene-soundscape.yaml").read_text(encoding="utf-8"), None)
    assert issues == []


def _scene_source(tmp_path: Path, bpm_ok: bool):
    from studio_core.export.source import Source

    names = ["music_a_1_loop.ogg", "music_b_1_loop.ogg", "ambience_x_1_loop.ogg", "sfx_bell_1.ogg", "sfx_hit_1.wav"]
    for n in names:
        (tmp_path / n).write_bytes(b"x")
    roles = ["music", "music_tense", "ambience", "sfx", "sfx"]
    items = [{"stem": Path(n).stem, "role": r, "label": r, "volume_db": -10, "loop": "_loop" in n}
             for n, r in zip(names, roles)]
    check = {"ok": bpm_ok, "message": "music_b ước lượng 123 BPM, lệch 96"}
    return Source(asset="Harbour", uid="u1", audio=[(n, tmp_path / n) for n in names],
                  soundscape={"bpm": 96, "keyscale": "D minor", "items": items, "checks": {"bpm": check}})


def test_engine_import_settings_and_bpm_mismatch_readme(tmp_path: Path) -> None:
    from studio_core.export import godot, unity

    src = _scene_source(tmp_path, bpm_ok=False)
    u = dict(unity.export(src))
    load = {n: int(u[f"Audio/{n}.meta"].decode().split("loadType: ")[1][0]) for n in
            ("music_a_1_loop.ogg", "music_b_1_loop.ogg", "ambience_x_1_loop.ogg", "sfx_bell_1.ogg")}
    assert load == {"music_a_1_loop.ogg": 2, "music_b_1_loop.ogg": 2, "ambience_x_1_loop.ogg": 1, "sfx_bell_1.ogg": 0}
    assert "preloadAudioData: 1" in u["Audio/sfx_bell_1.ogg.meta"].decode()
    readme = u["README.txt"].decode()
    assert "LỆCH BPM" in readme and "PlayScheduled" not in readme
    g = dict(godot.export(src))
    imp = g["Audio/music_a_1_loop.ogg.import"].decode()
    assert 'importer="oggvorbisstr"' in imp and "loop=true" in imp
    assert "Audio/ambience_x_1_loop.ogg.import" in g and "Audio/sfx_bell_1.ogg.import" not in g
    assert "AudioStreamSynchronized" not in g["README.txt"].decode()

    ok = _scene_source(tmp_path, bpm_ok=True)
    assert "PlayScheduled" in dict(unity.export(ok))["README.txt"].decode()
    assert "AudioStreamSynchronized" in dict(godot.export(ok))["README.txt"].decode()
