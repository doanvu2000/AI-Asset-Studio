"""Blob-47 autotile export (op.autotile): Godot 4 TileSet with a Match Corners and Sides terrain set, Unity RuleTile
(2D Tilemap Extras) + plain Tile for the fill terrain over a sliced sprite sheet."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from studio_core.contracts import BlobStore, StepRow
from studio_core.export.source import Files, Source

# RuleTile.cs in com.unity.2d.tilemap.extras; the package keeps this guid across versions.
RULE_TILE_GUID = "9d1514134bc4fbd41bb739b1b9a49231"
TERRAIN_COLORS = ("Color(0.36, 0.62, 0.3, 1)", "Color(0.66, 0.5, 0.33, 1)")
ASSET_FILE_ID = 11400000


def autotile_set(step: StepRow, blobs: BlobStore) -> tuple[dict[str, Any] | None, Path | None]:
    """op.autotile's autotile.json and its atlas, also when a gate passed them through under another port name."""
    from studio_core.ops.autotile import AUTOTILE_FILE, AUTOTILE_TYPE

    for refs in step.outputs.values():
        for ref in refs:
            if (ref.name or "") != AUTOTILE_FILE:
                continue
            try:
                doc = json.loads(blobs.path(ref.hash).read_bytes())
                atlas = blobs.path(str(doc["atlas"]["hash"]))
                if doc.get("type") != AUTOTILE_TYPE or not atlas.is_file() or len(doc["tiles"]) != 48:
                    return None, None
                return doc, atlas
            except (OSError, ValueError, KeyError, TypeError):
                return None, None
    return None, None


def _names(src: Source) -> tuple[str, str]:
    a, b = (src.autotile or {}).get("terrains") or ["a", "b"]
    return str(a), str(b)


def _sheet(src: Source) -> str:
    return f"Autotile/{src.asset}_autotile.png"


def _json(src: Source) -> bytes:
    doc = {k: v for k, v in (src.autotile or {}).items() if k != "atlas"}
    return json.dumps({**doc, "atlas": Path(_sheet(src)).name}, indent=1).encode()


def godot_tileset(doc: dict[str, Any], atlas: str) -> str:
    t = int(doc["tile_size"])
    lines = ['[gd_resource type="TileSet" load_steps=3 format=3]', '',
             f'[ext_resource type="Texture2D" path="{atlas}" id="1_atlas"]', '',
             '[sub_resource type="TileSetAtlasSource" id="TileSetAtlasSource_1"]', 'texture = ExtResource("1_atlas")',
             f"texture_region_size = Vector2i({t}, {t})"]
    for e in doc["tiles"]:
        k = f"{e['col']}:{e['row']}/0"
        lines += [f"{k} = 0", f"{k}/terrain_set = 0", f"{k}/terrain = {0 if e['mask'] is not None else 1}"]
        lines += [f"{k}/terrains_peering_bit/{bit} = {v}" for bit, v in e["godot_peering"].items()]
    lines += ['', '[resource]', f"tile_size = Vector2i({t}, {t})", "terrain_set_0/mode = 0"]
    for i, name in enumerate(doc["terrains"]):
        lines += [f'terrain_set_0/terrain_{i}/name = "{name}"', f"terrain_set_0/terrain_{i}/color = {TERRAIN_COLORS[i]}"]
    lines.append('sources/0 = SubResource("TileSetAtlasSource_1")')
    return "\n".join(lines) + "\n"


def godot_files(src: Source) -> tuple[Files, list[str]]:
    assert src.autotile is not None and src.autotile_atlas is not None
    a, b = _names(src)
    tres = f"{src.asset}_autotile_tileset.tres"
    files: Files = [(_sheet(src), src.autotile_atlas), (f"Autotile/{src.asset}_autotile.json", _json(src)),
                    (tres, godot_tileset(src.autotile, _sheet(src)).encode())]
    note = (f"- {tres}: TileSet autotile 47 ô + 1 ô {b} đầy ({_sheet(src)}, 8×6 ô), terrain set 0 kiểu Match Corners "
            f"and Sides: terrain 0 = \"{a}\", terrain 1 = \"{b}\". Gắn vào TileMapLayer, tab Terrains tô {b} làm nền "
            f"rồi tô {a} (hoặc set_cells_terrain_connect) là Godot tự chọn ô khớp cạnh + góc."
            + (" Pixel art: đặt Texture Filter = Nearest trên TileMapLayer." if src.autotile.get("pixel") else ""))
    return files, [note]


def _sprite(src: Source, tex_guid: str, name: str) -> str:
    from studio_core.export.unity import file_id

    return f"{{fileID: {file_id('sprite', src.asset, name)}, guid: {tex_guid}, type: 3}}"


def _neighbors_hex(values: list[int]) -> str:
    return "".join(int(v).to_bytes(4, "little").hex() for v in values)


