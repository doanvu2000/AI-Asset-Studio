"""op.paperdoll: equipment layer frames (blender.paperdoll) packed into sheets on the character sheet's exact cells,
sockets.json from the rig poses, and previews of character + equipment composited."""
from __future__ import annotations

import asyncio
import json
import math
from typing import TYPE_CHECKING, Any

from PIL import Image, ImageDraw

from ..adapters.blender import paperdoll_math as pdm
from ..contracts import BlobRef, Outputs, StepContext, StepError, StepResult
from .executors import OpExecutor, _load, _put_png, _read_json

if TYPE_CHECKING:
    # export.sheet imports ops.presets, so a module-level import here would be circular.
    from ..export.sheet import Cell, Layout, Track

DOLL_FILE = "paperdoll.json"
DOLL_TYPE = "asset-studio/paperdoll"
SOCKETS_FILE = "sockets.json"
PREVIEW_ANGLES = (0, 90, 180, 270)
SOCKET_COLORS = {"hand_r": (235, 64, 52), "hand_l": (52, 140, 235), "head": (245, 200, 40), "chest": (80, 200, 120)}


def cell_frames(lay: Layout) -> list[tuple[Track, int, Cell]]:
    """(track, frame index, cell) in the order blender.paperdoll emits frames: clip, direction, frame."""
    return [(t, i, c) for t in lay.tracks for i, c in enumerate(t.cells)]


def check_rig(rig: dict[str, Any], lay: Layout) -> None:
    clips = {c["name"]: int(c["frames"]) for c in rig.get("clips") or []}
    for t in lay.tracks:
        if clips.get(t.clip) != len(t.cells):
            raise ValueError(f"sheet clip {t.clip} has {len(t.cells)} frames, rig has {clips.get(t.clip)}")
    if len(lay.tracks) != len(clips) * len(rig.get("angles") or []):
        raise ValueError("sheet rows do not match the rig clips × directions")


def sockets_doc(rig: dict[str, Any], lay: Layout) -> dict[str, Any]:
    """Cell pixels like the sheet itself; the transform from the rig's render frame is a pure scale because
    downscale and the sheet keep each frame's canvas."""
    scale = lay.frame_w / float(rig["width"])
    pivot = (float(rig["pivot_exact"][0]) * scale, float(rig["pivot_exact"][1]) * scale)
    k = lay.frame_w / float(rig["ortho_scale"])
    elev, height = float(rig["elevation"]), float(rig["body_height"])
    frames = []
    for t, i, cell in cell_frames(lay):
        rec = pdm.socket_frame(rig["poses"][t.clip][i], t.angle, elev, k, pivot, height)
        frames.append({"cell": cell.name, "anim": t.name, "frame": i, "angle": t.angle, **rec})
    return {"type": "asset-studio/sockets", "version": 1, "frame_w": lay.frame_w, "frame_h": lay.frame_h,
            "pivot": list(lay.pivot), "sockets": list(pdm.SOCKETS), "layers": [e["name"] for e in rig["equip"]],
            "coords": "x/y: pixels inside the cell, origin top-left, y down. rot: socket bone direction in degrees, "
                      "clockwise on screen from +x. scale: on-screen length / 3D length (foreshortening). z: front = "
                      "in front of the chest from this view, back = behind the body. order: equipment layers "
                      "back to front (draw all of them above the character; their pixels are already occluded by "
                      "the body).",
            "frames": frames}


def pack_layer(frames: list[Image.Image], lay: Layout) -> Image.Image:
    sheet = Image.new("RGBA", (lay.width, lay.height), (0, 0, 0, 0))
    for img, (_, _, cell) in zip(frames, cell_frames(lay)):
        if img.size != (cell.w, cell.h):
            raise ValueError(f"layer frame is {img.size}, sheet cell is {(cell.w, cell.h)}")
        sheet.alpha_composite(img, (cell.x, cell.y))
    return sheet


