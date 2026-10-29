"""Texture atlas across assets for mobile: alpha-trimmed sprites MaxRects-packed into page(s) with padding + edge
extrude, pivots carried through the trim. JSON (TexturePacker "hash" layout + pivot), Unity pre-sliced pages
(Sprite Mode Multiple, rect + pivot per sprite in the .meta), Godot AtlasTexture .tres per sprite."""
from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from PIL import Image

from studio_core.export.sheet import ident, layout
from studio_core.export.source import Files, Source

if TYPE_CHECKING:
    from studio_core.contracts import BlobStore, StepRow

MAX_PAGES = 64


class AtlasError(ValueError):
    pass


@dataclass(frozen=True)
class AtlasOptions:
    max_size: int = 2048
    padding: int = 2
    extrude: int = 1
    pot: bool = True
    trim: bool = True
    # Neither Unity sprite rects nor Godot AtlasTexture can sample a rotated region, so engine exports force it off.
    rotate: bool = False


@dataclass
class Sprite:
    name: str
    image: Image.Image
    # Pixels from the top-left of the untrimmed image.
    pivot: tuple[float, float]
    # 9-slice (left, top, right, bottom): such sprites are never trimmed so the border stays on the art.
    border: tuple[int, int, int, int] | None = None
    shapes: list[list[tuple[float, float]]] = field(default_factory=list)
    pixel: bool = False


@dataclass
class Frame:
    sprite: Sprite
    page: int
    x: int
    y: int
    # Trim rect inside the source image.
    tx: int
    ty: int
    w: int
    h: int
    rotated: bool = False
    alias_of: str | None = None

    @property
    def source_size(self) -> tuple[int, int]:
        return self.sprite.image.size

    def pivot_norm(self) -> tuple[float, float]:
        """Pivot as a fraction of the trimmed rect, origin bottom-left (Unity); may fall outside 0..1."""
        px, py = self.sprite.pivot
        return round((px - self.tx) / self.w, 5), round(1 - (py - self.ty) / self.h, 5)


@dataclass
class Atlas:
    name: str
    pages: list[Image.Image]
    frames: list[Frame]
    pixel: bool
    stats: dict[str, Any]
    png: list[bytes] = field(default_factory=list)


class _MaxRects:
    """Best-short-side-fit MaxRects (Jukka Jylänki's survey)."""

    def __init__(self, w: int, h: int) -> None:
        self.free: list[tuple[int, int, int, int]] = [(0, 0, w, h)]
        self.right = 0
        self.bottom = 0

    def insert(self, w: int, h: int, rotate: bool) -> tuple[int, int, bool] | None:
        best: tuple[tuple[int, int], int, int, int, int, bool] | None = None
        options = [(w, h, False)] + ([(h, w, True)] if rotate and w != h else [])
        for fx, fy, fw, fh in self.free:
            for rw, rh, rot in options:
                if rw <= fw and rh <= fh:
                    key = (min(fw - rw, fh - rh), max(fw - rw, fh - rh))
                    if best is None or key < best[0]:
                        best = (key, fx, fy, rw, rh, rot)
        if best is None:
            return None
        _, x, y, rw, rh, rot = best
        self._place(x, y, rw, rh)
        self.right, self.bottom = max(self.right, x + rw), max(self.bottom, y + rh)
        return x, y, rot

    def _place(self, x: int, y: int, w: int, h: int) -> None:
        out: list[tuple[int, int, int, int]] = []
        for f in self.free:
            fx, fy, fw, fh = f
            if x >= fx + fw or x + w <= fx or y >= fy + fh or y + h <= fy:
                out.append(f)
                continue
            if x > fx:
                out.append((fx, fy, x - fx, fh))
            if x + w < fx + fw:
                out.append((x + w, fy, fx + fw - x - w, fh))
            if y > fy:
                out.append((fx, fy, fw, y - fy))
            if y + h < fy + fh:
                out.append((fx, y + h, fw, fy + fh - y - h))
        out = list(dict.fromkeys(out))
        self.free = [a for i, a in enumerate(out) if not any(
            i != j and b[0] <= a[0] and b[1] <= a[1] and a[0] + a[2] <= b[0] + b[2] and a[1] + a[3] <= b[1] + b[3]
            for j, b in enumerate(out))]


