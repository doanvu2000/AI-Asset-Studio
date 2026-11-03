"""Android / Jetpack Compose exports for the 2D kinds beyond sprites and UI kits: animated tiles, blob-47 autotile (both
with a tile-map JSON), particle VFX, weather, UI screens, minimap, card sets. Images + JSON go to `assets/` (read through
AssetManager), generated Kotlin to `kotlin/`; the shared runtime lives in android_fx_kotlin.py."""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from PIL import Image

from studio_core.export.android_fx_kotlin import FX_FILE, FX_KOTLIN
from studio_core.export.sheet import ident
from studio_core.export.source import Files, Source

DEMO_FIELD = (8, 5)
DEMO_BLOB = (12, 8)
HEX6 = re.compile(r"[0-9a-fA-F]{6}")

FxResult = tuple[Files, list[str]]


def kt_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("\n", "\\n") + '"'


def pascal(text: str, fallback: str = "Asset") -> str:
    toks = [t for t in "".join(c if c.isalnum() and c.isascii() else " " for c in text).split() if t]
    out = "".join(t[0].upper() + t[1:] for t in toks) or fallback
    return f"A{out}" if out[0].isdigit() else out


def _name(src: Source) -> str:
    return ident(src.asset, "asset")


def _runtime(package: str) -> tuple[str, bytes]:
    return FX_FILE, FX_KOTLIN.replace("@PKG@", package).encode()


def _unique(raw: list[str], fallback: str) -> list[str]:
    seen: set[str] = set()
    out = []
    for r in raw:
        base, n = ident(r, fallback), 2
        name = base
        while name.lower() in seen:
            name, n = f"{base}_{n}", n + 1
        seen.add(name.lower())
        out.append(name)
    return out


def _json(doc: object) -> bytes:
    return json.dumps(doc, indent=1, ensure_ascii=False, default=str).encode()


def _blob_grid(tiles: list[dict[str, Any]], w: int, h: int, cols: int) -> list[list[int]]:
    from studio_core.ops import autotile as at

    by_mask = {e["mask"]: e for e in tiles}
    cells = at.random_cells(w, h, 1)
    return [[(e := by_mask[at.mask_at(cells, y, x) if cells[y, x] else None])["row"] * cols + e["col"]
             for x in range(w)] for y in range(h)]


def tile_anim_files(src: Source, package: str) -> FxResult:
    assert src.tile_anim is not None and src.tile_anim_sheet is not None
    doc, a = src.tile_anim, _name(src)
    t, n, fps = int(doc["tile_size"]), int(doc["frames"]), float(doc["fps"])
    dx, dy = doc["layout"]["frame_offset"]
    cols, rows = int(doc["layout"]["cols"]), int(doc["layout"]["rows"])
    blob = doc.get("mode") == "blob47"
    if blob:
        w, h = DEMO_BLOB
        grid = _blob_grid(doc["autotile"]["tiles"], w, h, cols)
    else:
        w, h = DEMO_FIELD
        grid = [[0] * w for _ in range(h)]
    sheet = f"{a}_tile_anim.png"
    tile_map = {"tile": t, "width": w, "height": h, "image": sheet, "cols": cols, "rows": rows,
                "pixel": bool(doc.get("pixel")),
                "animation": {"frames": n, "fps": fps, "step": int(dy) * cols + int(dx)}, "tiles": grid}
    files: Files = [(f"assets/tiles/{sheet}", src.tile_anim_sheet),
                    (f"assets/tiles/{a}_tile_anim.json", _json({**doc, "sheet": sheet})),
                    (f"assets/tiles/{a}_tile_anim_map.json", _json(tile_map)), _runtime(package)]
    note = (f"- assets/tiles/{a}_tile_anim.png + .json: {n} khung ô {t}px ({fps:g} fps). val anim = loadTileAnimation(context, "
            f"\"{a}\") rồi TileAnimationImage(anim, Modifier.size(64.dp)) (ô khác của bộ autotile: col = c, row = r). "
            f"{a}_tile_anim_map.json: map demo {w}×{h} ô động — loadTileMap(context, \"{a}_tile_anim\") rồi "
            "TileMapView(map, Modifier.fillMaxWidth()).")
    return files, [note]


