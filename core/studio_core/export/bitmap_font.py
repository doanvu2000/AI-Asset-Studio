"""Bitmap font export (op.bitmap_font): atlas + BMFont .fnt under Fonts/. Godot 4 imports the .fnt as a FontFile
(plus a demo scene); Unity gets a legacy Custom Font (.fontsettings + UI/Default material) for UI Text / TextMesh,
and the .fnt for third-party TextMeshPro importers."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from studio_core.contracts import BlobStore, StepRow
from studio_core.export.source import Files, Source

FONT_FILE_ID = 12800000
MATERIAL_FILE_ID = 2100000
UI_DEFAULT_SHADER = "{fileID: 10770, guid: 0000000000000000f000000000000000, type: 0}"


def font_set(step: StepRow, blobs: BlobStore) -> tuple[dict[str, Any] | None, Path | None]:
    """op.bitmap_font's font.json and atlas, also when a gate passed it through under another port name."""
    from studio_core.ops.bitmap_font import FONT_FILE, FONT_TYPE

    for refs in step.outputs.values():
        for ref in refs:
            if (ref.name or "") != FONT_FILE:
                continue
            try:
                doc = json.loads(blobs.path(ref.hash).read_bytes())
                atlas = blobs.path(str(doc["atlas"]["hash"]))
                if doc.get("type") != FONT_TYPE or not doc.get("chars") or not atlas.is_file():
                    return None, None
                return doc, atlas
            except (OSError, ValueError, KeyError, TypeError):
                return None, None
    return None, None


def _fnt(src: Source) -> bytes:
    from studio_core.ops.bitmap_font import fnt_text

    return fnt_text(src.font or {}, f"{src.asset}.png").encode()


def _sample(doc: dict[str, Any]) -> str:
    from studio_core.ops.bitmap_font import sample_texts

    return sample_texts("".join(c["char"] for c in doc["chars"]))[0]


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def godot_scene(src: Source) -> str:
    doc = src.font or {}
    lines = ["[gd_scene format=3]", "", f'[ext_resource type="FontFile" path="Fonts/{src.asset}.fnt" id="1_font"]',
             "", f'[node name="{src.asset}_font_demo" type="Control"]', "layout_mode = 3", "anchors_preset = 15",
             "anchor_right = 1.0", "anchor_bottom = 1.0", "", '[node name="Label" type="Label" parent="."]',
             "offset_left = 24.0", "offset_top = 24.0", 'theme_override_fonts/font = ExtResource("1_font")',
             f"theme_override_font_sizes/font_size = {int(doc.get('size') or 16)}",
             f'text = "{_esc(_sample(doc))}"']
    if doc.get("pixel"):
        lines.append("texture_filter = 1")
    return "\n".join(lines) + "\n"


def godot_files(src: Source) -> tuple[Files, list[str]]:
    assert src.font_atlas is not None
    a = src.asset
    files: Files = [(f"Fonts/{a}.png", src.font_atlas), (f"Fonts/{a}.fnt", _fnt(src)),
                    (f"{a}_font_demo.tscn", godot_scene(src).encode())]
    size = int((src.font or {}).get("size") or 16)
    note = (f"- Fonts/{a}.fnt + {a}.png: bitmap font BMFont, Godot 4 tự import thành FontFile (giữ 2 file cùng thư "
            f"mục). Gán vào Label/Button: Theme Overrides → Fonts → Font, cỡ chữ {size} là đúng 1:1 (cỡ khác thì "
            f"Godot co giãn ảnh). {a}_font_demo.tscn là Label mẫu.")
    if (src.font or {}).get("pixel"):
        note += " Pixel art: đặt Texture Filter = Nearest (scene mẫu đã đặt) hoặc Project Settings → Rendering → " \
                "Textures → Default Texture Filter = Nearest."
    return files, [note]


