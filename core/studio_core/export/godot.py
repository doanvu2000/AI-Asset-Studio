"""Godot 4 drop-in folder: SpriteFrames .tres (AtlasTexture regions per clip × direction) + AnimatedSprite2D scene."""
from __future__ import annotations

from pathlib import Path

from studio_core.export.collider import GODOT_SCRIPT, GODOT_SCRIPT_FILE, for_sheet, godot_body_scene
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
    lines = ['[gd_scene format=3]', '', f'[ext_resource type="SpriteFrames" path="{frames_file}" id="1_frames"]']
    if lay.facing:
        lines.append('[ext_resource type="Script" path="asset_studio_sprite.gd" id="2_script"]')
    lines += ['', f'[node name="{name}" type="AnimatedSprite2D"]']
    if pixel:
        lines.append("texture_filter = 1")
    lines += ['sprite_frames = ExtResource("1_frames")', f'animation = &"{anim}"', f'autoplay = "{anim}"',
              f"offset = Vector2({_num(ox)}, {_num(oy)})"]
    if lay.facing:
        lines.append('script = ExtResource("2_script")')
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


LOD_SCRIPT = """@tool
extends Node3D
## Shows the *_LOD0, *_LOD1... meshes of the instanced GLB by camera distance through visibility ranges.

@export var lod_distance := 10.0:
	set(value):
		lod_distance = value
		_apply()


func _ready() -> void:
	_apply()


func _apply() -> void:
	if not is_inside_tree():
		return
	var lods := {}
	for node in find_children("*_LOD*", "GeometryInstance3D", true, false):
		var node_name := String(node.name)
		var tail := node_name.substr(node_name.rfind("_LOD") + 4)
		if tail.is_valid_int():
			lods[tail.to_int()] = node
	var keys := lods.keys()
	keys.sort()
	for k in keys.size():
		var g: GeometryInstance3D = lods[keys[k]]
		g.visibility_range_begin = 0.0 if k == 0 else lod_distance * pow(2.0, k - 1)
		g.visibility_range_end = 0.0 if k == keys.size() - 1 else lod_distance * pow(2.0, k)
"""


def lod_scene(name: str, glb: str) -> str:
    return (f'[gd_scene format=3]\n\n[ext_resource type="PackedScene" path="{glb}" id="1_model"]\n'
            '[ext_resource type="Script" path="asset_studio_lods.gd" id="2_script"]\n\n'
            f'[node name="{name}" type="Node3D"]\nscript = ExtResource("2_script")\n\n'
            '[node name="Model" parent="." instance=ExtResource("1_model")]\n')


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


WANG_COLORS = ("Color(0.36, 0.62, 0.3, 1)", "Color(0.66, 0.5, 0.33, 1)")
_PEERING = {"nw": "top_left_corner", "ne": "top_right_corner", "sw": "bottom_left_corner", "se": "bottom_right_corner"}


def wang_tileset(atlas: str, size: tuple[int, int], normal: str | None = None) -> str:
    """TileSet whose 4×4 corner-Wang atlas carries a Match Corners terrain set (terrain 0 = material a, 1 = b), so
    TileMapLayer terrain painting picks the matching tile; with a normal atlas the source is a CanvasTexture."""
    from studio_core.ops.tile import wang_layout

    cell = size[0] // 4
    ext = [f'[ext_resource type="Texture2D" path="{atlas}" id="1_atlas"]']
    subs = []
    tex = 'ExtResource("1_atlas")'
    if normal:
        ext.append(f'[ext_resource type="Texture2D" path="{normal}" id="2_normal"]')
        subs += ['[sub_resource type="CanvasTexture" id="CanvasTexture_1"]', 'diffuse_texture = ExtResource("1_atlas")',
                 'normal_texture = ExtResource("2_normal")', '']
        tex = 'SubResource("CanvasTexture_1")'
    lines = [f'[gd_resource type="TileSet" load_steps={len(ext) + len(subs) // 4 + 2} format=3]', '', *ext, '',
             *subs, '[sub_resource type="TileSetAtlasSource" id="TileSetAtlasSource_1"]', f"texture = {tex}",
             f"texture_region_size = Vector2i({cell}, {cell})"]
    for t in wang_layout():
        bits = [int(t["corners"][c] == "b") for c in _PEERING]
        major = int(sum(bits) >= 2)
        # Godot paints a cell's centre with the brush terrain, so a cell on the rim of a painted area needs its
        # mixed-corner tile with either centre; alternative 1 is the same image with the other centre terrain.
        centres = [major] if sum(bits) in (0, 4) else [major, 1 - major]
        for alt, centre in enumerate(centres):
            k = f"{t['col']}:{t['row']}/{alt}"
            lines += [f"{k} = {alt}", f"{k}/terrain_set = 0", f"{k}/terrain = {centre}"]
            lines += [f"{k}/terrains_peering_bit/{_PEERING[c]} = {b}" for c, b in zip(_PEERING, bits)]
    lines += ['', '[resource]', f"tile_size = Vector2i({cell}, {cell})", "terrain_set_0/mode = 1"]
    for i, name in enumerate("ab"):
        lines += [f'terrain_set_0/terrain_{i}/name = "{name}"', f"terrain_set_0/terrain_{i}/color = {WANG_COLORS[i]}"]
    lines.append('sources/0 = SubResource("TileSetAtlasSource_1")')
    return "\n".join(lines) + "\n"