def autotile_files(src: Source, package: str) -> FxResult:
    assert src.autotile is not None and src.autotile_atlas is not None
    doc, a = src.autotile, _name(src)
    t = int(doc["tile_size"])
    cols, rows = doc["grid"]
    w, h = DEMO_BLOB
    atlas = f"{a}_autotile.png"
    tile_map = {"tile": t, "width": w, "height": h, "image": atlas, "cols": int(cols), "rows": int(rows),
                "pixel": bool(doc.get("pixel")), "animation": None, "tiles": _blob_grid(doc["tiles"], w, h, int(cols))}
    files: Files = [(f"assets/tiles/{atlas}", src.autotile_atlas),
                    (f"assets/tiles/{a}_autotile.json", _json({**doc, "atlas": atlas})),
                    (f"assets/tiles/{a}_autotile_map.json", _json(tile_map)), _runtime(package)]
    names = doc.get("terrains") or ["a", "b"]
    note = (f"- assets/tiles/{a}_autotile.png + .json: 47 ô viền cho \"{names[0]}\" + ô nền \"{names[1]}\" (atlas {cols}×{rows}, "
            f"ô {t}px). val tiles = AutotileSet.load(context, \"{a}\"); AutotileMap(tiles, cells, Modifier.fillMaxWidth()) "
            f"với cells[y][x] = true là {names[0]} (ô tự chọn theo 8 hàng xóm, ngoài map tính là {names[1]}). "
            f"{a}_autotile_map.json: map demo, nạp bằng loadTileMap(context, \"{a}_autotile\").")
    return files, [note]


def vfx_files(src: Source, package: str) -> FxResult:
    doc, a = src.vfx or {}, _name(src)
    base = f"assets/vfx/{a}"
    files: Files = []
    textures: dict[str, dict[str, Any]] = {}
    for key, path in src.vfx_files.items():
        spec = (doc.get("textures") or {}).get(key) or {}
        textures[key] = {"file": f"{a}_{key}.png", "cols": int(spec.get("cols") or 1), "rows": int(spec.get("rows") or 1)}
        files.append((f"{base}/{a}_{key}.png", path))
    out = {"asset": a, "ppu": src.ppu, "textures": textures,
           "presets": [{k: v for k, v in p.items() if k != "trail"} for p in doc["presets"]]}
    files += [(f"{base}/{a}_vfx.json", _json(out)), _runtime(package)]
    names = ", ".join(p["name"] for p in doc["presets"])
    note = (f"- assets/vfx/{a}/: {len(textures)} texture + {a}_vfx.json ({names}). val vfx = VfxLibrary.load(context, \"{a}\"); "
            "VfxView(vfx, \"explosion\", Modifier.fillMaxSize(), origin = Offset(x, y), onFinished = { ... }) — preset một lần "
            "tự dừng khi xong, preset loop chạy mãi (Composable rời cây thì dừng). Toạ độ hạt tính bằng pixel màn hình, "
            f"1 đơn vị thế giới = {src.ppu:g}px; cộng sáng = BlendMode.Plus. Vệt đạn (trail) của projectile_trail chưa có.")
    return files, [note]


def _weather_emitter(la: dict[str, Any]) -> dict[str, Any]:
    from studio_core.export.weather import _ramp

    em = la["emitter"]
    return {"name": la["name"], "texture": em["texture"], "blend": em["blend"], "delay": 0.0, "burst": 0,
            "rate": em["amount"] / em["lifetime"], "lifetime": [em["lifetime"], em["lifetime"]],
            "speed": list(em["speed"]), "shape": {"type": "point"}, "gravity": 0.0, "drag": 0.0,
            "size": list(em["quad_px"]), "size_curve": [[0, 1], [1, 1]],
            "rotation": [0, 360] if em["random_angle"] else [0, 0], "spin": list(em["spin"]),
            "align": "velocity" if em["align_velocity"] else "billboard", "space": "local", "colors": _ramp(em),
            "box": list(em["box"]), "direction": list(em["direction"]), "spread": em["spread"]}


