"""Ops for animating an existing Spine skeleton from a reference video: `op.video_frames`, `op.spine_rig_summary`,
`op.spine_add_anim`, `op.spine_render_strip`."""
from __future__ import annotations

import asyncio
import json
import math
import re
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ..config import _default_ffmpeg_exe
from ..contracts import BlobRef, Outputs, StepContext, StepError, StepResult
from . import spine_anim as sa
from . import spine_runtime as rt
from .executors import OpExecutor, _put_png
from .rig import _put_json, _read_json

STRIP_BG = (236, 232, 244, 255)
MAX_FRAMES = 48


def _one(inputs: Outputs, name: str) -> BlobRef:
    refs = inputs.get(name) or []
    if not refs:
        raise StepError("op_no_input", f"input '{name}' is empty")
    return refs[0]


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _label(img: Image.Image, text: str) -> None:
    d = ImageDraw.Draw(img)
    size = max(14, img.height // 28)
    f = _font(size)
    box = d.textbbox((0, 0), text, font=f)
    d.rectangle((0, 0, box[2] + size, box[3] + size // 2), fill=(0, 0, 0, 170))
    d.text((size // 2, size // 4), text, fill=(255, 255, 255, 255), font=f)


async def read_text(ctx: StepContext, ref: BlobRef) -> str:
    return await asyncio.to_thread(ctx.blobs.path(ref.hash).read_text, encoding="utf-8-sig")


async def read_skeleton(ctx: StepContext, inputs: Outputs, name: str = "skeleton") -> tuple[str, dict[str, Any]]:
    text = await read_text(ctx, _one(inputs, name))
    try:
        doc = json.loads(text)
    except ValueError as e:
        raise StepError("op_bad_input", f"'{name}' is not skeleton JSON: {e}") from e
    if not isinstance(doc, dict) or "bones" not in doc:
        raise StepError("op_bad_input", f"'{name}' is not a Spine skeleton JSON (no bones)")
    return text, doc


def region_images(ctx: StepContext, inputs: Outputs, name: str = "atlas") -> rt.RegionImages:
    refs = inputs.get(name) or []
    if not refs:
        raise StepError("op_no_input", f"input '{name}' is empty")
    atlas_path = ctx.blobs.path(refs[0].hash)
    images = rt.RegionImages.from_files(atlas_path, [ctx.blobs.path(r.hash) for r in refs[1:]])
    need = len(images.atlas.pages)
    if len(refs) - 1 < need:
        raise StepError("op_bad_input", f"atlas has {need} page images but {len(refs) - 1} were given; pass the "
                        "atlas file path so its pages next to it come along")
    return images


def _times(raw: Any, duration: float, n: int) -> list[float]:
    if isinstance(raw, (list, tuple)):
        return [float(t) for t in raw]
    if isinstance(raw, str) and raw.strip() and raw.strip() != "auto":
        return [float(t) for t in re.split(r"[,\s]+", raw.strip()) if t]
    n = max(2, n)
    return [round(duration * i / (n - 1), 3) for i in range(n)]


def animation_duration(doc: dict[str, Any], anim: str) -> float:
    a = (doc.get("animations") or {}).get(anim)
    if a is None:
        raise StepError("op_bad_input", f"skeleton has no animation '{anim}'")
    return max((float(k.get("time", 0)) for tl in a.get("bones", {}).values() for tr in tl.values() for k in tr),
               default=0.0)


def render_strip(doc: dict[str, Any], images: rt.RegionImages, anim: str | None, times: list[float],
                 parts: list[str], height: int = 560) -> tuple[Image.Image, dict[str, Any]]:
    """One labelled cell per time, all framed by the same box (union over the clip) so nothing is cropped."""
    skins, missing = rt.resolve_skins(doc, parts)
    atts = rt.skin_attachments(doc, skins)
    poses = [rt.Pose(doc, anim, t) for t in times]
    pts = np.concatenate([rt.attachment_points(p, atts) for p in [rt.Pose(doc), *poses]])
    if not len(pts):
        raise StepError("op_bad_input", "nothing visible with these skins")
    x0, y0 = pts.min(0)
    x1, y1 = pts.max(0)
    pad = 0.04 * max(x1 - x0, y1 - y0)
    box = (x0 - pad, y0 - pad, x1 + pad, y1 + pad)
    scale = height / (box[3] - box[1])
    cells = []
    for t, pose in zip(times, poses):
        cell = Image.new("RGBA", (round((box[2] - box[0]) * scale), height), STRIP_BG)
        cell.alpha_composite(rt.render(pose, atts, images, box, scale))
        _label(cell, f"t={t:g}s")
        cells.append(cell)
    strip = Image.new("RGBA", (sum(c.width for c in cells) + 4 * (len(cells) - 1), height), (40, 36, 52, 255))
    x = 0
    for c in cells:
        strip.paste(c, (x, 0))
        x += c.width + 4
    return strip, {"times": times, "skins": skins, "missing_skins": missing, "box": [round(v, 2) for v in box],
                   "anim": anim}


class SpineRigSummary(OpExecutor):
    kind = "op.spine_rig_summary"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        _, doc = await read_skeleton(ctx, inputs)
        text = await asyncio.to_thread(sa.rig_summary, doc, int(params.get("limit", 3000)))
        ref = await ctx.blobs.put_bytes(text.encode("utf-8"), name="rig_summary.txt", mime="text/plain")
        return StepResult(outputs={"summary": [ref]}, meta={"chars": len(text), "bones": len(doc["bones"])})


class SpineAddAnim(OpExecutor):
    kind = "op.spine_add_anim"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        text, _ = await read_skeleton(ctx, inputs)
        spec = await _read_json(ctx, inputs, "spec")
        if isinstance(spec, dict) and isinstance(spec.get("spec"), dict):
            spec = spec["spec"]  # a review result {score, critique, spec}
        name = str(params.get("name") or "anim").strip()
        res = await asyncio.to_thread(add_anim, text, spec, name)
        stem = Path(_one(inputs, "skeleton").name or "skeleton.json").stem
        out: Outputs = {
            "skeleton": [await ctx.blobs.put_bytes(res["text"].encode("utf-8"), name=f"{stem}.json",
                                                   mime="application/json")],
            "anim": [await _put_json(ctx, res["anim"], f"{name}.anim.json")],
        }
        return StepResult(outputs=out, meta={"name": name, "bones": len(res["anim"]["bones"])})


def add_anim(text: str, spec: Any, name: str) -> dict[str, Any]:
    if not isinstance(spec, dict) or "duration" not in spec:
        raise StepError("spec_invalid", "motion spec must be an object with at least 'duration'")
    try:
        anim = sa.build_animation(spec)
    except (sa.SpecError, KeyError, TypeError, ValueError) as e:
        raise StepError("spec_invalid", f"motion spec: {e}") from e
    new_text = sa.add_animation(text, name, anim)
    problems = sa.check_animation(json.loads(new_text), name)
    if problems:
        raise StepError("spec_invalid", "; ".join(problems[:6]), detail={"problems": problems})
    return {"text": new_text, "anim": anim}


class SpineRenderStrip(OpExecutor):
    kind = "op.spine_render_strip"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        _, doc = await read_skeleton(ctx, inputs)
        images = region_images(ctx, inputs)
        anim = params.get("anim") or None
        dur = animation_duration(doc, anim) if anim else 0.0
        times = _times(params.get("times", "auto"), dur, int(params.get("count", 8))) if anim else [0.0]
        parts = [p for p in re.split(r"[,\s]+", str(params.get("skin") or "")) if p]
        strip, meta = await asyncio.to_thread(render_strip, doc, images, anim, times, parts,
                                              int(params.get("height", 560)))
        if meta["missing_skins"]:
            ctx.log(f"skins not found: {', '.join(meta['missing_skins'])}", "warning")
        return StepResult(outputs={"strip": [await _put_png(ctx, strip, f"{anim or 'setup'}_strip.png")]}, meta=meta)


def parse_window(raw: Any) -> tuple[float | None, float | None]:
    s = str(raw or "auto").strip().lower()
    if s in ("", "auto", "all"):
        return None, None
    m = re.fullmatch(r"([\d.]+)\s*[-–:]\s*([\d.]+)", s)
    if not m or float(m.group(2)) <= float(m.group(1)):
        raise StepError("op_bad_param", f"window must look like '1.6-5.6' or 'auto', got {raw!r}")
    return float(m.group(1)), float(m.group(2))


def figure_box(frames: list[Image.Image], pad: float = 0.06) -> tuple[int, int, int, int] | None:
    """Union bbox of pixels that differ from the frame border colour (the figure on a plain backdrop)."""
    box = None
    for f in frames:
        a = np.asarray(f.convert("RGB"), np.int16)
        border = np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]])
        bg = np.median(border, axis=0)
        mask = np.abs(a - bg).max(axis=2) > 40
        ys, xs = np.nonzero(mask)
        if len(xs) < 50:
            continue
        b = (xs.min(), ys.min(), xs.max(), ys.max())
        box = b if box is None else (min(box[0], b[0]), min(box[1], b[1]), max(box[2], b[2]), max(box[3], b[3]))
    if box is None:
        return None
    w, h = frames[0].size
    px, py = (box[2] - box[0]) * pad, (box[3] - box[1]) * pad
    return (max(0, int(box[0] - px)), max(0, int(box[1] - py)), min(w, int(box[2] + px) + 1),
            min(h, int(box[3] + py) + 1))


def contact_sheets(frames: list[Image.Image], times: list[float], cols: int, rows: int, cell_h: int
                   ) -> list[Image.Image]:
    per = cols * rows
    sheets = []
    for start in range(0, len(frames), per):
        chunk = list(zip(frames[start:start + per], times[start:start + per]))
        cell_w = round(frames[0].width * cell_h / frames[0].height)
        n_rows = math.ceil(len(chunk) / cols)
        sheet = Image.new("RGB", (cols * cell_w + (cols - 1) * 4, n_rows * cell_h + (n_rows - 1) * 4), (40, 36, 52))
        for i, (f, t) in enumerate(chunk):
            cell = f.convert("RGBA").resize((cell_w, cell_h), Image.Resampling.LANCZOS)
            _label(cell, f"#{start + i} {t:.2f}s")
            sheet.paste(cell.convert("RGB"), ((i % cols) * (cell_w + 4), (i // cols) * (cell_h + 4)))
        sheets.append(sheet)
    return sheets


class VideoFrames(OpExecutor):
    kind = "op.video_frames"
    version = "1"

    def __init__(self, settings: Any = None):
        self.settings = settings

    def _ffmpeg(self) -> str:
        exe = getattr(self.settings, "ffmpeg_exe", None)
        return str(exe) if exe else _default_ffmpeg_exe()

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        src = ctx.blobs.path(_one(inputs, "video").hash)
        fps = min(max(float(params.get("fps", 5)), 0.5), 12.0)
        t0, t1 = parse_window(params.get("window"))
        cols, _, rows = str(params.get("tile", "8x2")).lower().partition("x")
        cols_n, rows_n = max(1, int(cols or 8)), max(1, int(rows or 2))
        out_dir = Path(ctx.job_dir) / "frames"
        out_dir.mkdir(parents=True, exist_ok=True)
        args = [self._ffmpeg(), "-v", "error", "-y"]
        if t0 is not None:
            args += ["-ss", f"{t0}", "-to", f"{t1}"]
        args += ["-i", str(src), "-vf", f"fps={fps}", "-frames:v", str(MAX_FRAMES), str(out_dir / "f_%03d.png")]
        try:
            proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.PIPE)
        except OSError as e:
            raise StepError("ffmpeg_missing", f"ffmpeg not runnable ({args[0]}): {e}. Set its path in Settings.") from e
        _, err = await proc.communicate()
        files = sorted(out_dir.glob("f_*.png"))
        if proc.returncode != 0 or not files:
            raise StepError("ffmpeg_failed", (err or b"").decode("utf-8", "replace")[-800:] or "no frames decoded")
        start = t0 or 0.0
        times = [round(start + i / fps, 3) for i in range(len(files))]
        frames = [Image.open(f).convert("RGB") for f in files]
        box = figure_box(frames) if params.get("crop", "auto") == "auto" else None
        if box:
            frames = [f.crop(box) for f in frames]
        sheets = await asyncio.to_thread(contact_sheets, frames, times, cols_n, rows_n, int(params.get("cell", 360)))
        meta = {"fps": fps, "t0": start, "times": times, "count": len(frames), "crop": list(box) if box else None,
                "tiles": f"{cols_n}x{rows_n}"}
        out: Outputs = {
            "sheets": [await _put_png(ctx, s, f"ref_sheet_{i + 1}.png") for i, s in enumerate(sheets)],
            "frames": [await _put_png(ctx, f, f"ref_{t:06.2f}s.png") for f, t in zip(frames, times)],
            "meta": [await _put_json(ctx, meta, "frames_meta.json")],
        }
        return StepResult(outputs=out, meta=meta)


SPINE_REF_OPS: tuple[type[OpExecutor], ...] = (SpineRigSummary, SpineAddAnim, SpineRenderStrip)
