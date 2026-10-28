"""`op.normal2d`: sprite or sprite sheet -> 2D-light normal map (CPU, export/normal2d.py)."""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import numpy as np
from PIL import Image

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


class Normal2D(OpExecutor):
    kind = "op.normal2d"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        # export.sheet imports ops at module load, so the export side is pulled in lazily.
        from ..export.normal2d import lit_preview, normal_image
        from ..export.sheet import layout

        if not _enabled(params.get("enabled", True)):
            return StepResult(outputs={"images": [], "preview": []}, meta={"skipped": True})
        opts, cols, rows = _options(params)
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
            nmap = await asyncio.to_thread(normal_image, rgba, opts, cells)
            stem = (ref.name or f"sprite_{i:02d}").rsplit(".", 1)[0]
            out.append(await _put_png(ctx, Image.fromarray(nmap, "RGB"), f"{stem}_normal.png"))
            previews.append(await _put_png(ctx, await asyncio.to_thread(lit_preview, img, nmap),
                                           f"{stem}_normal_preview.png"))
        ctx.progress(len(refs), len(refs))
        return StepResult(outputs={"images": out, "preview": previews},
                          meta={"normal": True, "strength": opts.strength, "bevel": opts.bevel or "auto",
                                "detail": opts.detail, "green": "opengl"})


NORMAL2D_OPS = (Normal2D,)