def weather_files(src: Source, package: str) -> FxResult:
    doc, a = src.weather or {}, _name(src)
    base = f"assets/weather/{a}"
    presets, mists, used = [], [], set()
    for la in doc["layers"]:
        if la["kind"] == "mist" and "mist" in src.weather_files:
            used.add("mist")
            mists.append({"name": la["name"], "texture": "mist", "tint": la["tint"], "density": la["density"],
                          "tiles": la["tiles"], "scroll": la["scroll"]})
        elif la.get("emitter") and la["emitter"]["texture"] in src.weather_files:
            used.add(la["emitter"]["texture"])
            presets.append({"name": la["name"], "loop": True, "duration": float(la["emitter"]["lifetime"]),
                            "emitters": [_weather_emitter(la)]})
    if not presets and not mists:
        return [], []
    files: Files = [(f"{base}/{a}_{k}.png", src.weather_files[k]) for k in sorted(used)]
    out = {"asset": a, "ppu": 1, "area": doc["area"],
           "textures": {k: {"file": f"{a}_{k}.png", "cols": 1, "rows": 1} for k in sorted(used)},
           "presets": presets, "mists": mists}
    files += [(f"{base}/{a}_weather.json", _json(out)), _runtime(package)]
    layers = ", ".join(p["name"] for p in presets + mists)
    w, h = doc["area"]
    note = (f"- assets/weather/{a}/: lớp {layers} ({w}×{h}px). val weather = VfxLibrary.loadWeather(context, \"{a}\"); xếp "
            "chồng trong Box phủ màn: WeatherView(weather, \"rain_light\", Modifier.fillMaxSize()) cho lớp hạt (tự co theo "
            f"bề rộng/{w}), MistView(weather, \"mist\", Modifier.fillMaxSize()) cho sương (cuộn tile/giây). "
            "Loop sheet Textures/*_sheet không xuất — hạt đã mô phỏng trực tiếp.")
    return files, [note]


def minimap_files(src: Source, package: str) -> FxResult:
    doc, a = src.minimap or {}, _name(src)
    base = f"assets/minimap/{a}"
    key = "framed" if "framed" in src.minimap_files else "map"
    img = f"{a}_minimap.png"
    with Image.open(src.minimap_files[key]) as im:
        w, h = im.size
    pois = [(p["type"], p["px"][0] / w, p["px"][1] / h) for p in doc.get("pois") or []]
    cls = pascal(a)
    lines = [f"package {package}", "", f"object {cls}Minimap {{", f"    const val ASSET = {kt_str(f'minimap/{a}/{img}')}", "",
             "    val pois = listOf("]
    lines += [f"        MinimapPoi({kt_str(str(k))}, {x:.4f}f, {y:.4f}f)," for k, x, y in pois]
    lines += ["    )", "}", ""]
    files: Files = [(f"{base}/{img}", src.minimap_files[key]),
                    (f"{base}/{a}_minimap.json", _json({**doc, "asset": a})),
                    (f"kotlin/{a}_minimap.kt", "\n".join(lines).encode()), _runtime(package)]
    note = (f"- assets/minimap/{a}/{img}: MinimapView(asset = {cls}Minimap.ASSET, pois = {cls}Minimap.pois, "
            f"Modifier.size(160.dp)) {{ poi -> ... }} ({len(pois)} điểm, toạ độ 0–1 từ góc trên-trái, kind = poi.kind). "
            "Mask tròn: Modifier.clip(CircleShape).")
    return files, [note]


def cards_files(src: Source, package: str) -> FxResult:
    doc, a = src.cards or {}, _name(src)
    base = f"assets/cards/{a}"
    files: Files = []
    entries = []
    cards = doc.get("cards") or []
    ids = _unique([str(c["id"]) for c in cards], "card")
    for c, cid in zip(cards, ids):
        front = src.cards_files.get(f"front:{c['id']}")
        if front is None:
            continue
        files.append((f"{base}/{cid}.png", front))
        art = src.cards_files.get(f"art:{c['id']}")
        if art is not None:
            files.append((f"{base}/art_{cid}.png", art))
        entries.append((str(c["id"]), str(c.get("name") or c["id"]), f"cards/{a}/{cid}.png",
                        f"cards/{a}/art_{cid}.png" if art is not None else None))
    if not entries:
        return [], []
    back = src.cards_files.get("back")
    if back is not None:
        files.append((f"{base}/card_back.png", back))
    cls = pascal(a)
    lines = [f"package {package}", "", f"object {cls}Cards {{",
             f"    val back: String? = {kt_str(f'cards/{a}/card_back.png') if back is not None else 'null'}", "",
             "    val all = listOf("]
    lines += [f"        CardEntry({kt_str(i)}, {kt_str(n)}, {kt_str(f)}, {kt_str(r) if r else 'null'})," for i, n, f, r in entries]
    lines += ["    )", "}", ""]
    meta = {**doc, "asset": a, "cards": [{k: v for k, v in c.items() if k != "texts"} for c in cards]}
    files += [(f"{base}/{a}_cards.json", _json(meta)), (f"kotlin/{a}_cards.kt", "\n".join(lines).encode()),
              _runtime(package)]
    note = (f"- assets/cards/{a}/: {len(entries)} thẻ đã in chữ (ảnh {{id}}.png, art_{{id}}.png, card_back.png) + "
            f"{a}_cards.json (dữ liệu: tên, chi phí, công/máu...). {cls}Cards.all là danh sách CardEntry; hiển thị: "
            f"CardFace(entry.front, Modifier.width(180.dp)) hoặc CardFace({cls}Cards.back!!).")
    return files, [note]


