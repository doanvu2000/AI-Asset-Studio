"""`tts.npc_voice`: CPU text-to-speech for NPC lines (sherpa-onnx, no GPU so it never waits on ComfyUI's VRAM).
Vietnamese = Piper vi_VN vais1000 fed with IPA from tts_text (or espeak-ng when installed), English = Kokoro v1.0
with its US lexicon. Models live under <comfy_models_dir>/tts/."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import threading
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ..contracts import Lane, Outputs, StepContext, StepError, StepResult
from . import tts_text as tt

MODELS_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models"
VI_MODEL = "vits-piper-vi_VN-vais1000-medium"
EN_MODEL = "kokoro-int8-multi-lang-v1_0"
KOKORO_VOICES = ["af_alloy", "af_aoede", "af_bella", "af_heart", "af_jessica", "af_kore", "af_nicole", "af_nova",
                 "af_river", "af_sarah", "af_sky", "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam",
                 "am_michael", "am_onyx", "am_puck", "am_santa", "bf_alice", "bf_emma", "bf_isabella", "bf_lily",
                 "bm_daniel", "bm_fable", "bm_george", "bm_lewis"]
EN_AUTO = {("male", "young"): "am_puck", ("male", "adult"): "am_michael", ("male", "old"): "bm_george",
           ("female", "young"): "af_nicole", ("female", "adult"): "af_heart", ("female", "old"): "bf_emma"}
LANGS = ("vi", "en")
INSTALL_HINT = (
    "CPU TTS chưa cài đủ. Trong core/: `uv add sherpa-onnx` (Apache-2.0), rồi tải và giải nén vào "
    "{dir}: {url}/{vi}.tar.bz2 (tiếng Việt) và {url}/{en}.tar.bz2 (tiếng Anh). Tuỳ chọn: espeak-ng "
    "(đặt biến STUDIO_ESPEAK_NG = đường dẫn espeak-ng.exe) để đọc tiếng Việt chuẩn hơn.")


def models_dir(settings: Any) -> Path:
    env = os.environ.get("STUDIO_TTS_MODELS")
    if env:
        return Path(env)
    base = getattr(settings, "comfy_models_dir", None)
    return Path(base) / "tts" if base else Path("tts")


def missing(root: Path, langs: list[str]) -> list[str]:
    need = {"vi": VI_MODEL, "en": EN_MODEL}
    out = []
    try:
        import sherpa_onnx  # noqa: F401
    except ImportError:
        out.append("sherpa-onnx")
    out += [need[lang] for lang in langs if lang in need and not (root / need[lang]).is_dir()]
    return out


def install_error(root: Path, what: list[str]) -> StepError:
    return StepError("tts_missing", f"thiếu {', '.join(what)}. "
                     + INSTALL_HINT.format(dir=root, url=MODELS_URL, vi=VI_MODEL, en=EN_MODEL),
                     detail={"missing": what, "models_dir": str(root)})


def find_espeak() -> str | None:
    env = os.environ.get("STUDIO_ESPEAK_NG")
    if env and Path(env).is_file():
        return env
    return shutil.which("espeak-ng")


def espeak_phonemes(exe: str, text: str) -> list[str]:
    """Per sentence, so the Piper voice gets its pauses; stdin keeps Vietnamese out of the ANSI command line."""
    out = []
    for sent in tt.cue_chunks(tt.spell_numbers(text, "vi"), 10_000):
        end = sent[-1] if sent[-1] in ".!?" else "."
        proc = subprocess.run([exe, "-q", "--ipa", "-b", "1", "-v", "vi", "--stdin"], input=sent.encode("utf-8"),
                              capture_output=True, timeout=30)
        ipa = " ".join(proc.stdout.decode("utf-8", "replace").split())
        if proc.returncode != 0 or not ipa:
            raise RuntimeError(proc.stderr.decode("utf-8", "replace")[-300:] or "espeak-ng returned nothing")
        out.append(ipa + end)
    return out


@dataclass
class Speech:
    samples: np.ndarray
    rate: int
    voice: str
    engine: str


class SherpaTts:
    """Loads each model once; sherpa's OfflineTts is not documented as re-entrant, so calls are serialised."""

    def __init__(self, root: Path):
        self.root = root
        self._tts: dict[str, Any] = {}
        self._lexicon: set[str] | None = None
        self._lock = threading.Lock()

    def _load(self, lang: str) -> Any:
        if lang in self._tts:
            return self._tts[lang]
        import sherpa_onnx as so

        if lang == "vi":
            d = self.root / VI_MODEL
            model = so.OfflineTtsModelConfig(vits=so.OfflineTtsVitsModelConfig(
                model=str(d / "vi_VN-vais1000-medium.onnx"), tokens=str(d / "tokens.txt")), num_threads=4)
        else:
            d = self.root / EN_MODEL
            model = so.OfflineTtsModelConfig(kokoro=so.OfflineTtsKokoroModelConfig(
                model=str(d / "model.int8.onnx"), voices=str(d / "voices.bin"), tokens=str(d / "tokens.txt"),
                lexicon=str(d / "lexicon-us-en.txt"), dict_dir=str(d / "dict")), num_threads=4)
        self._tts[lang] = so.OfflineTts(so.OfflineTtsConfig(model=model, max_num_sentences=1))
        return self._tts[lang]

    def _known(self, word: str) -> bool:
        if self._lexicon is None:
            with open(self.root / EN_MODEL / "lexicon-us-en.txt", encoding="utf-8") as f:
                self._lexicon = tt.lexicon_words(f)
        return word in self._lexicon

    def synth(self, text: str, lang: str, voice: str, speed: float) -> Speech:
        import sherpa_onnx as so

        with self._lock:
            tts = self._load(lang)
            if lang == "vi":
                exe = find_espeak()
                engine = "piper-vi"
                try:
                    sentences = espeak_phonemes(exe, text) if exe else tt.vi_phonemes(text)
                    engine += "+espeak" if exe else "+g2p"
                except (OSError, RuntimeError, subprocess.SubprocessError):
                    sentences = tt.vi_phonemes(text)
                    engine += "+g2p"
                if not sentences:
                    raise StepError("tts_empty", f"nothing to say in {text!r}")
                g = so.GenerationConfig()
                g.sid, g.speed = 0, speed
                g.phoneme_codepoints = [[ord(c) for c in s] for s in sentences]
                audio = tts.generate(text, g)
                voice = "vais1000"
            else:
                sid = KOKORO_VOICES.index(voice) if voice in KOKORO_VOICES else 0
                audio = tts.generate(tt.en_respell(text, self._known), sid=sid, speed=speed)
                engine = "kokoro-v1.0"
        samples = np.asarray(audio.samples, np.float32)
        if not len(samples):
            raise StepError("tts_empty", f"TTS produced no audio for {text!r}")
        return Speech(samples, int(audio.sample_rate), voice, engine)


