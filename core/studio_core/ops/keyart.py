"""Key art -> short looping teaser around Wan 2.2 TI2V 5B. `op.keyart_prep` fits the image to the model's 32px grid at
a target pixel budget and picks a 4k+1 frame count; `op.keyart_video` turns the generated frames into a seamless loop
(tail crossfaded into head, or ping-pong) and encodes MP4 (H.264) + WebM (VP9) + GIF preview + poster frame."""
from __future__ import annotations

import asyncio
import math
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from ..config import _default_ffmpeg_exe
from ..contracts import Outputs, StepContext, StepError, StepResult
from .audio import _ffmpeg
from .executors import OpExecutor, _images_in, _load, _put_png
from .rig import _put_json

FPS = 24
# Wan 2.2 TI2V 5B is trained on 121 frames (5 s at 24 fps); its VAE packs 4 frames per latent step plus the first.
MAX_FRAMES = 121
RESOLUTIONS = {"720p": 1280 * 704, "540p": 960 * 544, "480p": 832 * 480}
LOOP_MODES = ("crossfade", "pingpong", "none")


def grid_size(w: int, h: int, area: int, step: int = 32) -> tuple[int, int]:
    aspect = w / h
    gw = max(step, round(math.sqrt(area * aspect) / step) * step)
    gh = max(step, round(math.sqrt(area / aspect) / step) * step)
    return gw, gh


def frame_count(seconds: float, fps: int = FPS) -> int:
    n = max(2, round(seconds * fps / 4))
    return min(MAX_FRAMES, 4 * n + 1)


def fit_cover(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    if img.mode in ("RGBA", "LA", "P"):
        rgba = img.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (0, 0, 0, 255))
        img = Image.alpha_composite(bg, rgba)
    img = img.convert("RGB")
    tw, th = size
    scale = max(tw / img.width, th / img.height)
    rw, rh = max(tw, round(img.width * scale)), max(th, round(img.height * scale))
    img = img.resize((rw, rh), Image.Resampling.LANCZOS)
    x, y = (rw - tw) // 2, (rh - th) // 2
    return img.crop((x, y, x + tw, y + th))