def _pot(n: int) -> int:
    p = 1
    while p < n:
        p *= 2
    return p


def _trim_box(img: Image.Image, trim: bool, border: Any) -> tuple[int, int, int, int]:
    w, h = img.size
    if not trim or border:
        return 0, 0, w, h
    box = img.getchannel("A").getbbox()
    if box is None:
        return 0, 0, 1, 1
    return box[0], box[1], box[2] - box[0], box[3] - box[1]


def unique_names(sprites: list[Sprite]) -> None:
    seen: set[str] = set()
    for s in sprites:
        base, n = ident(s.name, "sprite"), 2
        name = base
        while name.lower() in seen:
            name, n = f"{base}_{n}", n + 1
        seen.add(name.lower())
        s.name = name


def pack(sprites: list[Sprite], name: str, opts: AtlasOptions = AtlasOptions(),
         sources: dict[str, tuple[int, int, int]] | None = None) -> Atlas:
    """sources: key → (file bytes, width, height) of the textures the sprites came from, for the before/after report."""
    if not sprites:
        raise AtlasError("no sprites to pack")
    unique_names(sprites)
    pad, ext, size = max(0, opts.padding), max(0, opts.extrude), int(opts.max_size)
    if size < 16 or size > 16384:
        raise AtlasError("max page size must be 16..16384")
    crops: list[tuple[Sprite, tuple[int, int, int, int], np.ndarray, str]] = []
    for s in sprites:
        s.image = s.image.convert("RGBA") if s.image.mode != "RGBA" else s.image
        box = _trim_box(s.image, opts.trim, s.border)
        arr = np.asarray(s.image.crop((box[0], box[1], box[0] + box[2], box[1] + box[3])))
        digest = hashlib.blake2b(arr.tobytes() + f"{box[2]}x{box[3]}".encode(), digest_size=16).hexdigest()
        crops.append((s, box, arr, digest))
    order = sorted(range(len(crops)), key=lambda i: (-max(crops[i][1][2:]), -crops[i][1][2] * crops[i][1][3], i))
    bins: list[_MaxRects] = []
    placed: dict[str, Frame] = {}
    frames: dict[int, Frame] = {}
    content: dict[str, np.ndarray] = {}
    for i in order:
        s, (tx, ty, w, h), arr, digest = crops[i]
        if digest in placed:
            first = placed[digest]
            frames[i] = Frame(s, first.page, first.x, first.y, tx, ty, w, h, first.rotated, first.sprite.name)
            continue
        if max(w, h) + 2 * ext > size:
            raise AtlasError(f"sprite {s.name} ({w}x{h}) does not fit a {size}px page")
        sw, sh = w + 2 * ext + pad, h + 2 * ext + pad
        spot = None
        for p, b in enumerate(bins):
            got = b.insert(sw, sh, opts.rotate)
            if got:
                spot = (p, *got)
                break
        if spot is None:
            if len(bins) >= MAX_PAGES:
                raise AtlasError(f"more than {MAX_PAGES} atlas pages; raise the max page size")
            # Trailing padding may hang past the page edge: it only separates a sprite from its right/bottom neighbour.
            bins.append(_MaxRects(size + pad, size + pad))
            got = bins[-1].insert(sw, sh, opts.rotate)
            assert got is not None
            spot = (len(bins) - 1, *got)
        p, x, y, rot = spot
        f = Frame(s, p, x + ext, y + ext, tx, ty, w, h, rot)
        frames[i] = placed[digest] = f
        content[digest] = arr
    pages: list[Image.Image] = []
    for p, b in enumerate(bins):
        pw, ph = max(1, b.right - pad), max(1, b.bottom - pad)
        if opts.pot:
            pw, ph = _pot(pw), _pot(ph)
        page = np.zeros((min(ph, size), min(pw, size), 4), np.uint8)
        for digest, f in placed.items():
            if f.page != p:
                continue
            arr = np.rot90(content[digest], k=-1) if f.rotated else content[digest]
            blk = np.pad(arr, ((ext, ext), (ext, ext), (0, 0)), mode="edge") if ext else arr
            page[f.y - ext:f.y - ext + blk.shape[0], f.x - ext:f.x - ext + blk.shape[1]] = blk
        pages.append(Image.fromarray(page, "RGBA"))
    ordered = [frames[i] for i in range(len(crops))]
    atlas = Atlas(name=ident(name, "atlas"), pages=pages, frames=ordered,
                  pixel=all(s.pixel for s in sprites), stats={})
    atlas.png = [_png(pg) for pg in pages]
    src = sources or {}
    used = sum(f.w * f.h for f in placed.values())
    area = sum(pg.width * pg.height for pg in pages)
    atlas.stats = {
        "sprites": len(ordered), "unique": len(placed), "pages": len(pages),
        "page_sizes": [list(pg.size) for pg in pages], "fill": round(used / area, 4) if area else 0.0,
        "before": {"textures": len(src), "file_bytes": sum(v[0] for v in src.values()),
                   "gpu_bytes": sum(v[1] * v[2] * 4 for v in src.values())},
        "after": {"textures": len(pages), "file_bytes": sum(len(b) for b in atlas.png), "gpu_bytes": area * 4},
    }
    return atlas


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def page_file(atlas: Atlas, i: int) -> str:
    return f"{atlas.name}.png" if len(atlas.pages) == 1 else f"{atlas.name}_{i}.png"


