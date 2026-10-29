"""Mesh retexture export (#41): the textured GLB and the original sit in Models/ (generic GLB export), the URP Lit /
StandardMaterial3D come from the PBR exporter; this adds a Built-in RP Standard material on the same maps and the
bake report."""
from __future__ import annotations

import json
from typing import Any

from studio_core.export.source import Files, Source

STANDARD_SHADER = "{fileID: 46, guid: 0000000000000000f000000000000000, type: 0}"


def retexture_doc(step: Any, blobs: Any) -> dict[str, Any] | None:
    """op.project_bake's retexture.json, also when a gate passed it through under another port name."""
    from studio_core.ops.retexture import REPORT_FILE, REPORT_TYPE

    for refs in step.outputs.values():
        for ref in refs:
            if (ref.name or "") != REPORT_FILE:
                continue
            try:
                doc = json.loads(blobs.path(ref.hash).read_bytes())
            except (OSError, ValueError):
                return None
            return doc if isinstance(doc, dict) and doc.get("type") == REPORT_TYPE else None
    return None


def standard_material(name: str, albedo: str, normal: str | None, mask: str | None, ao: str | None) -> str:
    from studio_core.export.pbr import MATERIAL_FILE_ID, _tex_env

    keywords = [k for k, g in (("_METALLICGLOSSMAP", mask), ("_NORMALMAP", normal)) if g]
    envs = "".join(_tex_env(p, g) for p, g in (
        ("_BumpMap", normal), ("_DetailAlbedoMap", None), ("_DetailMask", None), ("_DetailNormalMap", None),
        ("_EmissionMap", None), ("_MainTex", albedo), ("_MetallicGlossMap", mask), ("_OcclusionMap", ao),
        ("_ParallaxMap", None)))
    floats = {"_BumpScale": 1, "_Cutoff": 0.5, "_DetailNormalMapScale": 1, "_DstBlend": 0, "_GlossMapScale": 1,
              "_Glossiness": 0.5, "_GlossyReflections": 1, "_Metallic": 0, "_Mode": 0, "_OcclusionStrength": 1,
              "_Parallax": 0.02, "_SmoothnessTextureChannel": 0, "_SpecularHighlights": 1, "_SrcBlend": 1,
              "_UVSec": 0, "_ZWrite": 1}
    kw = "".join(f"  - {k}\n" for k in keywords)
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
  m_Shader: {STANDARD_SHADER}
  m_Parent: {{fileID: 0}}
  m_ModifiedSerializedProperties: 0
  m_ValidKeywords:{chr(10) + kw.rstrip(chr(10)) if kw else " []"}
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
{envs}    m_Ints: []
    m_Floats:
{"".join(f"    - {k}: {v}{chr(10)}" for k, v in floats.items())}    m_Colors:
    - _Color: {{r: 1, g: 1, b: 1, a: 1}}
    - _EmissionColor: {{r: 0, g: 0, b: 0, a: 1}}
  m_BuildTextureStacks: []
  m_AllowLocking: 1
"""


def _summary(src: Source) -> str:
    doc = src.retexture or {}
    uv = doc.get("uv") or {}
    warn = ", ".join(doc.get("warnings") or []) or "không"
    return (f"- Retexture: {doc.get('stem', src.asset)}_retextured.glb (texture {doc.get('texture')}px, "
            f"{len(doc.get('views') or [])} góc, bề mặt nhìn thấy chưa tô {doc.get('unpainted_pct')}% "
            f"đã lấp từ vùng bên cạnh; "
            f"UV {'unwrap mới' if uv.get('unwrapped') else 'giữ nguyên'}, chồng lấp {uv.get('overlap')}; "
            f"cảnh báo: {warn}). *_original.glb là bản gốc để so; hình học và tỉ lệ giữ nguyên.")


def unity_files(src: Source) -> tuple[Files, list[str]]:
    from studio_core.export.pbr import _prefix
    from studio_core.export.unity import guid, native_meta

    files: Files = [(f"{src.asset}_retexture.json", json.dumps(src.retexture, indent=1).encode())]
    notes = [_summary(src)]
    for name, maps in src.pbr[:1]:
        pre = _prefix(src, name)

        def g(role: str, present: bool) -> str | None:
            return guid(src.uid, src.asset, f"PBR/{pre}_{role}.png") if present else None

        mask = g("metallic_smoothness", "metallic" in maps and "roughness" in maps)
        rel = f"PBR/{pre}_Standard.mat"
        mat = standard_material(f"{pre}_Standard", g("albedo", True) or "", g("normal", "normal" in maps), mask,
                                g("ao", "ao" in maps))
        files += [(rel, mat.encode()), (f"{rel}.meta", native_meta(guid(src.uid, src.asset, rel),
                                                                     2100000).encode())]
        notes.append(f"- PBR/{pre}_Standard.mat: cùng bộ map cho Built-in RP (shader Standard); project URP dùng "
                     f"PBR/{pre}.mat. glTFast tự tạo material từ GLB, kéo .mat lên mesh nếu muốn đổi.")
    return files, notes


def godot_files(src: Source) -> tuple[Files, list[str]]:
    files: Files = [(f"{src.asset}_retexture.json", json.dumps(src.retexture, indent=1).encode())]
    notes = [_summary(src), "- Godot nhập GLB kèm material (albedo, normal, roughness/metallic); PBR/*.tres là "
                            "StandardMaterial3D cùng bộ map + AO, gán vào Material Override nếu cần."]
    return files, notes