def crossfade_loop(frames: list[np.ndarray], k: int) -> list[np.ndarray]:
    """Drops the first k frames and blends them under the last k, so the final frame flows into the first one."""
    n = len(frames)
    k = min(k, n // 3)
    if k <= 0:
        return list(frames)
    out = list(frames[k:n - k])
    for i in range(k):
        w = (i + 1) / (k + 1)
        mix = frames[n - k + i].astype(np.float32) * (1 - w) + frames[i].astype(np.float32) * w
        out.append(np.clip(mix + 0.5, 0, 255).astype(np.uint8))
    return out


def pingpong_loop(frames: list[np.ndarray]) -> list[np.ndarray]:
    return list(frames) + list(frames[-2:0:-1])


def make_loop(frames: list[np.ndarray], mode: str, crossfade_frames: int) -> list[np.ndarray]:
    if mode == "crossfade":
        return crossfade_loop(frames, crossfade_frames)
    if mode == "pingpong":
        return pingpong_loop(frames)
    return list(frames)


def _small(a: np.ndarray) -> np.ndarray:
    h, w = a.shape[:2]
    return np.asarray(Image.fromarray(a).resize((64, max(1, round(64 * h / w))), Image.Resampling.BILINEAR),
                      np.float32)


def seam_ratio(frames: list[np.ndarray]) -> float:
    """Wrap-around jump (last -> first) relative to the median frame-to-frame change; ~1 means the seam is invisible."""
    if len(frames) < 3:
        return 0.0
    small = [_small(f) for f in frames]
    steps = [float(np.abs(small[i + 1] - small[i]).mean()) for i in range(len(small) - 1)]
    seam = float(np.abs(small[0] - small[-1]).mean())
    return round(seam / max(float(np.median(steps)), 1e-3), 3)


def motion_level(frames: list[np.ndarray]) -> float:
    """Mean absolute change from the first frame to the farthest one (0-255 scale): near 0 means a frozen video."""
    if len(frames) < 2:
        return 0.0
    small = [_small(f) for f in frames]
    return round(max(float(np.abs(s - small[0]).mean()) for s in small[1:]), 2)


def encode_all(exe: str, frame_dir: Path, out_dir: Path, stem: str, fps: int, gif_width: int, gif_fps: int,
               formats: list[str]) -> list[Path]:
    src = ["-framerate", str(fps), "-i", str(frame_dir / "f_%04d.png")]
    files = []
    if "mp4" in formats:
        f = out_dir / f"{stem}.mp4"
        _ffmpeg(exe, [*src, "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p",
                      "-movflags", "+faststart", str(f)])
        files.append(f)
    if "webm" in formats:
        f = out_dir / f"{stem}.webm"
        _ffmpeg(exe, [*src, "-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-row-mt", "1", "-cpu-used", "4",
                      "-pix_fmt", "yuv420p", str(f)])
        files.append(f)
    if "gif" in formats:
        f = out_dir / f"{stem}.gif"
        vf = (f"fps={gif_fps},scale={gif_width}:-2:flags=lanczos,split[a][b];"
              "[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4")
        _ffmpeg(exe, [*src, "-vf", vf, "-loop", "0", str(f)])
        files.append(f)
    return files


def _write_frames(frames: list[np.ndarray], frame_dir: Path) -> None:
    frame_dir.mkdir(parents=True, exist_ok=True)
    for old in frame_dir.glob("f_*.png"):
        old.unlink()
    for i, a in enumerate(frames):
        Image.fromarray(a).save(frame_dir / f"f_{i:04d}.png", compress_level=1)


class KeyartPrep(OpExecutor):
    kind = "op.keyart_prep"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        ref = _images_in(inputs)[0]
        res = str(params.get("resolution", "720p")).lower()
        if res not in RESOLUTIONS:
            raise StepError("op_bad_param", f"resolution must be one of {list(RESOLUTIONS)}, got {res!r}")
        seconds = min(max(float(params.get("seconds", 5)), 1.0), MAX_FRAMES / FPS)
        img = await _load(ctx, ref)
        w, h = grid_size(img.width, img.height, RESOLUTIONS[res])
        start = await asyncio.to_thread(fit_cover, img, (w, h))
        length = frame_count(seconds)
        info = {"width": w, "height": h, "length": length, "fps": FPS, "seconds": round(length / FPS, 3),
                "resolution": res, "source": [img.width, img.height]}
        out = {"image": [await _put_png(ctx, start, "keyart_start.png")],
               "json": [await _put_json(ctx, info, "keyart_video.json")]}
        return StepResult(outputs=out, meta=info)


class KeyartVideo(OpExecutor):
    kind = "op.keyart_video"
    version = "1"

    def __init__(self, settings: Any = None):
        self.settings = settings

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        refs = _images_in(inputs, "frames")
        if len(refs) < 4:
            raise StepError("op_bad_input", f"need at least 4 frames, got {len(refs)}")
        mode = str(params.get("loop", "crossfade")).lower()
        if mode not in LOOP_MODES:
            raise StepError("op_bad_param", f"loop must be one of {list(LOOP_MODES)}, got {mode!r}")
        formats = [f.strip() for f in str(params.get("formats", "mp4,webm,gif")).split(",") if f.strip()]
        if not formats or any(f not in ("mp4", "webm", "gif") for f in formats):
            raise StepError("op_bad_param", f"formats must be among mp4, webm, gif, got {params.get('formats')!r}")
        fps = int(params.get("fps", FPS))
        xfade = max(0, round(float(params.get("crossfade", 0.75)) * fps))
        gif_width = int(params.get("gif_width", 480)) // 2 * 2
        gif_fps = int(params.get("gif_fps", 12))
        stem = str(params.get("name") or "keyart_teaser")
        if mode != "none":
            # The app's players key off the name to loop playback.
            stem += "_loop"

        raw = []
        for r in refs:
            raw.append(np.asarray((await _load(ctx, r)).convert("RGB")))
        if any(a.shape != raw[0].shape for a in raw):
            raise StepError("op_bad_input", "frames differ in size")
        seq = await asyncio.to_thread(make_loop, raw, mode, xfade)
        exe = str(getattr(self.settings, "ffmpeg_exe", None) or _default_ffmpeg_exe())
        out_dir = Path(ctx.job_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(_write_frames, seq, out_dir / "frames")
        ctx.progress(0.3, 1.0, "encode")
        files = await asyncio.to_thread(encode_all, exe, out_dir / "frames", out_dir, stem, fps, gif_width,
                                        gif_fps, formats)
        mimes = {".mp4": "video/mp4", ".webm": "video/webm", ".gif": "image/gif"}
        video, gif = [], []
        for f in files:
            ref = await ctx.blobs.put_file(f, name=f.name, mime=mimes[f.suffix])
            (gif if f.suffix == ".gif" else video).append(ref)
        h, w = seq[0].shape[:2]
        meta = {"loop": mode, "source_frames": len(raw), "frames": len(seq), "fps": fps,
                "seconds": round(len(seq) / fps, 3), "width": w, "height": h,
                "crossfade_frames": min(xfade, len(raw) // 3) if mode == "crossfade" else 0,
                "seam_ratio": seam_ratio(seq) if mode != "none" else None, "motion": motion_level(raw),
                "files": [f.name for f in files]}
        if meta["motion"] < 1.0:
            ctx.log("generated video barely moves; try a stronger motion prompt or another seed", "warning")
        out: Outputs = {"video": video, "gif": gif,
                        "poster": [await _put_png(ctx, Image.fromarray(seq[0]), f"{stem}_poster.png")],
                        "json": [await _put_json(ctx, meta, "teaser.json")]}
        return StepResult(outputs={k: v for k, v in out.items() if v}, meta=meta)


KEYART_OPS: tuple[type[OpExecutor], ...] = (KeyartPrep,)