def wang_file(asset: str, name: str) -> str:
    """Wang atlases get the asset prefix: two exported sets dropped into one project must not overwrite each other."""
    return f"{asset}_{name}" if Path(name).stem in ("wang", "wang_normal") else name


def _wang_doc() -> bytes:
    import json

    from studio_core.ops.tile import WANG_CORNERS, WANG_TYPE, wang_layout

    return json.dumps({"type": WANG_TYPE, "corner_order": list(WANG_CORNERS), "grid": [4, 4],
                       "tiles": wang_layout()}, indent=1).encode()


def _image_size(p: Path) -> tuple[int, int]:
    from PIL import Image

    with Image.open(p) as im:
        return im.size


README_TEXTURES = """- Textures/: texture liền mạch (lặp được). {asset}_material.tres: StandardMaterial3D đã gắn albedo{normal},
  texture_repeat bật — chỉnh uv1_scale để lặp nhiều lần. {asset}_tiled.tscn: TextureRect lát 3×3 cho 2D
  (stretch_mode Tile). Sprite2D/TileMap muốn lặp thì bật texture_repeat của node."""


README_WANG = """- {asset}_wang_tileset.tres: TileSet Wang góc từ {atlas} (4×4 ô), terrain set 0 kiểu Match Corners: terrain
  "a" = vật liệu 1, "b" = vật liệu 2. Gắn vào TileMapLayer, tab Terrains vẽ a/b là Godot tự chọn ô khớp góc.
  Tên ô wang_<số>_<góc NW NE SW SE> (vd wang_06_abba), chi tiết nhãn góc/cạnh trong Textures/{asset}_wang.json."""


def stylebox(tex: str, border: tuple[int, int, int, int]) -> str:
    l, t, r, b = border
    return "\n".join(['[gd_resource type="StyleBoxTexture" load_steps=2 format=3]', '',
                      f'[ext_resource type="Texture2D" path="{tex}" id="1_tex"]', '', '[resource]',
                      'texture = ExtResource("1_tex")', f"texture_margin_left = {_num(l)}",
                      f"texture_margin_top = {_num(t)}", f"texture_margin_right = {_num(r)}",
                      f"texture_margin_bottom = {_num(b)}"]) + "\n"


def ui_scene(name: str, items: list[tuple[str, tuple[int, int, int, int], tuple[int, int]]]) -> str:
    """Every sliced element as a NinePatchRect drawn at its stretch target, stacked down the screen."""
    ext = [f'[ext_resource type="Texture2D" path="UI/{n}.png" id="{i + 1}_{n}"]' for i, (n, _, _) in enumerate(items)]
    lines = ['[gd_scene format=3]', '', *ext, '', f'[node name="{name}" type="Control"]', 'layout_mode = 3',
             "anchors_preset = 15", "anchor_right = 1.0", "anchor_bottom = 1.0"]
    y = 16
    for i, (n, (l, t, r, b), (w, h)) in enumerate(items):
        lines += ['', f'[node name="{n}" type="NinePatchRect" parent="."]', "layout_mode = 0", "offset_left = 16.0",
                  f"offset_top = {_num(y)}", f"offset_right = {_num(16 + w)}", f"offset_bottom = {_num(y + h)}",
                  f'texture = ExtResource("{i + 1}_{n}")', f"patch_margin_left = {l}", f"patch_margin_top = {t}",
                  f"patch_margin_right = {r}", f"patch_margin_bottom = {b}"]
        y += h + 16
    return "\n".join(lines) + "\n"