INTERACTIVE = ("button", "tab")


class _Screen:
    def __init__(self, src: Source, names: dict[str, str]) -> None:
        self.src, self.doc = src, src.ui_screen or {}
        self.dir = f"screens/{_name(src)}"
        self.names = names
        self.borders = {n: (b, p) for n, p, b, _ in src.ui}
        self.specs: dict[str, str] = {}
        self.sizes: dict[str, tuple[int, int]] = {}
        self.states: dict[str, list[str]] = self.doc.get("sprites") or {}

    def spec(self, sprite: str) -> str | None:
        if sprite not in self.names or sprite not in self.borders:
            return None
        if sprite not in self.specs:
            self.specs[sprite] = f"S{len(self.specs)}"
            with Image.open(self.borders[sprite][1]) as im:
                self.sizes[sprite] = im.size
        return self.specs[sprite]

    def asset(self, sprite: str) -> str | None:
        return f"{self.dir}/{self.names[sprite]}.png" if sprite in self.names else None

    def children(self, parent: str) -> list[dict[str, Any]]:
        return [n for n in self.doc["nodes"] if (n.get("parent") or "") == parent]

    def text(self, n: dict[str, Any]) -> str:
        raw = str(n.get("color", "#ffffff")).lstrip("#")[:6]
        color = raw if HEX6.fullmatch(raw) else "FFFFFF"
        align = n.get("align", "center") if n.get("align") in ("left", "center", "right") else "center"
        return (f"StudioText(texts[{kt_str(n['name'])}] ?: {kt_str(str(n.get('text') or ''))}, Color(0xFF{color}), "
                f"{int(n.get('font_size', 44))}f, {kt_str(align)}, s)")

    def node(self, n: dict[str, Any], pad: str) -> str:
        name, t = n["name"], n["type"]
        x, y, w, h = n["rect"]
        inner = pad + "    "
        sprite = n.get("sprite")
        parts: list[str] = [self.node(k, inner + "    ") for k in self.children(name)]
        if t == "label" and n.get("text"):
            parts.append(self.text(n))
        if t in INTERACTIVE:
            parts += self.button_parts(n, inner)
        if t == "icon" and n.get("icon_sprite") and self.asset(n["icon_sprite"]):
            parts.append(f"StudioImage({kt_str(self.asset(n['icon_sprite']) or '')})")
        if t == "bar" and n.get("fill_sprite"):
            from studio_core.ops.ui_screen import fill_rect

            fx, fy, fw, fh = fill_rect(n)
            fill = self.spec(n["fill_sprite"])
            parts.append(f"StudioPlace({fx}f, {fy}f, {fw}f, {fh}f) {{ StudioSkin({fill or 'null'}, s) {{ }} }}")
        body = ("\n" + inner).join(parts)
        if t in INTERACTIVE:
            normal = self.spec(sprite) if sprite else None
            pressed = self.spec(f"{sprite}_pressed") if sprite and "pressed" in self.states.get(sprite, []) else None
            wrapped = (f"StudioButton({normal or 'null'}, {pressed or 'null'}, s, onTap?.let {{ cb -> {{ cb({kt_str(name)}) }} }}) {{\n"
                       f"{inner}{body}\n{pad}}}")
        else:
            skin = self.spec(sprite) if sprite and t in ("panel", "slot", "bar") else None
            wrapped = f"StudioSkin({skin or 'null'}, s) {{\n{inner}{body}\n{pad}}}"
        return f"StudioPlace({x}f, {y}f, {w}f, {h}f) {{\n{pad}    {wrapped}\n{pad}}}"

    def button_parts(self, n: dict[str, Any], pad: str) -> list[str]:
        out = []
        if n.get("icon_sprite") and n.get("icon_rect") and self.asset(n["icon_sprite"]):
            ix, iy, iw, ih = n["icon_rect"]
            out.append(f"StudioPlace({ix}f, {iy}f, {iw}f, {ih}f) {{ StudioImage({kt_str(self.asset(n['icon_sprite']) or '')}) }}")
        if n.get("text"):
            tx, ty, tw, th = n.get("text_rect") or [0, 0, 1, 1]
            out.append(f"StudioPlace({tx}f, {ty}f, {tw}f, {th}f) {{ {self.text(n)} }}")
        return out


