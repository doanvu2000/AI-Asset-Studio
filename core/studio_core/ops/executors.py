from __future__ import annotations

import asyncio
import io
import json
from typing import Any, Callable

from PIL import Image

from ..contracts import BlobRef, Lane, Outputs, StepContext, StepError, StepResult
from . import glb
from . import image as im


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=False, compress_level=6)
    return buf.getvalue()


def _images_in(inputs: Outputs, name: str = "images") -> list[BlobRef]:
    refs = inputs.get(name) or []
    if not refs:
        raise StepError("op_no_input", f"input '{name}' is empty")
    return refs


def _out_name(ref: BlobRef, i: int, suffix: str) -> str:
    stem = (ref.name or f"img{i:02d}").rsplit(".", 1)[0]
    return f"{stem}_{suffix}.png"


async def _load(ctx: StepContext, ref: BlobRef) -> Image.Image:
    try:
        return await asyncio.to_thread(im.open_image, ctx.blobs.path(ref.hash))
    except (ValueError, OSError) as e:
        raise StepError("op_bad_image", str(e), detail={"hash": ref.hash}) from e


async def _put_png(ctx: StepContext, img: Image.Image, name: str) -> BlobRef:
    data = await asyncio.to_thread(_png, img)
    return await ctx.blobs.put_bytes(data, name=name, mime="image/png")


class OpExecutor:
    lane = Lane.CPU
    version = "1"
    kind = ""

    def cacheable(self, params: dict[str, Any]) -> bool:
        return True

    def cache_material(self, params: dict[str, Any]) -> str:
        return ""

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        raise NotImplementedError


class PerImageOp(OpExecutor):
    """Applies one pure image function to every input image independently."""

    suffix = ""

    def transform(self, params: dict[str, Any]) -> Callable[[Image.Image], Image.Image]:
        raise NotImplementedError

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        refs = _images_in(inputs)
        fn = self.transform(params)
        out: list[BlobRef] = []
        for i, ref in enumerate(refs):
            if ctx.cancelled.is_set():
                raise asyncio.CancelledError
            src = await _load(ctx, ref)
            try:
                res = await asyncio.to_thread(fn, src)
            except ValueError as e:
                raise StepError("op_bad_params", str(e)) from e
            out.append(await _put_png(ctx, res, _out_name(ref, i, self.suffix)))
            ctx.progress(i + 1, len(refs))
        return StepResult(outputs={"images": out})


class TrimPadResize(PerImageOp):
    kind = "op.trim_pad_resize"
    suffix = "fit"

    def transform(self, params: dict[str, Any]) -> Callable[[Image.Image], Image.Image]:
        size = int(params.get("size", 512))
        pad = float(params.get("pad", 0.06))
        square = bool(params.get("square", True))
        resample = str(params.get("resample", "lanczos"))
        return lambda img: im.trim_pad_resize(img, size, pad=pad, square=square, resample=resample)  # type: ignore[arg-type]


class Downscale(PerImageOp):
    kind = "op.downscale"
    suffix = "down"

    def transform(self, params: dict[str, Any]) -> Callable[[Image.Image], Image.Image]:
        factor = float(params.get("factor", 2))
        resample = str(params.get("resample", "box"))
        return lambda img: im.downscale(img, factor, resample=resample)  # type: ignore[arg-type]


class Pixelate(PerImageOp):
    kind = "op.pixelate"
    suffix = "px"

    def transform(self, params: dict[str, Any]) -> Callable[[Image.Image], Image.Image]:
        size = int(params.get("size", 64))
        colors = params.get("palette_colors")
        outline = params.get("outline")
        return lambda img: im.pixelate(img, size, palette_colors=int(colors) if colors else None, outline=outline)


async def _load_all(ctx: StepContext, refs: list[BlobRef]) -> list[Image.Image]:
    imgs = []
    for i, ref in enumerate(refs):
        if ctx.cancelled.is_set():
            raise asyncio.CancelledError
        imgs.append(await _load(ctx, ref))
        ctx.progress(i + 1, len(refs) * 2, label="load")
    return imgs


async def _save_all(ctx: StepContext, imgs: list[Image.Image], names: list[str]) -> list[BlobRef]:
    out = []
    for i, (img, name) in enumerate(zip(imgs, names)):
        out.append(await _put_png(ctx, img, name))
        ctx.progress(len(imgs) + i + 1, len(imgs) * 2, label="save")
    return out