README_UI = """- UI/: mỗi phần tử một ảnh PNG{sliced}.{states}"""
README_ICONS = """ (icon/sprite rời: TextureRect, Button.icon hoặc Sprite2D)"""
README_UI_SLICED = """ + <tên>.tres (StyleBoxTexture, texture_margin = viền 9-slice): gán vào Theme
  Overrides > Styles của Button/Panel/PanelContainer là kéo giãn giữ góc. NinePatchRect thì đặt patch_margin
  theo số trong .tres. {asset}_ui.tscn: xem trước mọi phần tử kéo giãn bằng NinePatchRect"""
README_UI_STATES = """
  Nút có thêm <tên>_hover/_pressed/_disabled.tres cùng viền: Button > Theme Overrides > Styles gán normal/hover/
  pressed/disabled tương ứng."""


README_HEAD = "{asset} — xuất từ AI Asset Studio cho Godot 4. Chép cả thư mục này vào project (res://...).\n\n"
README_SHEET = """- {asset}.tres: SpriteFrames, animation <clip>_<hướng> (S, SW, W, NW, N, NE, E, SE; S = quay mặt xuống
  màn hình), mỗi khung là AtlasTexture cắt từ {asset}.png, tốc độ = fps, loop theo clip.
- {asset}.tscn: AnimatedSprite2D dùng SpriteFrames trên, offset đặt gốc ở chân nhân vật.
- asset_studio_sprite.gd: gọi face("walk", velocity) để chọn hướng gần nhất."""
README_FLIPBOOK = """- {asset}.tres: SpriteFrames, một animation <clip> đọc các ô theo hàng, tốc độ = fps lúc tạo, loop theo chế độ
  (oneshot dừng ở khung cuối). Mỗi khung là AtlasTexture cắt từ {asset}.png.
- {asset}.tscn: AnimatedSprite2D tự phát, gốc ở giữa ô (đặt node tại tâm hiệu ứng)."""


