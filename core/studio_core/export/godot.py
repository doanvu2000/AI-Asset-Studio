"""Godot 4 drop-in folder: SpriteFrames .tres (AtlasTexture regions per clip × direction) + AnimatedSprite2D scene."""
from __future__ import annotations

from pathlib import Path

from studio_core.export.sheet import Layout, layout
from studio_core.export.source import Files, Source


def _num(v: float) -> str:
    s = f"{float(v):.6g}"
    return s if any(ch in s for ch in ".e") else f"{s}.0"


def sprite_frames(lay: Layout, tex: str, normal: str | None) -> str:
    """ext_resource paths are relative to the .tres, so the folder can sit anywhere under res://."""
    ext = [f'[ext_resource type="Texture2D" path="{tex}" id="1_sheet"]']
    if normal:
        ext.append(f'[ext_resource type="Texture2D" path="{normal}" id="2_normal"]')
    subs: list[str] = []
    atlas = 'ExtResource("1_sheet")'
    if normal:
        subs.append('[sub_resource type="CanvasTexture" id="CanvasTexture_sheet"]\n'
                    'diffuse_texture = ExtResource("1_sheet")\nnormal_texture = ExtResource("2_normal")\n')
        atlas = 'SubResource("CanvasTexture_sheet")'
    ids: dict[str, str] = {}
    for i, c in enumerate(lay.cells):
        ids[c.name] = f"AtlasTexture_{i}"
        subs.append(f'[sub_resource type="AtlasTexture" id="{ids[c.name]}"]\natlas = {atlas}\n'
                    f"region = Rect2({c.x}, {c.y}, {c.w}, {c.h})\n")
    anims = []
    for t in lay.tracks:
        frames = ", ".join(f'{{\n"duration": 1.0,\n"texture": SubResource("{ids[c.name]}")\n}}' for c in t.cells)
        anims.append(f'{{\n"frames": [{frames}],\n"loop": {"true" if t.loop else "false"},\n'
                     f'"name": &"{t.name}",\n"speed": {_num(t.fps)}\n}}')
    if not anims:
        frames = ", ".join(f'{{\n"duration": 1.0,\n"texture": SubResource("{ids[c.name]}")\n}}' for c in lay.cells)
        anims.append(f'{{\n"frames": [{frames}],\n"loop": true,\n"name": &"default",\n"speed": 8.0\n}}')
    return ('[gd_resource type="SpriteFrames" format=3]\n\n' + "\n".join(ext) + "\n\n" + "\n".join(subs)
            + "\n[resource]\nanimations = [" + ", ".join(anims) + "]\n")


def default_animation(lay: Layout) -> str:
    if not lay.tracks:
        return "default"
    names = [t.name for t in lay.tracks]
    return next((n for n in ("idle_S", f"{lay.tracks[0].clip}_S") if n in names), names[0])


def scene(name: str, lay: Layout, frames_file: str, pixel: bool) -> str:
    ox, oy = lay.frame_w / 2 - lay.pivot[0], lay.frame_h / 2 - lay.pivot[1]
    anim = default_animation(lay)
    lines = [
        '[gd_scene format=3]', '',
        f'[ext_resource type="SpriteFrames" path="{frames_file}" id="1_frames"]',
        '[ext_resource type="Script" path="asset_studio_sprite.gd" id="2_script"]', '',
        f'[node name="{name}" type="AnimatedSprite2D"]',
    ]
    if pixel:
        lines.append("texture_filter = 1")
    lines += ['sprite_frames = ExtResource("1_frames")', f'animation = &"{anim}"', f'autoplay = "{anim}"',
              f"offset = Vector2({_num(ox)}, {_num(oy)})", 'script = ExtResource("2_script")']
    return "\n".join(lines) + "\n"


HELPER = """extends AnimatedSprite2D
## face("walk", velocity) plays <clip>_<dir> for the closest exported direction (S = facing down the screen).

const DIR_ANGLES := {__DIR_ANGLES__}


func face(clip: String, direction: Vector2) -> void:
	if direction.length_squared() < 0.0001:
		return
	var angle := fposmod(rad_to_deg(atan2(-direction.x, direction.y)), 360.0)
	var best := ""
	var best_diff := 999.0
	for dir_name in DIR_ANGLES:
		var anim_name := "%s_%s" % [clip, dir_name]
		if not sprite_frames.has_animation(anim_name):
			continue
		var diff := absf(wrapf(angle - DIR_ANGLES[dir_name], -180.0, 180.0))
		if diff < best_diff:
			best_diff = diff
			best = anim_name
	if best != "" and animation != best:
		play(best)
"""


def helper(lay: Layout) -> str:
    angles = {t.dir: t.angle % 360 for t in reversed(lay.tracks)}
    pairs = ", ".join(f'"{d}": {_num(a)}' for d, a in sorted(angles.items(), key=lambda kv: kv[1]))
    return HELPER.replace("__DIR_ANGLES__", pairs)


def texture_material(albedo: str | None, normal: str | None, pixel: bool) -> str:
    ext, props = [], []
    if albedo:
        ext.append(f'[ext_resource type="Texture2D" path="{albedo}" id="1_albedo"]')
        props.append('albedo_texture = ExtResource("1_albedo")')
    if normal:
        ext.append(f'[ext_resource type="Texture2D" path="{normal}" id="2_normal"]')
        props += ["normal_enabled = true", 'normal_texture = ExtResource("2_normal")']
    if pixel:
        props.append("texture_filter = 0")
    props.append("texture_repeat = true")
    return ('[gd_resource type="StandardMaterial3D" format=3]\n\n' + "\n".join(ext) + "\n\n[resource]\n"
            + "\n".join(props) + "\n")


