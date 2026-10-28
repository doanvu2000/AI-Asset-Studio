"""`op.audio_post`: game-ready clips from generated audio. Trims silence, fades, peak-normalises, and for music makes a
seamless loop (the tail crossfaded into the head); writes WAV + OGG and a waveform image for the gallery."""
from __future__ import annotations

import asyncio
import re
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from ..config import _default_ffmpeg_exe
from ..contracts import Outputs, StepContext, StepError, StepResult
from .executors import OpExecutor, _put_png

RATE = 48000
SILENCE_DB = -50.0
RELATIVE_DB = -40.0


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40] or "clip"


def _ffmpeg(exe: str, args: list[str], data: bytes | None = None) -> bytes:
    try:
        proc = subprocess.run([exe, "-v", "error", "-y", *args], input=data, capture_output=True)
    except OSError as e:
        raise StepError("ffmpeg_missing", f"ffmpeg not runnable ({exe}): {e}. Set its path in Settings.") from e
    if proc.returncode != 0:
        raise StepError("ffmpeg_failed", proc.stderr.decode("utf-8", "replace")[-800:])
    return proc.stdout


def decode(exe: str, path: Path) -> np.ndarray:
    """(samples, 2) float32 at RATE."""
    raw = _ffmpeg(exe, ["-i", str(path), "-f", "f32le", "-ac", "2", "-ar", str(RATE), "-"])
    return np.frombuffer(raw, np.float32).reshape(-1, 2).copy()


def encode(exe: str, audio: np.ndarray, path: Path) -> None:
    codec = ["-c:a", "libvorbis", "-q:a", "6"] if path.suffix == ".ogg" else ["-c:a", "pcm_s16le"]
    _ffmpeg(exe, ["-f", "f32le", "-ac", "2", "-ar", str(RATE), "-i", "-", *codec, str(path)],
            np.ascontiguousarray(audio, np.float32).tobytes())


def trim_silence(a: np.ndarray, pad_s: float = 0.01) -> np.ndarray:
    level = np.abs(a).max(1)
    # Generated clips carry a low noise floor after the sound; measure silence against this clip's own peak.
    floor = max(10 ** (SILENCE_DB / 20), float(level.max()) * 10 ** (RELATIVE_DB / 20))
    loud = np.nonzero(level > floor)[0]
    if not len(loud):
        return a
    pad = int(pad_s * RATE)
    return a[max(loud[0] - pad, 0):min(loud[-1] + pad + 1, len(a))]


def fade(a: np.ndarray, in_s: float, out_s: float) -> np.ndarray:
    a = a.copy()
    n_in, n_out = (min(int(s * RATE), len(a) // 2) for s in (in_s, out_s))
    if n_in > 0:
        a[:n_in] *= np.linspace(0, 1, n_in, dtype=np.float32)[:, None]
    if n_out > 0:
        a[-n_out:] *= np.linspace(1, 0, n_out, dtype=np.float32)[:, None]
    return a


def make_loop(a: np.ndarray, xfade_s: float) -> np.ndarray:
    """Drop the first `xfade` and blend it under the tail, so the last sample flows into the first one."""
    n = min(int(xfade_s * RATE), len(a) // 4)
    if n <= 0:
        return a
    # Equal-power curves keep the loudness steady through the blend.
    t = np.linspace(0, np.pi / 2, n, dtype=np.float32)[:, None]
    out = a[n:].copy()
    out[-n:] = a[-n:] * np.cos(t) + a[:n] * np.sin(t)
    return out


def normalize(a: np.ndarray, peak_db: float) -> np.ndarray:
    peak = float(np.abs(a).max())
    return a if peak < 1e-6 else a * (10 ** (peak_db / 20) / peak)


def waveform(a: np.ndarray, size: tuple[int, int] = (800, 160)) -> Image.Image:
    w, h = size
    img = Image.new("RGB", size, (24, 22, 32))
    d = ImageDraw.Draw(img)
    mono = np.abs(a).max(1)
    if len(mono):
        cols = np.array_split(mono, w)
        for x, c in enumerate(cols):
            v = float(c.max()) if len(c) else 0.0
            d.line((x, h / 2 - v * h / 2, x, h / 2 + v * h / 2), fill=(140, 200, 255))
    return img


def process(exe: str, src: Path, out_dir: Path, stem: str, p: dict[str, Any]) -> dict[str, Any]:
    a = decode(exe, src)
    raw_s = len(a) / RATE
    if p["trim"]:
        a = trim_silence(a)
    if p["max_s"] > 0:
        a = a[:int(p["max_s"] * RATE)]
    if p["loop"]:
        a = make_loop(a, p["crossfade"])
    else:
        a = fade(a, p["fade_in"], p["fade_out"])
    a = normalize(a, p["peak_db"])
    files = []
    for ext in p["formats"]:
        f = out_dir / f"{stem}.{ext}"
        encode(exe, a, f)
        files.append(f)
    seam = 0.0
    if p["loop"] and len(a) > 1:
        seam = float(np.abs(a[-1] - a[0]).max())
    return {"files": files, "wave": waveform(a), "seconds": round(len(a) / RATE, 3), "raw_seconds": round(raw_s, 3),
            "peak": round(float(np.abs(a).max()), 4), "loop_seam": round(seam, 4)}


class AudioPost(OpExecutor):
    kind = "op.audio_post"
    version = "1"

    def __init__(self, settings: Any = None):
        self.settings = settings

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        refs = inputs.get("audio") or []
        if not refs:
            raise StepError("op_no_input", "input 'audio' is empty")
        loop = bool(params.get("loop", False))
        formats = [f.strip() for f in str(params.get("formats", "ogg,wav")).split(",") if f.strip()]
        if not formats or any(f not in ("ogg", "wav") for f in formats):
            raise StepError("op_bad_param", f"formats must be ogg and/or wav, got {params.get('formats')!r}")
        p = {"loop": loop, "trim": bool(params.get("trim", not loop)), "max_s": float(params.get("max_s", 0)),
             "crossfade": float(params.get("crossfade", 1.5)), "fade_in": float(params.get("fade_in", 0.005)),
             "fade_out": float(params.get("fade_out", 0.05)), "peak_db": float(params.get("peak_db", -1.0)),
             "formats": formats}
        exe = str(getattr(self.settings, "ffmpeg_exe", None) or _default_ffmpeg_exe())
        out_dir = Path(ctx.job_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        labels = [_slug(ln) for ln in str(params.get("labels") or "").splitlines() if ln.strip()]
        per = max(1, int(params.get("per_label", 1)))
        audio, waves, rows = [], [], []
        for i, ref in enumerate(refs, 1):
            if labels:
                stem = f"{labels[min((i - 1) // per, len(labels) - 1)]}_{(i - 1) % per + 1}"
            else:
                stem = (ref.name or "audio").rsplit(".", 1)[0] + (f"_{i:02d}" if len(refs) > 1 else "")
            res = await asyncio.to_thread(process, exe, ctx.blobs.path(ref.hash), out_dir, stem, p)
            for f in res.pop("files"):
                mime = "audio/ogg" if f.suffix == ".ogg" else "audio/wav"
                audio.append(await ctx.blobs.put_file(f, name=f.name, mime=mime))
            waves.append(await _put_png(ctx, res.pop("wave"), f"{stem}_wave.png"))
            rows.append({"name": stem, **res})
        return StepResult(outputs={"audio": audio, "images": waves}, meta={"clips": rows, "params": p})
