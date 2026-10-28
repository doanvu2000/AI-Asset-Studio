"""`op.soundscape`: gathers the post-processed clips of a scene soundscape into soundscape.json (role, label, file,
suggested volume, loop flag) and checks that the music stems really play at the planned BPM so they can be layered."""
from __future__ import annotations

import asyncio
import json
import re
import wave
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from ..config import _default_ffmpeg_exe
from ..contracts import BlobRef, Outputs, StepContext, StepError, StepResult
from . import audio as au
from .executors import OpExecutor, _put_png

SOUNDSCAPE_FILE = "soundscape.json"
SOUNDSCAPE_TYPE = "soundscape"
ANALYSIS_RATE = 24000
HOP = 256
N_FFT = 1024
TEMPO_RANGE = (50.0, 220.0)


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    """Mono float32 of a PCM WAV (op.audio_post writes s16le)."""
    with wave.open(str(path), "rb") as w:
        width, ch, rate = w.getsampwidth(), w.getnchannels(), w.getframerate()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError(f"{path.name}: {8 * width}-bit WAV not supported")
    a = np.frombuffer(raw, "<i2").astype(np.float32) / 32768.0
    return a.reshape(-1, ch).mean(1), rate


def onset_envelope(mono: np.ndarray, rate: int) -> tuple[np.ndarray, float]:
    """Positive log-spectral flux, mean-removed; returns (envelope, frames per second)."""
    step = max(1, int(round(rate / ANALYSIS_RATE)))
    if step > 1:
        mono = mono[:len(mono) // step * step].reshape(-1, step).mean(1)
    sr = rate / step
    if len(mono) < N_FFT * 4:
        return np.zeros(0, np.float32), sr / HOP
    n = 1 + (len(mono) - N_FFT) // HOP
    win = np.hanning(N_FFT).astype(np.float32)
    flux = np.empty(n - 1, np.float32)
    prev = None
    # Chunked so a 4-minute cue does not allocate one huge frame matrix.
    for s in range(0, n, 2048):
        idx = np.arange(N_FFT)[None, :] + HOP * np.arange(s, min(n, s + 2048))[:, None]
        spec = np.log1p(100.0 * np.abs(np.fft.rfft(mono[idx] * win, axis=1))).astype(np.float32)
        if prev is not None:
            spec = np.vstack([prev, spec])
            flux[s - 1:s - 1 + len(spec) - 1] = np.maximum(0, np.diff(spec, axis=0)).sum(1)
        else:
            flux[:len(spec) - 1] = np.maximum(0, np.diff(spec, axis=0)).sum(1)
        prev = spec[-1:]
    fps = sr / HOP
    k = max(1, int(fps * 0.5))
    local = np.convolve(flux, np.ones(k, np.float32) / k, mode="same")
    return np.maximum(0, flux - local), fps


def estimate_bpm(mono: np.ndarray, rate: int) -> float | None:
    """Onset autocorrelation with a log-normal tempo prior around 120 BPM (librosa's beat_track default)."""
    env, fps = onset_envelope(mono, rate)
    if len(env) < fps * 4 or float(env.std()) < 1e-6:
        return None
    env = env - env.mean()
    size = 1 << int(np.ceil(np.log2(2 * len(env))))
    spec = np.fft.rfft(env, size)
    ac = np.fft.irfft(spec * np.conj(spec), size)[:len(env)]
    ac /= ac[0] if ac[0] > 0 else 1.0
    lo, hi = int(fps * 60 / TEMPO_RANGE[1]), int(np.ceil(fps * 60 / TEMPO_RANGE[0]))
    hi = min(hi, len(ac) - 2)
    if hi <= lo + 1:
        return None
    lags = np.arange(lo, hi + 1)
    bpms = 60.0 * fps / lags
    prior = np.exp(-0.5 * np.log2(bpms / 120.0) ** 2)
    score = ac[lags] * prior
    i = int(np.argmax(score))
    lag = float(lags[i])
    if 0 < i < len(lags) - 1:
        y0, y1, y2 = ac[lags[i] - 1], ac[lags[i]], ac[lags[i] + 1]
        d = y0 - 2 * y1 + y2
        if d < 0:
            lag += 0.5 * (y0 - y2) / d
    return round(float(60.0 * fps / lag), 1)


def tempo_error(est: float, target: float) -> float:
    """Relative error, forgiving the usual half/double-time octave confusion."""
    return min(abs(est * f - target) / target for f in (0.5, 1.0, 2.0))


def bpm_check(estimates: dict[str, float | None], target: float, tolerance: float) -> dict[str, Any]:
    stems = [{"stem": s, "estimated": e, "error": None if e is None else round(tempo_error(e, target), 4),
              "ok": e is not None and tempo_error(e, target) <= tolerance} for s, e in estimates.items()]
    known = [e for e in estimates.values() if e is not None]
    pair_ok = True
    if len(known) >= 2:
        pair_ok = all(tempo_error(e, known[0]) <= tolerance for e in known[1:])
    ok = all(s["ok"] for s in stems) and pair_ok
    msg = ""
    if not ok:
        bad = ", ".join(f"{s['stem']} ~{s['estimated']}" for s in stems if not s["ok"])
        msg = f"music stems do not keep {target:g} BPM" + (f" ({bad})" if bad else "") + \
              ("" if pair_ok else "; the stems disagree with each other")
    return {"target": target, "tolerance": tolerance, "stems": stems, "stems_agree": pair_ok, "ok": ok,
            "message": msg}


def _by_stem(refs: list[BlobRef]) -> dict[str, dict[str, BlobRef]]:
    out: dict[str, dict[str, BlobRef]] = {}
    for r in refs:
        name = r.name or ""
        stem, _, ext = name.rpartition(".")
        if stem:
            out.setdefault(stem, {})[ext.lower()] = r
    return out


def _match(stems: dict[str, dict[str, BlobRef]], prefix: str) -> list[str]:
    """op.audio_post names clips <label>_<take>[_loop]; an exact pattern keeps `wind` from claiming `wind_2_1`."""
    pat = re.compile(re.escape(prefix) + r"_\d+(?:_loop)?")
    return [s for s in stems if pat.fullmatch(s)]


def _seconds(path: Path) -> float | None:
    try:
        with wave.open(str(path), "rb") as w:
            return round(w.getnframes() / float(w.getframerate()), 3)
    except (OSError, wave.Error, EOFError):
        return None


def _mono(ctx: StepContext, files: dict[str, BlobRef], ffmpeg: str) -> tuple[np.ndarray, int]:
    if "wav" in files:
        try:
            return read_wav(ctx.blobs.path(files["wav"].hash))
        except (OSError, wave.Error, ValueError, EOFError):
            pass
    ref = next(iter(files.values()))
    return au.decode(ffmpeg, ctx.blobs.path(ref.hash)).mean(1), au.RATE


def preview(rows: list[dict[str, Any]], waves: dict[str, Path], check: dict[str, Any] | None) -> Image.Image:
    w, row_h, head = 900, 64, 40
    img = Image.new("RGB", (w, head + row_h * max(1, len(rows))), (18, 17, 24))
    d = ImageDraw.Draw(img)
    title = "soundscape" if check is None else (f"BPM {check['target']:g}: " + ("OK" if check["ok"] else "MISMATCH"))
    d.text((12, 12), title, fill=(120, 230, 140) if check is None or check["ok"] else (255, 120, 90))
    for i, r in enumerate(rows):
        y = head + i * row_h
        loop = " loop" if r["loop"] else ""
        est = f" ~{r['bpm_estimated']} BPM" if r.get("bpm_estimated") else ""
        d.text((12, y + 8), f"{r['role']}{loop}", fill=(160, 160, 190))
        d.text((12, y + 26), f"{r['label']}  {r['volume_db']:+.0f} dB{est}", fill=(235, 235, 240))
        wp = waves.get(r["stem"])
        if wp is not None and wp.is_file():
            with Image.open(wp) as wi:
                img.paste(wi.convert("RGB").resize((560, row_h - 8)), (w - 572, y + 4))
    return img


class SoundscapeMix(OpExecutor):
    kind = "op.soundscape"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        plan_refs = inputs.get("plan") or []
        if not plan_refs:
            raise StepError("op_no_input", "input 'plan' (claude.soundscape json) is empty")
        plan = json.loads(await asyncio.to_thread(ctx.blobs.path(plan_refs[0].hash).read_bytes))
        tol = float(params.get("bpm_tolerance", 0.04))
        ffmpeg = str(_default_ffmpeg_exe())
        groups = {port: _by_stem(inputs.get(port) or []) for port in ("music", "ambience", "sfx")}
        waves = {(r.name or "").removesuffix("_wave.png"): ctx.blobs.path(r.hash) for r in inputs.get("waves") or []}

        cues: list[tuple[str, dict[str, Any]]] = [("music", c) for c in plan.get("music") or []]
        cues += [("ambience", plan["ambience"])] if plan.get("ambience") else []
        cues += [("sfx", c) for c in plan.get("sfx") or []]
        rows: list[dict[str, Any]] = []
        estimates: dict[str, float | None] = {}
        prefixes = {"music": "music_", "ambience": "ambience_", "sfx": "sfx_"}
        for port, cue in cues:
            for stem in _match(groups[port], prefixes[port] + cue["label"]):
                files = groups[port][stem]
                row = {"role": cue["role"], "label": cue["label"], "stem": stem, "prompt": cue["prompt"],
                       "volume_db": cue["volume_db"], "loop": bool(cue.get("loop")),
                       "files": sorted(r.name for r in files.values() if r.name),
                       "hashes": {ext: r.hash for ext, r in files.items()},
                       "seconds": _seconds(ctx.blobs.path(files["wav"].hash)) if "wav" in files else None}
                if port == "music":
                    mono, rate = await asyncio.to_thread(_mono, ctx, files, ffmpeg)
                    row["bpm_estimated"] = estimates[stem] = await asyncio.to_thread(estimate_bpm, mono, rate)
                rows.append(row)
        if not rows:
            raise StepError("op_no_input", "no clip matched the soundscape plan labels")
        check = bpm_check(estimates, float(plan.get("bpm") or 0), tol) if estimates and plan.get("bpm") else None
        doc = {"type": SOUNDSCAPE_TYPE, "version": 1, "scene": plan.get("scene", ""), "bpm": plan.get("bpm"),
               "keyscale": plan.get("keyscale"), "items": rows, "checks": {"bpm": check}}
        ref = await ctx.blobs.put_bytes(json.dumps(doc, ensure_ascii=False, indent=1).encode("utf-8"),
                                        name=SOUNDSCAPE_FILE, mime="application/json")
        prev = await _put_png(ctx, await asyncio.to_thread(preview, rows, waves, check), "soundscape_preview.png")
        if check is not None and not check["ok"]:
            ctx.log(check["message"], "warning")
        meta: dict[str, Any] = {"items": len(rows), "bpm": plan.get("bpm"), "keyscale": plan.get("keyscale"),
                                "bpm_estimates": estimates}
        if check is not None and not check["ok"]:
            meta["flagged"] = [check["message"]]
        return StepResult(outputs={"soundscape": [ref], "preview": [prev]}, meta=meta)


SOUNDSCAPE_OPS: tuple[type[OpExecutor], ...] = (SoundscapeMix,)
