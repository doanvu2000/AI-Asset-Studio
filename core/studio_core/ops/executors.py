from __future__ import annotations

import asyncio
import functools
import io
import json
from pathlib import Path
from typing import Any, Callable

from blake3 import blake3
from PIL import Image

from ..contracts import BlobRef, Lane, Outputs, StepContext, StepError, StepResult
from . import glb
from . import image as im
from . import viewcheck as vc
from .presets import ExportPreset, PresetStore, annotate_sheet_meta, apply_preset, resolve_preset
from .sheet_layout import clip_layout


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=False, compress_level=6)
    return buf.getvalue()


def _skip_empty(params: dict[str, Any], inputs: Outputs, name: str = "images") -> bool:
    """`optional: true` lets a step pass an empty input through (e.g. normal frames when normals are off)."""
    return bool(params.get("optional")) and not inputs.get(name)


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


@functools.cache
def ops_source_hash() -> str:
    # Whole package, since ops share helpers (image.py, presets.py...). Frozen builds only have these
    # files if the spec bundles them; without them the class `version` is the only invalidation.
    files = sorted(Path(__file__).parent.glob("*.py"))
    if not files:
        return ""
    h = blake3()
    for f in files:
        h.update(f.name.encode())
        h.update(f.read_bytes())
    return h.hexdigest()


class OpExecutor:
    lane = Lane.CPU
    version = "1"
    kind = ""

    def cacheable(self, params: dict[str, Any]) -> bool:
        return True

    def cache_material(self, params: dict[str, Any]) -> str:
        return ops_source_hash()

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        raise NotImplementedError


class PerImageOp(OpExecutor):
    """Applies one pure image function to every input image independently."""

    suffix = ""

    def transform(self, params: dict[str, Any]) -> Callable[[Image.Image], Image.Image]:
        raise NotImplementedError

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        if _skip_empty(params, inputs):
            return StepResult(outputs={"images": []})
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


def _flip_normal_x(img: Image.Image) -> Image.Image:
    """A horizontally mirrored normal map must also negate n.x (R -> 255 - R)."""
    r, g, b, a = img.convert("RGBA").split()
    return Image.merge("RGBA", (r.point(lambda v: 255 - v), g, b, a))


class MirrorDirs(OpExecutor):
    kind = "op.mirror_dirs"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        if _skip_empty(params, inputs):
            return StepResult(outputs={"images": []}, meta={"mirrored": False})
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
        normal = bool(params.get("normal"))
        if normal:
            full[len(refs):] = [_flip_normal_x(f) for f in full[len(refs):]]
        names = [f"dir_{a * step:03d}{'_n' if normal else ''}.png" for a in range(count)]
        # Unmirrored angles keep their original blobs so hashes (and downstream cache keys) stay stable.
        mirrored = await _save_all(ctx, full[len(refs):], names[len(refs):])
        angles = [a * step for a in range(count)]
        return StepResult(outputs={"images": list(refs) + mirrored}, meta={"mirrored": True, "angles": angles})


async def _read_json(ctx: StepContext, ref: BlobRef) -> Any:
    try:
        return json.loads(await asyncio.to_thread(ctx.blobs.path(ref.hash).read_bytes))
    except (OSError, ValueError) as e:
        raise StepError("op_bad_input", f"bad pivot json: {e}") from e


async def _pivot_from_blob(ctx: StepContext, ref: BlobRef, frame_size: tuple[int, int]) -> tuple[int, int]:
    """JSON {pivot: [x, y], width, height} in source-frame pixels, rescaled to the frames actually packed."""
    try:
        info = await _read_json(ctx, ref)
        x, y = (float(v) for v in info["pivot"])
        sx = frame_size[0] / float(info.get("width") or frame_size[0])
        sy = frame_size[1] / float(info.get("height") or frame_size[1])
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise StepError("op_bad_input", f"bad pivot json: {e}") from e
    return round(x * sx), round(y * sy)


