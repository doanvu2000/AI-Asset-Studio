"""`op.engine_pack`: the 8-direction sprite frames plus any animation sheets of one character become one atlas and a
Unity and a Godot package (atlas pages, TexturePacker JSON, engine import files) with a clip manifest."""
from __future__ import annotations

import asyncio
import io
import json
import zipfile
from typing import Any

from PIL import Image

from ..contracts import Outputs, StepContext, StepError, StepResult
from .executors import OpExecutor, _load, _read_json

DEFAULT_CLIPS = "idle,walk,attack,hit"
# Clips whose last frame must not wrap back to the first; anything else loops.
ONESHOT = {"attack", "slash", "hit", "death", "cast", "jump"}
DIRECTIONS = 8
# Same bottom pivot op.sheet(pivot=bottom) and op.normalize(anchor=bottom) use.
PIVOT_PAD = 0.06
ALPHA_MIN = 8
# Beyond this the anim was generated so small that upscaling it would only blur; keep it and warn.
MAX_MATCH_SCALE = 4.0
# A direction sprite this far off the median direction height is a stray-sized view (the front view is often the
# unscaled concept) and is rescaled on its feet.
DIR_OUTLIER = 0.15
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)


def _zip(files: list[tuple[str, bytes]], root: str) -> bytes:
    # Fixed timestamps keep the package bytes (and so its blob hash) stable between identical runs.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, data in files:
            info = zipfile.ZipInfo(f"{root}/{rel}", _ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    return buf.getvalue()


def _bottom_pivot(size: tuple[int, int]) -> tuple[float, float]:
    return size[0] // 2, size[1] - round(size[1] * PIVOT_PAD)


def _clip_frames(sheet: Image.Image, meta: dict[str, Any], clip: str) -> list[Image.Image]:
    try:
        fw, fh = int(meta["frame_w"]), int(meta["frame_h"])
        cols = int(meta.get("cols") or 1)
        rows = int(meta.get("rows") or 1)
        pad = int(meta.get("padding") or 0)
        count = int(meta.get("count") or cols * rows)
    except (KeyError, TypeError, ValueError) as e:
        raise StepError("op_bad_input", f"bad meta of clip '{clip}': {e!r}") from e
    if fw <= 0 or fh <= 0 or cols < 1 or rows < 1 or pad < 0 or not 0 < count <= cols * rows:
        raise StepError("op_bad_input", f"meta of clip '{clip}' has an impossible grid")
    out: list[Image.Image] = []
    for i in range(count):
        r, c = divmod(i, cols)
        x, y = c * (fw + pad), r * (fh + pad)
        if x + fw > sheet.width or y + fh > sheet.height:
            raise StepError("op_bad_input", f"sheet of clip '{clip}' is {sheet.size}, meta puts frame {i} outside it")
        out.append(sheet.crop((x, y, x + fw, y + fh)))
    return out


def _pivot(meta: dict[str, Any], size: tuple[int, int]) -> tuple[float, float]:
    pv = meta.get("pivot")
    if isinstance(pv, list) and len(pv) == 2:
        try:
            return float(pv[0]), float(pv[1])
        except (TypeError, ValueError):
            pass
    return _bottom_pivot(size)


def _body_height(img: Image.Image) -> int:
    box = img.getchannel("A").point(lambda a: 255 if a >= ALPHA_MIN else 0).getbbox()
    return box[3] - box[1] if box else 0


def _match_scale(frames: list[Image.Image], ref_h: int) -> float | None:
    # Median body height: a raised weapon in one frame must not shrink the whole clip.
    heights = sorted(h for h in map(_body_height, frames) if h)
    if not heights or ref_h <= 0:
        return None
    return ref_h / heights[len(heights) // 2]


def _rescale_on_feet(img: Image.Image, k: float, pixel: bool) -> Image.Image | None:
    box = img.getchannel("A").point(lambda a: 255 if a >= ALPHA_MIN else 0).getbbox()
    if not box:
        return None
    size = (max(1, round(img.width * k)), max(1, round(img.height * k)))
    scaled = img.resize(size, Image.NEAREST if pixel else Image.LANCZOS)
    dx = round((box[0] + box[2]) / 2 - (box[0] + box[2]) / 2 * k)
    dy = round(box[3] - box[3] * k)
    out = Image.new("RGBA", img.size, (0, 0, 0, 0))
    out.paste(scaled.convert("RGBA"), (dx, dy))
    return out


class EnginePack(OpExecutor):
    kind = "op.engine_pack"
    version = "4"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        from ..export.atlas import AtlasError, AtlasOptions, Sprite, engine_files, pack
        from ..export.sheet import compass, ident

        try:
            fps = float(params.get("fps") or 12)
            ppu = float(params.get("ppu") or 100)
            opts = AtlasOptions(max_size=int(params.get("max_size") or 2048), padding=int(params.get("padding", 2)),
                                extrude=int(params.get("extrude", 1)), pot=True, trim=True)
        except (TypeError, ValueError) as e:
            raise StepError("op_bad_params", f"bad numeric param: {e}") from e
        if fps <= 0 or ppu <= 0:
            raise StepError("op_bad_params", "fps and ppu must be > 0")
        asset = ident(str(params.get("asset") or "character"), "character")
        pixel = bool(params.get("pixel"))
        match_scale = params.get("match_scale", True) not in (False, "false", "0", 0)
        warnings: list[str] = []
        sprites: list[Sprite] = []
        dir_sprites: dict[str, tuple[int, Sprite]] = {}
        clips: dict[str, list[Sprite]] = {}
        sources: dict[str, tuple[int, int, int]] = {}
        scales: dict[str, float] = {}

        dirs = inputs.get("frames") or []
        if dirs and len(dirs) != DIRECTIONS:
            raise StepError("op_bad_input", f"expected {DIRECTIONS} direction frames, got {len(dirs)}")
        for i, ref in enumerate(dirs):
            img = await _load(ctx, ref)
            sources[f"dir{i}"] = (ref.size or 0, img.width, img.height)
            angle = i * 360 // DIRECTIONS
            s = Sprite(f"{asset}_dir_{compass(angle)}", img, _bottom_pivot(img.size), pixel=pixel)
            dir_sprites[compass(angle)] = (angle, s)
            sprites.append(s)

        dir_heights = sorted(h for h in (_body_height(s.image) for _, s in dir_sprites.values()) if h)
        ref_h = dir_heights[len(dir_heights) // 2] if dir_heights else 0
        if match_scale and ref_h:
            for name, (_, s) in dir_sprites.items():
                h = _body_height(s.image)
                if h and abs(h / ref_h - 1) > DIR_OUTLIER and ref_h / h <= MAX_MATCH_SCALE:
                    fixed = _rescale_on_feet(s.image, ref_h / h, pixel)
                    if fixed is not None and _body_height(fixed) >= ref_h * 0.97:
                        s.image = fixed
                        scales[f"dir_{name}"] = round(ref_h / h, 3)
                    else:
                        warnings.append(f"direction {name} is {h}px tall vs {ref_h}px median, rescale would clip")

        for raw in str(params.get("clips") or DEFAULT_CLIPS).split(","):
            if not raw.strip():
                continue
            key = ident(raw.strip(), "clip").lower()
            if key in clips:
                continue
            sheets, metas = inputs.get(f"sheet_{key}") or [], inputs.get(f"meta_{key}") or []
            if not sheets and not metas:
                continue
            if len(sheets) != 1 or len(metas) != 1:
                raise StepError("op_bad_input", f"clip '{key}' needs exactly 1 sheet and 1 meta")
            meta = await _read_json(ctx, metas[0])
            if not isinstance(meta, dict):
                raise StepError("op_bad_input", f"meta of clip '{key}' is not an object")
            sheet = (await _load(ctx, sheets[0])).convert("RGBA")
            sources[f"sheet_{key}"] = (sheets[0].size or 0, sheet.width, sheet.height)
            frames = _clip_frames(sheet, meta, key)
            pivot = _pivot(meta, frames[0].size)
            if match_scale and ref_h:
                k = _match_scale(frames, ref_h)
                if k is None:
                    warnings.append(f"clip '{key}' has no visible pixels, scale not matched")
                elif k > MAX_MATCH_SCALE:
                    warnings.append(f"clip '{key}' is {k:.1f}x smaller than the direction sprites, scale not matched")
                elif abs(k - 1) > 0.01:
                    size = (max(1, round(frames[0].width * k)), max(1, round(frames[0].height * k)))
                    resample = Image.NEAREST if pixel else Image.LANCZOS
                    frames = [f.resize(size, resample) for f in frames]
                    pivot = (pivot[0] * k, pivot[1] * k)
                    scales[key] = round(k, 3)
            clips[key] = [Sprite(f"{asset}_{key}_{f:02d}", img, pivot, pixel=pixel) for f, img in enumerate(frames)]
            sprites += clips[key]
        if not sprites:
            raise StepError("op_no_input", "no direction frames and no animation sheet to pack")

        try:
            atlas = await asyncio.to_thread(pack, sprites, asset, opts, sources)
        except AtlasError as e:
            raise StepError("op_bad_params", str(e)) from e
        # pack() renames clashing sprites in place, so the manifest reads the names after packing.
        manifest: dict[str, Any] = {
            "asset": asset,
            "directions": {d: {"angle": a, "sprite": s.name} for d, (a, s) in dir_sprites.items()},
            "clips": {k: {"fps": fps, "loop": k not in ONESHOT, "frames": [s.name for s in v]}
                      for k, v in clips.items()},
            "atlas": {"pages": len(atlas.pages), "sprites": len(atlas.frames)},
        }
        manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8")
        out: Outputs = {"manifest": [await ctx.blobs.put_bytes(manifest_bytes, name=f"{asset}_clips.json",
                                                               mime="application/json")]}
        want = str(params.get("engines") or "both")
        for engine in ("unity", "godot"):
            if want not in ("both", engine):
                continue
            files = await asyncio.to_thread(engine_files, atlas, engine, ppu, asset)
            files.append(("clips.json", manifest_bytes))
            data = await asyncio.to_thread(_zip, files, asset)
            out[engine] = [await ctx.blobs.put_bytes(data, name=f"{asset}_{engine}.zip", mime="application/zip")]
        multi = len(atlas.png) > 1
        out["atlas"] = [await ctx.blobs.put_bytes(png, name=f"{asset}_atlas{f'_{i}' if multi else ''}.png",
                                                  mime="image/png") for i, png in enumerate(atlas.png)]
        return StepResult(outputs=out, meta={"sprites": len(atlas.frames), "pages": len(atlas.pages),
                                             "clips": list(clips),
                                             "scaled": scales, "warnings": warnings})


ENGINE_PACK_OPS: tuple[type[OpExecutor], ...] = (EnginePack,)
