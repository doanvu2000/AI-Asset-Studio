"""iOS / SwiftUI exports for the 2D kinds beyond sprites and UI kits: animated tiles, blob-47 autotile (both with a
tile-map JSON), particle VFX, weather, UI screens, minimap, card sets. Images + JSON go to `Resources/AssetStudioFx/`
(a folder reference, so sub-paths survive), generated Swift to `Sources/`; the shared runtime is in ios_fx_swift.py.
Data layout and JSON schemas match android_fx.py, so the helpers that only shape data are reused from there."""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from PIL import Image

from studio_core.export import android_fx as base
from studio_core.export.ios_fx_swift import FX_DIR, FX_FILE, FX_SWIFT
from studio_core.export.ios_swift import GEN_HEAD
from studio_core.export.source import Files, Source

FxResult = base.FxResult
MAX_VIEWS = 10
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def sw_str(s: str) -> str:
    out = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return '"' + _CONTROL.sub(lambda m: f"\\u{{{ord(m[0]):x}}}", out) + '"'


def _line(s: object) -> str:
    return re.sub(r"\s+", " ", str(s)).strip()


def _runtime() -> tuple[str, bytes]:
    return FX_FILE, FX_SWIFT.encode()


def _res(kind: str, rel: str) -> str:
    return f"{FX_DIR}/{kind}/{rel}"


def tile_anim_files(src: Source) -> FxResult:
    assert src.tile_anim is not None and src.tile_anim_sheet is not None
    doc, a = src.tile_anim, base._name(src)
    t, n, fps = int(doc["tile_size"]), int(doc["frames"]), float(doc["fps"])
    dx, dy = doc["layout"]["frame_offset"]
    cols, rows = int(doc["layout"]["cols"]), int(doc["layout"]["rows"])
    blob = doc.get("mode") == "blob47"
    if blob:
        w, h = base.DEMO_BLOB
        grid = base._blob_grid(doc["autotile"]["tiles"], w, h, cols)
    else:
        w, h = base.DEMO_FIELD
        grid = [[0] * w for _ in range(h)]
    sheet = f"{a}_tile_anim.png"
    tile_map = {"tile": t, "width": w, "height": h, "image": sheet, "cols": cols, "rows": rows,
                "pixel": bool(doc.get("pixel")),
                "animation": {"frames": n, "fps": fps, "step": int(dy) * cols + int(dx)}, "tiles": grid}
    meta = {**doc, "sheet": sheet, "tile_size": t, "frames": n, "fps": fps}
    files: Files = [(_res("tiles", sheet), src.tile_anim_sheet),
                    (_res("tiles", f"{a}_tile_anim.json"), base._json(meta)),
                    (_res("tiles", f"{a}_tile_anim_map.json"), base._json(tile_map)), _runtime()]
    note = (f"- Resources/AssetStudioFx/tiles/{a}_tile_anim.png + .json: {n} khung ô {t}px ({fps:g} fps). "
            f"let anim = TileAnimation.load(\"{a}\") rồi TileAnimationImage(anim).frame(width: 64, height: 64) "
            f"(ô khác của bộ autotile: col = c, row = r). {a}_tile_anim_map.json: map demo {w}×{h} ô động — "
            f"TileMap.load(\"{a}_tile_anim\") rồi TileMapView(map).")
    return files, [note]