def _pick_cells(lay: Layout) -> list[Cell]:
    out = []
    for clip in lay.clips()[:4]:
        tracks = [t for t in lay.tracks if t.clip == clip]
        for want in PREVIEW_ANGLES:
            t = min(tracks, key=lambda t: abs(((t.angle - want) + 180) % 360 - 180))
            for i in sorted({0, len(t.cells) // 2}):
                out.append(t.cells[i])
    return out


def previews(body: Image.Image, layers: list[tuple[str, Image.Image]], doc: dict[str, Any],
             lay: Layout) -> tuple[Image.Image, Image.Image]:
    """Composited character + equipment, and the same cells with socket markers over a faded body."""
    picks = _pick_cells(lay)
    by_cell = {f["cell"]: f for f in doc["frames"]}
    per_row = 2 * len(PREVIEW_ANGLES)
    rows = -(-len(picks) // per_row)
    fw, fh = lay.frame_w, lay.frame_h
    outs = [Image.new("RGBA", (per_row * fw, rows * fh), bg) for bg in ((46, 48, 56, 255), (232, 232, 236, 255))]
    draw = ImageDraw.Draw(outs[1])
    named = dict(layers)
    for n, cell in enumerate(picks):
        box = (cell.x, cell.y, cell.x + cell.w, cell.y + cell.h)
        ox, oy = (n % per_row) * fw, (n // per_row) * fh
        tile = body.crop(box)
        for name in by_cell.get(cell.name, {}).get("order", list(named)):
            if name in named:
                tile.alpha_composite(named[name].crop(box))
        outs[0].alpha_composite(tile, (ox, oy))
        faded = tile.copy()
        faded.putalpha(faded.getchannel("A").point(lambda a: a * 2 // 5))
        outs[1].alpha_composite(faded, (ox, oy))
        r = max(2, fw // 48)
        for s in by_cell.get(cell.name, {}).get("sockets", []):
            x, y = ox + s["x"], oy + s["y"]
            color = SOCKET_COLORS.get(s["name"], (0, 0, 0))
            tip = (x + math.cos(math.radians(s["rot"])) * 4 * r, y + math.sin(math.radians(s["rot"])) * 4 * r)
            draw.line([(x, y), tip], fill=color, width=max(1, r // 2))
            shape = draw.ellipse if s["z"] == "front" else draw.rectangle
            shape([x - r, y - r, x + r, y + r], fill=color if s["z"] == "front" else None, outline=color)
    return outs[0], outs[1]


class Paperdoll(OpExecutor):
    kind = "op.paperdoll"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        for port in ("rig", "sheet", "sheet_meta"):
            if len(inputs.get(port) or []) != 1:
                raise StepError("op_bad_input", f"expected exactly 1 '{port}'")
        rig = await _read_json(ctx, inputs["rig"][0])
        meta = await _read_json(ctx, inputs["sheet_meta"][0])
        from ..export.sheet import layout

        try:
            lay = layout(meta, directional_rows=True)
            check_rig(rig, lay)
            doc = sockets_doc(rig, lay)
        except (KeyError, ValueError, TypeError, IndexError) as e:
            raise StepError("op_bad_input", f"rig / sheet mismatch: {e}") from e
        names = [e["name"] for e in rig.get("equip") or []]
        refs = inputs.get("layers") or []
        n = len(cell_frames(lay))
        if len(refs) != n * len(names):
            raise StepError("op_bad_input", f"{len(refs)} layer frames for {len(names)} items × {n} cells")
        body = await _load(ctx, inputs["sheet"][0])
        if body.size != (lay.width, lay.height):
            raise StepError("op_bad_input", f"sheet is {body.size}, meta says {(lay.width, lay.height)}")
        packed: list[tuple[str, Image.Image]] = []
        for k, name in enumerate(names):
            frames = [await _load(ctx, r) for r in refs[k * n:(k + 1) * n]]
            try:
                packed.append((name, await asyncio.to_thread(pack_layer, frames, lay)))
            except ValueError as e:
                raise StepError("op_bad_input", str(e)) from e
            ctx.progress(k + 1, len(names) + 1, label=name)
        sheets = [await _put_png(ctx, img, f"{name}_sheet.png") for name, img in packed]
        sockets = await ctx.blobs.put_bytes(json.dumps(doc, indent=1).encode(), name=SOCKETS_FILE,
                                            mime="application/json")
        comp, marks = await asyncio.to_thread(previews, body, packed, doc, lay)
        preview = await _put_png(ctx, comp, "paperdoll_preview.png")
        socket_preview = await _put_png(ctx, marks, "sockets_preview.png")
        doll = {"type": DOLL_TYPE, "version": 1, "sheet": inputs["sheet"][0].hash,
                "sheet_meta": inputs["sheet_meta"][0].hash, "sockets": sockets.hash,
                "layers": [{"name": name, "slot": e["slot"], "bone": e["bone"], "shape": e.get("shape"),
                            "hash": ref.hash} for (name, _), e, ref in zip(packed, rig["equip"], sheets)]}
        doll_ref = await ctx.blobs.put_bytes(json.dumps(doll, indent=1).encode(), name=DOLL_FILE,
                                             mime="application/json")
        ctx.progress(1.0)
        outputs: dict[str, list[BlobRef]] = {"sheets": sheets, "sockets": [sockets], "preview": [preview],
                                             "socket_preview": [socket_preview], "doll": [doll_ref]}
        return StepResult(outputs=outputs, meta={"layers": names, "cells": n, "sockets": list(pdm.SOCKETS)})


PAPERDOLL_OPS: tuple[type[OpExecutor], ...] = (Paperdoll,)
