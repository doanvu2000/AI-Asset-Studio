"""Flutter + Flame exporter: paste the output into a project (assets/, lib/gen/, pubspec snippet)."""
from __future__ import annotations

import io
import json
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from PIL import Image

from studio_core.export.android import APP_ICON_NAMES
from studio_core.export.flutter_dart import asset_paths, assets_dart, flame_dart, nine_slices_dart, pubspec_snippet
from studio_core.export.sheet import ident, layout
from studio_core.export.source import Files, Source

DENSITIES = ((1, ""), (2, "2.0x/"), (3, "3.0x/"))
ICON_SIZE = 1024
ADAPTIVE_SAFE = 66 / 108
ICON_YAML = """flutter_launcher_icons:
  android: true
  ios: true
  image_path: "assets/icon/app_icon.png"
  adaptive_icon_background: "#FFFFFF"
  adaptive_icon_foreground: "assets/icon/app_icon_foreground.png"
  remove_alpha_ios: true
"""

README = """{asset} — xuất từ AI Asset Studio cho Flutter / Flame. Chép các thư mục assets/ và lib/gen/ vào project.

1. Dán nội dung pubspec_assets.yaml vào pubspec.yaml (khối flutter: gộp vào khối có sẵn, dependencies: nếu có thì thêm). Flutter không đệ quy thư mục
   nên mỗi thư mục được liệt kê riêng; thư mục 2.0x/ 3.0x/ tự được nhận qua thư mục cha.
2. Package: {packages}.
3. lib/gen/asset_studio_assets.dart: hằng đường dẫn (AssetStudioAssets.xxx) dùng với Image.asset / rootBundle.
{notes}"""
NOTE_IMAGES = """- assets/images/<tên>.png: ảnh 1x, kèm 2.0x/ và 3.0x/. {scale}"""
NOTE_SCALE = ("Ảnh gốc coi là 3x, thu nhỏ bằng LANCZOS.", "Pixel art: ảnh gốc coi là 1x, 2x/3x phóng nguyên bội bằng NEAREST "
              "(dùng FilterQuality.none).")
NOTE_NINE = """- assets/ui/ + ui_9slice.json + lib/gen/asset_studio_ui.dart: NineSliceImage(spec: AssetStudioNineSlices.xxx,
  width:, height:) kéo giãn giữ góc (Image.asset centerSlice). Flame: NineSliceComponent.load(spec)."""
NOTE_ANIM = """- assets/images/sprites/{asset}.png + .json (TexturePacker hash) + _anim.json: loadAsAnimations(game.images, '{asset}')
  trả Map<String, SpriteAnimation> tên clip (có hậu tố hướng S, SW, W, NW, N, NE, E, SE nếu sheet nhiều hướng);
  loadAnimationAnchor('{asset}') cho Anchor theo chân nhân vật."""
NOTE_PARALLAX = """- assets/images/parallax/{asset}/: loadParallax(game.images, '{asset}', baseVelocity: Vector2(20, 0)) trả
  ParallaxComponent; velocityMultiplier = hệ số cuộn (0 cố định theo camera, 1 theo thế giới)."""
NOTE_FONT = """- assets/fonts/{asset}.fnt + _font.json, assets/images/fonts/{asset}.png: BitmapFont.load(game.images, '{asset}')
  rồi BitmapTextComponent(font, 'text'). Flame không có đọc BMFont sẵn nên helper tự vẽ glyph (có kerning)."""
NOTE_SPINE = """- assets/spine/{asset}/: json + atlas + ảnh trang cho spine_flutter (package spine_flutter của Esoteric): nạp
  bằng SkeletonDrawable / loadSkeleton từ các đường dẫn này; giữ nguyên tên file vì atlas tham chiếu ảnh theo tên."""
NOTE_ICON = """- flutter_launcher_icons.yaml + assets/icon/: thêm flutter_launcher_icons vào dev_dependencies rồi chạy
  `dart run flutter_launcher_icons` (Android adaptive + iOS bỏ kênh alpha). Mỗi project chỉ dùng được 1 icon."""
NOTE_TILED = """- assets/tiles/: {maps} + ảnh tileset nằm cạnh nhau; loadTiledMap('{name}', Vector2.all(16)) (flame_tiled) trả
  TiledComponent, tham số 2 là kích thước ô khi vẽ."""
