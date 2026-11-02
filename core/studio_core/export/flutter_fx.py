"""Flutter / Flame exports for the 2D kinds beyond sprites and UI kits: animated tiles, blob-47 autotile (both with a
.tmx for flame_tiled), particle VFX, weather, UI screens as widgets, minimap, card sets. Each `*_files` returns the
files plus README notes; the shared Dart runtime lives in flutter_fx_dart.py."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from PIL import Image

from studio_core.export.flutter_dart import GEN_HEAD
from studio_core.export.flutter_fx_dart import TILES_DART, TILES_FILE, VFX_DART, VFX_FILE, WIDGETS_DART, WIDGETS_FILE
from studio_core.export.sheet import ident
from studio_core.export.source import Files, Source

DEMO_FIELD = (8, 5)
DEMO_BLOB = (12, 8)


def dart_str(s: str) -> str:
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'").replace("$", "\\$").replace("\n", "\\n") + "'"


def pascal(text: str, fallback: str = "Asset") -> str:
    toks = [t for t in "".join(c if c.isalnum() and c.isascii() else " " for c in text).split() if t]
    out = "".join(t[0].upper() + t[1:] for t in toks) or fallback
    return f"A{out}" if out[0].isdigit() else out


def _name(src: Source) -> str:
    return ident(src.asset, "asset")


def _tmx(width: int, height: int, tile: int, tileset: ET.Element, grid: list[list[int]],
         wang: ET.Element | None = None) -> bytes:
    m = ET.Element("map", {"version": "1.10", "tiledversion": "1.11.0", "orientation": "orthogonal",
                           "renderorder": "right-down", "width": str(width), "height": str(height),
                           "tilewidth": str(tile), "tileheight": str(tile), "infinite": "0", "nextlayerid": "2",
                           "nextobjectid": "1"})
    m.append(tileset)
    if wang is not None:
        tileset.append(wang)
    layer = ET.SubElement(m, "layer", {"id": "1", "name": "tiles", "width": str(width), "height": str(height)})
    data = ET.SubElement(layer, "data", {"encoding": "csv"})
    data.text = "\n" + ",\n".join(",".join(str(g) for g in row) for row in grid) + "\n"
    ET.indent(m, space=" ")
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(m, encoding="utf-8")


def _tileset(name: str, image: str, tile: int, cols: int, rows: int) -> ET.Element:
    ts = ET.Element("tileset", {"firstgid": "1", "name": name, "tilewidth": str(tile), "tileheight": str(tile),
                                "tilecount": str(cols * rows), "columns": str(cols)})
    ET.SubElement(ts, "image", {"source": image, "width": str(cols * tile), "height": str(rows * tile)})
    return ts


def _wangset(doc: dict[str, Any]) -> ET.Element:
    """Terrain set for the Tiled editor: wangid runs top, top-right, ... top-left, 1 = terrain A, 2 = B."""
    ws = ET.Element("wangsets")
    s = ET.SubElement(ws, "wangset", {"name": "terrain", "type": "mixed", "tile": "-1"})
    names = doc.get("terrains") or ["a", "b"]
    for name, color in zip(names, ("#5c9e4c", "#a98054")):
        ET.SubElement(s, "wangcolor", {"name": str(name), "color": color, "tile": "-1", "probability": "1"})
    cols = 8
    for e in doc["tiles"]:
        m = e["mask"]
        wid = ",".join("2" if m is None or not (m >> i) & 1 else "1" for i in range(8))
        ET.SubElement(s, "wangtile", {"tileid": str(e["row"] * cols + e["col"]), "wangid": wid})
    return ws


def _blob_grid(tiles: list[dict[str, Any]], w: int, h: int, cols: int) -> list[list[int]]:
    from studio_core.ops import autotile as at

    by_mask = {e["mask"]: e for e in tiles}
    cells = at.random_cells(w, h, 1)
    out = []
    for y in range(h):
        row = []
        for x in range(w):
            e = by_mask[at.mask_at(cells, y, x) if cells[y, x] else None]
            row.append(e["row"] * cols + e["col"] + 1)
        out.append(row)
    return out


def tile_anim_files(src: Source) -> tuple[Files, list[str]]:
    assert src.tile_anim is not None and src.tile_anim_sheet is not None
    doc, a = src.tile_anim, _name(src)
    t, n, fps = int(doc["tile_size"]), int(doc["frames"]), float(doc["fps"])
    dx, dy = doc["layout"]["frame_offset"]
    cols, rows = int(doc["layout"]["cols"]), int(doc["layout"]["rows"])
    blob = doc.get("mode") == "blob47"
    ms = str(max(1, round(1000 / fps)))
    ts = _tileset(f"{a}_tile_anim", f"{a}_tile_anim.png", t, cols, rows)
    bases = doc["autotile"]["tiles"] if blob else [{"col": 0, "row": 0, "mask": None}]
    for e in bases:
        base = e["row"] * cols + e["col"]
        anim = ET.SubElement(ET.SubElement(ts, "tile", {"id": str(base)}), "animation")
        for k in range(n):
            ET.SubElement(anim, "frame", {"tileid": str(base + k * (dy * cols + dx)), "duration": ms})
    if blob:
        grid = _blob_grid(bases, *DEMO_BLOB, cols)
        w, h = DEMO_BLOB
    else:
        w, h = DEMO_FIELD
        grid = [[1] * w for _ in range(h)]
    doc_out = {**doc, "sheet": f"{a}_tile_anim.png"}
    files: Files = [(f"assets/tiles/{a}_tile_anim.png", src.tile_anim_sheet),
                    (f"assets/tiles/{a}_tile_anim.json", json.dumps(doc_out, indent=1).encode()),
                    (f"assets/tiles/{a}_tile_anim.tmx", _tmx(w, h, t, ts, grid, _wangset(doc["autotile"]) if blob else None)),
                    (TILES_FILE, TILES_DART.encode())]
    note = (f"- assets/tiles/{a}_tile_anim.png + .json: {n} frame ô {t}px ({fps:g} fps). loadTileAnimation('{a}') trả "
            "SpriteAnimation của ô (0, 0); ô khác của bộ autotile: loadTileAnimation('" + a + "', col: c, row: r). "
            f"{a}_tile_anim.tmx: ô động + map demo cho flame_tiled — loadTiledMap('{a}_tile_anim', Vector2.all({t})) "
            "(TiledComponent tự chạy hoạt ảnh)." + (" Bộ 47 ô có wangset terrain để tô trong Tiled." if blob else ""))
    return files, [note]


def autotile_files(src: Source) -> tuple[Files, list[str]]:
    assert src.autotile is not None and src.autotile_atlas is not None
    doc, a = src.autotile, _name(src)
    t = int(doc["tile_size"])
    cols, rows = doc["grid"]
    w, h = DEMO_BLOB
    ts = _tileset(f"{a}_autotile", f"{a}_autotile.png", t, int(cols), int(rows))
    tmx = _tmx(w, h, t, ts, _blob_grid(doc["tiles"], w, h, int(cols)), _wangset(doc))
    doc_out = {k: v for k, v in doc.items() if k != "atlas"} | {"atlas": f"{a}_autotile.png"}
    files: Files = [(f"assets/tiles/{a}_autotile.png", src.autotile_atlas),
                    (f"assets/tiles/{a}_autotile.json", json.dumps(doc_out, indent=1).encode()),
                    (f"assets/tiles/{a}_autotile.tmx", tmx), (TILES_FILE, TILES_DART.encode())]
    names = doc.get("terrains") or ["a", "b"]
    note = (f"- assets/tiles/{a}_autotile.png + .json + .tmx: 47 ô viền cho \"{names[0]}\" + ô nền \"{names[1]}\" (atlas "
            f"{cols}×{rows}, ô {t}px). Code: final tiles = await AutotileSet.load('{a}'); "
            "add(AutotileComponent(tiles, cells)) với cells[y][x] = true là "
            f"{names[0]} (tile tự chọn theo 8 hàng xóm, ngoài map tính là {names[1]}). Tiled: loadTiledMap('{a}_autotile', "
            f"Vector2.all({t})) nạp map demo; mở .tmx trong Tiled có wangset terrain để vẽ map thật.")
    return files, [note]


def vfx_files(src: Source) -> tuple[Files, list[str]]:
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
    files += [(f"{base}/{a}_vfx.json", json.dumps(out, indent=1, ensure_ascii=False).encode()),
              (VFX_FILE, VFX_DART.encode())]
    names = ", ".join(p["name"] for p in doc["presets"])
    note = (f"- assets/vfx/{a}/: {len(textures)} texture + {a}_vfx.json ({names}). final vfx = await VfxLibrary.load('{a}'); "
            "game.add(vfx.spawn('explosion', position: Vector2(x, y))) — preset một lần tự gỡ khi xong, preset loop chạy tới "
            f"khi removeFromParent(); onFinished báo khi xong. 1 đơn vị thế giới = {src.ppu:g}px. Hạt tự mô phỏng bằng "
            "Canvas.drawAtlas (cộng sáng = BlendMode.plus); vệt đạn (trail) của projectile_trail chưa có — hạt world-space "
            "đã để lại vệt.")
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


def weather_files(src: Source) -> tuple[Files, list[str]]:
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
    files += [(f"{base}/{a}_weather.json", json.dumps(out, indent=1, ensure_ascii=False).encode()),
              (VFX_FILE, VFX_DART.encode())]
    layers = ", ".join(p["name"] for p in presets + mists)
    w, h = doc["area"]
    note = (f"- assets/weather/{a}/: lớp {layers} ({w}×{h}px). final weather = await VfxLibrary.loadWeather('{a}'); "
            "gắn vào camera.viewport: viewport.add(weather.spawn('rain_light', prewarm: true)) cho lớp hạt, "
            f"viewport.add(weather.mist('mist', Vector2({w}, {h}))) cho sương (cuộn tile/giây). Loop sheet "
            "Textures/*_sheet không xuất — hạt đã mô phỏng trực tiếp.")
    return files, [note]


def minimap_files(src: Source) -> tuple[Files, list[str]]:
    doc, a = src.minimap or {}, _name(src)
    base = f"assets/minimap/{a}"
    key = "framed" if "framed" in src.minimap_files else "map"
    img = f"{a}_minimap.png"
    with Image.open(src.minimap_files[key]) as im:
        w, h = im.size
    pois = [(p["type"], p["px"][0] / w, p["px"][1] / h) for p in doc.get("pois") or []]
    cls = pascal(a)
    lines = [GEN_HEAD, "import '../asset_studio_widgets.dart';", "",
             f"class {cls}Minimap {{", f"  {cls}Minimap._();", "", f"  static const String asset = {dart_str(f'{base}/{img}')};",
             "  static const List<MinimapPoi> pois = ["]
    lines += [f"    MinimapPoi({dart_str(str(k))}, {x:.4f}, {y:.4f})," for k, x, y in pois]
    lines += ["  ];", "}", ""]
    rel = f"lib/gen/minimap/{a.lower()}_minimap.dart"
    files: Files = [(f"{base}/{img}", src.minimap_files[key]),
                    (f"{base}/{a}_minimap.json", json.dumps({**doc, "asset": a}, indent=1, ensure_ascii=False).encode()),
                    (rel, "\n".join(lines).encode()), (WIDGETS_FILE, WIDGETS_DART.encode())]
    note = (f"- assets/minimap/{a}/{img}: MinimapView(asset: {cls}Minimap.asset, pois: {cls}Minimap.pois, marker: (context, "
            f"poi) => ...) ({len(pois)} điểm, toạ độ 0–1 từ góc trên-trái, kind = poi.kind). Mask tròn: bọc ClipOval.")
    return files, [note]


def cards_files(src: Source) -> tuple[Files, list[str]]:
    doc, a = src.cards or {}, _name(src)
    base = f"assets/cards/{a}"
    files: Files = []
    entries = []
    cards = doc.get("cards") or []
    ids = _unique_ids([str(c["id"]) for c in cards])
    for c, cid in zip(cards, ids):
        front = src.cards_files.get(f"front:{c['id']}")
        if front is None:
            continue
        files.append((f"{base}/{cid}.png", front))
        art = src.cards_files.get(f"art:{c['id']}")
        if art is not None:
            files.append((f"{base}/art_{cid}.png", art))
        entries.append((str(c["id"]), str(c.get("name") or c["id"]), f"{base}/{cid}.png",
                        f"{base}/art_{cid}.png" if art is not None else None))
    if not entries:
        return [], []
    back = src.cards_files.get("back")
    if back is not None:
        files.append((f"{base}/card_back.png", back))
    cls = pascal(a)
    lines = [GEN_HEAD, "import '../asset_studio_widgets.dart';", "", f"class {cls}Cards {{", f"  {cls}Cards._();", ""]
    lines.append(f"  static const String back = {dart_str(f'{base}/card_back.png')};" if back is not None
                 else "  static const String? back = null;")
    lines += ["", "  static const List<CardEntry> all = ["]
    lines += [f"    CardEntry({dart_str(i)}, {dart_str(n)}, {dart_str(f)}, {dart_str(r) if r else 'null'})," for i, n, f, r in entries]
    lines += ["  ];", "}", ""]
    rel = f"lib/gen/cards/{a.lower()}_cards.dart"
    files += [(f"{base}/{a}_cards.json", json.dumps({**doc, "asset": a, "cards": [
        {k: v for k, v in c.items() if k not in ("texts",)} for c in cards]}, indent=1, ensure_ascii=False,
        default=str).encode()), (rel, "\n".join(lines).encode()), (WIDGETS_FILE, WIDGETS_DART.encode())]
    note = (f"- assets/cards/{a}/: {len(entries)} thẻ đã in chữ (ảnh {{id}}.png, art_{{id}}.png, card_back.png) + "
            f"{a}_cards.json (dữ liệu: tên, chi phí, công/máu...). {cls}Cards.all là danh sách CardEntry; hiển thị: "
            f"CardFace(asset: entry.front, width: 180) hoặc CardFace(asset: {cls}Cards.back).")
    return files, [note]


def _unique_ids(raw: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for r in raw:
        base, n = ident(r, "card"), 2
        name = base
        while name.lower() in seen:
            name, n = f"{base}_{n}", n + 1
        seen.add(name.lower())
        out.append(name)
    return out


# ---- ui_screen -> widget -----------------------------------------------------------------------------------------

SCREEN_PLACE = """class _Place extends StatelessWidget {
  const _Place({required this.rect, required this.builder});

  final Rect rect;
  final Widget Function(BuildContext context, Size size) builder;

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, box) {
        final size = Size(box.maxWidth * rect.width, box.maxHeight * rect.height);
        return Stack(
          children: [
            Positioned(
              left: box.maxWidth * rect.left,
              top: box.maxHeight * rect.top,
              width: size.width,
              height: size.height,
              child: builder(context, size),
            ),
          ],
        );
      },
    );
  }
}

