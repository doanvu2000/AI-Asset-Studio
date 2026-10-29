"""Trim sheet (op.trim_sheet) and decal pack (op.decal_pack) export. The trim sheet's material comes from the PBR
exporter (pbr_material.json); this adds trim.json. Decals become URP Decal Projector prefabs with a Shader Graphs/Decal
material in Unity and Decal nodes in Godot."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from studio_core.contracts import BlobStore, StepRow
from studio_core.export.source import Files, Source

# com.unity.render-pipelines.universal: Shaders/Decal.shadergraph and Runtime/Decal/DecalProjector.cs (17.x; the
# GUIDs have not changed since URP 12). A shader graph's main shader object is always this fileID.
DECAL_SHADER = "{fileID: -6465566751694194690, guid: 9b4e681081e2b4c469111bb649e2f7ee, type: 3}"
DECAL_PROJECTOR_SCRIPT = "{fileID: 11500000, guid: 0777d029ed3dffa4692f417d4aba19ca, type: 3}"
DECAL_DEPTH = 0.5


def _doc(step: StepRow, blobs: BlobStore, file: str, type_: str) -> dict[str, Any] | None:
    for refs in step.outputs.values():
        for ref in refs:
            if (ref.name or "") != file:
                continue
            try:
                doc = json.loads(blobs.path(ref.hash).read_bytes())
            except (OSError, ValueError):
                return None
            return doc if isinstance(doc, dict) and doc.get("type") == type_ else None
    return None


def trim_doc(step: StepRow, blobs: BlobStore) -> dict[str, Any] | None:
    from studio_core.ops.trim import TRIM_FILE, TRIM_TYPE

    return _doc(step, blobs, TRIM_FILE, TRIM_TYPE)


def decal_set(step: StepRow, blobs: BlobStore) -> tuple[dict[str, Any] | None, list[tuple[str, Path, Path]]]:
    """op.decal_pack's decal.json and (name, albedo, normal) paths, also through a gate."""
    from studio_core.export.sheet import ident
    from studio_core.ops.decal import DECAL_FILE, DECAL_TYPE

    doc = _doc(step, blobs, DECAL_FILE, DECAL_TYPE)
    if doc is None:
        return None, []
    out, seen = [], set()
    try:
        for d in doc["decals"]:
            name = ident(str(d["name"]), "decal")
            a, n = blobs.path(str(d["albedo"]["hash"])), blobs.path(str(d["normal"]["hash"]))
            if name in seen or not a.is_file() or not n.is_file():
                continue
            seen.add(name)
            d["name"] = name
            out.append((name, a, n))
    except (KeyError, TypeError):
        return None, []
    return doc, out


def _trim_note(doc: dict[str, Any]) -> str:
    names = ", ".join(f"{s['name']} ({s['px']}px)" for s in doc.get("strips") or [])
    return (f"- {doc.get('asset', 'trim')}_trim.json: các dải của trim sheet từ trên xuống — {names}. Mỗi dải có v0/v1 "
            "(UV, gốc dưới) và v0_safe/v1_safe (lùi 2px chống lem mipmap); map mặt vào dải theo tên, U lặp tự do.")


def _decal_entry(doc: dict[str, Any], name: str) -> dict[str, Any]:
    return next((d for d in doc.get("decals") or [] if d.get("name") == name), {})


def decal_material(name: str, albedo: str, normal: str) -> str:
    from studio_core.export.pbr import MATERIAL_FILE_ID

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
  m_Shader: {DECAL_SHADER}
  m_Parent: {{fileID: 0}}
  m_ModifiedSerializedProperties: 0
  m_ValidKeywords: []
  m_InvalidKeywords: []
  m_LightmapFlags: 4
  m_EnableInstancingVariants: 1
  m_DoubleSidedGI: 0
  m_CustomRenderQueue: -1
  stringTagMap: {{}}
  disabledShaderPasses: []
  m_LockedProperties:
  m_SavedProperties:
    serializedVersion: 3
    m_TexEnvs:
    - Base_Map:
        m_Texture: {{fileID: 2800000, guid: {albedo}, type: 3}}
        m_Scale: {{x: 1, y: 1}}
        m_Offset: {{x: 0, y: 0}}
    - Normal_Map:
        m_Texture: {{fileID: 2800000, guid: {normal}, type: 3}}
        m_Scale: {{x: 1, y: 1}}
        m_Offset: {{x: 0, y: 0}}
    m_Ints: []
    m_Floats:
    - Normal_Blend: 0.5
    - _DrawOrder: 0
    m_Colors: []
  m_BuildTextureStacks: []
  m_AllowLocking: 1