NOTE_DEMO = """- demo/: project Flutter + Flame chạy được (`cd demo && flutter pub get && flutter run`): nhân vật 8 hướng (bàn phím /
  joystick ảo), camera bám theo, map .tmx, parallax, prop có hitbox, đèn normal map nếu có; `flutter test` khởi game + golden."""
NOTE_SVG = """- assets/svg/: ảnh vector, hiển thị bằng package flutter_svg: SvgPicture.asset(AssetStudioAssets.xxx)."""


def _fx_kinds(src: Source) -> list[tuple[bool, Callable[[Source], tuple[Files, list[str]]]]]:
    from studio_core.export import flutter_fx as fx

    return [(bool(src.tile_anim and src.tile_anim_sheet), fx.tile_anim_files),
            (bool(src.autotile and src.autotile_atlas), fx.autotile_files),
            (bool(src.vfx and src.vfx_files), fx.vfx_files),
            (bool(src.weather and src.weather_files), fx.weather_files),
            (bool(src.ui_screen and src.ui), fx.screen_files),
            (bool(src.minimap and src.minimap_files), fx.minimap_files),
            (bool(src.cards and src.cards_files), fx.cards_files)]


def _unique(names: list[str], fallback: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in names:
        base, n = ident(raw, fallback), 2
        name = base
        while name.lower() in seen:
            name, n = f"{base}_{n}", n + 1
        seen.add(name.lower())
        out.append(name)
    return out


def _encode(img: Image.Image, webp: bool) -> bytes:
    buf = io.BytesIO()
    if webp:
        img.save(buf, "WEBP", lossless=True)
    else:
        img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def density_images(path: Path, stem: str, folder: str, pixel: bool, webp: bool) -> Files:
    """1x / 2.0x / 3.0x copies; images under 3 px or pixel art are 1x masters scaled up with NEAREST."""
    img = Image.open(path)
    img = img.convert("RGBA") if img.mode not in ("RGBA", "RGB", "LA", "L") else img
    w, h = img.size
    ext = "webp" if webp else "png"
    up = pixel or min(w, h) < 3
    out: Files = []
    for scale, sub in DENSITIES:
        if up:
            size, flt = (w * scale, h * scale), Image.NEAREST
        else:
            size, flt = (max(1, round(w * scale / 3)), max(1, round(h * scale / 3))), Image.LANCZOS
        pic = img if size == (w, h) else img.resize(size, flt)
        out.append((f"{folder}{sub}{stem}.{ext}", _encode(pic, webp)))
    return out


def icon_files(path: Path) -> Files:
    """Square 1024 master plus an adaptive foreground shrunk into the 66/108 safe zone."""
    img = Image.open(path).convert("RGBA").resize((ICON_SIZE, ICON_SIZE), Image.LANCZOS)
    inner = round(ICON_SIZE * ADAPTIVE_SAFE)
    fg = Image.new("RGBA", img.size, (0, 0, 0, 0))
    fg.paste(img.resize((inner, inner), Image.LANCZOS), ((ICON_SIZE - inner) // 2,) * 2)
    return [("assets/icon/app_icon.png", _encode(img, False)), ("assets/icon/app_icon_foreground.png", _encode(fg, False)),
            ("flutter_launcher_icons.yaml", ICON_YAML.encode())]


def tile_files(src: Source) -> tuple[Files, list[str]]:
    """Tiled maps (.tmx only, not LDtk) with the tileset / prop images they reference, relative paths kept."""
    if src.playable and src.playable_files:
        from studio_core.export.playable_scene import extra_files
    elif src.room_kit and src.room_kit_files:
        from studio_core.export.room_kit import extra_files
    else:
        return [], []
    got = [(f"assets/tiles/{n.removeprefix('Tiled/')}", d) for n, d in extra_files(src) if not n.endswith(".ldtk")]
    return got, [PurePosixPath(n).stem for n, _ in got if n.endswith(".tmx")]


def _json(doc: object) -> bytes:
    return json.dumps(doc, indent=1).encode()


def _sheet_files(src: Source, name: str) -> Files:
    lay = layout(src.meta or {}, src.clip_hint, directional_rows=src.directional_rows)
    stem = f"assets/images/sprites/{name}"
    frames: dict[str, dict] = {}
    for c in lay.cells:
        frames.setdefault(c.name, {
            "frame": {"x": c.x, "y": c.y, "w": c.w, "h": c.h}, "rotated": False, "trimmed": False,
            "spriteSourceSize": {"x": 0, "y": 0, "w": c.w, "h": c.h}, "sourceSize": {"w": c.w, "h": c.h},
            "pivot": {"x": round(lay.pivot[0] / lay.frame_w, 5), "y": round(lay.pivot[1] / lay.frame_h, 5)}})
    atlas = {"frames": frames, "meta": {"app": "AI Asset Studio", "image": f"{name}.png", "format": "RGBA8888",
                                        "size": {"w": lay.width, "h": lay.height}, "scale": "1"}}
    tracks = lay.tracks or []
    clips = [{"name": t.name, "frames": [{"name": c.name, "x": c.x, "y": c.y, "w": c.w, "h": c.h} for c in t.cells],
              "stepTime": round(1 / t.fps, 5) if t.fps > 0 else 0.1, "loop": t.loop} for t in tracks]
    if not clips and lay.cells:
        clips = [{"name": ident(src.clip_hint, "anim"),
                  "frames": [{"name": c.name, "x": c.x, "y": c.y, "w": c.w, "h": c.h} for c in lay.cells],
                  "stepTime": 0.125, "loop": True}]
    px, py = lay.pivot
    anim = {"clips": clips, "frameSize": [lay.frame_w, lay.frame_h],
            "pivot": {"x": px, "y": py, "anchorX": round(px / lay.frame_w, 5), "anchorY": round(py / lay.frame_h, 5)}}
    assert src.sheet is not None
    return [(f"{stem}.png", src.sheet), (f"{stem}.json", _json(atlas)), (f"{stem}_anim.json", _json(anim))]


def _font_doc(font: dict) -> dict:
    return {"face": font.get("face", ""), "size": font.get("size"), "lineHeight": font["line_height"],
            "base": font["base"], "chars": [{k: c[k] for k in ("id", "x", "y", "width", "height", "xoffset", "yoffset",
                                                               "xadvance")} for c in font["chars"]],
            "kernings": [list(k) for k in font.get("kernings") or []]}


def export(src: Source, webp: bool = False, shaders: bool | None = None, demo: bool | None = None) -> Files:
    """`shaders`: None = Shader Pack only when a normal map exists, True = always for a sprite sheet (a normal map is
    generated from alpha when missing), False = never. `demo`: add a runnable Flame project under demo/ (None = for a
    playable scene only)."""
    a = ident(src.asset, "asset")
    files: Files = []
    notes: list[str] = []
    packages = ["flame"]
    use_anim = use_nine = use_parallax = use_font = use_tiled = False
    pixel = src.is_pixel()

    if src.sheet is not None and src.meta is not None:
        files += _sheet_files(src, a)
        notes.append(NOTE_ANIM.format(asset=a))
        use_anim = True
    elif src.playable and src.playable_files:
        from studio_core.export.playable_scene import flutter_player_files

        player, name = flutter_player_files(src)
        if player:
            files += player
            notes.append(NOTE_ANIM.format(asset=name))
            use_anim = True

    if shaders is not False and src.sheet is not None and src.meta is not None and (shaders or src.normal is not None):
        normal = src.normal
        if normal is None:
            from studio_core.export.normal2d import Normal2DOptions, sheet_normal

            normal = sheet_normal(Path(src.sheet), layout(src.meta, src.clip_hint, directional_rows=src.directional_rows),
                                  Normal2DOptions())
        from studio_core.export.flutter_shaders import shader_files

        sh_files, sh_notes = shader_files(src, normal, a)
        files += sh_files
        notes += sh_notes

    names = _unique([n for n, *_ in src.ui], "ui")
    ui_items = [] if src.ui_screen else list(zip(names, src.ui))
    sliced: list[tuple[str, str, tuple[int, int, int, int], tuple[int, int]]] = []
    icons = svg = icon = False
    for nm, (_, p, border, _state) in ui_items:
        if p.suffix.lower() == ".svg":
            files.append((f"assets/svg/{nm}.svg", p))
            svg = True
            continue
        if nm.lower() in APP_ICON_NAMES and not any(border) and not icon:
            files += icon_files(p)
            icon = True
        if any(border):
            with Image.open(p) as im:
                size = im.size
            path = f"assets/ui/{nm}.png"
            files.append((path, p))
            sliced.append((nm, path, border, size))
        else:
            files += density_images(p, nm, "assets/images/", pixel, webp)
            icons = True
    if icons:
        notes.append(NOTE_IMAGES.format(scale=NOTE_SCALE[1] if pixel else NOTE_SCALE[0]))
    if svg:
        notes.append(NOTE_SVG)
        packages.append("flutter_svg")
    if icon:
        notes.append(NOTE_ICON)
    if sliced:
        doc = [{"name": n, "l": b[0], "t": b[1], "r": b[2], "b": b[3], "w": s[0], "h": s[1]} for n, _, b, s in sliced]
        files += [("assets/ui/ui_9slice.json", _json(doc)), ("lib/gen/asset_studio_ui.dart", nine_slices_dart(sliced).encode())]
        notes.append(NOTE_NINE)
        use_nine = True

    tiles, maps = tile_files(src)
    if maps:
        files += tiles
        notes.append(NOTE_TILED.format(maps=", ".join(f"{m}.tmx" for m in maps), name=maps[0]))
        packages.append("flame_tiled")
        use_tiled = True

    for present, build in _fx_kinds(src):
        if present:
            fx_files, fx_notes = build(src)
            have = {n for n, _ in files}
            files += [(n, d) for n, d in fx_files if n not in have]
            notes += fx_notes
            if any(n.endswith(".tmx") for n, _ in fx_files):
                use_tiled = True
                if "flame_tiled" not in packages:
                    packages.append("flame_tiled")

    if src.parallax:
        base = f"assets/images/parallax/{a}"
        layers = []
        for (n, p, scroll), nm in zip(src.parallax, _unique([n for n, *_ in src.parallax], "layer")):
            file = f"{len(layers)}_{nm}.png"
            files.append((f"{base}/{file}", p))
            layers.append({"image": f"parallax/{a}/{file}", "velocityMultiplier": scroll})
        files.append((f"{base}/parallax.json", _json({"size": list(src.parallax_size), "layers": layers})))
        notes.append(NOTE_PARALLAX.format(asset=a))
        use_parallax = True

    if src.font and src.font_atlas:
        from studio_core.ops.bitmap_font import fnt_text

        files += [(f"assets/images/fonts/{a}.png", src.font_atlas),
                  (f"assets/fonts/{a}.fnt", fnt_text(src.font, f"{a}.png").encode()),
                  (f"assets/fonts/{a}_font.json", _json(_font_doc(src.font)))]
        notes.append(NOTE_FONT.format(asset=a))
        use_font = True

    if src.spine_json is not None and src.spine_atlas is not None:
        base = f"assets/spine/{a}"
        files += [(f"{base}/{src.spine_json[0]}", src.spine_json[1]), (f"{base}/{src.spine_atlas[0]}", src.spine_atlas[1])]
        files += [(f"{base}/{n}", p) for n, p in src.spine_pages]
        notes.append(NOTE_SPINE.format(asset=a))
        packages.append("spine_flutter")

    if not files:
        return []
    if demo or (demo is None and src.playable):
        from studio_core.export.flutter_demo import DEMO_DIR, demo_project

        project = demo_project(files, a)
        if project:
            files += [(f"{DEMO_DIR}/{n}", d) for n, d in project]
            notes.append(NOTE_DEMO)
    if use_anim or use_nine or use_parallax or use_font or use_tiled:
        files.append(("lib/gen/asset_studio_flame.dart",
                      flame_dart(use_anim, use_nine, use_parallax, use_font, use_tiled).encode()))
    paths = [n for n, _ in files if n.startswith(("assets/", "shaders/"))]
    if asset_paths(paths):
        files += [("pubspec_assets.yaml", pubspec_snippet(paths, packages).encode()),
                  ("lib/gen/asset_studio_assets.dart", assets_dart(paths).encode())]
    files += [("README_FLUTTER.md", README.format(asset=a, packages=", ".join(packages), notes="\n".join(notes)).encode())]
    return files