class Sheet(OpExecutor):
    kind = "op.sheet"

    def __init__(self, presets: PresetStore | None = None):
        self.presets = presets

    def _preset(self, params: dict[str, Any]) -> ExportPreset | None:
        try:
            return resolve_preset(params.get("preset"), self.presets)
        except ValueError as e:
            raise StepError("op_bad_params", str(e)) from e

    def cache_material(self, params: dict[str, Any]) -> str:
        # The preset id alone would keep serving stale sheets after the user edits that preset.
        base = super().cache_material(params)
        try:
            p = resolve_preset(params.get("preset"), self.presets)
        except ValueError:
            return base
        return f"{base}|{p.model_dump_json()}" if p else base

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        if _skip_empty(params, inputs):
            return StepResult(outputs={"sheet": [], "meta": []})
        refs = _images_in(inputs)
        preset = self._preset(params)
        imgs = await _load_all(ctx, refs)
        cols = int(params.get("cols") or len(imgs))
        order = params.get("order")
        named: list[dict[str, Any]] | None = None
        if preset is not None:
            angles = [int(a) for a in order] if isinstance(order, list) else \
                [i * 360 // len(imgs) for i in range(len(imgs))]
            try:
                slots, named = apply_preset(preset, angles, str(params.get("asset") or "sprite"))
            except ValueError as e:
                raise StepError("op_bad_params", str(e)) from e
            imgs, refs = [imgs[i] for i in slots], [refs[i] for i in slots]
            order = [angles[i] for i in slots]
            cols = preset.cols_for(len(imgs))
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
        padding = preset.padding if preset is not None else int(params.get("padding", 0))
        clip_meta = None
        if preset is None and inputs.get("pivot"):
            clip_meta = await self._clip_slots(ctx, inputs["pivot"][0], len(refs))
        cell_refs: list[BlobRef | None] = list(refs)
        if clip_meta is not None:
            slots, cols, clips, row_angles = clip_meta
            blank = Image.new("RGBA", imgs[0].size, (0, 0, 0, 0))
            imgs = [blank if i is None else imgs[i] for i in slots]
            cell_refs = [None if i is None else refs[i] for i in slots]
        try:
            sheet, meta = await asyncio.to_thread(
                im.pack_sheet, imgs, cols, cell, padding, tuple(pivot) if pivot else None, order)
        except ValueError as e:
            raise StepError("op_bad_params", str(e)) from e
        meta["frames"] = [r.hash if r is not None else None for r in cell_refs]
        if clip_meta is not None:
            meta["clips"], meta["row_angles"] = clips, row_angles
        explicit_order = preset is not None or isinstance(params.get("order"), list)
        if clip_meta is None and not explicit_order and inputs.get("pivot"):
            # The pivot json lists angles in render order, which only matches the rows when nothing reordered them.
            if angles_in := await self._row_angles(ctx, inputs["pivot"][0], meta["rows"]):
                meta["row_angles"] = angles_in
        if explicit_order and len(meta["order"]) == meta["count"] \
                and all(isinstance(a, (int, float)) for a in meta["order"]):
            meta["angles"] = list(meta["order"])
            if meta["cols"] == 1 and "row_angles" not in meta:
                meta["row_angles"] = list(meta["order"])
        if params.get("normal"):
            meta["normal"] = True
        sheet_name = "sheet.png"
        if preset is not None and named is not None:
            annotate_sheet_meta(meta, named, preset)
            sheet_name = f"{params.get('asset') or 'sprite'}_sheet.png"
        meta_name = "sheet.json"
        if meta.get("normal"):
            # Viewers and the gallery tell normal maps apart from the color sheet by file name.
            sheet_name = sheet_name.replace(".png", "_normal.png")
            meta_name = "sheet_normal.json"
        sheet_ref = await _put_png(ctx, sheet, sheet_name)
        meta_ref = await ctx.blobs.put_bytes(json.dumps(meta, indent=2).encode(), name=meta_name,
                                             mime="application/json")
        ctx.progress(1.0)
        return StepResult(outputs={"sheet": [sheet_ref], "meta": [meta_ref]}, meta=meta)

    async def _clip_slots(self, ctx: StepContext, ref: BlobRef,
                          count: int) -> tuple[list[int | None], int, list[dict[str, Any]], list[float]] | None:
        info = await _read_json(ctx, ref)
        clips, angles = (info.get("clips"), info.get("angles")) if isinstance(info, dict) else (None, None)
        if not isinstance(clips, list) or not clips or not isinstance(angles, list):
            return None
        try:
            return clip_layout(clips, [float(a) for a in angles], count)
        except (ValueError, KeyError, TypeError) as e:
            raise StepError("op_bad_input", f"anim clips do not match the frames: {e}") from e

    @staticmethod
    async def _row_angles(ctx: StepContext, ref: BlobRef, rows: int) -> list[float] | None:
        """Clip-less anim.json (one row per direction): its `angles` label the rows when the counts agree."""
        info = await _read_json(ctx, ref)
        angles = info.get("angles") if isinstance(info, dict) else None
        if not isinstance(angles, list) or len(angles) != rows:
            return None
        try:
            return [float(a) for a in angles]
        except (TypeError, ValueError):
            return None


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


class ViewCheck(OpExecutor):
    """Checks generated views against the front; passing views go to `images`, scores to `report`."""
    kind = "op.view_check"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        front_refs = _images_in(inputs, "front")
        refs = _images_in(inputs)
        cfg = vc.ViewCheckConfig.from_params(params)
        front = await _load(ctx, front_refs[0])
        views = await _load_all(ctx, refs)
        try:
            results = await asyncio.to_thread(vc.check_views, front, views, cfg)
        except ValueError as e:
            raise StepError("op_bad_input", str(e)) from e
        names = list(params.get("names") or [])
        report = [{"name": names[i] if i < len(names) else str(i), "hash": r.hash, **res}
                  for i, (r, res) in enumerate(zip(refs, results))]
        failed = [x["name"] for x in report if not x["ok"]]
        if failed and params.get("fail", False):
            raise StepError("views_rejected", f"bad views: {', '.join(failed)}", detail={"views": report})
        data = json.dumps({"views": report, "failed": failed, "config": vc.config_dict(cfg)}, indent=1).encode()
        report_ref = await ctx.blobs.put_bytes(data, name="view_check.json", mime="application/json")
        passed = [r for r, res in zip(refs, results) if res["ok"]]
        return StepResult(outputs={"images": passed, "report": [report_ref]}, meta={"failed": failed})


ALL_OPS: tuple[type[OpExecutor], ...] = (TrimPadResize, Normalize, MirrorDirs, Sheet, Downscale, Pixelate,
                                         GlbValidate, ViewCheck)