def atlas_json(atlas: Atlas) -> bytes:
    frames: dict[str, Any] = {}
    for f in atlas.frames:
        sw, sh = f.source_size
        px, py = f.sprite.pivot
        # TexturePacker convention (Pixi/Phaser readers): frame w/h stay unrotated even when the region is turned 90° CW.
        entry: dict[str, Any] = {
            "frame": {"x": f.x, "y": f.y, "w": f.w, "h": f.h}, "rotated": f.rotated,
            "trimmed": (f.tx, f.ty, f.w, f.h) != (0, 0, sw, sh),
            "spriteSourceSize": {"x": f.tx, "y": f.ty, "w": f.w, "h": f.h}, "sourceSize": {"w": sw, "h": sh},
            "pivot": {"x": round(px / sw, 5), "y": round(py / sh, 5)}, "page": f.page,
        }
        if f.sprite.border:
            entry["border"] = dict(zip(("left", "top", "right", "bottom"), f.sprite.border))
        if f.alias_of:
            entry["alias"] = f.alias_of
        frames[f.sprite.name] = entry
    doc = {"frames": frames, "meta": {
        "app": "AI Asset Studio", "format": "RGBA8888", "scale": "1",
        "image": page_file(atlas, 0),
        "pages": [{"image": page_file(atlas, i), "size": {"w": pg.width, "h": pg.height}}
                  for i, pg in enumerate(atlas.pages)],
        "pivot": "fraction of sourceSize, origin top-left", "stats": atlas.stats}}
    return (json.dumps(doc, indent=1, ensure_ascii=False) + "\n").encode()


def _unity_meta(atlas: Atlas, i: int, tex_guid: str, ppu: float, uid: str) -> str:
    from studio_core.export.collider import unity_physics_shape
    from studio_core.export.unity import _border, _sprite_entry, file_id, texture_meta

    page = atlas.pages[i]
    key = f"{uid}:{atlas.name}_{i}"
    entries, table = "", ""
    for f in atlas.frames:
        if f.page != i:
            continue
        shapes = [[(x - f.tx, y - f.ty) for x, y in poly] for poly in f.sprite.shapes]
        entry = _sprite_entry(key, f.sprite.name, f.x, page.height - f.y - f.h, f.w, f.h, f.pivot_norm(),
                              unity_physics_shape(shapes, f.w, f.h))
        if f.sprite.border:
            entry = entry.replace("border: {x: 0, y: 0, z: 0, w: 0}", f"border: {_border(f.sprite.border)}", 1)
        entries += entry
        table += f"      {f.sprite.name}: {file_id('sprite', key, f.sprite.name)}\n"
    meta = texture_meta(f"{atlas.name}_{i}", tex_guid, None, page.size, ppu, atlas.pixel)
    # texture_meta writes a Single sprite for lay=None; switch it to a pre-sliced Multiple sheet with tight meshes.
    for old, new in (("  spriteMode: 1\n", "  spriteMode: 2\n"), ("  spriteMeshType: 1\n", "  spriteMeshType: 0\n"),
                     ("    sprites: []\n", "    sprites:\n" + entries),
                     ("    nameFileIdTable: {}\n", "    nameFileIdTable:\n" + table)):
        if old not in meta:
            raise AtlasError(f"unexpected Unity texture meta layout ({old.strip()})")
        meta = meta.replace(old, new, 1)
    return meta