def autotile_files(src: Source) -> FxResult:
    assert src.autotile is not None and src.autotile_atlas is not None
    doc, a = src.autotile, base._name(src)
    t = int(doc["tile_size"])
    cols, rows = doc["grid"]
    w, h = base.DEMO_BLOB
    atlas = f"{a}_autotile.png"
    tile_map = {"tile": t, "width": w, "height": h, "image": atlas, "cols": int(cols), "rows": int(rows),
                "pixel": bool(doc.get("pixel")), "animation": None,
                "tiles": base._blob_grid(doc["tiles"], w, h, int(cols))}
    files: Files = [(_res("tiles", atlas), src.autotile_atlas),
                    (_res("tiles", f"{a}_autotile.json"), base._json({**doc, "atlas": atlas, "tile_size": t})),
                    (_res("tiles", f"{a}_autotile_map.json"), base._json(tile_map)), _runtime()]
    names = doc.get("terrains") or ["a", "b"]
    note = (f"- Resources/AssetStudioFx/tiles/{a}_autotile.png + .json: 47 ô viền cho \"{names[0]}\" + ô nền "
            f"\"{names[1]}\" (atlas {cols}×{rows}, ô {t}px). let tiles = AutotileSet.load(\"{a}\"); "
            f"AutotileMap(tiles, cells: cells) với cells[y][x] = true là {names[0]} (ô tự chọn theo 8 hàng xóm, "
            f"ngoài map tính là {names[1]}). {a}_autotile_map.json: map demo, nạp bằng TileMap.load(\"{a}_autotile\").")
    return files, [note]


def vfx_files(src: Source) -> FxResult:
    doc, a = src.vfx or {}, base._name(src)
    root = f"vfx/{a}"
    files: Files = []
    textures: dict[str, dict[str, Any]] = {}
    for key, path in src.vfx_files.items():
        spec = (doc.get("textures") or {}).get(key) or {}
        textures[key] = {"file": f"{a}_{key}.png", "cols": int(spec.get("cols") or 1), "rows": int(spec.get("rows") or 1)}
        files.append((_res(root, f"{a}_{key}.png"), path))
    out = {"asset": a, "ppu": src.ppu, "textures": textures,
           "presets": [{k: v for k, v in p.items() if k != "trail"} for p in doc["presets"]]}
    files += [(_res(root, f"{a}_vfx.json"), base._json(out)), _runtime()]
    names = ", ".join(p["name"] for p in doc["presets"])
    note = (f"- Resources/AssetStudioFx/vfx/{a}/: {len(textures)} texture + {a}_vfx.json ({names}). "
            f"let vfx = VfxLibrary.load(\"{a}\"); VfxView(vfx, \"explosion\", origin: CGPoint(x: 160, y: 240), "
            "onFinished: { ... }) — preset một lần tự dừng khi xong, preset loop chạy mãi (View rời cây thì dừng). "
            f"Toạ độ hạt tính bằng point màn hình, 1 đơn vị thế giới = {src.ppu:g}pt; cộng sáng = blendMode "
            ".plusLighter. Vệt đạn (trail) của projectile_trail chưa có.")
    return files, [note]


def weather_files(src: Source) -> FxResult:
    doc, a = src.weather or {}, base._name(src)
    root = f"weather/{a}"
    presets, mists, used = [], [], set()
    for la in doc["layers"]:
        if la["kind"] == "mist" and "mist" in src.weather_files:
            used.add("mist")
            mists.append({"name": la["name"], "texture": "mist", "tint": la["tint"], "density": la["density"],
                          "tiles": la["tiles"], "scroll": la["scroll"]})
        elif la.get("emitter") and la["emitter"]["texture"] in src.weather_files:
            used.add(la["emitter"]["texture"])
            presets.append({"name": la["name"], "loop": True, "duration": float(la["emitter"]["lifetime"]),
                            "emitters": [base._weather_emitter(la)]})
    if not presets and not mists:
        return [], []
    files: Files = [(_res(root, f"{a}_{k}.png"), src.weather_files[k]) for k in sorted(used)]
    out = {"asset": a, "ppu": 1, "area": doc["area"],
           "textures": {k: {"file": f"{a}_{k}.png", "cols": 1, "rows": 1} for k in sorted(used)},
           "presets": presets, "mists": mists}
    files += [(_res(root, f"{a}_weather.json"), base._json(out)), _runtime()]
    layers = ", ".join(p["name"] for p in presets + mists)
    w, h = doc["area"]
    note = (f"- Resources/AssetStudioFx/weather/{a}/: lớp {layers} ({w}×{h}pt). let weather = "
            f"VfxLibrary.loadWeather(\"{a}\"); xếp chồng trong ZStack phủ màn: WeatherView(weather, \"rain_light\") cho "
            f"lớp hạt (tự co theo bề rộng/{w}), MistView(weather, \"mist\") cho sương (cuộn tile/giây). "
            "Loop sheet Textures/*_sheet không xuất — hạt đã mô phỏng trực tiếp.")
    return files, [note]