"""


def decal_prefab(name: str, mat_guid: str, w: float, h: float) -> str:
    """Rotated 90° about X so the projector looks straight down: dropped on a floor it paints it; turn it to face a
    wall for wall decals. Size is (width, height, projection depth)."""
    from studio_core.export.unity import file_id

    go, tr, mb = (file_id("decal", name, k) for k in ("go", "tr", "mb"))
    return f"""%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!1 &{go}
GameObject:
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {{fileID: 0}}
  m_PrefabInstance: {{fileID: 0}}
  m_PrefabAsset: {{fileID: 0}}
  serializedVersion: 6
  m_Component:
  - component: {{fileID: {tr}}}
  - component: {{fileID: {mb}}}
  m_Layer: 0
  m_Name: {name}
  m_TagString: Untagged
  m_Icon: {{fileID: 0}}
  m_NavMeshLayer: 0
  m_StaticEditorFlags: 0
  m_IsActive: 1
--- !u!4 &{tr}
Transform:
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {{fileID: 0}}
  m_PrefabInstance: {{fileID: 0}}
  m_PrefabAsset: {{fileID: 0}}
  m_GameObject: {{fileID: {go}}}
  serializedVersion: 2
  m_LocalRotation: {{x: 0.70710677, y: 0, z: 0, w: 0.70710677}}
  m_LocalPosition: {{x: 0, y: 0, z: 0}}
  m_LocalScale: {{x: 1, y: 1, z: 1}}
  m_ConstrainProportionsScale: 0
  m_Children: []
  m_Father: {{fileID: 0}}
  m_LocalEulerAnglesHint: {{x: 90, y: 0, z: 0}}
--- !u!114 &{mb}
MonoBehaviour:
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {{fileID: 0}}
  m_PrefabInstance: {{fileID: 0}}
  m_PrefabAsset: {{fileID: 0}}
  m_GameObject: {{fileID: {go}}}
  m_Enabled: 1
  m_EditorHideFlags: 0
  m_Script: {DECAL_PROJECTOR_SCRIPT}
  m_Name:
  m_EditorClassIdentifier:
  m_Material: {{fileID: 2100000, guid: {mat_guid}, type: 2}}
  m_DrawDistance: 1000
  m_FadeScale: 0.9
  m_StartAngleFade: 180
  m_EndAngleFade: 180
  m_UVScale: {{x: 1, y: 1}}
  m_UVBias: {{x: 0, y: 0}}
  m_ScaleMode: 0
  m_Offset: {{x: 0, y: 0, z: {DECAL_DEPTH / 2:g}}}
  m_Size: {{x: {w:.4g}, y: {h:.4g}, z: {DECAL_DEPTH:g}}}
  m_FadeFactor: 1
