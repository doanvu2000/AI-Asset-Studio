"""iOS exporter (SwiftUI / SpriteKit): drag AssetStudio.xcassets, Resources/, Fonts/ and Sources/ into an Xcode target."""
from __future__ import annotations

import io
import json
from collections.abc import Callable
from pathlib import Path

from PIL import Image

from studio_core.export.ios_swift import (assets_swift, kit_swift, nine_slices_swift, parallax_swift,
                                          plist_fonts_snippet)
from studio_core.export.sheet import ident, layout
from studio_core.export.source import Files, Source

CATALOG = "AssetStudio.xcassets"
SCALES = (1, 2, 3)
INFO = {"author": "xcode", "version": 1}
FONT_EXT = (".ttf", ".otf")
ICON_SIZE = 1024

README = """{asset} — xuất từ AI Asset Studio cho iOS (SwiftUI / SpriteKit).

1. Kéo AssetStudio.xcassets vào project (hoặc gộp vào catalog có sẵn); Sources/*.swift, Resources/, Fonts/ thêm vào target
   (Add Files, chọn Create groups để file nằm phẳng trong bundle).
2. Xcode 15+ tự sinh symbol từ catalog: Image(.tenAnh). Dự án cũ dùng AssetStudioAssets.tenAnh (tên chuỗi) với Image(_:).
3. Ảnh gốc coi là 3x, thu nhỏ bằng LANCZOS ra 1x/2x. Pixel art: ảnh gốc là 1x, 2x/3x phóng nguyên bội bằng NEAREST
   (thêm .interpolation(.none)).
{notes}"""
NOTE_NINE = """- 9-slice: cap-insets đã nằm trong Contents.json của từng ảnh (tính theo pixel từng scale). SwiftUI:
  NineSliceImage(name: AssetStudioAssets.x, insets: AssetStudioNineSlices.x).frame(width:, height:)."""
NOTE_ANIM = """- Sprite: {atlas}.spriteatlas (SpriteKit: SKTextureAtlas(named: "{atlas}").textureNamed("<tên frame>")) và
  Resources/{anim}.json. SwiftUI: AssetStudioAnimation(clip: "<tên clip>", resource: AssetStudioAssets.<tên>).
  Clip có hậu tố hướng S, SW, W, NW, N, NE, E, SE nếu sheet nhiều hướng. Kit dùng SpriteKit để đọc frame từ atlas."""
NOTE_PARALLAX = """- Parallax: AssetStudioParallax(layers: AssetStudioParallaxSets.x, offset: scroll) — offset nhân hệ số từng lớp
  (0 đứng yên, 1 cuộn theo thế giới); Resources/{asset}_parallax.json giữ cùng dữ liệu."""
NOTE_FONT = """- Font bitmap (BMFont): Fonts/{asset}.fnt + .png + _font.json chỉ để sao chép, SwiftUI không vẽ BMFont sẵn
  (SpriteKit cần thư viện ngoài)."""
NOTE_PLIST = """- Font TTF/OTF: dán Info_plist_fonts.xml vào Info.plist (khoá UIAppFonts), file nằm ở Fonts/."""
NOTE_SPINE = """- Resources/Spine/{asset}/: json + atlas + ảnh trang cho spine-ios; giữ nguyên tên file vì atlas tham chiếu ảnh theo tên."""


def _json(doc: object) -> bytes:
    return json.dumps(doc, indent=2).encode()


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _open(path: Path) -> Image.Image:
    img = Image.open(path)
    return img.convert("RGBA") if img.mode not in ("RGBA", "RGB", "LA", "L") else img


def _namer() -> Callable[..., str]:
    seen: set[str] = set()

    def take(raw: str, fallback: str = "asset") -> str:
        base, n = ident(raw, fallback), 2
        name = base
        while name.lower() in seen:
            name, n = f"{base}_{n}", n + 1
        seen.add(name.lower())
        return name

    return take


def scaled(img: Image.Image, pixel: bool) -> list[tuple[int, Image.Image]]:
    """1x/2x/3x versions: the master is 3x (LANCZOS) unless pixel art or under 3 px (1x master, NEAREST)."""
    w, h = img.size
    up = pixel or min(w, h) < 3
    out = []
    for s in SCALES:
        if up:
            size, flt = (w * s, h * s), Image.NEAREST
        else:
            size, flt = (max(1, round(w * s / 3)), max(1, round(h * s / 3))), Image.LANCZOS
        out.append((s, img if size == (w, h) else img.resize(size, flt)))
    return out