def minimap_files(src: Source) -> FxResult:
    doc, a = src.minimap or {}, base._name(src)
    root = f"minimap/{a}"
    key = "framed" if "framed" in src.minimap_files else "map"
    img = f"{a}_minimap.png"
    with Image.open(src.minimap_files[key]) as im:
        w, h = im.size
    pois = [(str(p.get("type", "poi")), p["px"][0] / w, p["px"][1] / h)
            for p in doc.get("pois") or [] if isinstance(p, dict) and len(p.get("px") or ()) >= 2]
    cls = base.pascal(a)
    lines = [GEN_HEAD + "import CoreGraphics", "", f"enum {cls}Minimap {{",
             f"    static let asset = {sw_str(f'minimap/{a}/{img}')}", "", "    static let pois: [MinimapPoi] = ["]
    lines += [f"        MinimapPoi({sw_str(k)}, {x:.4f}, {y:.4f})," for k, x, y in pois]
    lines += ["    ]", "}", ""]
    files: Files = [(_res(root, img), src.minimap_files[key]),
                    (_res(root, f"{a}_minimap.json"), base._json({**doc, "asset": a})),
                    (f"Sources/{a}_minimap.swift", "\n".join(lines).encode()), _runtime()]
    note = (f"- Resources/AssetStudioFx/minimap/{a}/{img}: MinimapView(asset: {cls}Minimap.asset, pois: "
            f"{cls}Minimap.pois) {{ poi in ... }}.frame(width: 160, height: 160) ({len(pois)} điểm, toạ độ 0–1 từ góc "
            "trên-trái, kind = poi.kind). Mask tròn: .clipShape(Circle()).")
    return files, [note]


def cards_files(src: Source) -> FxResult:
    doc, a = src.cards or {}, base._name(src)
    root = f"cards/{a}"
    files: Files = []
    entries = []
    cards = doc.get("cards") or []
    ids = base._unique([str(c["id"]) for c in cards], "card")
    for c, cid in zip(cards, ids):
        front = src.cards_files.get(f"front:{c['id']}")
        if front is None:
            continue
        files.append((_res(root, f"{cid}.png"), front))
        art = src.cards_files.get(f"art:{c['id']}")
        if art is not None:
            files.append((_res(root, f"art_{cid}.png"), art))
        entries.append((str(c["id"]), str(c.get("name") or c["id"]), f"cards/{a}/{cid}.png",
                        f"cards/{a}/art_{cid}.png" if art is not None else None))
    if not entries:
        return [], []
    back = src.cards_files.get("back")
    if back is not None:
        files.append((_res(root, "card_back.png"), back))
    cls = base.pascal(a)
    lines = [GEN_HEAD.rstrip("\n"), "", f"enum {cls}Cards {{",
             f"    static let back: String? = {sw_str(f'cards/{a}/card_back.png') if back is not None else 'nil'}", "",
             "    static let all: [CardEntry] = ["]
    lines += [f"        CardEntry({sw_str(i)}, {sw_str(n)}, {sw_str(f)}, {sw_str(r) if r else 'nil'})," for i, n, f, r in entries]
    lines += ["    ]", "}", ""]
    meta = {**doc, "asset": a, "cards": [{k: v for k, v in c.items() if k != "texts"} for c in cards]}
    files += [(_res(root, f"{a}_cards.json"), base._json(meta)), (f"Sources/{a}_cards.swift", "\n".join(lines).encode()),
              _runtime()]
    note = (f"- Resources/AssetStudioFx/cards/{a}/: {len(entries)} thẻ đã in chữ (ảnh {{id}}.png, art_{{id}}.png, "
            f"card_back.png) + {a}_cards.json (dữ liệu: tên, chi phí, công/máu...). {cls}Cards.all là danh sách "
            f"CardEntry; hiển thị: CardFace(entry.front).frame(width: 180) hoặc CardFace({cls}Cards.back!).")
    return files, [note]