def export(src: Source) -> Files:
    a = src.asset
    files: Files = []
    notes: list[str] = []
    if src.sheet is not None and src.meta is not None:
        lay = layout(src.meta, src.clip_hint, directional_rows=src.directional_rows)
        col = for_sheet(src, lay)
        tex, normal = f"{a}.png", (f"{a}_normal.png" if src.normal is not None else None)
        files.append((tex, src.sheet))
        notes.append((README_SHEET if lay.facing else README_FLIPBOOK).format(asset=a))
        if normal and src.normal is not None:
            files.append((normal, src.normal))
            notes.append(f"- {normal}: normal map, gắn qua CanvasTexture nên sprite nhận ánh sáng Light2D.")
        files += [(f"{a}.tres", sprite_frames(lay, tex, normal).encode()),
                  (f"{a}.tscn", scene(a, lay, f"{a}.tres", src.is_pixel()).encode())]
        if lay.facing:
            files.append(("asset_studio_sprite.gd", helper(lay).encode()))
        if col is not None and col.groups:
            body = "CharacterBody2D" if lay.facing else "Area2D"
            opts = src.collider
            body_scene = godot_body_scene(f"{a}_body", f"{a}.tscn", col, lay.pivot, default_animation(lay), body,
                                          opts.occluder)
            files += [(f"{a}_body.tscn", body_scene.encode()), (GODOT_SCRIPT_FILE, GODOT_SCRIPT.encode())]
            notes.append(f"- {a}_body.tscn: {body} bọc {a}.tscn + CollisionPolygon2D vẽ theo alpha"
                         f"{' và LightOccluder2D' if opts.occluder else ''} (Collider_<animation>"
                         f"{'_<khung>' if opts.per_frame else ''}); {GODOT_SCRIPT_FILE} bật đúng polygon theo "
                         "animation/khung đang phát.")
    if src.spine_json is not None and src.spine_atlas is not None:
        stem = Path(src.spine_json[0]).stem
        files += [(f"Spine/{stem}.spine-json", src.spine_json[1]), (f"Spine/{stem}.atlas", src.spine_atlas[1])]
        files += [(f"Spine/{n}", p) for n, p in src.spine_pages]
        notes.append(f"- Spine/: {stem}.spine-json + {stem}.atlas + ảnh trang — cần spine-godot 4.2 (bản Godot có Spine).")
    for n, p in src.glb:
        files.append((f"Models/{n}", p))
    if src.glb:
        notes.append("- Models/*.glb: Godot import GLB sẵn, kéo vào scene là dùng được.")
        from studio_core.export.rig3d import godot_note

        notes += godot_note(src)
    lods = [n for n, _ in src.glb if n.endswith("_lods.glb")]
    for n in lods:
        files.append((f"{Path(n).stem}.tscn", lod_scene(Path(n).stem, f"Models/{n}").encode()))
    if lods:
        files.append(("asset_studio_lods.gd", LOD_SCRIPT.encode()))
        notes.append("- *_lods.tscn: GLB *_lods + asset_studio_lods.gd đặt visibility range cho node *_LOD0, *_LOD1… theo "
                     "khoảng cách camera (LOD n bắt đầu ở lod_distance × 2^(n-1), mặc định 10m, chỉnh trong Inspector). "
                     "Nên tắt Generate LODs ở tab Import của GLB này để không chồng LOD tự sinh.")
    if src.textures:
        rels = [(f"Textures/{wang_file(a, n)}", p, role) for n, p, role in src.textures]
        files += [(rel, p) for rel, p, _ in rels]
        color = next(((r, p) for r, p, role in rels if role == "color"), None) or (rels[0][0], rels[0][1])
        normal = next((r for r, _, role in rels if role == "normal"), None)
        pixel = src.pixel is True or not any(role == "color" for _, _, role in rels)
        files += [(f"{a}_material.tres", texture_material(color[0], normal, pixel).encode()),
                  (f"{a}_tiled.tscn", tiled_scene(f"{a}_tiled", color[0], _image_size(color[1]), pixel).encode())]
        notes.append(README_TEXTURES.format(asset=a, normal=" + normal map" if normal else ""))
        wang = next(((r, p) for r, p, role in rels if role == "atlas" and Path(r).stem == f"{a}_wang"), None)
        wang_n = next((r for r, _, role in rels if role == "atlas" and Path(r).stem == f"{a}_wang_normal"), None)
        atlas = next(((r, p) for r, p, role in rels if role == "atlas" and r not in (wang and wang[0], wang_n)), None)
        if wang:
            files += [(f"{a}_wang_tileset.tres", wang_tileset(wang[0], _image_size(wang[1]), wang_n).encode()),
                      (f"Textures/{a}_wang.json", _wang_doc())]
            notes.append(README_WANG.format(asset=a, atlas=wang[0]))
        elif atlas:
            files.append((f"{a}_tileset.tres", tileset(atlas[0], _image_size(atlas[1])).encode()))
            notes.append(f"- {a}_tileset.tres: TileSet từ {atlas[0]} (mỗi biến thể một ô) cho TileMapLayer.")
    for n, p, b, _ in src.ui:
        files.append((f"UI/{n}.png", p))
        if any(b):
            files.append((f"UI/{n}.tres", stylebox(f"{n}.png", b).encode()))
    sliced = [(n, p, b) for n, p, b, state in src.ui if any(b) and not state]
    if sliced:
        from studio_core.ops.ui import stretch_target

        files.append((f"{a}_ui.tscn", ui_scene(f"{a}_ui", [(n, b, stretch_target(n, *_image_size(p)))
                                                            for n, p, b in sliced]).encode()))
    if src.ui:
        notes.append(README_UI.format(sliced=README_UI_SLICED.format(asset=a) if sliced else README_ICONS,
                                      states=README_UI_STATES if any(s for *_, s in src.ui) else ""))
    files += [(f"Audio/{n}", p) for n, p in src.audio]
    if src.audio:
        notes.append("- Audio/: Godot tự import. File *_loop nối liền đầu-cuối: chọn file > tab Import > bật Loop > "
                     "Reimport (WAV: Loop Mode = Forward).")
    if src.pbr:
        from studio_core.export.pbr import godot_files

        pbr_files, pbr_notes = godot_files(src)
        files += pbr_files
        notes += pbr_notes
    if src.rarity:
        from studio_core.export.rarity import godot_files as rarity_files

        r_files, r_notes = rarity_files(src)
        files += r_files
        notes += r_notes
    if not files:
        return files
    files.append(("README.txt", (README_HEAD.format(asset=a) + "\n".join(notes) + "\n").encode()))
    return files