def unity_rule_tile(src: Source, tex_guid: str) -> str:
    from studio_core.export.unity import HEADER
    from studio_core.ops.autotile import UNITY_NEIGHBORS

    assert src.autotile is not None
    a, _ = _names(src)
    tiles = [e for e in src.autotile["tiles"] if e["mask"] is not None]
    alone = next(e for e in tiles if e["mask"] == 0)
    positions = "".join(f"\n    - {{x: {dx}, y: {dy}, z: 0}}" for _, dx, dy in UNITY_NEIGHBORS)
    rules = "".join(f"""
  - m_Id: {i}
    m_Sprites:
    - {_sprite(src, tex_guid, e['name'])}
    m_GameObject: {{fileID: 0}}
    m_MinAnimationSpeed: 1
    m_MaxAnimationSpeed: 1
    m_PerlinScale: 0.5
    m_Output: 0
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
  m_DefaultSprite: {_sprite(src, tex_guid, alone['name'])}
  m_DefaultGameObject: {{fileID: 0}}
  m_DefaultColliderType: 1
  m_TilingRules:{rules}
"""


def unity_tile(src: Source, tex_guid: str, name: str, sprite: str) -> str:
    from studio_core.export.unity import HEADER

    return HEADER + f"""--- !u!114 &{ASSET_FILE_ID}
MonoBehaviour:
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {{fileID: 0}}
  m_PrefabInstance: {{fileID: 0}}
  m_PrefabAsset: {{fileID: 0}}
  m_GameObject: {{fileID: 0}}
  m_Enabled: 1
  m_EditorHideFlags: 0
  m_Script: {{fileID: 13312, guid: 0000000000000000e000000000000000, type: 0}}
  m_Name: {name}
  m_EditorClassIdentifier:
  m_Sprite: {_sprite(src, tex_guid, sprite)}
  m_Color: {{r: 1, g: 1, b: 1, a: 1}}
  m_Transform:
    e00: 1
    e01: 0
    e02: 0
    e03: 0
    e10: 0
    e11: 1
    e12: 0
    e13: 0
    e20: 0
    e21: 0
    e22: 1
    e23: 0
    e30: 0
    e31: 0
    e32: 0
    e33: 1
  m_InstancedGameObject: {{fileID: 0}}
  m_Flags: 1
  m_ColliderType: 1
"""


def unity_files(src: Source) -> tuple[Files, list[str]]:
    from studio_core.export.sheet import Cell, Layout
    from studio_core.export.unity import guid, native_meta, texture_meta

    assert src.autotile is not None and src.autotile_atlas is not None
    a, b = _names(src)
    t = int(src.autotile["tile_size"])
    cols, rows = src.autotile["grid"]
    lay = Layout(width=cols * t, height=rows * t, frame_w=t, frame_h=t, pivot=(t / 2, t / 2),
                 cells=[Cell(e["name"], e["col"] * t, e["row"] * t, t, t) for e in src.autotile["tiles"]])
    sheet = _sheet(src)
    tex_guid = guid(src.uid, src.asset, sheet)
    # One tile = one Tilemap cell (1 unit), whatever the export PPU.
    files: Files = [(sheet, src.autotile_atlas),
                    (f"{sheet}.meta", texture_meta(src.asset, tex_guid, lay, (cols * t, rows * t), t,
                                                   bool(src.autotile.get("pixel"))).encode()),
                    (f"Autotile/{src.asset}_autotile.json", _json(src))]
    fill = next(e["name"] for e in src.autotile["tiles"] if e["mask"] is None)
    for rel, body in ((f"Autotile/{src.asset}_{a}_rule.asset", unity_rule_tile(src, tex_guid)),
                      (f"Autotile/{src.asset}_{b}.asset", unity_tile(src, tex_guid, f"{src.asset}_{b}", fill))):
        files += [(rel, body.encode()), (f"{rel}.meta", native_meta(guid(src.uid, src.asset, rel),
                                                                      ASSET_FILE_ID).encode())]
    note = (f"- Autotile/: {Path(sheet).name} (8×6 ô {t}px, đã cắt 48 sprite, PPU = {t} nên 1 ô = 1 cell Tilemap), "
            f"{src.asset}_{a}_rule.asset = RuleTile 47 luật cho \"{a}\" — CẦN gói 2D Tilemap Extras "
            f"(com.unity.2d.tilemap.extras, Package Manager) nếu không asset báo thiếu script; {src.asset}_{b}.asset = "
            f"Tile thường cho nền \"{b}\". Tô {b} trước (hoặc layer dưới), tô RuleTile lên trên: ô cạnh nền khác "
            f"RuleTile (NotThis) tự lấy mép {b}. Bảng mặt nạ từng ô trong {src.asset}_autotile.json.")
    return files, [note]