class _Button extends StatefulWidget {
  const _Button({
    required this.normal,
    required this.size,
    required this.child,
    this.pressed,
    this.onTap,
  });

  final NineSliceSpec normal;
  final NineSliceSpec? pressed;
  final Size size;
  final Widget child;
  final VoidCallback? onTap;

  @override
  State<_Button> createState() => _ButtonState();
}

class _ButtonState extends State<_Button> {
  bool _down = false;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTapDown: (_) => setState(() => _down = true),
      onTapUp: (_) => setState(() => _down = false),
      onTapCancel: () => setState(() => _down = false),
      onTap: widget.onTap,
      child: NineSliceImage(
        spec: _down ? widget.pressed ?? widget.normal : widget.normal,
        width: widget.size.width,
        height: widget.size.height,
        child: widget.child,
      ),
    );
  }
}
"""

ALIGN = {"left": ("Alignment.centerLeft", "TextAlign.left"), "center": ("Alignment.center", "TextAlign.center"),
         "right": ("Alignment.centerRight", "TextAlign.right")}
INTERACTIVE = ("button", "tab")


class _Screen:
    def __init__(self, src: Source, names: dict[str, str]) -> None:
        self.src, self.doc = src, src.ui_screen or {}
        self.dir = f"assets/screens/{_name(src)}"
        self.names = names
        self.borders = {n: (b, p) for n, p, b, _ in src.ui}
        self.specs: dict[str, str] = {}
        self.sizes: dict[str, tuple[int, int]] = {}
        self.states: dict[str, list[str]] = self.doc.get("sprites") or {}

    def spec(self, sprite: str) -> str | None:
        if sprite not in self.names or sprite not in self.borders:
            return None
        if sprite not in self.specs:
            self.specs[sprite] = f"_s{len(self.specs)}"
            with Image.open(self.borders[sprite][1]) as im:
                self.sizes[sprite] = im.size
        return self.specs[sprite]

    def asset(self, sprite: str) -> str | None:
        return f"{self.dir}/{self.names[sprite]}.png" if sprite in self.names else None

    def children(self, parent: str) -> list[dict[str, Any]]:
        return [n for n in self.doc["nodes"] if (n.get("parent") or "") == parent]

    def text_widget(self, n: dict[str, Any]) -> str:
        color = n.get("color", "#ffffff").lstrip("#")[:6]
        align, talign = ALIGN.get(n.get("align", "center"), ALIGN["center"])
        size = int(n.get("font_size", 44))
        return (f"FittedBox(fit: BoxFit.scaleDown, alignment: {align}, child: Text("
                f"texts[{dart_str(n['name'])}] ?? {dart_str(str(n.get('text') or ''))}, textAlign: {talign}, "
                f"style: TextStyle(color: const Color(0xFF{color}), fontSize: {size} * s, fontWeight: FontWeight.w700, "
                "decoration: TextDecoration.none)))")

    def skin(self, sprite: str | None, inner: str) -> str:
        spec = self.spec(sprite) if sprite else None
        if spec is None:
            return f"SizedBox(width: size.width, height: size.height, child: {inner})"
        return f"NineSliceImage(spec: {spec}, width: size.width, height: size.height, child: {inner})"

    def node(self, n: dict[str, Any]) -> str:
        name, t = n["name"], n["type"]
        x, y, w, h = n["rect"]
        kids = self.children(name)
        inner_nodes = [self.node(k) for k in kids]
        sprite = n.get("sprite")
        content: list[str] = list(inner_nodes)
        if t == "label" and n.get("text"):
            content.append(self.text_widget(n))
        if t in INTERACTIVE:
            content += self.button_parts(n)
        if t == "icon" and n.get("icon_sprite") and self.asset(n["icon_sprite"]):
            content.append(f"Image.asset({dart_str(self.asset(n['icon_sprite']) or '')}, fit: BoxFit.contain)")
        if t == "bar" and n.get("fill_sprite"):
            from studio_core.ops.ui_screen import fill_rect

            fx, fy, fw, fh = fill_rect(n)
            fill = self.skin(n["fill_sprite"], "const SizedBox.shrink()")
            content.append(f"_Place(rect: const Rect.fromLTWH({fx}, {fy}, {fw}, {fh}), builder: (context, size) => {fill})")
        stack = f"Stack(children: [{', '.join(content)}])" if content else "const SizedBox.shrink()"
        if t in INTERACTIVE:
            normal = self.spec(sprite) if sprite else None
            if normal is not None:
                pressed = self.spec(f"{sprite}_pressed") if "pressed" in self.states.get(sprite, []) else None
                body = (f"_Button(normal: {normal}, pressed: {pressed or 'null'}, size: size, "
                        f"onTap: onTap == null ? null : () => onTap!({dart_str(name)}), child: {stack})")
            else:
                body = (f"GestureDetector(behavior: HitTestBehavior.opaque, onTap: onTap == null ? null : () => "
                        f"onTap!({dart_str(name)}), child: SizedBox(width: size.width, height: size.height, child: {stack}))")
        else:
            body = self.skin(sprite if t in ("panel", "slot", "bar") else None, stack)
        return f"_Place(rect: const Rect.fromLTWH({x}, {y}, {w}, {h}), builder: (context, size) => {body})"

    def button_parts(self, n: dict[str, Any]) -> list[str]:
        out = []
        if n.get("icon_sprite") and n.get("icon_rect") and self.asset(n["icon_sprite"]):
            ix, iy, iw, ih = n["icon_rect"]
            out.append(f"_Place(rect: const Rect.fromLTWH({ix}, {iy}, {iw}, {ih}), builder: (context, size) => "
                       f"Image.asset({dart_str(self.asset(n['icon_sprite']) or '')}, fit: BoxFit.contain))")
        if n.get("text"):
            tx, ty, tw, th = n.get("text_rect") or [0, 0, 1, 1]
            out.append(f"_Place(rect: const Rect.fromLTWH({tx}, {ty}, {tw}, {th}), builder: (context, size) => "
                       f"{self.text_widget(n)})")
        return out


def screen_files(src: Source) -> tuple[Files, list[str]]:
    from studio_core.export.flutter_dart import nine_slices_dart

    doc, a = src.ui_screen or {}, _name(src)
    ref = doc.get("ref") or [1080, 1920]
    uniq = _unique_ids([n for n, *_ in src.ui])
    names = {n: u for (n, *_), u in zip(src.ui, uniq)}
    files: Files = [(f"assets/screens/{a}/{names[n]}.png", p) for n, p, *_ in src.ui]
    scr = _Screen(src, names)
    roots = scr.children("")
    body = ",\n          ".join(scr.node(n) for n in roots)
    bg = doc.get("background")
    bg_widget = (f"Positioned.fill(child: Image.asset({dart_str(scr.asset(bg) or '')}, fit: BoxFit.cover)),\n          "
                 if bg and scr.asset(bg) else "")
    cls = f"{pascal(a)}Screen"
    spec_lines = []
    for sprite, ident_ in scr.specs.items():
        b = scr.borders[sprite][0]
        w, h = scr.sizes[sprite]
        spec_lines.append(f"const {ident_} = NineSliceSpec({dart_str(scr.asset(sprite) or '')}, {b[0]}, {b[1]}, {b[2]}, "
                          f"{b[3]}, {w}, {h});")
    code = (GEN_HEAD + "import 'package:flutter/widgets.dart';\n\nimport '../asset_studio_ui.dart';\n\n"
            + "\n".join(spec_lines) + "\n\n" + SCREEN_PLACE + f"""
