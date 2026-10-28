"""NPC pack ops: `op.npc_lines` (lines CSV → dialogue doc), `op.npc_portraits` (Klein expression edits put back on the
base portrait's exact crop, with an identity check) and `op.npc_pack` (dialogue + voiced clips + portraits → npc.json
with subtitle cues)."""
from __future__ import annotations

import asyncio
import json
import re
import wave
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from ..adapters import tts_text as tt
from ..adapters.npc import lines_csv, parse_expressions, parse_lines_csv, put_dialogue
from ..contracts import BlobRef, Outputs, StepContext, StepError, StepResult
from .executors import OpExecutor, _images_in, _load, _put_png

PORTRAITS_FILE = "npc_portraits.json"
PORTRAITS_TYPE = "npc_portraits"
NPC_FILE = "npc.json"
NPC_TYPE = "npc_pack"
ALIGN_SIDE = 256


def fit_to(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Centre-crop to the base aspect, then resize: Klein may hand back a slightly different canvas."""
    w, h = img.size
    tw, th = size
    if abs(w / h - tw / th) > 1e-3:
        if w / h > tw / th:
            nw = round(h * tw / th)
            img = img.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
        else:
            nh = round(w * th / tw)
            img = img.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))
    return img.resize(size, Image.Resampling.LANCZOS) if img.size != size else img


def _gray(img: Image.Image, side: int) -> np.ndarray:
    s = min(1.0, side / max(img.size))
    small = img.convert("L").resize((max(1, round(img.width * s)), max(1, round(img.height * s))),
                                    Image.Resampling.BOX)
    return np.asarray(small, np.float32) / 255.0


def phase_shift(ref: np.ndarray, mov: np.ndarray) -> tuple[float, float]:
    """(dy, dx) that moves `mov` onto `ref`, by phase correlation with a Hann window."""
    win = np.outer(np.hanning(ref.shape[0]), np.hanning(ref.shape[1])).astype(np.float32)
    a, b = np.fft.fft2((ref - ref.mean()) * win), np.fft.fft2((mov - mov.mean()) * win)
    r = a * np.conj(b)
    r /= np.abs(r) + 1e-9
    corr = np.fft.ifft2(r).real
    dy, dx = np.unravel_index(int(np.argmax(corr)), corr.shape)
    h, w = corr.shape
    return float(dy - h if dy > h // 2 else dy), float(dx - w if dx > w // 2 else dx)


def shift_image(img: Image.Image, dx: int, dy: int) -> Image.Image:
    if not dx and not dy:
        return img
    a = np.asarray(img)
    a = np.roll(a, (dy, dx), axis=(0, 1))
    # Edge pixels repeat into the gap instead of the wrapped-around opposite edge.
    if dy > 0:
        a[:dy] = a[dy]
    elif dy < 0:
        a[dy:] = a[dy - 1]
    if dx > 0:
        a[:, :dx] = a[:, dx:dx + 1]
    elif dx < 0:
        a[:, dx:] = a[:, dx - 1:dx]
    return Image.fromarray(a, img.mode)


def similarity(a: Image.Image, b: Image.Image) -> float:
    """Normalised cross-correlation of small grey copies: ~1 = same framing/identity, a big drop = redrawn."""
    x, y = _gray(a, 64).ravel(), _gray(b, 64).ravel()
    x, y = x - x.mean(), y - y.mean()
    d = float(np.linalg.norm(x) * np.linalg.norm(y))
    return round(float(x @ y) / d, 4) if d > 1e-9 else 0.0


def align_portrait(base: Image.Image, edit: Image.Image, max_shift: float) -> tuple[Image.Image, tuple[int, int]]:
    edit = fit_to(edit.convert("RGB"), base.size)
    ref, mov = _gray(base, ALIGN_SIDE), _gray(edit, ALIGN_SIDE)
    dy, dx = phase_shift(ref, mov)
    scale = base.width / ref.shape[1]
    sx, sy = round(dx * scale), round(dy * scale)
    if abs(sx) > max_shift * base.width or abs(sy) > max_shift * base.height:
        sx = sy = 0
    out = shift_image(edit, sx, sy)
    if base.mode == "RGBA":
        # Expression edits keep the head outline, so the base cut-out stays valid and edges stay as clean as the input.
        out = out.convert("RGBA")
        out.putalpha(base.getchannel("A"))
    return out, (sx, sy)


def grid(names: list[str], images: list[Image.Image], scores: list[float | None], min_sim: float,
         cell: int = 256) -> Image.Image:
    cols = min(4, max(1, len(images)))
    rows = (len(images) + cols - 1) // cols
    head = 22
    out = Image.new("RGB", (cols * cell, rows * (cell + head)), (24, 22, 32))
    d = ImageDraw.Draw(out)
    for i, (n, img, s) in enumerate(zip(names, images, scores)):
        x, y = (i % cols) * cell, (i // cols) * (cell + head)
        t = img.copy()
        t.thumbnail((cell - 8, cell - 8))
        tile = Image.new("RGBA", t.size, (60, 58, 70, 255))
        tile.alpha_composite(t.convert("RGBA"))
        out.paste(tile.convert("RGB"), (x + (cell - t.width) // 2, y + head + (cell - t.height) // 2))
        bad = s is not None and s < min_sim
        label = n if s is None else f"{n}  {s:.2f}"
        d.text((x + 6, y + 5), label, fill=(255, 120, 90) if bad else (225, 225, 235))
    return out


def _stem(ref: BlobRef) -> str:
    return (ref.name or "").rsplit(".", 1)[0]


class NpcLines(OpExecutor):
    kind = "op.npc_lines"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        refs = inputs.get("csv") or []
        if not refs:
            raise StepError("op_no_input", "input 'csv' (id,lang,text,expression) is empty")
        data = await asyncio.to_thread(ctx.blobs.path(refs[0].hash).read_bytes)
        doc = parse_lines_csv(data, name=str(params.get("name") or "npc"), persona=str(params.get("persona") or ""))
        doc["voice"].update({k: params[k] for k in ("gender", "age") if params.get(k)})
        return StepResult(outputs=await put_dialogue(ctx, doc),
                          meta={"lines": len(doc["lines"]), "languages": doc["languages"]})


class NpcPortraits(OpExecutor):
    kind = "op.npc_portraits"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        base_ref = _images_in(inputs)[0]
        edits = _images_in(inputs, "edits")
        names = parse_expressions(params.get("expressions"))[1:]
        if len(names) != len(edits):
            names = [f"expr_{i + 1:02d}" for i in range(len(edits))]
        max_shift = float(params.get("max_shift", 0.06))
        min_sim = float(params.get("min_similarity", 0.55))
        base = await _load(ctx, base_ref)
        base = base.convert("RGBA") if base.mode in ("RGBA", "LA", "PA") else base.convert("RGB")
        portraits: list[tuple[str, Image.Image, float | None, tuple[int, int]]] = []
        if "neutral" not in names:
            portraits.append(("neutral", base, None, (0, 0)))
        for name, ref in zip(names, edits):
            edit = await _load(ctx, ref)
            aligned, shift = await asyncio.to_thread(align_portrait, base, edit, max_shift)
            portraits.append((name, aligned, await asyncio.to_thread(similarity, base, aligned), shift))
        refs, rows = [], []
        for name, img, sim, shift in portraits:
            ref = await _put_png(ctx, img, f"portrait_{name}.png")
            refs.append(ref)
            rows.append({"expression": name, "name": ref.name, "hash": ref.hash, "similarity": sim,
                         "shift": list(shift)})
        doc = {"type": PORTRAITS_TYPE, "version": 1, "size": list(base.size), "portraits": rows}
        doc_ref = await ctx.blobs.put_bytes(json.dumps(doc, indent=1).encode(), name=PORTRAITS_FILE,
                                            mime="application/json")
        prev = await _put_png(ctx, await asyncio.to_thread(grid, [p[0] for p in portraits], [p[1] for p in portraits],
                                                           [p[2] for p in portraits], min_sim),
                              "npc_portraits_grid.png")
        low = [f"{r['expression']} {r['similarity']:.2f}" for r in rows
               if r["similarity"] is not None and r["similarity"] < min_sim]
        meta: dict[str, Any] = {"portraits": len(rows), "similarity": {r["expression"]: r["similarity"] for r in rows}}
        if low:
            meta["flagged"] = [f"portrait drifted from the base face/crop: {', '.join(low)}"]
            ctx.log(meta["flagged"][0], "warning")
        return StepResult(outputs={"images": refs, "preview": [prev], "portraits": [doc_ref]}, meta=meta)


def _seconds(path: Path) -> float | None:
    try:
        with wave.open(str(path), "rb") as w:
            return round(w.getnframes() / float(w.getframerate()), 3)
    except (OSError, wave.Error, EOFError):
        return None


def card(doc: dict[str, Any], portrait: Image.Image | None, width: int = 900) -> Image.Image:
    lines = doc.get("lines") or []
    lang = (doc.get("languages") or ["en"])[0]
    row_h, head = 18, 34
    img = Image.new("RGB", (width, head + row_h * max(4, min(len(lines), 30)) + 12), (24, 22, 32))
    d = ImageDraw.Draw(img)
    x0 = 12
    if portrait is not None:
        t = portrait.copy()
        t.thumbnail((200, 200))
        tile = Image.new("RGBA", t.size, (60, 58, 70, 255))
        tile.alpha_composite(t.convert("RGBA"))
        img.paste(tile.convert("RGB"), (12, head))
        x0 = 224
    d.text((12, 10), f"{doc.get('name')}  ({', '.join(doc.get('languages') or [])}, {len(lines)} lines)",
           fill=(235, 235, 240))
    for i, ln in enumerate(lines[:30]):
        # PIL's default bitmap font has no Vietnamese glyphs; the preview stays ASCII.
        text = str(ln["text"].get(lang) or next(iter(ln["text"].values()), ""))
        text = text.encode("ascii", "replace").decode() if lang != "en" else text
        d.text((x0, head + i * row_h), f"[{ln['category']}/{ln['expression']}] {text}"[:110], fill=(200, 200, 215))
    return img


class NpcPack(OpExecutor):
    kind = "op.npc_pack"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        dref = inputs.get("dialogue") or []
        if not dref:
            raise StepError("op_no_input", "input 'dialogue' (npc_dialogue json) is empty")
        doc = json.loads(await asyncio.to_thread(ctx.blobs.path(dref[0].hash).read_bytes))
        portraits: list[dict[str, Any]] = []
        for ref in inputs.get("portraits") or []:
            if ref.name == PORTRAITS_FILE:
                pdoc = json.loads(await asyncio.to_thread(ctx.blobs.path(ref.hash).read_bytes))
                portraits = [{k: p[k] for k in ("expression", "name", "hash")} for p in pdoc.get("portraits") or []]
        clips: dict[str, dict[str, BlobRef]] = {}
        for r in inputs.get("audio") or []:
            stem, _, ext = (r.name or "").rpartition(".")
            # op.audio_post names clips <label>_<take>; one take per line here.
            key = re.sub(r"_\d+$", "", stem)
            clips.setdefault(key, {})[ext.lower()] = r
        have = {p["expression"] for p in portraits}
        lines, missing = [], []
        for ln in doc.get("lines") or []:
            entry = {**ln, "audio": {}, "subtitles": {}}
            if have and entry.get("expression") not in have:
                entry["expression"] = "neutral" if "neutral" in have else portraits[0]["expression"]
            for lang, text in (ln.get("text") or {}).items():
                files = clips.get(f"{lang}_{ln['id']}")
                if not files:
                    missing.append(f"{lang}_{ln['id']}")
                    continue
                secs = _seconds(ctx.blobs.path(files["wav"].hash)) if "wav" in files else None
                entry["audio"][lang] = {"stem": _stem(next(iter(files.values()))), "seconds": secs,
                                        "files": sorted(r.name for r in files.values() if r.name),
                                        "hashes": {ext: r.hash for ext, r in files.items()}}
                entry["subtitles"][lang] = tt.subtitle_cues(text, secs or 0.0)
            lines.append(entry)
        pack = {"type": NPC_TYPE, "version": 1, "name": doc.get("name"), "persona": doc.get("persona", ""),
                "languages": doc.get("languages") or [], "voice": doc.get("voice") or {}, "portraits": portraits,
                "lines": lines, "missing_audio": missing}
        subs = {lang: {ln["id"]: ln["subtitles"][lang] for ln in lines if lang in ln["subtitles"]}
                for lang in pack["languages"]}
        npc_ref = await ctx.blobs.put_bytes(json.dumps(pack, ensure_ascii=False, indent=1).encode("utf-8"),
                                            name=NPC_FILE, mime="application/json")
        sub_ref = await ctx.blobs.put_bytes(json.dumps(subs, ensure_ascii=False, indent=1).encode("utf-8"),
                                            name="npc_subtitles.json", mime="application/json")
        csv_ref = await ctx.blobs.put_bytes(lines_csv(doc), name="npc_lines.csv", mime="text/csv")
        face = None
        if portraits:
            face = await _load(ctx, BlobRef(hash=portraits[0]["hash"], name=portraits[0]["name"], mime="image/png",
                                            size=0))
        prev = await _put_png(ctx, await asyncio.to_thread(card, pack, face), "npc_card.png")
        meta: dict[str, Any] = {"lines": len(lines), "clips": sum(len(ln["audio"]) for ln in lines),
                                "portraits": len(portraits)}
        if missing:
            meta["flagged"] = [f"{len(missing)} line(s) without audio: {', '.join(missing[:6])}"]
        return StepResult(outputs={"npc": [npc_ref], "subtitles": [sub_ref], "csv": [csv_ref], "preview": [prev]},
                          meta=meta)


NPC_OPS: tuple[type[OpExecutor], ...] = (NpcLines, NpcPortraits, NpcPack)