def _grouped(parts: list[str], pad: str) -> list[str]:
    """ViewBuilder takes at most 10 siblings, so wider levels are nested in Groups (transparent in a ZStack)."""
    while len(parts) > MAX_VIEWS:
        parts = [f"Group {{\n{pad}    " + f"\n{pad}    ".join(parts[i:i + MAX_VIEWS]) + f"\n{pad}}}"
                 for i in range(0, len(parts), MAX_VIEWS)]
    return parts


class _Screen(base._Screen):
    def spec(self, sprite: str) -> str | None:
        got = super().spec(sprite)
        return f"Self.{got.lower()}" if got else None

    def text(self, n: dict[str, Any]) -> str:
        raw = str(n.get("color", "#ffffff")).lstrip("#")[:6]
        color = raw if base.HEX6.fullmatch(raw) else "FFFFFF"
        align = n.get("align", "center") if n.get("align") in ("left", "center", "right") else "center"
        return (f"StudioText(texts[{sw_str(n['name'])}] ?? {sw_str(str(n.get('text') or ''))}, "
                f"color: Color(studioHex: 0x{color}), size: {int(n.get('font_size', 44))}, align: {sw_str(align)}, "
                "scale: s)")

    def node(self, n: dict[str, Any], pad: str) -> str:
        name, t = n["name"], n["type"]
        x, y, w, h = n["rect"]
        inner = pad + "    "
        sprite = n.get("sprite")
        parts: list[str] = [self.node(k, inner + "    ") for k in self.children(name)]
        if t == "label" and n.get("text"):
            parts.append(self.text(n))
        if t in base.INTERACTIVE:
            parts += self.button_parts(n, inner)
        if t == "icon" and n.get("icon_sprite") and self.asset(n["icon_sprite"]):
            parts.append(f"StudioImage({sw_str(self.asset(n['icon_sprite']) or '')})")
        if t == "bar" and n.get("fill_sprite"):
            from studio_core.ops.ui_screen import fill_rect

            fx, fy, fw, fh = fill_rect(n)
            fill = self.spec(n["fill_sprite"])
            parts.append(f"StudioPlace({fx}, {fy}, {fw}, {fh}) {{ StudioSkin({fill or 'nil'}, s) {{ }} }}")
        body = ("\n" + inner).join(_grouped(parts, inner))
        if t in base.INTERACTIVE:
            normal = self.spec(sprite) if sprite else None
            pressed = self.spec(f"{sprite}_pressed") if sprite and "pressed" in self.states.get(sprite, []) else None
            wrapped = (f"StudioButton(normal: {normal or 'nil'}, pressed: {pressed or 'nil'}, scale: s, "
                       f"onTap: {{ onTap?({sw_str(name)}) }}) {{\n{inner}{body}\n{pad}}}")
        else:
            skin = self.spec(sprite) if sprite and t in ("panel", "slot", "bar") else None
            wrapped = f"StudioSkin({skin or 'nil'}, s) {{\n{inner}{body}\n{pad}}}"
        return f"StudioPlace({x}, {y}, {w}, {h}) {{\n{pad}    {wrapped}\n{pad}}}"

    def button_parts(self, n: dict[str, Any], pad: str) -> list[str]:
        out = []
        if n.get("icon_sprite") and n.get("icon_rect") and self.asset(n["icon_sprite"]):
            ix, iy, iw, ih = n["icon_rect"]
            out.append(f"StudioPlace({ix}, {iy}, {iw}, {ih}) {{ StudioImage({sw_str(self.asset(n['icon_sprite']) or '')}) }}")
        if n.get("text"):
            tx, ty, tw, th = n.get("text_rect") or [0, 0, 1, 1]
            out.append(f"StudioPlace({tx}, {ty}, {tw}, {th}) {{ {self.text(n)} }}")
        return out