def screen_files(src: Source, package: str) -> FxResult:
    doc, a = src.ui_screen or {}, _name(src)
    ref = doc.get("ref") or [1080, 1920]
    uniq = _unique([n for n, *_ in src.ui], "sprite")
    names = {n: u for (n, *_), u in zip(src.ui, uniq)}
    files: Files = [(f"assets/screens/{a}/{names[n]}.png", p) for n, p, *_ in src.ui]
    scr = _Screen(src, names)
    body = "\n".join(f"        {scr.node(n, '        ')}" for n in scr.children(""))
    bg = doc.get("background")
    bg_arg = f", background = {kt_str(scr.asset(bg) or '')}" if bg and scr.asset(bg) else ""
    cls = f"{pascal(a)}Screen"
    specs = []
    for sprite, ident_ in scr.specs.items():
        b = scr.borders[sprite][0]
        w, h = scr.sizes[sprite]
        specs.append(f"private val {ident_} = NineSliceSpec({kt_str(scr.asset(sprite) or '')}, {b[0]}, {b[1]}, {b[2]}, "
                     f"{b[3]}, {w}, {h})")
    code = "\n".join([
        f"package {package}", "", "import androidx.compose.runtime.Composable", "import androidx.compose.ui.Modifier",
        "import androidx.compose.ui.graphics.Color", "", *specs, "", f"/** Screen \"{' '.join(str(doc.get('screen')).replace('*/', '* /').split())}\": every rect is a "
        f"fraction of its parent, text scales with width / {ref[0]}. [onTap] gets the node name of a pressed button. */",
        "@Composable", f"fun {cls}(", "    modifier: Modifier = Modifier,", "    onTap: ((String) -> Unit)? = null,",
        "    texts: Map<String, String> = emptyMap(),", ") {",
        f"    StudioScreen({float(ref[0])}f, modifier{bg_arg}) {{ s ->", body, "    }", "}", ""])
    files += [(f"kotlin/{a}_screen.kt", code.encode()), _runtime(package)]
    note = (f"- kotlin/{a}_screen.kt: Composable {cls} cho màn \"{doc.get('screen')}\" (BoxWithConstraints + vị trí theo tỉ lệ "
            f"cha, safeDrawingPadding, chữ co theo bề rộng/{ref[0]}, nền 9-slice vẽ bằng Canvas). Dùng: {cls}(onTap = "
            f"{{ node -> ... }}, texts = mapOf(\"Coins\" to \"9,999\")). Ảnh ở assets/screens/{a}/. Chữ dùng font hệ "
            "thống, không bitmap font; nút đổi sprite khi nhấn nếu bộ UI có _pressed.")
    return files, [note]


def kinds(src: Source) -> list[tuple[str, bool, Callable[[Source, str], FxResult]]]:
    return [("tile_anim", bool(src.tile_anim and src.tile_anim_sheet), tile_anim_files),
            ("autotile", bool(src.autotile and src.autotile_atlas), autotile_files),
            ("vfx", bool(src.vfx and src.vfx_files), vfx_files),
            ("weather", bool(src.weather and src.weather_files), weather_files),
            ("ui_screen", bool(src.ui_screen and src.ui), screen_files),
            ("minimap", bool(src.minimap and src.minimap_files), minimap_files),
            ("cards", bool(src.cards and src.cards_files), cards_files)]


README_FX = ("- assets/: chép vào app/src/main/assets/ (ảnh + JSON đọc bằng AssetManager); kotlin/AssetStudioFx.kt là "
             "runtime dùng chung (package khớp namespace app), các kotlin/<asset>_*.kt là mã sinh riêng cho từng asset.")