def tiled_scene(name: str, tex: str, size: tuple[int, int], pixel: bool, n: int = 3) -> str:
    """TextureRect in tile stretch mode: repeats in 2D without touching the import settings."""
    lines = ['[gd_scene format=3]', '', f'[ext_resource type="Texture2D" path="{tex}" id="1_tex"]', '',
             f'[node name="{name}" type="TextureRect"]', f"offset_right = {_num(size[0] * n)}",
             f"offset_bottom = {_num(size[1] * n)}", 'texture = ExtResource("1_tex")', "expand_mode = 1",
             "stretch_mode = 1", "texture_repeat = 2"]
    if pixel:
        lines.append("texture_filter = 1")
    return "\n".join(lines) + "\n"


def tileset(atlas: str, size: tuple[int, int]) -> str:
    """TileSet with one atlas source: the variants side by side, each a square cell as tall as the atlas."""
    cell = size[1]
    lines = ['[gd_resource type="TileSet" format=3]', '',
             f'[ext_resource type="Texture2D" path="{atlas}" id="1_atlas"]', '',
             '[sub_resource type="TileSetAtlasSource" id="TileSetAtlasSource_1"]', 'texture = ExtResource("1_atlas")',
             f"texture_region_size = Vector2i({cell}, {cell})",
             *(f"{i}:0/0 = 0" for i in range(max(1, size[0] // cell))), '', '[resource]',
             f"tile_size = Vector2i({cell}, {cell})", 'sources/0 = SubResource("TileSetAtlasSource_1")']
    return "\n".join(lines) + "\n"


def _image_size(p: Path) -> tuple[int, int]:
    from PIL import Image

    with Image.open(p) as im:
        return im.size


README_TEXTURES = """- Textures/: texture liền mạch (lặp được). {asset}_material.tres: StandardMaterial3D đã gắn albedo{normal},
  texture_repeat bật — chỉnh uv1_scale để lặp nhiều lần. {asset}_tiled.tscn: TextureRect lát 3×3 cho 2D
  (stretch_mode Tile). Sprite2D/TileMap muốn lặp thì bật texture_repeat của node."""


README_HEAD = "{asset} — xuất từ AI Asset Studio cho Godot 4. Chép cả thư mục này vào project (res://...).\n\n"
README_SHEET = """- {asset}.tres: SpriteFrames, animation <clip>_<hướng> (S, SW, W, NW, N, NE, E, SE; S = quay mặt xuống
  màn hình), mỗi khung là AtlasTexture cắt từ {asset}.png, tốc độ = fps, loop theo clip.
- {asset}.tscn: AnimatedSprite2D dùng SpriteFrames trên, offset đặt gốc ở chân nhân vật.
- asset_studio_sprite.gd: gọi face("walk", velocity) để chọn hướng gần nhất."""


def export(src: Source) -> Files:
    a = src.asset
    files: Files = []
    notes: list[str] = []
    if src.sheet is not None and src.meta is not None:
        lay = layout(src.meta, src.clip_hint, directional_rows=src.directional_rows)
        tex, normal = f"{a}.png", (f"{a}_normal.png" if src.normal is not None else None)
        files.append((tex, src.sheet))
        notes.append(README_SHEET.format(asset=a))
        if normal and src.normal is not None:
            files.append((normal, src.normal))
            notes.append(f"- {normal}: normal map, gắn qua CanvasTexture nên sprite nhận ánh sáng Light2D.")
        files += [(f"{a}.tres", sprite_frames(lay, tex, normal).encode()),
                  (f"{a}.tscn", scene(a, lay, f"{a}.tres", src.is_pixel()).encode()),
                  ("asset_studio_sprite.gd", helper(lay).encode())]
    if src.spine_json is not None and src.spine_atlas is not None:
        stem = Path(src.spine_json[0]).stem
        files += [(f"Spine/{stem}.spine-json", src.spine_json[1]), (f"Spine/{stem}.atlas", src.spine_atlas[1])]
        files += [(f"Spine/{n}", p) for n, p in src.spine_pages]
        notes.append(f"- Spine/: {stem}.spine-json + {stem}.atlas + ảnh trang — cần spine-godot 4.2 (bản Godot có Spine).")
    for n, p in src.glb:
        files.append((f"Models/{n}", p))
    if src.glb:
        notes.append("- Models/*.glb: Godot import GLB sẵn, kéo vào scene là dùng được.")
    if src.textures:
        rels = [(f"Textures/{n}", p, role) for n, p, role in src.textures]
        files += [(rel, p) for rel, p, _ in rels]
        color = next(((r, p) for r, p, role in rels if role == "color"), None) or (rels[0][0], rels[0][1])
        normal = next((r for r, _, role in rels if role == "normal"), None)
        pixel = src.pixel is True or not any(role == "color" for _, _, role in rels)
        files += [(f"{a}_material.tres", texture_material(color[0], normal, pixel).encode()),
                  (f"{a}_tiled.tscn", tiled_scene(f"{a}_tiled", color[0], _image_size(color[1]), pixel).encode())]
        notes.append(README_TEXTURES.format(asset=a, normal=" + normal map" if normal else ""))
        atlas = next(((r, p) for r, p, role in rels if role == "atlas"), None)
        if atlas:
            files.append((f"{a}_tileset.tres", tileset(atlas[0], _image_size(atlas[1])).encode()))
            notes.append(f"- {a}_tileset.tres: TileSet từ {atlas[0]} (mỗi biến thể một ô) cho TileMapLayer.")
    if not files:
        return files
    files.append(("README.txt", (README_HEAD.format(asset=a) + "\n".join(notes) + "\n").encode()))
    return files