"""


def _decal_tex_meta(asset: str, g: str, size: tuple[int, int], normal: bool) -> bytes:
    from studio_core.export.unity import texture_meta

    meta = texture_meta(asset, g, None, size, 100.0, False, is_normal=normal, repeat=True)
    # Decals clamp (a repeated texture would paint its opposite edge inside the projector) and keep their alpha.
    meta = meta.replace("wrapU: 0", "wrapU: 1").replace("wrapV: 0", "wrapV: 1").replace("wrapW: 0", "wrapW: 1")
    return (meta if normal else meta.replace("alphaIsTransparency: 0", "alphaIsTransparency: 1")).encode()


def _size(p: Path) -> tuple[int, int]:
    from PIL import Image

    with Image.open(p) as im:
        return im.size


def unity_files(src: Source) -> tuple[Files, list[str]]:
    from studio_core.export.pbr import MATERIAL_FILE_ID
    from studio_core.export.unity import guid, native_meta, prefab_meta

    files: Files = []
    notes: list[str] = []
    if src.trim:
        files.append((f"{src.trim.get('asset', src.asset)}_trim.json", json.dumps(src.trim, indent=1).encode()))
        notes.append(_trim_note(src.trim))
    if src.decals_doc and src.decals:
        a, u = src.asset, src.uid
        for name, albedo, normal in src.decals:
            e = _decal_entry(src.decals_doc, name)
            rel = f"Decals/{name}"
            ga, gn = guid(u, a, f"{rel}.png"), guid(u, a, f"{rel}_normal.png")
            gm, gp = guid(u, a, f"{rel}.mat"), guid(u, a, f"{rel}.prefab")
            files += [(f"{rel}.png", albedo), (f"{rel}.png.meta", _decal_tex_meta(a, ga, _size(albedo), False)),
                      (f"{rel}_normal.png", normal),
                      (f"{rel}_normal.png.meta", _decal_tex_meta(a, gn, _size(normal), True)),
                      (f"{rel}.mat", decal_material(name, ga, gn).encode()),
                      (f"{rel}.mat.meta", native_meta(gm, MATERIAL_FILE_ID).encode()),
                      (f"{rel}.prefab", decal_prefab(name, gm, float(e.get("width_m") or 1),
                                                     float(e.get("height_m") or 1)).encode()),
                      (f"{rel}.prefab.meta", prefab_meta(gp).encode())]
        notes.append(f"- Decals/: {len(src.decals)} decal, mỗi cái .prefab (URP Decal Projector nhìn xuống, cỡ theo "
                     "decal.json, sâu 0.5 m) + .mat (Shader Graphs/Decal, Base Map + Normal Map). Cần URP và thêm "
                     "renderer feature Decal vào URP Renderer (Add Renderer Feature > Decal); project Built-in không "
                     "có Decal Projector. Xoay prefab cho mặt chiếu hướng vào tường để dán lên tường.")
    return files, notes


def decal_scene(name: str, albedo: str, normal: str, w: float, h: float) -> str:
    """Godot's Decal projects along its local -Y over size (x, y = depth, z)."""
    return ("[gd_scene load_steps=3 format=3]\n\n"
            f'[ext_resource type="Texture2D" path="{albedo}" id="1_albedo"]\n'
            f'[ext_resource type="Texture2D" path="{normal}" id="2_normal"]\n\n'
            f'[node name="{name}" type="Decal"]\n'
            f"size = Vector3({w:.4g}, {DECAL_DEPTH:g}, {h:.4g})\n"
            'texture_albedo = ExtResource("1_albedo")\ntexture_normal = ExtResource("2_normal")\n')


def godot_files(src: Source) -> tuple[Files, list[str]]:
    files: Files = []
    notes: list[str] = []
    if src.trim:
        files.append((f"{src.trim.get('asset', src.asset)}_trim.json", json.dumps(src.trim, indent=1).encode()))
        notes.append(_trim_note(src.trim))
    if src.decals_doc and src.decals:
        for name, albedo, normal in src.decals:
            e = _decal_entry(src.decals_doc, name)
            files += [(f"Decals/{name}.png", albedo), (f"Decals/{name}_normal.png", normal),
                      (f"Decals/{name}.tscn", decal_scene(name, f"{name}.png", f"{name}_normal.png",
                                                         float(e.get("width_m") or 1),
                                                         float(e.get("height_m") or 1)).encode())]
        notes.append(f"- Decals/*.tscn: {len(src.decals)} node Decal (chiếu xuống theo -Y, cỡ theo decal.json, sâu "
                     "0.5 m) gắn albedo + normal; kéo vào scene, xoay để dán lên tường. Godot nhận normal map khi "
                     "Decal dùng tới; chạy Forward+/Mobile (Compatibility không vẽ Decal).")
    return files, notes