README_UNITY = """- {name}.png{more}: atlas {n} trang (Sprite Mode Multiple, Mesh Tight), {sprites} sprite đã cắt sẵn, đã bỏ viền trong
  suốt — pivot từng sprite tính lại theo phần cắt nên vị trí đứng giữ nguyên. Kéo sprite từ trang atlas vào scene/Animator
  thay cho sprite lẻ để gộp draw call. {name}.json: toạ độ khung (kiểu TexturePacker) cho code tự đọc."""
README_GODOT = """- {name}.png{more}: atlas {n} trang; mỗi sprite một AtlasTexture `{name}/<sprite>.tres` (region + margin giữ đúng
  kích thước gốc, metadata `pivot` = điểm neo tính từ góc trên trái ảnh gốc). Sprite2D: offset = kích thước/2 - pivot.
  {name}.json: toạ độ khung (kiểu TexturePacker)."""


def _num(v: float) -> str:
    s = f"{float(v):.6g}"
    return s if any(ch in s for ch in ".e") else f"{s}.0"


def godot_tres(atlas: Atlas, f: Frame) -> str:
    sw, sh = f.source_size
    px, py = f.sprite.pivot
    return ('[gd_resource type="AtlasTexture" load_steps=2 format=3]\n\n'
            f'[ext_resource type="Texture2D" path="../{page_file(atlas, f.page)}" id="1_page"]\n\n'
            '[resource]\natlas = ExtResource("1_page")\n'
            f"region = Rect2({f.x}, {f.y}, {f.w}, {f.h})\n"
            f"margin = Rect2({f.tx}, {f.ty}, {sw - f.w}, {sh - f.h})\n"
            "filter_clip = true\n"
            f"metadata/pivot = Vector2({_num(px)}, {_num(py)})\n")


def stats_lines(atlas: Atlas) -> list[str]:
    b, a = atlas.stats["before"], atlas.stats["after"]
    mb = 1024 * 1024
    return [f"- Atlas: {atlas.stats['sprites']} sprite ({atlas.stats['unique']} ảnh khác nhau) từ {b['textures']} texture "
            f"→ {a['textures']} trang, lấp đầy {atlas.stats['fill'] * 100:.0f}%. Tệp PNG {b['file_bytes'] / mb:.2f} MB "
            f"→ {a['file_bytes'] / mb:.2f} MB; RAM GPU (RGBA32) {b['gpu_bytes'] / mb:.2f} MB → {a['gpu_bytes'] / mb:.2f} MB."]


def engine_files(atlas: Atlas, engine: str | None, ppu: float = 100.0, uid: str = "") -> Files:
    if engine and any(f.rotated for f in atlas.frames):
        raise AtlasError("rotated sprites are only supported in the JSON atlas (engine=None)")
    more = f" … {page_file(atlas, len(atlas.pages) - 1)}" if len(atlas.pages) > 1 else ""
    files: Files = [(page_file(atlas, i), atlas.png[i]) for i in range(len(atlas.pages))]
    files.append((f"{atlas.name}.json", atlas_json(atlas)))
    fmt = dict(name=atlas.name, more=more, n=len(atlas.pages), sprites=len(atlas.frames))
    lines: list[str] = []
    if engine == "unity":
        from studio_core.export.unity import guid

        for i in range(len(atlas.pages)):
            rel = page_file(atlas, i)
            files.append((f"{rel}.meta", _unity_meta(atlas, i, guid(uid, atlas.name, rel), ppu, uid).encode()))
        lines.append(README_UNITY.format(**fmt))
    elif engine == "godot":
        files += [(f"{atlas.name}/{f.sprite.name}.tres", godot_tres(atlas, f).encode()) for f in atlas.frames]
        lines.append(README_GODOT.format(**fmt))
        if atlas.pixel:
            lines.append("  Pixel art: đặt Texture Filter = Nearest trên node (hoặc mặc định của project).")
    else:
        lines.append(f"- {atlas.name}.png{more} + {atlas.name}.json: atlas kiểu TexturePacker (JSON hash), pivot là tỉ lệ "
                     "theo ảnh gốc, gốc trên trái.")
    lines += stats_lines(atlas)
    files.append(("README.txt", ("\n".join(lines) + "\n").encode()))
    return files