class Normalize(OpExecutor):
    kind = "op.normalize"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        refs = _images_in(inputs)
        imgs = await _load_all(ctx, refs)
        size = int(params.get("size", 512))
        frames, pivot = await asyncio.to_thread(
            im.normalize_frames, imgs, size, float(params.get("pad", 0.06)), str(params.get("anchor", "bottom")),
            str(params.get("resample", "lanczos")))
        names = [_out_name(r, i, "norm") for i, r in enumerate(refs)]
        out = await _save_all(ctx, frames, names)
        return StepResult(outputs={"images": out}, meta={"pivot": list(pivot), "size": size})


class MirrorDirs(OpExecutor):
    kind = "op.mirror_dirs"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        refs = _images_in(inputs)
        count = int(params.get("count", 8))
        if len(refs) == count:
            return StepResult(outputs={"images": list(refs)}, meta={"mirrored": False})
        imgs = await _load_all(ctx, refs)
        try:
            full = await asyncio.to_thread(im.mirror_dirs, imgs, count)
        except ValueError as e:
            raise StepError("op_bad_input", str(e)) from e
        step = 360 // count
        names = [f"dir_{a * step:03d}.png" for a in range(count)]
        # Unmirrored angles keep their original blobs so hashes (and downstream cache keys) stay stable.
        mirrored = await _save_all(ctx, full[len(refs):], names[len(refs):])
        angles = [a * step for a in range(count)]
        return StepResult(outputs={"images": list(refs) + mirrored}, meta={"mirrored": True, "angles": angles})


async def _pivot_from_blob(ctx: StepContext, ref: BlobRef, frame_size: tuple[int, int]) -> tuple[int, int]:
    """JSON {pivot: [x, y], width, height} in source-frame pixels, rescaled to the frames actually packed."""
    try:
        info = json.loads(await asyncio.to_thread(ctx.blobs.path(ref.hash).read_bytes))
        x, y = (float(v) for v in info["pivot"])
        sx = frame_size[0] / float(info.get("width") or frame_size[0])
        sy = frame_size[1] / float(info.get("height") or frame_size[1])
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise StepError("op_bad_input", f"bad pivot json: {e}") from e
    return round(x * sx), round(y * sy)


class Sheet(OpExecutor):
    kind = "op.sheet"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        refs = _images_in(inputs)
        imgs = await _load_all(ctx, refs)
        cols = int(params.get("cols") or len(imgs))
        cell = params.get("cell")
        if isinstance(cell, list):
            cell = (int(cell[0]), int(cell[1]))
        elif cell is not None:
            cell = int(cell)
        pivot = params.get("pivot")
        if inputs.get("pivot"):
            pivot = await _pivot_from_blob(ctx, inputs["pivot"][0], imgs[0].size)
        elif pivot == "bottom":
            # Recipes cannot read another step's meta; this reproduces op.normalize(anchor=bottom)'s pivot.
            w, h = imgs[0].size
            pivot = (w // 2, h - round(h * float(params.get("pivot_pad", 0.06))))
        try:
            sheet, meta = await asyncio.to_thread(
                im.pack_sheet, imgs, cols, cell, int(params.get("padding", 0)),
                tuple(pivot) if pivot else None, params.get("order"))
        except ValueError as e:
            raise StepError("op_bad_params", str(e)) from e
        meta["frames"] = [r.hash for r in refs]
        sheet_ref = await _put_png(ctx, sheet, "sheet.png")
        meta_ref = await ctx.blobs.put_bytes(json.dumps(meta, indent=2).encode(), name="sheet.json",
                                             mime="application/json")
        ctx.progress(1.0)
        return StepResult(outputs={"sheet": [sheet_ref], "meta": [meta_ref]}, meta=meta)


class GlbValidate(OpExecutor):
    kind = "op.glb_validate"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        refs = _images_in(inputs, "mesh")
        if len(refs) != 1:
            raise StepError("op_bad_input", f"expected exactly 1 mesh, got {len(refs)}")
        try:
            report = await asyncio.to_thread(glb.read_report, ctx.blobs.path(refs[0].hash))
        except glb.GlbError as e:
            raise StepError("glb_invalid", str(e), detail={"hash": refs[0].hash}) from e
        except OSError as e:
            raise StepError("glb_invalid", f"cannot read mesh: {e}", detail={"hash": refs[0].hash}) from e
        bad = glb.problems(report, bool(params.get("require_textures", True)),
                           int(params["max_triangles"]) if params.get("max_triangles") else None,
                           int(params["max_bytes"]) if params.get("max_bytes") else None)
        if bad:
            raise StepError("glb_invalid", "; ".join(bad), detail={"report": report, "problems": bad})
        ctx.progress(1.0)
        return StepResult(outputs={"meshes": list(refs)}, meta=report)


ALL_OPS: tuple[type[OpExecutor], ...] = (TrimPadResize, Normalize, MirrorDirs, Sheet, Downscale, Pixelate,
                                         GlbValidate)
