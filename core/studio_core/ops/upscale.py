"""`op.upscale_prep` / `op.upscale_post` around the `comfy.upscale_esrgan` model pass.

ESRGAN only sees RGB, so prep fills the colour under transparent pixels from their visible neighbours (black there
would bleed a dark fringe into every edge) and post resizes the source alpha on its own and puts it back. Pixel art
skips the model and takes a nearest-neighbour integer scale, which keeps its hard pixels. An atlas (Spine/libgdx)
upscales page by page and its coordinates are rewritten to match, so it still drops in beside the same skeleton."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from ..contracts import BlobRef, Outputs, StepContext, StepError, StepResult
from ..spine_atlas import parse_atlas
from .executors import OpExecutor, _put_png

MODEL_SCALE = 4
SCALES = (2, 4)
MODES = ("auto", "ai", "nearest")
PIXEL_MAX_COLORS = 64
PIXEL_SOFT_ALPHA = 0.02
# Pixel lengths in an atlas: page/region size, packed bounds, whitespace offsets, nine-patch split/pad.
ATLAS_PX_KEYS = {"size", "bounds", "xy", "offsets", "offset", "orig", "split", "pad"}


def looks_pixel_art(img: Image.Image) -> bool:
    """Hard alpha and few colours: anti-aliased or painted art has soft edges and thousands of shades."""
    a = np.asarray(img.convert("RGBA"))
    alpha = a[..., 3]
    seen = alpha > 0
    if not seen.any():
        return False
    soft = float(((alpha > 0) & (alpha < 255)).sum()) / float(seen.sum())
    if soft > PIXEL_SOFT_ALPHA:
        return False
    rgb = a[..., :3][seen].astype(np.uint32)
    codes = (rgb[:, 0] << 16) | (rgb[:, 1] << 8) | rgb[:, 2]
    return len(np.unique(codes)) <= PIXEL_MAX_COLORS


def _half(c: np.ndarray, w: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    h, wd = w.shape
    ph, pw = h % 2, wd % 2
    if ph or pw:
        c = np.pad(c, ((0, ph), (0, pw), (0, 0)))
        w = np.pad(w, ((0, ph), (0, pw)))
    c = (c[0::2, 0::2] + c[1::2, 0::2] + c[0::2, 1::2] + c[1::2, 1::2]) / 4
    w = (w[0::2, 0::2] + w[1::2, 0::2] + w[0::2, 1::2] + w[1::2, 1::2]) / 4
    return c, w


def _pull(c: np.ndarray, w: np.ndarray) -> np.ndarray:
    """Push-pull fill: premultiplied colour `c` with coverage `w` composited over its own coarser levels."""
    if min(w.shape) <= 1:
        total = float(w.sum())
        mean = c.reshape(-1, 3).sum(0) / total if total > 0 else np.zeros(3)
        return np.broadcast_to(mean, c.shape).astype(np.float32)
    cc, cw = _half(c, w)
    coarse = _pull(cc, cw)
    up = coarse.repeat(2, 0).repeat(2, 1)[:w.shape[0], :w.shape[1]]
    return c + up * (1 - w)[..., None]


def bleed_rgb(img: Image.Image) -> Image.Image:
    """RGB where visible pixels keep their colour and hidden ones take the nearby visible colour."""
    a = np.asarray(img.convert("RGBA")).astype(np.float32) / 255
    rgb, w = a[..., :3], a[..., 3]
    if w.min() >= 1:
        return img.convert("RGB")
    filled = _pull(rgb * w[..., None], w)
    out = np.where((w > 0)[..., None], rgb, filled)
    return Image.fromarray((np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8), "RGB")


def unpremultiply(img: Image.Image) -> Image.Image:
    a = np.asarray(img.convert("RGBA")).astype(np.float32)
    alpha = a[..., 3:]
    a[..., :3] = np.where(alpha > 0, a[..., :3] * 255 / np.maximum(alpha, 1), 0)
    return Image.fromarray(np.clip(a + 0.5, 0, 255).astype(np.uint8), "RGBA")


def premultiply(img: Image.Image) -> Image.Image:
    a = np.asarray(img.convert("RGBA")).astype(np.float32)
    a[..., :3] *= a[..., 3:] / 255
    return Image.fromarray(np.clip(a + 0.5, 0, 255).astype(np.uint8), "RGBA")


def recombine(src: Image.Image, upscaled_rgb: Image.Image, scale: int) -> Image.Image:
    """Model RGB at `scale` x the source, with the source alpha resized separately (Lanczos)."""
    size = (src.width * scale, src.height * scale)
    rgb = upscaled_rgb.convert("RGB")
    if rgb.size != size:
        rgb = rgb.resize(size, Image.Resampling.LANCZOS)
    src = src.convert("RGBA")
    out = rgb.convert("RGBA")
    if src.getextrema()[3][0] < 255:
        out.putalpha(src.getchannel("A").resize(size, Image.Resampling.LANCZOS))
    return out


def nearest(src: Image.Image, scale: int) -> Image.Image:
    return src.convert("RGBA").resize((src.width * scale, src.height * scale), Image.Resampling.NEAREST)


def scale_atlas(text: str, scale: int) -> str:
    def line(raw: str) -> str:
        key, sep, val = raw.partition(":")
        if not sep or key.strip() not in ATLAS_PX_KEYS:
            return raw
        try:
            nums = [float(x) for x in val.split(",")]
        except ValueError:
            return raw
        return f"{key}:{' ' if val.startswith(' ') else ''}" + ",".join(f"{round(n * scale):d}" for n in nums)
    return "\n".join(line(r) for r in text.splitlines()) + "\n"


def compare(src: Image.Image, out: Image.Image, side: int = 768) -> Image.Image:
    """Plain bicubic resize (left) beside the result (right), on a checkerboard so the alpha edge shows."""
    k = min(1.0, side / max(out.size))
    size = (max(1, round(out.width * k)), max(1, round(out.height * k)))
    left = src.convert("RGBA").resize(size, Image.Resampling.BICUBIC)
    right = out.resize(size, Image.Resampling.LANCZOS) if k < 1 else out
    gap = 8
    canvas = Image.new("RGBA", (size[0] * 2 + gap, size[1]), (40, 40, 40, 255))
    yy, xx = np.mgrid[:size[1], :size[0]]
    cell = ((yy // 16 + xx // 16) % 2).astype(np.uint8)
    board = Image.fromarray(np.stack([np.where(cell, 200, 150).astype(np.uint8)] * 3 + [np.full_like(cell, 255)], -1),
                            "RGBA")
    for x, im in ((0, left), (size[0] + gap, right)):
        tile = board.copy()
        tile.alpha_composite(im)
        canvas.paste(tile, (x, 0))
    return canvas


def _params(params: dict[str, Any]) -> tuple[int, str]:
    scale = int(params.get("scale", MODEL_SCALE))
    if scale not in SCALES:
        raise StepError("op_bad_param", f"scale must be one of {SCALES}, got {scale}")
    mode = str(params.get("mode", "auto")).lower()
    if mode not in MODES:
        raise StepError("op_bad_param", f"mode must be one of {MODES}, got {mode!r}")
    return scale, mode


def _sources(ctx: StepContext, inputs: Outputs) -> tuple[list[tuple[BlobRef, Image.Image]], str | None, list[bool]]:
    """(name-ref, image) per source in order; the atlas text when an atlas came in; per-page premultiplied flag."""
    out = [(r, Image.open(ctx.blobs.path(r.hash))) for r in inputs.get("images") or []]
    pma = [False] * len(out)
    atlas_refs = inputs.get("atlas") or []
    text = None
    if atlas_refs:
        text = ctx.blobs.path(atlas_refs[0].hash).read_text(encoding="utf-8-sig", errors="replace")
        pages = parse_atlas(text).pages
        if len(atlas_refs) - 1 < len(pages):
            raise StepError("op_bad_input", f"atlas has {len(pages)} page images but {len(atlas_refs) - 1} were "
                            "given; pass the atlas file path so its pages next to it come along")
        for page, ref in zip(pages, atlas_refs[1:]):
            out.append((ref.model_copy(update={"name": page.name}), Image.open(ctx.blobs.path(ref.hash))))
            pma.append(page.pma)
    if not out:
        raise StepError("op_no_input", "give an image or an atlas to upscale")
    return out, text, pma


def _use_model(img: Image.Image, mode: str) -> bool:
    return mode == "ai" or (mode == "auto" and not looks_pixel_art(img))


class UpscalePrep(OpExecutor):
    """Sources that go through the model, as RGB with the hidden colour filled in."""
    kind = "op.upscale_prep"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        _, mode = _params(params)
        sources, _, pma = _sources(ctx, inputs)
        out, plan = [], []
        for (ref, img), p in zip(sources, pma):
            ai = _use_model(img, mode)
            plan.append({"name": ref.name, "size": list(img.size), "ai": ai, "pma": p})
            if ai:
                rgba = unpremultiply(img) if p else img
                rgb = await asyncio.to_thread(bleed_rgb, rgba)
                out.append(await _put_png(ctx, rgb, f"{Path(ref.name or 'img').stem}_rgb.png"))
        nearest_n = sum(not p["ai"] for p in plan)
        note = (f"{nearest_n} ảnh pixel art phóng bằng nearest-neighbor (giữ cạnh pixel), không qua AI"
                if nearest_n else "")
        if note:
            ctx.log(note)
        return StepResult(outputs={"rgb": out}, meta={"plan": plan, "mode": mode, **({"note": note} if note else {})})


class UpscalePost(OpExecutor):
    """Puts the alpha back on the model output, scales pixel art by nearest neighbour, rewrites an atlas."""
    kind = "op.upscale_post"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        scale, mode = _params(params)
        sources, atlas_text, pma = _sources(ctx, inputs)
        upscaled = list(inputs.get("upscaled") or [])
        want = sum(_use_model(img, mode) for _, img in sources)
        if len(upscaled) != want:
            raise StepError("op_bad_input", f"expected {want} upscaled image(s) from the model, got {len(upscaled)}")
        n_images = len(inputs.get("images") or [])
        images, pages, previews, plan = [], [], [], []
        for i, ((ref, img), p) in enumerate(zip(sources, pma)):
            ai = _use_model(img, mode)
            if ai:
                model_out = Image.open(ctx.blobs.path(upscaled.pop(0).hash))
                rgba = unpremultiply(img) if p else img
                res = await asyncio.to_thread(recombine, rgba, model_out, scale)
                if p:
                    res = premultiply(res)
            else:
                res = nearest(img, scale)
            atlas_page = i >= n_images
            stem = Path(ref.name or f"img{i:02d}").stem
            name = ref.name if atlas_page else f"{stem}_x{scale}.png"
            out_ref = await _put_png(ctx, res, name)
            (pages if atlas_page else images).append(out_ref)
            previews.append(await _put_png(ctx, await asyncio.to_thread(compare, img, res), f"{stem}_compare.png"))
            plan.append({"name": name, "from": list(img.size), "to": list(res.size),
                         "method": "esrgan" if ai else "nearest"})
        outputs: dict[str, list[BlobRef]] = {"images": images, "previews": previews}
        if atlas_text is not None:
            atlas_ref = inputs["atlas"][0]
            stem = Path(atlas_ref.name or "atlas").stem
            atlas_out = await ctx.blobs.put_bytes(scale_atlas(atlas_text, scale).encode("utf-8"),
                                                  name=f"{stem}.atlas", mime="text/plain")
            outputs["atlas"] = [atlas_out, *pages]
        return StepResult(outputs=outputs, meta={"scale": scale, "mode": mode, "results": plan})


UPSCALE_OPS = (UpscalePrep, UpscalePost)