def _pair(a: float, b: float, total: int) -> tuple[int, int]:
    a, b = round(a), round(b)
    room = max(total - 1, 0)
    if a + b > room:
        a = round(room * a / (a + b))
        b = room - a
    return a, b


def cap_insets(border: tuple[int, int, int, int], size: tuple[int, int], k: float) -> tuple[int, int, int, int]:
    """(left, top, right, bottom) at scale factor k, clamped so a 1 px centre always remains."""
    left, right = _pair(border[0] * k, border[2] * k, size[0])
    top, bottom = _pair(border[1] * k, border[3] * k, size[1])
    return left, top, right, bottom


def _resizing(caps: tuple[int, int, int, int], size: tuple[int, int]) -> dict | None:
    left, top, right, bottom = caps
    horiz, vert = bool(left or right), bool(top or bottom)
    if not (horiz or vert):
        return None
    center: dict = {"mode": "stretch"}
    if horiz:
        center["width"] = size[0] - left - right
    if vert:
        center["height"] = size[1] - top - bottom
    mode = "9-part" if horiz and vert else "3-part-horizontal" if horiz else "3-part-vertical"
    return {"mode": mode, "center": center,
            "cap-insets": {"bottom": bottom, "left": left, "right": right, "top": top}}


def imageset(folder: str, name: str, path: Path, pixel: bool, border: tuple[int, int, int, int] = (0, 0, 0, 0),
             ) -> tuple[Files, tuple[int, int, int, int] | None]:
    """`<folder>/<name>.imageset` with @1x/@2x/@3x PNGs; 9-slice borders (master px) become per-scale cap-insets."""
    base = f"{folder}/{name}.imageset"
    img = _open(path)
    up = pixel or min(img.size) < 3
    files: Files = []
    images: list[dict] = []
    caps_1x = None
    for s, pic in scaled(img, pixel):
        fn = f"{name}@{s}x.png"
        files.append((f"{base}/{fn}", _png(pic)))
        entry: dict = {"idiom": "universal", "filename": fn, "scale": f"{s}x"}
        if any(border):
            caps = cap_insets(border, pic.size, s if up else s / 3)
            if rs := _resizing(caps, pic.size):
                entry["resizing"] = rs
            if s == 1:
                caps_1x = caps
        images.append(entry)
    files.append((f"{base}/Contents.json", _json({"images": images, "info": INFO})))
    return files, caps_1x


def _atlas_files(src: Source, name: str, anim_name: str, pixel: bool) -> Files:
    lay = layout(src.meta or {}, src.clip_hint, directional_rows=src.directional_rows)
    assert src.sheet is not None
    sheet = _open(src.sheet)
    base = f"{CATALOG}/{name}.spriteatlas"
    files: Files = [(f"{base}/Contents.json", _json({"info": INFO}))]
    for c in {c.name: c for c in lay.cells}.values():
        fn = f"{c.name}.png"
        files += [(f"{base}/{c.name}.imageset/{fn}", _png(sheet.crop((c.x, c.y, c.x + c.w, c.y + c.h)))),
                  (f"{base}/{c.name}.imageset/Contents.json",
                   _json({"images": [{"idiom": "universal", "filename": fn, "scale": "1x"}], "info": INFO}))]
    clips = [{"name": t.name, "fps": t.fps if t.fps > 0 else 8.0, "loop": t.loop, "frames": [c.name for c in t.cells]}
             for t in lay.tracks]
    if not clips and lay.cells:
        clips = [{"name": ident(src.clip_hint, "anim"), "fps": 8.0, "loop": True, "frames": [c.name for c in lay.cells]}]
    px, py = lay.pivot
    doc = {"atlas": name, "pixel": pixel, "frameSize": [lay.frame_w, lay.frame_h],
           "pivot": {"x": px, "y": py, "anchorX": round(px / lay.frame_w, 5), "anchorY": round(py / lay.frame_h, 5)},
           "clips": clips}
    files.append((f"Resources/{anim_name}.json", _json(doc)))
    return files


