"""PBR material export (op.pbr maps): Unity URP Lit .mat with hand-written .meta files, Godot StandardMaterial3D."""
from __future__ import annotations

import io
from pathlib import Path

from studio_core.export.source import Files, Source

MATERIAL_FILE_ID = 2100000
URP_LIT_GUID = "933532a4fcc9baf4fa0491de14d08ed7"


def _prefix(src: Source, name: str) -> str:
    return src.asset if len(src.pbr) == 1 else f"{src.asset}_{name}"


def _image_size(p: Path) -> tuple[int, int]:
    from PIL import Image

    with Image.open(p) as im:
        return im.size


def metallic_smoothness(metallic: Path, roughness: Path) -> bytes:
    """Unity's metallic workflow reads metallic from R and smoothness (1 - roughness) from A of one texture."""
    import numpy as np
    from PIL import Image

    with Image.open(metallic) as m, Image.open(roughness) as r:
        met = np.asarray(m.convert("L"))
        rough = np.asarray(r.convert("L").resize(m.size))
    zero = np.zeros_like(met)
    rgba = np.stack([met, zero, zero, 255 - rough], -1).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, "PNG")
    return buf.getvalue()


def _tex_env(prop: str, guid: str | None) -> str:
    tex = f"{{fileID: 2800000, guid: {guid}, type: 3}}" if guid else "{fileID: 0}"
    return (f"    - {prop}:\n        m_Texture: {tex}\n        m_Scale: {{x: 1, y: 1}}\n"
            f"        m_Offset: {{x: 0, y: 0}}\n")


def urp_lit_material(name: str, albedo: str, normal: str | None, mask: str | None, ao: str | None,
                     height: str | None) -> str:
    keywords = [k for k, g in (("_METALLICSPECGLOSSMAP", mask), ("_NORMALMAP", normal), ("_OCCLUSIONMAP", ao),
                               ("_PARALLAXMAP", height)) if g]
    envs = "".join(_tex_env(p, g) for p, g in (
        ("_BaseMap", albedo), ("_BumpMap", normal), ("_DetailAlbedoMap", None), ("_DetailMask", None),
        ("_DetailNormalMap", None), ("_EmissionMap", None), ("_MainTex", albedo), ("_MetallicGlossMap", mask),
        ("_OcclusionMap", ao), ("_ParallaxMap", height), ("_SpecGlossMap", None)))
    floats = {"_AlphaClip": 0, "_Blend": 0, "_BumpScale": 1, "_Cull": 2, "_Cutoff": 0.5, "_DstBlend": 0,
              "_EnvironmentReflections": 1, "_GlossMapScale": 1, "_Glossiness": 0.5,
              "_Metallic": 1 if mask else 0, "_OcclusionStrength": 1, "_Parallax": 0.005, "_QueueOffset": 0,
              "_ReceiveShadows": 1, "_Smoothness": 1 if mask else 0.25, "_SmoothnessTextureChannel": 0,
              "_SpecularHighlights": 1, "_SrcBlend": 1, "_Surface": 0, "_WorkflowMode": 1, "_ZWrite": 1}
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
  m_Shader: {{fileID: 4800000, guid: {URP_LIT_GUID}, type: 3}}
  m_Parent: {{fileID: 0}}
  m_ModifiedSerializedProperties: 0
  m_ValidKeywords:{chr(10) + kw.rstrip(chr(10)) if kw else " []"}
  m_InvalidKeywords: []
  m_LightmapFlags: 4
  m_EnableInstancingVariants: 0
  m_DoubleSidedGI: 0
  m_CustomRenderQueue: -1
  stringTagMap:
    RenderType: Opaque
  disabledShaderPasses: []
  m_LockedProperties:
  m_SavedProperties:
    serializedVersion: 3
    m_TexEnvs:
{envs}    m_Ints: []
    m_Floats:
{"".join(f"    - {k}: {v}{chr(10)}" for k, v in floats.items())}    m_Colors:
    - _BaseColor: {{r: 1, g: 1, b: 1, a: 1}}
    - _Color: {{r: 1, g: 1, b: 1, a: 1}}
    - _EmissionColor: {{r: 0, g: 0, b: 0, a: 1}}
    - _SpecColor: {{r: 0.2, g: 0.2, b: 0.2, a: 1}}
  m_BuildTextureStacks: []
  m_AllowLocking: 1
