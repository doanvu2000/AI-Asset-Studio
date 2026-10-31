"""Animated tile export (op.tile_animate): Godot 4 TileSet whose atlas tiles carry the animation (frames laid out by
animation_columns / animation_separation), Unity AnimatedTile or animated RuleTile (2D Tilemap Extras) over a sliced
sheet, plus a plain sprite AnimationClip for projects without the extras package."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from studio_core.contracts import BlobStore, StepRow
from studio_core.export.source import Files, Source

# AnimatedTile.cs in com.unity.2d.tilemap.extras (Runtime/Tiles/AnimatedTile); stable across package versions.
ANIMATED_TILE_GUID = "13b75c95f34a00d4e8c04f76b73312e6"
ASSET_FILE_ID = 11400000
# RuleTile.TilingRuleOutput.OutputSprite: Single = 0, Random = 1, Animation = 2.
RULE_OUTPUT_ANIMATION = 2


def tile_anim_set(step: StepRow, blobs: BlobStore) -> tuple[dict[str, Any] | None, Path | None]:
    """op.tile_animate's tile_anim.json and its sheet, also when a gate passed them through."""
    from studio_core.ops.tile_anim import TILE_ANIM_FILE, TILE_ANIM_TYPE

    for refs in step.outputs.values():
        for ref in refs:
            if (ref.name or "") != TILE_ANIM_FILE:
                continue
            try:
                doc = json.loads(blobs.path(ref.hash).read_bytes())
                sheet = blobs.path(str(doc["sheet"]["hash"]))
                if doc.get("type") != TILE_ANIM_TYPE or not sheet.is_file() or int(doc["frames"]) < 2:
                    return None, None
                if doc.get("mode") == "blob47" and len((doc.get("autotile") or {}).get("tiles") or []) != 48:
                    return None, None
                return doc, sheet
            except (OSError, ValueError, KeyError, TypeError):
                return None, None
    return None, None


def _sheet(src: Source) -> str:
    return f"TileAnim/{src.asset}_tiles.png"


def _blob(doc: dict[str, Any]) -> bool:
    return doc.get("mode") == "blob47"


