"""Aseprite interchange, written against docs/ase-file-specs.md: a .aseprite writer and reader (RGBA / grayscale /
indexed, zlib cels, layers, tags, slices) and Aseprite's array JSON sheet. Export turns a sheet's clip × direction
tracks into frames + tags; import turns frames + tags back into the clips op.sheet lays out."""
from __future__ import annotations

import io
import json
import re
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from studio_core.export.source import Files, Source

MAGIC = 0xA5E0
FRAME_MAGIC = 0xF1FA
CHUNK_OLD_PALETTE = 0x0004
CHUNK_LAYER = 0x2004
CHUNK_CEL = 0x2005
CHUNK_PROFILE = 0x2007
CHUNK_TAGS = 0x2018
CHUNK_PALETTE = 0x2019
CHUNK_SLICE = 0x2022
HEADER_LAYER_OPACITY = 1
LAYER_VISIBLE = 1
LAYER_EDITABLE = 2
LAYER_BACKGROUND = 8
CEL_RAW, CEL_LINKED, CEL_COMPRESSED, CEL_TILEMAP = 0, 1, 2, 3
DIR_FORWARD, DIR_REVERSE, DIR_PINGPONG, DIR_PINGPONG_REVERSE = 0, 1, 2, 3
WORD_MAX = 0xFFFF
PIVOT_SLICE = "pivot"
JSON_APP = "AI Asset Studio"


class AsepriteError(ValueError):
    pass


@dataclass
class AseTag:
    name: str
    start: int
    end: int
    loop: bool = True
    direction: int = DIR_FORWARD

    def frames(self) -> list[int]:
        """Play order with ping-pong unrolled, so an engine clip looping forward plays the same."""
        seq = list(range(self.start, self.end + 1))
        if self.direction in (DIR_REVERSE, DIR_PINGPONG_REVERSE):
            seq.reverse()
        if self.direction in (DIR_PINGPONG, DIR_PINGPONG_REVERSE) and len(seq) > 2:
            seq += seq[-2:0:-1]
        return seq


@dataclass
class AseLayer:
    name: str
    visible: bool = True
    opacity: int = 255


@dataclass
class AseDoc:
    """Sprite: per frame one full-canvas uint8 RGBA array (or None) per layer, layers bottom to top."""
    width: int
    height: int
    layers: list[AseLayer]
    frames: list[list[np.ndarray | None]]
    durations: list[int]
    tags: list[AseTag] = field(default_factory=list)
    pivot: tuple[int, int] | None = None
    palette: list[tuple[int, int, int, int]] = field(default_factory=list)

    def flatten(self, frame: int) -> np.ndarray:
        out = np.zeros((self.height, self.width, 4), np.uint8)
        for layer, px in zip(self.layers, self.frames[frame]):
            if layer.visible and px is not None:
                out = over(out, px, layer.opacity)
        return out


def over(dst: np.ndarray, src: np.ndarray, opacity: int = 255) -> np.ndarray:
    """Straight-alpha source-over, as Aseprite's normal blend mode."""
    sa = src[..., 3:4].astype(np.float32) / 255.0 * (opacity / 255.0)
    da = dst[..., 3:4].astype(np.float32) / 255.0
    oa = sa + da * (1 - sa)
    rgb = src[..., :3] * sa + dst[..., :3] * da * (1 - sa)
    rgb = np.where(oa > 0, rgb / np.maximum(oa, 1e-6), 0)
    return np.concatenate([np.clip(np.round(rgb), 0, 255), np.clip(np.round(oa * 255), 0, 255)], -1).astype(np.uint8)


def _string(s: str) -> bytes:
    b = s.encode("utf-8")[:WORD_MAX]
    return struct.pack("<H", len(b)) + b


def _chunk(kind: int, data: bytes) -> bytes:
    return struct.pack("<IH", len(data) + 6, kind) + data


