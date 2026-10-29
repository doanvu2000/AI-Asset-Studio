"""`op.aseprite_import`: a .aseprite / .ase file, or an Aseprite JSON sheet + its PNG -> frames + the anim json op.sheet
reads (clips from frame tags, directions from `<clip>_<S|SW|…>` tag names, pivot from a slice)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PIL import Image

from ..contracts import BlobRef, Outputs, StepContext, StepError, StepResult
from .executors import OpExecutor, _images_in, _put_png

if TYPE_CHECKING:
    from ..export.aseprite import Imported

ASE_EXT = (".aseprite", ".ase")


def _ext(ref: BlobRef) -> str:
    return Path(ref.name or "").suffix.lower()


def _named(ref: BlobRef, i: int) -> str:
    """Blob name with an extension `load` can sort on; unnamed blobs fall back to their mime type."""
    name = ref.name or f"file{i}"
    if Path(name).suffix.lower() in (*ASE_EXT, ".json", ".png", ".webp"):
        return name
    if ref.mime == "application/json":
        return f"{name}.json"
    return f"{name}.png" if ref.mime.startswith("image/") else f"{name}.aseprite"


def load(files: list[tuple[str, Path]], fallback: str) -> Imported:
    from ..export.aseprite import AsepriteError, import_ase, import_json, json_image_name

    ase = next((p for n, p in files if Path(n).suffix.lower() in ASE_EXT), None)
    try:
        if ase is not None:
            return import_ase(ase.read_bytes(), fallback)
        doc_path = next((p for n, p in files if Path(n).suffix.lower() == ".json"), None)
        if doc_path is None:
            raise AsepriteError("need a .aseprite/.ase file, or an Aseprite JSON sheet with its PNG")
        doc = json.loads(doc_path.read_bytes())
        pngs = [(n, p) for n, p in files if Path(n).suffix.lower() in (".png", ".webp")]
        want = json_image_name(doc).lower()
        img = next((p for n, p in pngs if Path(n).name.lower() == want), None)
        if img is None and len(pngs) == 1:
            img = pngs[0][1]
        if img is None:
            raise AsepriteError(f"the PNG sheet{f' {want}' if want else ''} named by the JSON is missing")
        import numpy as np

        with Image.open(img) as im:
            sheet = np.asarray(im.convert("RGBA"))
        return import_json(doc, sheet, fallback)
    except (OSError, ValueError) as e:
        raise StepError("op_bad_input", f"cannot import Aseprite sprite: {e}") from e


class AsepriteImport(OpExecutor):
    kind = "op.aseprite_import"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        from ..export.sheet import ident

        refs = _images_in(inputs, "files")
        first = next((r for r in refs if _ext(r) in (*ASE_EXT, ".json")), refs[0])
        fallback = ident(str(params.get("clip") or Path(first.name or "anim").stem), "anim")
        files = [(_named(r, i), ctx.blobs.path(r.hash)) for i, r in enumerate(refs)]
        got = await asyncio.to_thread(load, files, fallback)
        out: list[BlobRef] = []
        for i, (arr, name) in enumerate(zip(got.frames, got.names)):
            if ctx.cancelled.is_set():
                raise asyncio.CancelledError
            ctx.progress(i, len(got.frames), label="frames")
            out.append(await _put_png(ctx, Image.fromarray(arr, "RGBA"), f"{name}.png"))
        anim = await ctx.blobs.put_bytes(json.dumps(got.anim, indent=1).encode(), name="aseprite_anim.json",
                                         mime="application/json")
        ctx.progress(1.0)
        clips = [c["name"] for c in got.anim["clips"]]
        return StepResult(outputs={"images": out, "anim": [anim]},
                          meta={"source": got.source, "frames": len(out), "clips": clips,
                                "directions": len(got.anim["angles"])})


ASEPRITE_OPS = (AsepriteImport,)
