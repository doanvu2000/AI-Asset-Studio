"""`op.normal2d`: sprite or sprite sheet -> 2D-light normal map (CPU, export/normal2d.py)."""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import numpy as np
from PIL import Image

from ..adapters import normal_ai
from ..contracts import BlobRef, Outputs, StepContext, StepError, StepResult
from .executors import OpExecutor, _images_in, _load, _put_png, _read_json
from .tile import _enabled

if TYPE_CHECKING:
    from ..export.normal2d import Normal2DOptions
    from ..export.sheet import Cell


def _grid(w: int, h: int, cols: int, rows: int) -> list[Cell] | None:
    if cols <= 1 and rows <= 1:
        return None
    from ..export.sheet import Cell

    fw, fh = w // max(cols, 1), h // max(rows, 1)
    return [Cell(f"f{r}_{c}", c * fw, r * fh, fw, fh) for r in range(rows) for c in range(cols)]


def stem_of(ref: BlobRef, i: int) -> str:
    return (ref.name or f"sprite_{i:02d}").rsplit(".", 1)[0]


def _options(params: dict[str, Any]) -> tuple[Normal2DOptions, int, int]:
    from ..export.normal2d import parse

    try:
        opts = parse(params)
        cols, rows = int(params.get("cols") or 1), int(params.get("rows") or 1)
    except (TypeError, ValueError) as e:
        raise StepError("op_bad_params", f"bad normal2d parameter: {e}") from e
    if not 0 < opts.strength <= 10 or not 0 <= opts.detail <= 4 or (opts.bevel or 0) > 64:
        raise StepError("op_bad_params", "strength must be in (0, 10], detail 0..4, bevel 0..64 (0 = auto)")
    if not 1 <= cols <= 64 or not 1 <= rows <= 64:
        raise StepError("op_bad_params", "cols and rows must be 1..64")
    return opts, cols, rows


METHODS = ("auto", "ai", "filter")


def _method(params: dict[str, Any]) -> str:
    m = str(params.get("method") or "auto").lower()
    if m not in METHODS:
        raise StepError("op_bad_params", f"method must be one of {', '.join(METHODS)}")
    return m


class Normal2D(OpExecutor):
    kind = "op.normal2d"
    version = "2"

    def __init__(self, settings: Any = None) -> None:
        self.settings = settings

    def _resolve(self, params: dict[str, Any]) -> tuple[str, normal_ai.NormalAi, str | None]:
        """(method actually used, model location, why AI is unavailable)."""
        ai = normal_ai.locate(self.settings)
        method = _method(params)
        why = ai.missing() if method != "filter" else None
        return ("filter" if method == "filter" or why else "ai"), ai, why

    def cache_material(self, params: dict[str, Any]) -> str:
        used, ai, _ = self._resolve(params)
        return f"{super().cache_material(params)}|method={used}" + (f"|{ai.material}" if used == "ai" else "")

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        # export.sheet imports ops at module load, so the export side is pulled in lazily.
        from ..export.normal2d import lit_preview, normal_image
        from ..export.sheet import layout

        if not _enabled(params.get("enabled", True)):
            return StepResult(outputs={"images": [], "preview": []}, meta={"skipped": True})
        opts, cols, rows = _options(params)
        used, ai, why = self._resolve(params)
        warnings: list[str] = []
        ai_failed = False
        if why and _method(params) == "ai":
            warnings.append(f"AI normal unavailable ({why}); used the filter method")
        refs = _images_in(inputs)
        sheet_meta = None
        if inputs.get("meta"):
            sheet_meta = await _read_json(ctx, inputs["meta"][0])
        out: list[BlobRef] = []
        previews: list[BlobRef] = []
        for i, ref in enumerate(refs):
            if ctx.cancelled.is_set():
                raise asyncio.CancelledError
            ctx.progress(i, len(refs), label="normal2d")
            img = await _load(ctx, ref)
            cells = _grid(img.width, img.height, cols, rows)
            if isinstance(sheet_meta, dict) and sheet_meta.get("frame_w") and len(refs) == 1:
                cells = list(layout(sheet_meta).cells) or cells
            rgba = np.asarray(img.convert("RGBA"))
            nmap = None
            if used == "ai":
                try:
                    nmap, fell = await normal_ai.normal_image_ai(rgba, opts, cells, ai, ctx.cancelled)
                    if fell:
                        warnings.append(f"{stem_of(ref, i)}: {fell} frame(s) too small for the model, used the filter")
                except normal_ai.NormalAiError as e:
                    ai_failed = True
                    warnings.append(f"AI normal failed on {stem_of(ref, i)} ({e}); used the filter method")
            if nmap is None:
                nmap = await asyncio.to_thread(normal_image, rgba, opts, cells)
            stem = stem_of(ref, i)
            out.append(await _put_png(ctx, Image.fromarray(nmap, "RGB"), f"{stem}_normal.png"))
            previews.append(await _put_png(ctx, await asyncio.to_thread(lit_preview, img, nmap),
                                           f"{stem}_normal_preview.png"))
        ctx.progress(len(refs), len(refs))
        for w in warnings:
            ctx.log(w, "warning")
        ran_ai = used == "ai" and not ai_failed
        meta = {"normal": True, "method": "ai" if ran_ai else "filter", "warnings": warnings,
                "strength": opts.strength, "bevel": opts.bevel or "auto", "detail": opts.detail, "green": "opengl"}
        if ran_ai:
            meta["model"] = normal_ai.MODEL_ID
        elif why:
            meta["ai_unavailable"] = why
        return StepResult(outputs={"images": out, "preview": previews}, meta=meta)


NORMAL2D_OPS = (Normal2D,)