"""


README_UNITY = """- PBR/{name}.mat: material URP Lit đã gắn Base Map, Normal Map, Metallic Map (R = metallic, A = smoothness
  = 1 − roughness), Occlusion và Height (parallax nhẹ 0.005). Texture import sẵn Wrap = Repeat; normal map đặt
  Texture Type = Normal map, map dữ liệu (metallic, AO, height) tắt sRGB. Kéo .mat lên mesh, chỉnh Tiling để lặp.
  Built-in RP thì đổi shader sang Standard rồi gán lại các map."""


def unity_files(src: Source) -> tuple[Files, list[str]]:
    from studio_core.export.unity import guid, native_meta, texture_meta

    a, u = src.asset, src.uid
    files: Files = []
    notes: list[str] = []
    for name, maps in src.pbr:
        pre = _prefix(src, name)
        size = _image_size(maps["albedo"])
        guids: dict[str, str] = {}

        def add(role: str, data: bytes | Path, *, normal: bool = False, linear: bool = False) -> None:
            rel = f"PBR/{pre}_{role}.png"
            guids[role] = guid(u, a, rel)
            meta = texture_meta(a, guids[role], None, size, src.ppu, False, is_normal=normal, repeat=True)
            if linear:
                meta = meta.replace("sRGBTexture: 1", "sRGBTexture: 0", 1)
            files.extend([(rel, data), (f"{rel}.meta", meta.encode())])

        add("albedo", maps["albedo"])
        if "normal" in maps:
            add("normal", maps["normal"], normal=True)
        if "metallic" in maps and "roughness" in maps:
            add("metallic_smoothness", metallic_smoothness(maps["metallic"], maps["roughness"]), linear=True)
        for role in ("ao", "height"):
            if role in maps:
                add(role, maps[role], linear=True)
        mat_rel = f"PBR/{pre}.mat"
        files += [(mat_rel, urp_lit_material(pre, guids["albedo"], guids.get("normal"),
                                             guids.get("metallic_smoothness"), guids.get("ao"),
                                             guids.get("height")).encode()),
                  (f"{mat_rel}.meta", native_meta(guid(u, a, mat_rel), MATERIAL_FILE_ID).encode())]
        notes.append(README_UNITY.format(name=pre))
    return files, notes


def standard_material(pre: str, roles: list[str]) -> str:
    """ext_resource paths are relative to the .tres, which sits next to its maps."""
    ids = {r: f"{i + 1}_{r}" for i, r in enumerate(roles)}
    ext = [f'[ext_resource type="Texture2D" path="{pre}_{r}.png" id="{ids[r]}"]' for r in roles]
    props = ['albedo_texture = ExtResource("{}")'.format(ids["albedo"])]
    if "metallic" in ids:
        # Godot multiplies the texture by `metallic` (default 0), so the map only shows with the factor at 1.
        props += ["metallic = 1.0", f'metallic_texture = ExtResource("{ids["metallic"]}")']
    if "roughness" in ids:
        props += ["roughness = 1.0", f'roughness_texture = ExtResource("{ids["roughness"]}")']
    if "normal" in ids:
        props += ["normal_enabled = true", f'normal_texture = ExtResource("{ids["normal"]}")']
    if "ao" in ids:
        props += ["ao_enabled = true", f'ao_texture = ExtResource("{ids["ao"]}")']
    if "height" in ids:
        props += ["heightmap_scale = 1.0", f'heightmap_texture = ExtResource("{ids["height"]}")']
    props.append("texture_repeat = true")
    return (f'[gd_resource type="StandardMaterial3D" load_steps={len(ext) + 1} format=3]\n\n' + "\n".join(ext)
            + "\n\n[resource]\n" + "\n".join(props) + "\n")


README_GODOT = """- PBR/{name}.tres: StandardMaterial3D đã gắn albedo, normal, roughness, metallic, AO (texture_repeat bật,
  chỉnh uv1_scale để lặp). Height đã gán sẵn nhưng tắt: bật Height (parallax) trong Inspector nếu muốn nổi khối.
  Godot tự nhận normal/roughness map khi material dùng tới và import lại cho đúng."""


def godot_files(src: Source) -> tuple[Files, list[str]]:
    files: Files = []
    notes: list[str] = []
    for name, maps in src.pbr:
        pre = _prefix(src, name)
        roles = [r for r in ("albedo", "normal", "roughness", "metallic", "ao", "height") if r in maps]
        files += [(f"PBR/{pre}_{r}.png", maps[r]) for r in roles]
        files.append((f"PBR/{pre}.tres", standard_material(pre, roles).encode()))
        notes.append(README_GODOT.format(name=pre))
    return files, notes