def screen_files(src: Source) -> FxResult:
    doc, a = src.ui_screen or {}, base._name(src)
    ref = doc.get("ref") or [1080, 1920]
    uniq = base._unique([n for n, *_ in src.ui], "sprite")
    names = {n: u for (n, *_), u in zip(src.ui, uniq)}
    files: Files = [(_res(f"screens/{a}", f"{names[n]}.png"), p) for n, p, *_ in src.ui]
    scr = _Screen(src, names)
    body = "\n".join(f"            {n}" for n in _grouped([scr.node(n, "            ") for n in scr.children("")],
                                                         "            "))
    bg = doc.get("background")
    bg_arg = f", background: {sw_str(scr.asset(bg) or '')}" if bg and scr.asset(bg) else ""
    cls = f"{base.pascal(a)}Screen"
    specs = []
    for sprite, ident_ in scr.specs.items():
        b = scr.borders[sprite][0]
        specs.append(f"    private static let {ident_.lower()} = NineSliceSpec({sw_str(scr.asset(sprite) or '')}, "
                     f"left: {b[0]}, top: {b[1]}, right: {b[2]}, bottom: {b[3]})")
    code = "\n".join([
        GEN_HEAD + "import SwiftUI", "",
        f"/// Screen {sw_str(_line(doc.get('screen')))}: every rect is a fraction of its parent, text scales with width / "
        f"{float(ref[0]):g}. onTap gets the node name of a pressed button.",
        f"struct {cls}: View {{", "    var onTap: ((String) -> Void)?", "    var texts: [String: String] = [:]", "",
        *specs, *([""] if specs else []), "    var body: some View {",
        f"        StudioScreen(refWidth: {float(ref[0])}{bg_arg}) {{ s in", body, "        }", "    }", "}", ""])
    files += [(f"Sources/{a}_screen.swift", code.encode()), _runtime()]
    note = (f"- Sources/{a}_screen.swift: View {cls} cho màn \"{_line(doc.get('screen'))}\" (GeometryReader + vị trí "
            f"theo tỉ lệ cha, tôn trọng safe area, chữ co theo bề rộng/{float(ref[0]):g}, nền 9-slice bằng "
            f"resizable(capInsets:)). Dùng: {cls}(onTap: {{ node in ... }}, texts: [\"Coins\": \"9,999\"]). Ảnh ở "
            f"Resources/AssetStudioFx/screens/{a}/. Chữ dùng font hệ thống, không bitmap font; nút đổi sprite khi nhấn "
            "nếu bộ UI có _pressed.")
    return files, [note]


def kinds(src: Source) -> list[tuple[str, bool, Callable[[Source], FxResult]]]:
    return [("tile_anim", bool(src.tile_anim and src.tile_anim_sheet), tile_anim_files),
            ("autotile", bool(src.autotile and src.autotile_atlas), autotile_files),
            ("vfx", bool(src.vfx and src.vfx_files), vfx_files),
            ("weather", bool(src.weather and src.weather_files), weather_files),
            ("ui_screen", bool(src.ui_screen and src.ui), screen_files),
            ("minimap", bool(src.minimap and src.minimap_files), minimap_files),
            ("cards", bool(src.cards and src.cards_files), cards_files)]


def fx(src: Source) -> tuple[Files, list[str]]:
    files: Files = []
    notes: list[str] = []
    seen: set[str] = set()
    for _label, present, build in kinds(src):
        if not present:
            continue
        got, note = build(src)
        if not got:
            continue
        files += [f for f in got if f[0] not in seen]
        seen |= {f[0] for f in got}
        notes += note
    return files, ([README_FX, *notes] if notes else [])


README_FX = ("- Resources/AssetStudioFx/: kéo thư mục vào Xcode bằng \"Create folder references\" (thư mục xanh, giữ cấu trúc "
             "con); Sources/AssetStudioFx.swift là runtime dùng chung (SwiftUI Canvas + TimelineView, iOS 15+), các "
             "Sources/<asset>_*.swift là mã sinh riêng cho từng asset.")