def _base_tiles(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """(name, col, row, mask) of every frame-0 tile; a single tile sits at (0, 0)."""
    if _blob(doc):
        return list(doc["autotile"]["tiles"])
    return [{"name": "tile", "col": 0, "row": 0, "mask": None}]


def frame_cells(doc: dict[str, Any]) -> list[tuple[str, int, int]]:
    """(sprite name, col, row) of every tile in every frame, frame-major."""
    dx, dy = doc["layout"]["frame_offset"]
    return [(f"{e['name']}_f{k:02d}", e["col"] + k * dx, e["row"] + k * dy)
            for k in range(int(doc["frames"])) for e in _base_tiles(doc)]


def _json(src: Source) -> bytes:
    doc = dict(src.tile_anim or {})
    return json.dumps({**doc, "sheet": Path(_sheet(src)).name}, indent=1).encode()


def _f(v: float) -> str:
    return f"{v:.6g}"


def godot_tile_lines(doc: dict[str, Any], key: str) -> list[str]:
    n = int(doc["frames"])
    dx, dy = doc["layout"]["frame_offset"]
    # Frame k of a tile sits at coords + (k % columns, k // columns) · (1 + separation): one column with a separation
    # of rows-1 steps a whole atlas down per frame; n columns walk a strip to the right.
    cols, sep = (1, (0, dy - 1)) if dy else (n, (dx - 1, 0))
    return [f"{key}/animation_columns = {cols}", f"{key}/animation_separation = Vector2i({sep[0]}, {sep[1]})",
            f"{key}/animation_speed = {_f(float(doc['fps']))}", f"{key}/animation_frames_count = {n}",
            *[f"{key}/animation_frame_{k}/duration = 1.0" for k in range(n)]]


def godot_tileset(doc: dict[str, Any], atlas: str) -> str:
    from studio_core.export.autotile import TERRAIN_COLORS

    t = int(doc["tile_size"])
    lines = ['[gd_resource type="TileSet" load_steps=3 format=3]', '',
             f'[ext_resource type="Texture2D" path="{atlas}" id="1_atlas"]', '',
             '[sub_resource type="TileSetAtlasSource" id="TileSetAtlasSource_1"]', 'texture = ExtResource("1_atlas")',
             f"texture_region_size = Vector2i({t}, {t})"]
    for e in _base_tiles(doc):
        k = f"{e['col']}:{e['row']}"
        lines += godot_tile_lines(doc, k)
        lines.append(f"{k}/0 = 0")
        if _blob(doc):
            lines += [f"{k}/0/terrain_set = 0", f"{k}/0/terrain = {0 if e['mask'] is not None else 1}"]
            lines += [f"{k}/0/terrains_peering_bit/{bit} = {v}" for bit, v in e["godot_peering"].items()]
    lines += ['', '[resource]', f"tile_size = Vector2i({t}, {t})"]
    if _blob(doc):
        lines.append("terrain_set_0/mode = 0")
        for i, name in enumerate(doc["autotile"]["terrains"]):
            lines += [f'terrain_set_0/terrain_{i}/name = "{name}"',
                      f"terrain_set_0/terrain_{i}/color = {TERRAIN_COLORS[i]}"]
    lines.append('sources/0 = SubResource("TileSetAtlasSource_1")')
    return "\n".join(lines) + "\n"


def godot_files(src: Source) -> tuple[Files, list[str]]:
    assert src.tile_anim is not None and src.tile_anim_sheet is not None
    doc = src.tile_anim
    tres = f"{src.asset}_tileset.tres"
    files: Files = [(_sheet(src), src.tile_anim_sheet), (f"TileAnim/{src.asset}_tile_anim.json", _json(src)),
                    (tres, godot_tileset(doc, _sheet(src)).encode())]
    n, fps = int(doc["frames"]), _f(float(doc["fps"]))
    if _blob(doc):
        a, b = doc["autotile"]["terrains"]
        note = (f"- {tres}: TileSet autotile 47 ô + ô {b} đầy, mỗi ô là tile động {n} frame ({fps} fps) — "
                f"{_sheet(src)} xếp {n} atlas 8×6 chồng dọc, terrain set 0 (Match Corners and Sides): terrain 0 = "
                f"\"{a}\", terrain 1 = \"{b}\". Tô bằng tab Terrains như autotile thường, TileMapLayer tự chạy hoạt ảnh.")
    else:
        note = (f"- {tres}: TileSet 1 ô động {n} frame ({fps} fps, dải ngang {_sheet(src)}). Gắn vào TileMapLayer "
                "rồi tô ô (0, 0): mọi ô cùng nhịp, mép liền nhau.")
    if doc.get("pixel"):
        note += " Pixel art: đặt Texture Filter = Nearest trên TileMapLayer."
    return files, [note]


def _sprite_ref(src: Source, tex_guid: str, name: str) -> str:
    from studio_core.export.unity import file_id

    return f"{{fileID: {file_id('sprite', src.asset, name)}, guid: {tex_guid}, type: 3}}"


def unity_animated_tile(src: Source, tex_guid: str, name: str, base: str) -> str:
    from studio_core.export.unity import HEADER

    doc = src.tile_anim or {}
    names = [f"{base}_f{k:02d}" for k in range(int(doc["frames"]))]
    sprites = "".join(f"\n  - {_sprite_ref(src, tex_guid, s)}" for s in names)
    fps = _f(float(doc["fps"]))
    return HEADER + f"""--- !u!114 &{ASSET_FILE_ID}
MonoBehaviour:
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {{fileID: 0}}
  m_PrefabInstance: {{fileID: 0}}
  m_PrefabAsset: {{fileID: 0}}
  m_GameObject: {{fileID: 0}}
  m_Enabled: 1
  m_EditorHideFlags: 0
  m_Script: {{fileID: 11500000, guid: {ANIMATED_TILE_GUID}, type: 3}}
  m_Name: {name}
  m_EditorClassIdentifier:
  m_AnimatedSprites:{sprites}
  m_MinSpeed: {fps}
  m_MaxSpeed: {fps}
  m_AnimationStartTime: 0
  m_AnimationStartFrame: 0
  m_TileColliderType: 0
  m_TileAnimationFlags: 0
"""


def unity_rule_tile(src: Source, tex_guid: str) -> str:
    from studio_core.export.autotile import RULE_TILE_GUID, _neighbors_hex
    from studio_core.export.unity import HEADER
    from studio_core.ops.autotile import UNITY_NEIGHBORS

    doc = src.tile_anim or {}
    n, fps = int(doc["frames"]), _f(float(doc["fps"]))
    a = doc["autotile"]["terrains"][0]
    tiles = [e for e in doc["autotile"]["tiles"] if e["mask"] is not None]
    alone = next(e for e in tiles if e["mask"] == 0)
    positions = "".join(f"\n    - {{x: {dx}, y: {dy}, z: 0}}" for _, dx, dy in UNITY_NEIGHBORS)

    def frames(e: dict[str, Any]) -> str:
        names = [f"{e['name']}_f{k:02d}" for k in range(n)]
        return "".join(f"\n    - {_sprite_ref(src, tex_guid, s)}" for s in names)

    # Tilemap.animationFrameRate defaults to 1, so the speed multiplier is the frame rate itself.
    rules = "".join(f"""
  - m_Id: {i}
    m_Sprites:{frames(e)}
    m_GameObject: {{fileID: 0}}
    m_MinAnimationSpeed: {fps}
    m_MaxAnimationSpeed: {fps}
    m_PerlinScale: 0.5
    m_Output: {RULE_OUTPUT_ANIMATION}
    m_ColliderType: 1
    m_RandomTransform: 0
    m_Neighbors: {_neighbors_hex(e['unity_neighbors'])}
    m_NeighborPositions:{positions}
    m_RuleTransform: 0""" for i, e in enumerate(tiles))
    return HEADER + f"""--- !u!114 &{ASSET_FILE_ID}
MonoBehaviour:
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {{fileID: 0}}
  m_PrefabInstance: {{fileID: 0}}
  m_PrefabAsset: {{fileID: 0}}
  m_GameObject: {{fileID: 0}}
  m_Enabled: 1
  m_EditorHideFlags: 0
  m_Script: {{fileID: 11500000, guid: {RULE_TILE_GUID}, type: 3}}
  m_Name: {src.asset}_{a}_rule
  m_EditorClassIdentifier:
  m_DefaultSprite: {_sprite_ref(src, tex_guid, f"{alone['name']}_f00")}
  m_DefaultGameObject: {{fileID: 0}}
  m_DefaultColliderType: 1
  m_TilingRules:{rules}
"""


def unity_files(src: Source) -> tuple[Files, list[str]]:
    from studio_core.export.sheet import Cell, Layout, Track
    from studio_core.export.unity import (
        CLIP_FILE_ID,
        anim_clip,
        guid,
        native_meta,
        texture_meta,
    )

    assert src.tile_anim is not None and src.tile_anim_sheet is not None
    doc = src.tile_anim
    t, n, fps = int(doc["tile_size"]), int(doc["frames"]), float(doc["fps"])
    cells = frame_cells(doc)
    width = (max(c for _, c, _ in cells) + 1) * t
    height = (max(r for _, _, r in cells) + 1) * t
    lay = Layout(width=width, height=height, frame_w=t, frame_h=t, pivot=(t / 2, t / 2),
                 cells=[Cell(name, c * t, r * t, t, t) for name, c, r in cells])
    sheet = _sheet(src)
    tex_guid = guid(src.uid, src.asset, sheet)
    # PPU = tile size: one tile fills one Tilemap cell whatever the export PPU.
    files: Files = [(sheet, src.tile_anim_sheet),
                    (f"{sheet}.meta", texture_meta(src.asset, tex_guid, lay, (width, height), t,
                                                   bool(doc.get("pixel"))).encode()),
                    (f"TileAnim/{src.asset}_tile_anim.json", _json(src))]
    assets: list[tuple[str, str]] = []
    if _blob(doc):
        a, b = doc["autotile"]["terrains"]
        fill = next(e["name"] for e in doc["autotile"]["tiles"] if e["mask"] is None)
        assets += [(f"TileAnim/{src.asset}_{a}_rule.asset", unity_rule_tile(src, tex_guid)),
                   (f"TileAnim/{src.asset}_{b}.asset", unity_animated_tile(src, tex_guid, f"{src.asset}_{b}", fill))]
        note = (f"- TileAnim/: {Path(sheet).name} ({n} atlas 8×6 ô {t}px chồng dọc, đã cắt sprite <ô>_fNN, PPU = {t}), "
                f"{src.asset}_{a}_rule.asset = RuleTile 47 luật, mỗi luật Output = Animation {n} frame; "
                f"{src.asset}_{b}.asset = AnimatedTile nền \"{b}\". CẦN gói 2D Tilemap Extras "
                "(com.unity.2d.tilemap.extras). Tốc độ = fps khi Tilemap.animationFrameRate = 1 (mặc định).")
    else:
        assets.append((f"TileAnim/{src.asset}_tile.asset",
                       unity_animated_tile(src, tex_guid, f"{src.asset}_tile", "tile")))
        track = Track(f"{src.asset}_tile", "", 0.0, [Cell(f"tile_f{k:02d}", k * t, 0, t, t) for k in range(n)], fps,
                      True)
        clip_rel = f"TileAnim/{src.asset}_tile.anim"
        files += [(clip_rel, anim_clip(src.asset, tex_guid, track).encode()),
                  (f"{clip_rel}.meta", native_meta(guid(src.uid, src.asset, clip_rel), CLIP_FILE_ID).encode())]
        note = (f"- TileAnim/: {Path(sheet).name} (dải {n} frame {t}px, đã cắt sprite tile_fNN, PPU = {t}). "
                f"{src.asset}_tile.asset = AnimatedTile {_f(fps)} fps — kéo vào Tile Palette rồi tô; CẦN gói 2D Tilemap "
                f"Extras (com.unity.2d.tilemap.extras). Không có gói đó: dùng {src.asset}_tile.anim (AnimationClip "
                "đổi sprite) trên SpriteRenderer + Animator, hoặc xoá file .asset để Unity khỏi báo thiếu script.")
    for rel, body in assets:
        files += [(rel, body.encode()), (f"{rel}.meta", native_meta(guid(src.uid, src.asset, rel),
                                                                      ASSET_FILE_ID).encode())]
    return files, [note]