/// Screen "{doc.get('screen')}": every rect is a fraction of its parent, text scales with the width / {ref[0]}.
/// [onTap] gets the node name of a pressed button; [texts] overrides label text by node name.
class {cls} extends StatelessWidget {{
  const {cls}({{super.key, this.onTap, this.texts = const {{}}}});

  final void Function(String node)? onTap;
  final Map<String, String> texts;

  @override
  Widget build(BuildContext context) {{
    return LayoutBuilder(
      builder: (context, box) {{
        final s = box.maxWidth / {ref[0]};
        return Stack(
          fit: StackFit.expand,
          children: [
          {bg_widget}SafeArea(
            child: Stack(
              children: [
          {body},
              ],
            ),
          ),
          ],
        );
      }},
    );
  }}
}}
""")
    files += [(f"lib/gen/screens/{a.lower()}_screen.dart", code.encode()),
              ("lib/gen/asset_studio_ui.dart", nine_slices_dart([]).encode())]
    note = (f"- lib/gen/screens/{a.lower()}_screen.dart: widget {cls} cho màn \"{doc.get('screen')}\" (Stack + vị trí theo tỉ lệ "
            f"cha, SafeArea, chữ co theo bề rộng/{ref[0]}, nền 9-slice bằng NineSliceImage của asset_studio_ui.dart). "
            f"Dùng: {cls}(onTap: (node) => ..., texts: {{'Coins': '9,999'}}). Ảnh ở assets/screens/{a}/. Cỡ chữ mặc định, "
            "không dùng bitmap font; nút đổi sprite khi nhấn nếu bộ UI có _pressed.")
    return files, [note]