def _cel(layer: int, px: np.ndarray, level: int) -> bytes | None:
    alpha = px[..., 3]
    rows, cols = np.flatnonzero(alpha.any(1)), np.flatnonzero(alpha.any(0))
    if not len(rows):
        return None
    y0, y1, x0, x1 = int(rows[0]), int(rows[-1]) + 1, int(cols[0]), int(cols[-1]) + 1
    crop = np.ascontiguousarray(px[y0:y1, x0:x1])
    head = struct.pack("<HhhBHh5xHH", layer, x0, y0, 255, CEL_COMPRESSED, 0, x1 - x0, y1 - y0)
    return _chunk(CHUNK_CEL, head + zlib.compress(crop.tobytes(), level))


def palette_of(images: list[np.ndarray], limit: int = 256) -> list[tuple[int, int, int, int]]:
    """Exact colours when they fit (pixel art), else a median-cut palette of the opaque pixels."""
    opaque = [im[im[..., 3] > 0][:, :3] for im in images]
    px = np.concatenate(opaque) if opaque else np.zeros((0, 3), np.uint8)
    if not len(px):
        return [(0, 0, 0, 0)]
    packed = np.unique((px[:, 0].astype(np.uint32) << 16) | (px[:, 1].astype(np.uint32) << 8) | px[:, 2])
    if len(packed) < limit:
        return [(0, 0, 0, 0)] + [(int(c >> 16), int(c >> 8 & 255), int(c & 255), 255) for c in packed]
    from PIL import Image

    sample = px[:: max(1, len(px) // 262144)]
    q = Image.fromarray(sample.reshape(1, -1, 3), "RGB").quantize(limit - 1)
    pal = (q.getpalette() or [])[: (limit - 1) * 3]
    return [(0, 0, 0, 0)] + [(pal[i], pal[i + 1], pal[i + 2], 255) for i in range(0, len(pal), 3)]


def write(doc: AseDoc, level: int = 6) -> bytes:
    if not 0 < doc.width <= WORD_MAX or not 0 < doc.height <= WORD_MAX:
        raise AsepriteError(f"canvas {doc.width}x{doc.height} exceeds Aseprite's 65535 px limit")
    if not 0 < len(doc.frames) <= WORD_MAX or len(doc.durations) != len(doc.frames):
        raise AsepriteError("need 1..65535 frames, one duration each")
    palette = doc.palette or [(0, 0, 0, 0)]
    first: list[bytes] = [_chunk(CHUNK_PROFILE, struct.pack("<HHi8x", 1, 0, 0))]
    n = len(palette)
    first.append(_chunk(CHUNK_PALETTE, struct.pack("<III8x", n, 0, n - 1)
                        + b"".join(struct.pack("<H4B", 0, *c) for c in palette)))
    for la in doc.layers:
        flags = LAYER_EDITABLE | (LAYER_VISIBLE if la.visible else 0)
        first.append(_chunk(CHUNK_LAYER, struct.pack("<6HB3x", flags, 0, 0, 0, 0, 0, la.opacity) + _string(la.name)))
    if doc.tags:
        body = struct.pack("<H8x", len(doc.tags))
        for t in doc.tags:
            rep = 0 if t.loop else 1
            body += struct.pack("<HHBH6x3Bx", t.start, t.end, t.direction, rep, 0, 0, 0) + _string(t.name)
        first.append(_chunk(CHUNK_TAGS, body))
    if doc.pivot is not None:
        px, py = doc.pivot
        first.append(_chunk(CHUNK_SLICE, struct.pack("<III", 1, 2, 0) + _string(PIVOT_SLICE)
                            + struct.pack("<IiiIIii", 0, 0, 0, doc.width, doc.height, int(px), int(py))))
    frames = []
    for i, (cels, dur) in enumerate(zip(doc.frames, doc.durations)):
        chunks = list(first) if i == 0 else []
        for li, px_ in enumerate(cels):
            if px_ is not None and (c := _cel(li, px_, level)) is not None:
                chunks.append(c)
        body = b"".join(chunks)
        frames.append(struct.pack("<IHHH2xI", 16 + len(body), FRAME_MAGIC, min(len(chunks), WORD_MAX),
                                  max(1, min(int(dur), WORD_MAX)), len(chunks)) + body)
    data = b"".join(frames)
    header = struct.pack("<IHHHHHIHIIB3xHBBhhHH84x", 128 + len(data), MAGIC, len(doc.frames), doc.width, doc.height,
                         32, HEADER_LAYER_OPACITY, 100, 0, 0, 0, n, 1, 1, 0, 0, 16, 16)
    return header + data


class _Reader:
    def __init__(self, data: bytes, pos: int = 0, end: int | None = None):
        self.data, self.pos, self.end = data, pos, len(data) if end is None else end

    def take(self, fmt: str) -> tuple[Any, ...]:
        n = struct.calcsize(fmt)
        if self.pos + n > self.end:
            raise AsepriteError("truncated .aseprite file")
        out = struct.unpack_from(fmt, self.data, self.pos)
        self.pos += n
        return out

    def string(self) -> str:
        (n,) = self.take("<H")
        if self.pos + n > self.end:
            raise AsepriteError("truncated string")
        s = self.data[self.pos:self.pos + n].decode("utf-8", "replace")
        self.pos += n
        return s

    def rest(self) -> bytes:
        b = self.data[self.pos:self.end]
        self.pos = self.end
        return b


@dataclass
class _RawLayer:
    name: str
    flags: int
    kind: int
    level: int
    opacity: int


def _to_rgba(raw: bytes, w: int, h: int, depth: int, palette: list[tuple[int, int, int, int]], transparent: int,
             background: bool) -> np.ndarray:
    bpp = depth // 8
    if len(raw) < w * h * bpp:
        raise AsepriteError("cel pixel data is shorter than its size")
    a = np.frombuffer(raw, np.uint8, w * h * bpp)
    if depth == 32:
        return a.reshape(h, w, 4).copy()
    if depth == 16:
        g = a.reshape(h, w, 2)
        return np.dstack([g[..., 0], g[..., 0], g[..., 0], g[..., 1]])
    lut = np.zeros((256, 4), np.uint8)
    for i, c in enumerate(palette[:256]):
        lut[i] = c
    if not background and 0 <= transparent < 256:
        lut[transparent, 3] = 0
    return lut[a.reshape(h, w)]


def read(data: bytes) -> AseDoc:
    r = _Reader(data)
    (size, magic, n_frames, width, height, depth, flags, _speed, _z1, _z2, transparent, _ncol, _pw, _ph,
     _gx, _gy, _gw, _gh) = r.take("<IHHHHHIHIIB3xHBBhhHH84x")
    if magic != MAGIC:
        raise AsepriteError("not an Aseprite file (bad magic number)")
    if depth not in (8, 16, 32):
        raise AsepriteError(f"unsupported color depth {depth}")
    palette: list[tuple[int, int, int, int]] = [(0, 0, 0, 255)] * 256
    pal_size = 0
    layers: list[_RawLayer] = []
    tags: list[AseTag] = []
    pivot: tuple[int, int] | None = None
    durations: list[int] = []
    # Cels as (x, y, opacity, rgba), or (source frame, x, y, opacity) for a linked cel: like Aseprite, a link placed
    # or faded differently from its source becomes a copy at its own position.
    cels: list[dict[int, tuple[Any, ...]]] = []
    for _ in range(n_frames):
        start = r.pos
        f_size, f_magic, old_n, dur, new_n = r.take("<IHHH2xI")
        if f_magic != FRAME_MAGIC:
            raise AsepriteError("bad frame magic number")
        durations.append(dur or 100)
        frame: dict[int, tuple[Any, ...]] = {}
        for _c in range(new_n or old_n):
            c_size, c_kind = r.take("<IH")
            if c_size < 6:
                raise AsepriteError("bad chunk size")
            c_end = min(r.pos + c_size - 6, len(data))
            c = _Reader(data, r.pos, c_end)
            if c_kind == CHUNK_LAYER:
                lf, lk, lvl, _dw, _dh, _blend, op = c.take("<6HB3x")
                name = c.string()
                layers.append(_RawLayer(name, lf, lk, lvl, op if flags & HEADER_LAYER_OPACITY else 255))
            elif c_kind == CHUNK_CEL:
                li, x, y, op, ck, _z = c.take("<HhhBHh5x")
                if ck == CEL_LINKED:
                    frame[li] = (c.take("<H")[0], x, y, op)
                elif ck in (CEL_RAW, CEL_COMPRESSED):
                    w, h = c.take("<HH")
                    raw = c.rest()
                    if ck == CEL_COMPRESSED:
                        try:
                            raw = zlib.decompress(raw)
                        except zlib.error as e:
                            raise AsepriteError(f"bad cel compression: {e}") from e
                    bg = li < len(layers) and bool(layers[li].flags & LAYER_BACKGROUND)
                    frame[li] = (x, y, op, _to_rgba(raw, w, h, depth, palette, transparent, bg))
            elif c_kind == CHUNK_PALETTE:
                total, first, last = c.take("<III8x")
                pal_size = min(total, 256)
                if len(palette) < total:
                    palette += [(0, 0, 0, 255)] * (total - len(palette))
                for i in range(first, min(last, first + 0xFFFF) + 1):
                    if c.pos + 6 > c.end:
                        break
                    ef, rr, gg, bb, aa = c.take("<H4B")
                    if ef & 1:
                        c.string()
                    if i < len(palette):
                        palette[i] = (rr, gg, bb, aa)
            elif c_kind == CHUNK_OLD_PALETTE:
                (packets,) = c.take("<H")
                idx = 0
                for _p in range(packets):
                    skip, count = c.take("<BB")
                    idx += skip
                    for _k in range(count or 256):
                        rr, gg, bb = c.take("<3B")
                        if idx < len(palette):
                            palette[idx] = (rr, gg, bb, 255)
                        idx += 1
                pal_size = max(pal_size, min(idx, 256))
            elif c_kind == CHUNK_TAGS:
                (count,) = c.take("<H8x")
                for _t in range(count):
                    a, b, d, rep = c.take("<HHBH6x3Bx")[:4]
                    tags.append(AseTag(c.string(), a, b, rep != 1, d))
            elif c_kind == CHUNK_SLICE:
                n_keys, s_flags, _res = c.take("<III")
                name = c.string()
                for _k in range(n_keys):
                    _fr, sx, sy, _sw, _sh = c.take("<IiiII")
                    if s_flags & 1:
                        c.take("<iiII")
                    if s_flags & 2:
                        ppx, ppy = c.take("<ii")
                        if pivot is None or name.lower() == PIVOT_SLICE:
                            pivot = (sx + ppx, sy + ppy)
            r.pos = c_end
        r.pos = start + f_size
        cels.append(frame)
    if not layers:
        raise AsepriteError("the file has no layers")
    return _compose(width, height, layers, cels, durations, tags, pivot,
                    palette[:pal_size])


def _compose(width: int, height: int, layers: list[_RawLayer], cels: list[dict[int, Any]], durations: list[int],
             tags: list[AseTag], pivot: tuple[int, int] | None,
             palette: list[tuple[int, int, int, int]]) -> AseDoc:
    """Groups fold into their image layers (hidden group = hidden children); tilemap layers are skipped."""
    visible: list[bool] = []
    opacity: list[int] = []
    parents: list[int] = []
    for i, la in enumerate(layers):
        while parents and layers[parents[-1]].level >= la.level:
            parents.pop()
        up = parents[-1] if parents else None
        visible.append(bool(la.flags & LAYER_VISIBLE) and (up is None or visible[up]))
        opacity.append(la.opacity * (opacity[up] if up is not None else 255) // 255)
        if la.kind == 1:
            parents.append(i)
    keep = [i for i, la in enumerate(layers) if la.kind == 0]
    out_layers = [AseLayer(layers[i].name, visible[i], opacity[i]) for i in keep]
    frames: list[list[np.ndarray | None]] = []
    for fi, frame in enumerate(cels):
        row: list[np.ndarray | None] = []
        for li in keep:
            cel, hops = frame.get(li), 0
            place = cel[1:] if cel is not None and len(cel) == 4 and not isinstance(cel[3], np.ndarray) else None
            while cel is not None and not isinstance(cel[3], np.ndarray) and hops < len(cels):
                src = cel[0]
                cel, hops = (cels[src].get(li) if 0 <= src < len(cels) and src != fi else None), hops + 1
            if cel is None or not isinstance(cel[3], np.ndarray):
                row.append(None)
                continue
            x, y, op = place if place is not None else cel[:3]
            px = cel[3]
            canvas = np.zeros((height, width, 4), np.uint8)
            x0, y0 = max(x, 0), max(y, 0)
            x1, y1 = min(x + px.shape[1], width), min(y + px.shape[0], height)
            if x1 > x0 and y1 > y0:
                canvas[y0:y1, x0:x1] = px[y0 - y:y1 - y, x0 - x:x1 - x]
            if op < 255:
                canvas[..., 3] = (canvas[..., 3].astype(np.uint16) * op // 255).astype(np.uint8)
            row.append(canvas)
        frames.append(row)
    return AseDoc(width, height, out_layers, frames, durations, tags, pivot, palette)


def _rgba(path: Path | bytes) -> np.ndarray:
    from PIL import Image

    with Image.open(io.BytesIO(path) if isinstance(path, bytes) else path) as im:
        return np.asarray(im.convert("RGBA"))


def _png(arr: np.ndarray) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(arr, "RGBA").save(buf, "PNG", compress_level=6)
    return buf.getvalue()


def _crop(arr: np.ndarray, x: int, y: int, w: int, h: int) -> np.ndarray:
    out = np.zeros((h, w, 4), np.uint8)
    part = arr[y:y + h, x:x + w]
    out[:part.shape[0], :part.shape[1]] = part
    return out


@dataclass
class _Sheet:
    doc: AseDoc
    # Per Aseprite frame its rect in the PNG that ships next to the JSON.
    rects: list[tuple[int, int, int, int]]
    png: Path | bytes
    png_size: tuple[int, int]


def _from_sheet(src: Source) -> _Sheet | None:
    from studio_core.export.sheet import layout

    assert src.sheet is not None and src.meta is not None
    lay = layout(src.meta, src.clip_hint, directional_rows=src.directional_rows)
    base = _rgba(src.sheet)
    sheets: list[tuple[AseLayer, np.ndarray]] = [(AseLayer("sprite"), base)]
    for name, path in src.paperdoll_layers:
        arr = _rgba(path)
        if arr.shape == base.shape:
            sheets.append((AseLayer(name), arr))
    if src.normal is not None:
        n = _rgba(src.normal)
        if n.shape == base.shape:
            sheets.append((AseLayer("normal", visible=False), n))
    tracks = lay.tracks
    fb = src.meta.get("flipbook")
    fps = float(fb.get("fps") or 8) if isinstance(fb, dict) else 8.0
    cells = [c for t in tracks for c in t.cells] if tracks else list(lay.cells)
    if not cells:
        return None
    fw, fh = max(c.w for c in cells), max(c.h for c in cells)
    frames, durations, rects, tags = [], [], [], []
    for t in tracks or []:
        start = len(frames)
        for c in t.cells:
            frames.append([_crop(arr, c.x, c.y, fw, fh) for _, arr in sheets])
            durations.append(round(1000 / max(t.fps, 0.1)))
            rects.append((c.x, c.y, fw, fh))
        tags.append(AseTag(t.name, start, len(frames) - 1, t.loop))
    if not tracks:
        for c in cells:
            frames.append([_crop(arr, c.x, c.y, fw, fh) for _, arr in sheets])
            durations.append(round(1000 / fps))
            rects.append((c.x, c.y, fw, fh))
    if len(frames) > WORD_MAX:
        return None
    layers = [la for la, _ in sheets]
    pal = palette_of([base])
    pivot = (int(round(lay.pivot[0])), int(round(lay.pivot[1])))
    doc = AseDoc(fw, fh, layers, frames, durations, tags, pivot, pal)
    png: Path | bytes = src.sheet
    if len(sheets) > 1 and any(la.visible for la in layers[1:]):
        comp = base
        for la, arr in sheets[1:]:
            if la.visible:
                comp = over(comp, arr)
        png = _png(comp)
    return _Sheet(doc, rects, png, (base.shape[1], base.shape[0]))


def _from_layers(src: Source) -> _Sheet | None:
    w, h = src.image_layers_size
    if not src.image_layers or not 0 < w <= WORD_MAX or not 0 < h <= WORD_MAX:
        return None
    arrs = [(name, _crop(_rgba(p), 0, 0, w, h)) for name, p in src.image_layers]
    comp = np.zeros((h, w, 4), np.uint8)
    for _, a in arrs:
        comp = over(comp, a)
    doc = AseDoc(w, h, [AseLayer(n) for n, _ in arrs], [[a for _, a in arrs]], [100], [], None,
                 palette_of([comp]))
    return _Sheet(doc, [(0, 0, w, h)], _png(comp), (w, h))


def sheet_json(doc: AseDoc, rects: list[tuple[int, int, int, int]], image: str, size: tuple[int, int],
               title: str) -> dict[str, Any]:
    """Aseprite's `--format json-array` layout: frames + meta.frameTags / layers / slices."""
    frames = [{"filename": f"{title} {i}.aseprite", "frame": {"x": x, "y": y, "w": w, "h": h}, "rotated": False,
               "trimmed": False, "spriteSourceSize": {"x": 0, "y": 0, "w": w, "h": h},
               "sourceSize": {"w": w, "h": h}, "duration": d}
              for i, ((x, y, w, h), d) in enumerate(zip(rects, doc.durations))]
    tags = []
    for t in doc.tags:
        tag: dict[str, Any] = {"name": t.name, "from": t.start, "to": t.end,
                               "direction": ("forward", "reverse", "pingpong", "pingpong_reverse")[t.direction],
                               "color": "#000000ff"}
        if not t.loop:
            tag["repeat"] = "1"
        tags.append(tag)
    meta: dict[str, Any] = {
        "app": JSON_APP, "version": "1.3", "image": image, "format": "RGBA8888",
        "size": {"w": size[0], "h": size[1]}, "scale": "1", "frameTags": tags,
        "layers": [{"name": la.name, "opacity": la.opacity, "blendMode": "normal"} for la in doc.layers],
        "slices": [],
    }
    if doc.pivot is not None:
        meta["slices"].append({"name": PIVOT_SLICE, "color": "#0000ffff", "keys": [
            {"frame": 0, "bounds": {"x": 0, "y": 0, "w": doc.width, "h": doc.height},
             "pivot": {"x": doc.pivot[0], "y": doc.pivot[1]}}]})
    return {"frames": frames, "meta": meta}


def export_files(src: Source, folder: str = "aseprite") -> tuple[Files, str | None]:
    """`<folder>/<asset>.aseprite` + `<asset>.png` + `<asset>.json`, or nothing when the step has no sheet/layers."""
    s = _from_sheet(src) if src.sheet is not None and src.meta is not None else None
    if s is None and src.image_layers:
        s = _from_layers(src)
    if s is None:
        return [], None
    name = src.asset
    doc = sheet_json(s.doc, s.rects, f"{name}.png", s.png_size, name)
    files: Files = [(f"{folder}/{name}.aseprite", write(s.doc)),
                    (f"{folder}/{name}.png", s.png),
                    (f"{folder}/{name}.json", json.dumps(doc, indent=1, ensure_ascii=False).encode())]
    what = (f"{len(s.doc.frames)} khung, {len(s.doc.tags)} tag" if s.doc.tags else f"{len(s.doc.frames)} khung")
    note = (f"- Aseprite: {folder}/{name}.aseprite ({what}, {len(s.doc.layers)} layer, thời lượng khung theo fps "
            f"clip) + {name}.png/.json dạng sheet JSON array của Aseprite (frameTags, layers, slice pivot).")
    return files, note


_DIR = re.compile(r"^(?:(?P<clip>.*?)[ _\-.])?(?P<dir>SW|SE|NW|NE|S|W|N|E)$", re.I)


@dataclass
class Imported:
    """Frames in clip-major, direction, frame order + the anim json op.sheet's pivot input reads."""
    frames: list[np.ndarray]
    names: list[str]
    anim: dict[str, Any]
    source: str


def _clips(tags: list[AseTag], durations: list[int], n_frames: int, fallback: str
           ) -> tuple[list[dict[str, Any]], list[str], list[list[int]]]:
    """(clips, direction labels, per clip × direction its frame indices). Tag names `<clip>_<S|SW|…>` make a
    direction grid when every clip has the same directions and frame count; anything else is one clip per tag."""
    from studio_core.export.sheet import ident

    def fps(seq: list[int]) -> float:
        ms = sum(durations[i] for i in seq) / max(len(seq), 1)
        return round(1000 / ms, 3) if ms > 0 else 10.0

    tags = [t for t in tags if 0 <= t.start <= t.end < n_frames]
    if not tags:
        seq = list(range(n_frames))
        return [{"name": fallback, "frames": n_frames, "fps": fps(seq), "loop": True}], ["S"], [seq]
    parsed = [_DIR.match(t.name.strip()) for t in tags]
    if all(parsed):
        grid: dict[str, dict[str, AseTag]] = {}
        for t, m in zip(tags, parsed):
            assert m is not None
            grid.setdefault(ident(m["clip"] or fallback, fallback), {})[m["dir"].upper()] = t
        dirs = list(next(iter(grid.values())))
        uniform = all(list(g) == dirs or set(g) == set(dirs) for g in grid.values()) and all(
            len({len(g[d].frames()) for d in dirs}) == 1 for g in grid.values()) and \
            sum(len(g) for g in grid.values()) == len(tags)
        if uniform:
            clips, seqs = [], []
            for name, g in grid.items():
                per_dir = [g[d].frames() for d in dirs]
                clips.append({"name": name, "frames": len(per_dir[0]),
                              "fps": fps([i for s in per_dir for i in s]), "loop": g[dirs[0]].loop})
                seqs.extend(per_dir)
            return clips, dirs, seqs
    clips, seqs, seen = [], [], set()
    for t in tags:
        name, k = ident(t.name, fallback), 2
        while name in seen:
            name, k = f"{ident(t.name, fallback)}_{k}", k + 1
        seen.add(name)
        seq = t.frames()
        clips.append({"name": name, "frames": len(seq), "fps": fps(seq), "loop": t.loop})
        seqs.append(seq)
    return clips, ["S"], seqs


def _imported(frames: list[np.ndarray], durations: list[int], tags: list[AseTag], pivot: tuple[int, int] | None,
              fallback: str, source: str) -> Imported:
    from studio_core.ops.presets import COMPASS_ANGLE

    if not frames:
        raise AsepriteError("the sprite has no frames")
    h, w = frames[0].shape[:2]
    clips, dirs, seqs = _clips(tags, durations, len(frames), fallback)
    out, names = [], []
    if len(dirs) == 1 and len(clips) > 1:
        # One clip per tag: op.sheet's clip layout takes clips × directions, so each tag is its own clip.
        for c, seq in zip(clips, seqs):
            out.extend(frames[i] for i in seq)
            names.extend(f"{c['name']}_{k:02d}" for k in range(len(seq)))
    else:
        it = iter(seqs)
        for c in clips:
            for d in dirs:
                seq = next(it)
                out.extend(frames[i] for i in seq)
                names.extend(f"{c['name']}_{d}_{k:02d}" for k in range(len(seq)))
    pv = pivot if pivot is not None and 0 <= pivot[0] <= w and 0 <= pivot[1] <= h else (w // 2, h)
    anim = {"pivot": [int(pv[0]), int(pv[1])], "width": w, "height": h, "clips": clips,
            "angles": [COMPASS_ANGLE[d] for d in dirs], "source": source}
    return Imported(out, names, anim, source)


def import_ase(data: bytes, fallback: str = "anim") -> Imported:
    doc = read(data)
    frames = [doc.flatten(i) for i in range(len(doc.frames))]
    return _imported(frames, doc.durations, doc.tags, doc.pivot, fallback, "aseprite")


def _num(d: Any, *keys: str) -> list[int]:
    if not isinstance(d, dict):
        raise AsepriteError(f"expected an object with {', '.join(keys)}")
    try:
        return [int(d[k]) for k in keys]
    except (KeyError, TypeError, ValueError) as e:
        raise AsepriteError(f"bad frame field: {e}") from e


def json_image_name(doc: Any) -> str:
    meta = doc.get("meta") if isinstance(doc, dict) else None
    return Path(str((meta or {}).get("image") or "")).name


def import_json(doc: Any, sheet: np.ndarray, fallback: str = "anim") -> Imported:
    """Aseprite JSON sheet (array or hash format) + its PNG; trimmed frames go back onto their source canvas."""
    if not isinstance(doc, dict) or "frames" not in doc:
        raise AsepriteError("not an Aseprite JSON sheet (no `frames`)")
    raw = doc["frames"]
    entries = list(raw.values()) if isinstance(raw, dict) else list(raw) if isinstance(raw, list) else []
    if not entries:
        raise AsepriteError("the JSON sheet lists no frames")
    frames, durations = [], []
    sw, sh = None, None
    for e in entries:
        if not isinstance(e, dict):
            raise AsepriteError("bad frame entry")
        if e.get("rotated"):
            raise AsepriteError("rotated frames are not supported (Aseprite never rotates; re-export without it)")
        x, y, w, h = _num(e.get("frame"), "x", "y", "w", "h")
        src = e.get("sourceSize") or {"w": w, "h": h}
        cw, ch = _num(src, "w", "h")
        ox, oy = _num(e.get("spriteSourceSize") or {"x": 0, "y": 0}, "x", "y")
        sw, sh = max(sw or 0, cw), max(sh or 0, ch)
        canvas = np.zeros((ch, cw, 4), np.uint8)
        part = _crop(sheet, x, y, w, h)
        x1, y1 = min(ox + w, cw), min(oy + h, ch)
        if x1 > ox >= 0 and y1 > oy >= 0:
            canvas[oy:y1, ox:x1] = part[:y1 - oy, :x1 - ox]
        frames.append(canvas)
        durations.append(int(e.get("duration") or 100))
    if any(f.shape[:2] != (sh, sw) for f in frames):
        frames = [_crop(f, 0, 0, sw or 1, sh or 1) for f in frames]
    meta = doc.get("meta") or {}
    tags = []
    for t in meta.get("frameTags") or []:
        try:
            d = ("forward", "reverse", "pingpong", "pingpong_reverse").index(str(t.get("direction") or "forward"))
        except ValueError:
            d = DIR_FORWARD
        rep = str(t.get("repeat") or "0")
        tags.append(AseTag(str(t.get("name") or "tag"), int(t["from"]), int(t["to"]), rep in ("0", ""), d))
    pivot = None
    for s in meta.get("slices") or []:
        keys = s.get("keys") or []
        if keys and isinstance(keys[0].get("pivot"), dict) and isinstance(keys[0].get("bounds"), dict):
            b, p = keys[0]["bounds"], keys[0]["pivot"]
            cand = (int(b.get("x", 0)) + int(p.get("x", 0)), int(b.get("y", 0)) + int(p.get("y", 0)))
            if pivot is None or str(s.get("name") or "").lower() == PIVOT_SLICE:
                pivot = cand
    return _imported(frames, durations, tags, pivot, fallback, "json")