def font_settings(src: Source, tex_guid: str, mat_guid: str) -> str:
    """Legacy Custom Font: vert rects are relative to the baseline (y up), uv rects to the texture's bottom-left."""
    doc = src.font or {}
    W, H, base = int(doc["scale_w"]), int(doc["scale_h"]), int(doc["base"])
    rects = ""
    for c in doc["chars"]:
        x, y, w, h = int(c["x"]), int(c["y"]), int(c["width"]), int(c["height"])
        rects += f"""  - serializedVersion: 2
    index: {c['id']}
    uv:
      serializedVersion: 2
      x: {x / W:.6g}
      y: {(H - y - h) / H:.6g}
      width: {w / W:.6g}
      height: {h / H:.6g}
    vert:
      serializedVersion: 2
      x: {c['xoffset']}
      y: {base - int(c['yoffset'])}
      width: {w}
      height: {-h}
    advance: {c['xadvance']}
    flipped: 0
"""
    return f"""%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!128 &{FONT_FILE_ID}
Font:
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {{fileID: 0}}
  m_PrefabInstance: {{fileID: 0}}
  m_PrefabAsset: {{fileID: 0}}
  m_Name: {src.asset}
  serializedVersion: 5
  m_LineSpacing: {doc['line_height']}
  m_DefaultMaterial: {{fileID: {MATERIAL_FILE_ID}, guid: {mat_guid}, type: 2}}
  m_FontSize: {doc['size']}
  m_Texture: {{fileID: 2800000, guid: {tex_guid}, type: 3}}
  m_AsciiStartOffset: 0
  m_Tracking: 1
  m_CharacterSpacing: 0
  m_CharacterPadding: 1
  m_ConvertCase: 0
  m_CharacterRects:
{rects}  m_KerningValues: []
  m_PixelScale: 0.1
  m_FontData: []
  m_Ascent: {base}
  m_Descent: {int(doc['line_height']) - base}
  m_DefaultStyle: 0
  m_FontNames: []
  m_FallbackFonts: []
  m_FontRenderingMode: 0
  m_UseLegacyBoundsCalculation: 0
  m_ShouldRoundAdvanceValue: 1
"""


def font_material(name: str, tex_guid: str) -> str:
    return f"""%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!21 &{MATERIAL_FILE_ID}
Material:
  serializedVersion: 8
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {{fileID: 0}}
  m_PrefabInstance: {{fileID: 0}}
  m_PrefabAsset: {{fileID: 0}}
  m_Name: {name}
  m_Shader: {UI_DEFAULT_SHADER}
  m_Parent: {{fileID: 0}}
  m_ModifiedSerializedProperties: 0
  m_ValidKeywords: []
  m_InvalidKeywords: []
  m_LightmapFlags: 4
  m_EnableInstancingVariants: 0
  m_DoubleSidedGI: 0
  m_CustomRenderQueue: -1
  stringTagMap: {{}}
  disabledShaderPasses: []
  m_LockedProperties:
  m_SavedProperties:
    serializedVersion: 3
    m_TexEnvs:
    - _MainTex:
        m_Texture: {{fileID: 2800000, guid: {tex_guid}, type: 3}}
        m_Scale: {{x: 1, y: 1}}
        m_Offset: {{x: 0, y: 0}}
    m_Ints: []
    m_Floats: []
    m_Colors:
    - _Color: {{r: 1, g: 1, b: 1, a: 1}}
  m_BuildTextureStacks: []
  m_AllowLocking: 1
"""


def unity_files(src: Source) -> tuple[Files, list[str]]:
    from studio_core.export.unity import guid, native_meta, texture_meta

    assert src.font_atlas is not None
    a, doc = src.asset, src.font or {}
    png, fs, mat = f"Fonts/{a}.png", f"Fonts/{a}.fontsettings", f"Fonts/{a}_font.mat"
    tex_guid, fs_guid, mat_guid = guid(src.uid, a, png), guid(src.uid, a, fs), guid(src.uid, a, mat)
    size = (int(doc["scale_w"]), int(doc["scale_h"]))
    files: Files = [(png, src.font_atlas),
                    (f"{png}.meta", texture_meta(a, tex_guid, None, size, src.ppu, bool(doc.get("pixel"))).encode()),
                    (f"Fonts/{a}.fnt", _fnt(src)),
                    (fs, font_settings(src, tex_guid, mat_guid).encode()),
                    (f"{fs}.meta", native_meta(fs_guid, FONT_FILE_ID).encode()),
                    (mat, font_material(f"{a}_font", tex_guid).encode()),
                    (f"{mat}.meta", native_meta(mat_guid, MATERIAL_FILE_ID).encode())]
    note = (f"- Fonts/{a}.fontsettings: Custom Font (legacy) đã gắn atlas + material UI/Default (giữ màu atlas) — "
            f"dùng cho UI Text (legacy) / TextMesh, Font Size {doc.get('size')}, để màu chữ trắng. Chưa có kerning "
            f"(Custom Font không lưu cặp kern). TextMeshPro không đọc .fnt trực tiếp: dùng Fonts/{a}.fnt + {a}.png với "
            "importer BMFont bên thứ ba (vd. 'BMFont to TMP'), hoặc tạo TMP Sprite Asset từ atlas.")
    return files, [note]