def write_wav(path: Path, samples: np.ndarray, rate: int) -> None:
    s = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(s.tobytes())


def pick_voice(lang: str, requested: str, hint: dict[str, Any]) -> str:
    if lang != "en":
        return "vais1000"
    if requested in KOKORO_VOICES:
        return requested
    gender = "female" if str(hint.get("gender", "")).lower().startswith("f") else "male"
    age = str(hint.get("age", "adult")).lower()
    return EN_AUTO[(gender, age if age in ("young", "old") else "adult")]


class NpcVoiceExecutor:
    kind = "tts.npc_voice"
    lane = Lane.CPU
    version = "1"

    def __init__(self, settings: Any = None, engine: Any = None):
        self.settings = settings
        self.engine = engine

    def cacheable(self, params: dict[str, Any]) -> bool:
        return True

    def cache_material(self, params: dict[str, Any]) -> str:
        from blake3 import blake3

        from studio_core import __version__

        h = blake3(f"{VI_MODEL}|{EN_MODEL}|{__version__}".encode())
        # Frozen builds have only .pyc inside the archive, so the source files may not exist on disk.
        for f in (Path(__file__), Path(tt.__file__)):
            if f.is_file():
                h.update(f.read_bytes())
        return h.hexdigest()

    def _engine(self, langs: list[str]) -> Any:
        if self.engine is None:
            root = models_dir(self.settings)
            gone = missing(root, langs)
            if gone:
                raise install_error(root, gone)
            self.engine = SherpaTts(root)
        return self.engine

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        refs = inputs.get("dialogue") or []
        if not refs:
            raise StepError("op_no_input", "input 'dialogue' (npc_dialogue json) is empty")
        doc = json.loads(await asyncio.to_thread(ctx.blobs.path(refs[0].hash).read_bytes))
        langs = [lang for lang in doc.get("languages") or [] if lang in LANGS]
        skipped = [lang for lang in doc.get("languages") or [] if lang not in LANGS]
        if not langs:
            raise StepError("tts_unsupported_lang", f"no TTS voice for {doc.get('languages')} (có: vi, en)")
        engine = self._engine(langs)
        hint = doc.get("voice") or {}
        try:
            speed = float(params.get("speed") or 0) or float(hint.get("speed") or 1.0)
        except (TypeError, ValueError):
            speed = 1.0
        speed = min(1.4, max(0.7, speed))
        voices = {lang: pick_voice(lang, str(params.get(f"voice_{lang}") or "auto"), hint) for lang in langs}
        jobs = [(line, lang) for line in doc.get("lines") or [] for lang in langs
                if str((line.get("text") or {}).get(lang) or "").strip()]
        if not jobs and doc.get("allow_empty"):
            return StepResult(outputs={}, meta={"clips": 0})
        if not jobs:
            raise StepError("op_no_input", "dialogue has no lines to voice")
        await asyncio.to_thread(Path(ctx.job_dir).mkdir, parents=True, exist_ok=True)
        audio, items = [], []
        for i, (line, lang) in enumerate(jobs):
            ctx.progress(i, len(jobs), f"{lang} {line['id']}")
            text = str(line["text"][lang])
            # A line may carry its own speaker voice (storyboard cast); an explicit speed param still wins.
            lv = line.get("voice") if isinstance(line.get("voice"), dict) else None
            voice = pick_voice(lang, str(lv.get("name") or "auto"), lv) if lv else voices[lang]
            line_speed = (min(1.4, max(0.7, float(lv.get("speed") or speed)))
                          if lv and not float(params.get("speed") or 0) else speed)
            sp = await asyncio.to_thread(engine.synth, text, lang, voice, line_speed)
            stem = f"{lang}_{line['id']}"
            path = Path(ctx.job_dir) / f"{stem}.wav"
            await asyncio.to_thread(write_wav, path, sp.samples, sp.rate)
            audio.append(await ctx.blobs.put_file(path, name=path.name, mime="audio/wav"))
            items.append({"id": line["id"], "lang": lang, "stem": stem, "voice": sp.voice, "engine": sp.engine,
                          "seconds": round(len(sp.samples) / sp.rate, 3)})
        ctx.progress(len(jobs), len(jobs), "tts")
        report = {"type": "npc_tts", "labels": "\n".join(it["stem"] for it in items), "voices": voices,
                  "speed": speed, "items": items, "skipped_languages": skipped}
        ref = await ctx.blobs.put_bytes(json.dumps(report, ensure_ascii=False, indent=1).encode("utf-8"),
                                        name="npc_tts.json", mime="application/json")
        meta: dict[str, Any] = {"clips": len(items), "voices": voices, "speed": speed,
                                "engines": sorted({it["engine"] for it in items})}
        if skipped:
            meta["flagged"] = [f"no TTS voice for {', '.join(skipped)}"]
        return StepResult(outputs={"audio": audio, "json": [ref]}, meta=meta)