def app_icon_files(image_path: Path, variants: bool = False) -> Files:
    """AppIcon.appiconset: one 1024 universal icon (Xcode 14+), opaque; `variants` adds dark and tinted (Xcode 16)."""
    img = Image.open(image_path).convert("RGBA").resize((ICON_SIZE, ICON_SIZE), Image.LANCZOS)
    raw = img.resize((32, 32), Image.BOX).tobytes()
    solid = [p for p in zip(raw[0::4], raw[1::4], raw[2::4], raw[3::4]) if p[3] > 128]
    bg = tuple(round(sum(p[i] for p in solid) / len(solid)) for i in range(3)) if solid else (255, 255, 255)
    flat = Image.new("RGB", img.size, bg)
    flat.paste(img, mask=img.getchannel("A"))
    base = f"{CATALOG}/AppIcon.appiconset"
    entries = [{"filename": "AppIcon.png", "idiom": "universal", "platform": "ios", "size": "1024x1024"}]
    files: Files = [(f"{base}/AppIcon.png", _png(flat))]
    if variants:
        for kind, pic in (("dark", img), ("tinted", flat.convert("L").convert("RGB"))):
            fn = f"AppIcon-{kind}.png"
            files.append((f"{base}/{fn}", _png(pic)))
            entries.append({"appearances": [{"appearance": "luminosity", "value": kind}], "filename": fn,
                            "idiom": "universal", "platform": "ios", "size": "1024x1024"})
    files.append((f"{base}/Contents.json", _json({"images": entries, "info": INFO})))
    return files


def export(src: Source) -> Files:
    a = ident(src.asset, "asset")
    take = _namer()
    files: Files = []
    notes: list[str] = []
    values: list[str] = []
    pixel = src.is_pixel()
    use_anim = False
    nine: list[tuple[str, tuple[int, int, int, int]]] = []
    parallax: list[tuple[str, list[tuple[str, float]]]] = []

    if src.sheet is not None and src.meta is not None:
        atlas, anim = take(a), take(f"{a}_anim")
        files += _atlas_files(src, atlas, anim, pixel)
        values += [atlas, anim]
        notes.append(NOTE_ANIM.format(atlas=atlas, anim=anim))
        use_anim = True

    for raw, p, border, _state in src.ui:
        nm = take(raw, "ui")
        got, caps = imageset(CATALOG, nm, p, pixel, border)
        files += got
        values.append(nm)
        if caps is not None:
            nine.append((nm, caps))
    if nine:
        notes.append(NOTE_NINE)

    if src.parallax:
        layers: list[tuple[str, float]] = []
        for n, p, scroll in src.parallax:
            nm = take(f"{a}_{n}", "layer")
            files += imageset(CATALOG, nm, p, pixel)[0]
            values.append(nm)
            layers.append((nm, float(scroll)))
        parallax.append((a, layers))
        doc = {"size": list(src.parallax_size), "layers": [{"image": n, "scroll": f} for n, f in layers]}
        files.append((f"Resources/{a}_parallax.json", _json(doc)))
        notes.append(NOTE_PARALLAX.format(asset=a))

    if src.font and src.font_atlas:
        from studio_core.ops.bitmap_font import fnt_text

        font = src.font
        doc = {"face": font.get("face", ""), "size": font.get("size"), "lineHeight": font["line_height"],
               "base": font["base"], "chars": font["chars"], "kernings": [list(k) for k in font.get("kernings") or []]}
        files += [(f"Fonts/{a}.png", src.font_atlas), (f"Fonts/{a}.fnt", fnt_text(font, f"{a}.png").encode()),
                  (f"Fonts/{a}_font.json", _json(doc))]
        notes.append(NOTE_FONT.format(asset=a))

    if src.spine_json is not None and src.spine_atlas is not None:
        base = f"Resources/Spine/{a}"
        files += [(f"{base}/{src.spine_json[0]}", src.spine_json[1]), (f"{base}/{src.spine_atlas[0]}", src.spine_atlas[1])]
        files += [(f"{base}/{n}", p) for n, p in src.spine_pages]
        notes.append(NOTE_SPINE.format(asset=a))

    if not files:
        return []
    ttf = sorted({n.split("/", 1)[1] for n, _ in files if n.startswith("Fonts/") and n.lower().endswith(FONT_EXT)})
    if ttf:
        files.append(("Info_plist_fonts.xml", plist_fonts_snippet(ttf).encode()))
        notes.append(NOTE_PLIST)
    if any(n.startswith(CATALOG + "/") for n, _ in files):
        files.append((f"{CATALOG}/Contents.json", _json({"info": INFO})))
    nine_src = nine_slices_swift(nine) if nine else ""
    par_src = parallax_swift(parallax) if parallax else ""
    if use_anim or nine or parallax:
        files.append(("Sources/AssetStudioKit.swift", kit_swift(nine_src, use_anim, par_src).encode()))
    if values:
        files.append(("Sources/AssetStudioAssets.swift", assets_swift(values).encode()))
    files.append(("README_IOS.md", README.format(asset=a, notes="\n".join(notes)).encode()))
    return files