def _file_size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def _open(p: Path) -> Image.Image:
    from studio_core.ops.image import open_image

    return open_image(p)


def sprites_from_source(src: Source, label: str) -> tuple[list[Sprite], dict[str, tuple[int, int, int]]]:
    """Every standalone 2D image an engine export of `src` would ship as a sprite. Tileable textures, parallax layers,
    autotile/font atlases (their own grids) and 3D are left out."""
    from studio_core.export.collider import for_sheet

    sprites: list[Sprite] = []
    sources: dict[str, tuple[int, int, int]] = {}
    pixel = src.is_pixel()

    def single(name: str, path: Path, border: tuple[int, int, int, int] | None = None) -> None:
        img = _open(path)
        sources[str(path)] = (_file_size(path), *img.size)
        sprites.append(Sprite(f"{label}_{name}", img, (img.width / 2, img.height / 2),
                              border if border and any(border) else None, pixel=pixel))

    if src.sheet is not None and src.meta is not None:
        lay = layout(src.meta, src.clip_hint, directional_rows=src.directional_rows)
        col = for_sheet(src, lay)
        sheet = _open(Path(src.sheet))
        sources[str(src.sheet)] = (_file_size(Path(src.sheet)), *sheet.size)
        for c in lay.cells:
            img = sheet.crop((c.x, c.y, c.x + c.w, c.y + c.h))
            shapes = [list(p) for p in ((col.shapes.get(c.name) if col else None) or [])]
            sprites.append(Sprite(f"{label}_{c.name}", img, lay.pivot, shapes=shapes, pixel=pixel))
    for name, path, border, state in src.ui:
        single(f"{name}_{state}" if state and not name.endswith(state) else name, path, border)
    for tier, path in src.rarity:
        single(tier, path)
    for state, path, flash in src.damage_states:
        single(state, path)
        if flash is not None:
            single(f"{state}_flash", flash)
    for expr, path in src.npc_portraits:
        single(f"portrait_{expr}", path)
    for lang, path in src.localized:
        single(lang, path)
    for name, path in src.image_layers:
        single(name, path)
    return sprites, sources


def sprites_from_step(step: StepRow, siblings: list[StepRow], blobs: BlobStore, label: str, ppu: float = 100.0,
                      pixel: bool | None = None, collider: Any = None
                      ) -> tuple[list[Sprite], dict[str, tuple[int, int, int]]]:
    from studio_core.store.bundle import engine_source
    from studio_core.store.export import safe_filename

    src = engine_source(step, siblings, blobs, ident(label, "asset"), ppu, pixel)
    src.collider = collider
    sprites, sources = sprites_from_source(src, ident(label, "asset"))
    if sprites or src.textures or src.parallax or src.glb or src.autotile or src.font or src.spine_json:
        return sprites, sources
    seen: set[str] = set()
    for refs in step.outputs.values():
        for ref in refs:
            if ref.mime not in ("image/png", "image/webp") or ref.hash in seen:
                continue
            seen.add(ref.hash)
            path = blobs.path(ref.hash)
            img = _open(path)
            sources[ref.hash] = (_file_size(path), *img.size)
            stem = Path(safe_filename(None, ref)).stem
            sprites.append(Sprite(f"{ident(label, 'asset')}_{stem}", img, (img.width / 2, img.height / 2),
                                  pixel=bool(pixel)))
    return sprites, sources

