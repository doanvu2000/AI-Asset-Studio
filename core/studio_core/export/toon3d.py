"""Toon / outline material for exported 3D models: hand-written Unity URP HLSL (ramp-lit pass + inverted-hull outline pass)
and Godot spatial shader (+ `next_pass` outline), one ramp texture from the dominant colour of the model's base texture.
`apply` adds the files to an export (and, for Godot, a *_toon.tscn per GLB; for Unity an editor script that builds toon
prefabs from the imported models); wire `material_style: toon` to it from the recipe."""
from __future__ import annotations

import io
import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from studio_core.adapters.blender.toon_bands import band_levels
from studio_core.export.shaders import (Param, Preset, ShaderChoice, ShaderError, num as _num, parse_color,
                                        template_dir, unity_material)
from studio_core.export.source import Files, Source

BANDS = (2, 4)
TINT_MIX = 0.5
UNITY_SHADER_GUID = "7c3f1e5a9b2d4806a1d94e0b5f6c2a31"
UNITY_EDITOR_GUID = "b8e24d7f3a9c4c15902e6d1f4a7b3c58"
UNITY_SHADER_REL = "Shaders/AssetStudio_Toon3D.shader"
UNITY_EDITOR_REL = "Editor/AssetStudioToon3D.cs"
GODOT_SHADERS = ("toon3d.gdshader", "toon3d_outline.gdshader", "toon3d_apply.gd")

PARAMS = (
    Param("base_color", "albedo_color", "_BaseColor", "#ffffff"),
    Param("rim_color", "rim_color", "_RimColor", "#ffffff"),
    Param("rim", "rim_strength", "_RimStrength", 0.35),
    Param("rim_threshold", "rim_threshold", "_RimThreshold", 0.72),
    Param("outline_color", "outline_color", "_OutlineColor", "#0d0d12"),
    Param("outline_width", "width", "_OutlineWidth", 0.01, 0, 1),
    Param("smooth_normals", "smooth_normals", "_SmoothNormals", 0.35),
)
PRESET = Preset("toon3d", "Toon 3D", "Toon3D", PARAMS)


@dataclass(frozen=True)
class ToonPreset:
    bands: int = 3
    outline_color: str = "#0d0d12"
    outline_width: float = 0.012
    rim: float = 0.35
    rim_color: str = "#ffffff"
    smooth_normals: float = 0.35

    def __post_init__(self) -> None:
        if isinstance(self.bands, bool) or not isinstance(self.bands, int) or not BANDS[0] <= self.bands <= BANDS[1]:
            raise ShaderError(f"toon bands must be an integer in [{BANDS[0]}, {BANDS[1]}]")
        parse_color(self.outline_color)
        parse_color(self.rim_color)
        for name, lo, hi in (("outline_width", 0.0, 0.1), ("rim", 0.0, 1.0), ("smooth_normals", 0.0, 1.0)):
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= float(v) <= hi:
                raise ShaderError(f"toon {name} must be a number in [{lo:g}, {hi:g}]")


def preset_from(params: dict[str, Any] | None) -> ToonPreset:
    fields = ToonPreset.__dataclass_fields__
    unknown = [k for k in (params or {}) if k not in fields]
    if unknown:
        raise ShaderError(f"unknown toon parameter '{unknown[0]}' (use {', '.join(fields)})")
    return ToonPreset(**(params or {}))


@dataclass(frozen=True)
class ModelInfo:
    """Outline sizing (largest bounding-box side, centre in mesh space) and the base colour behind the ramp tint."""
    size: float = 1.0
    center: tuple[float, float, float] = (0.0, 0.0, 0.0)
    color: tuple[int, int, int] | None = None
    factor: tuple[float, float, float] = (1.0, 1.0, 1.0)


def _glb_json(data: bytes) -> tuple[dict[str, Any], bytes]:
    if len(data) < 20 or data[:4] != b"glTF":
        raise ShaderError("not a binary glTF (.glb) file")
    doc: dict[str, Any] = {}
    blob = b""
    pos = 12
    while pos + 8 <= len(data):
        n, kind = struct.unpack_from("<I4s", data, pos)
        chunk = data[pos + 8:pos + 8 + n]
        if kind == b"JSON":
            doc = json.loads(chunk.decode("utf-8"))
        elif kind == b"BIN\x00":
            blob = chunk
        pos += 8 + n
    return doc, blob


def _base_color(doc: dict[str, Any], blob: bytes) -> tuple[tuple[int, int, int] | None, tuple[float, float, float]]:
    """No base texture (vertex-colour or flat material) gives no dominant colour, so the ramp stays neutral."""
    pbr = next(((m.get("pbrMetallicRoughness") or {}) for m in doc.get("materials", [])), {})
    f = pbr.get("baseColorFactor") or [1, 1, 1, 1]
    factor = (float(f[0]), float(f[1]), float(f[2]))
    tex = (pbr.get("baseColorTexture") or {}).get("index")
    try:
        from PIL import Image

        from studio_core.ops.rarity import dominant

        view = doc["bufferViews"][doc["images"][doc["textures"][tex]["source"]]["bufferView"]]
        raw = blob[view.get("byteOffset", 0):view.get("byteOffset", 0) + view["byteLength"]]
        cols = dominant(Image.open(io.BytesIO(raw)))
        return (cols[len(cols) // 2] if cols else None), factor
    except (KeyError, IndexError, TypeError, OSError):
        return None, factor


def inspect_glb(path: Path) -> ModelInfo:
    doc, blob = _glb_json(Path(path).read_bytes())
    lo, hi = [1e30] * 3, [-1e30] * 3
    for mesh in doc.get("meshes", []):
        for prim in mesh.get("primitives", []):
            idx = (prim.get("attributes") or {}).get("POSITION")
            acc = doc["accessors"][idx] if idx is not None else {}
            if "min" in acc and "max" in acc:
                lo = [min(a, b) for a, b in zip(lo, acc["min"])]
                hi = [max(a, b) for a, b in zip(hi, acc["max"])]
    color, factor = _base_color(doc, blob)
    if lo[0] > hi[0]:
        return ModelInfo(color=color, factor=factor)
    size = max(h - l for l, h in zip(lo, hi))
    return ModelInfo(size=size if size > 1e-6 else 1.0, center=tuple((l + h) / 2 for l, h in zip(lo, hi)),
                     color=color, factor=factor)  # type: ignore[arg-type]


def ramp_colors(bands: int, color: tuple[int, int, int] | None) -> list[tuple[int, int, int]]:
    import numpy as np

    from studio_core.ops.rarity import ramp_lut

    levels = band_levels(bands)
    out = []
    for lv in levels:
        grey = np.full(3, lv, np.float32)
        if color is not None:
            tinted = ramp_lut(color)[round(lv * 255)]
            grey = grey + (tinted - grey) * TINT_MIX
        out.append(tuple(int(v) for v in np.clip(np.round(grey * 255), 0, 255)))
    return out  # type: ignore[return-value]


def ramp_png(bands: int, color: tuple[int, int, int] | None) -> bytes:
    from PIL import Image

    img = Image.new("RGB", (bands, 1))
    img.putdata(ramp_colors(bands, color))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{round(max(0.0, min(1.0, v)) * 255):02x}" for v in rgb)


def _values(t: ToonPreset, info: ModelInfo) -> dict[str, float | str]:
    return {"base_color": _hex(info.factor), "rim_color": t.rim_color, "rim": float(t.rim),
            "outline_color": t.outline_color, "outline_width": float(t.outline_width) * info.size,
            "smooth_normals": float(t.smooth_normals)}


def godot_material(t: ToonPreset, info: ModelInfo, ramp: str) -> str:
    v = _values(t, info)
    color = lambda h: "Color(" + ", ".join(_num(x) for x in parse_color(h)) + ")"  # noqa: E731
    return f"""[gd_resource type="ShaderMaterial" load_steps=5 format=3]

[ext_resource type="Shader" path="toon3d.gdshader" id="1_toon"]
[ext_resource type="Shader" path="toon3d_outline.gdshader" id="2_outline"]
[ext_resource type="Texture2D" path="{ramp}" id="3_ramp"]

[sub_resource type="ShaderMaterial" id="ShaderMaterial_outline"]
render_priority = 0
shader = ExtResource("2_outline")
shader_parameter/outline_color = {color(t.outline_color)}
shader_parameter/width = {_num(v["outline_width"])}
shader_parameter/smooth_normals = {_num(t.smooth_normals)}
shader_parameter/center = Vector3({", ".join(_num(c) for c in info.center)})

[resource]
resource_local_to_scene = true
render_priority = 0
next_pass = SubResource("ShaderMaterial_outline")
shader = ExtResource("1_toon")
shader_parameter/albedo_color = {color(v["base_color"])}
shader_parameter/ramp = ExtResource("3_ramp")
shader_parameter/rim_color = {color(t.rim_color)}
shader_parameter/rim_strength = {_num(t.rim)}
"""


def godot_scene(glb: str, material: str) -> str:
    stem = Path(glb).stem
    return f"""[gd_scene load_steps=4 format=3]

[ext_resource type="PackedScene" path="Models/{glb}" id="1_model"]
[ext_resource type="Script" path="Shaders/toon3d_apply.gd" id="2_apply"]
[ext_resource type="Material" path="{material}" id="3_toon"]

[node name="{stem}" type="Node3D"]
script = ExtResource("2_apply")
toon_material = ExtResource("3_toon")

[node name="Model" parent="." instance=ExtResource("1_model")]
"""


def unity_toon_material(t: ToonPreset, info: ModelInfo, name: str, ramp_guid: str) -> str:
    text = unity_material(ShaderChoice(PRESET, _values(t, info)), name, UNITY_SHADER_GUID, ramp_guid, t.bands)
    cx, cy, cz = info.center
    return text.replace("    m_Colors:\n", f"    m_Colors:\n    - _Center: {{r: {cx:.6g}, g: {cy:.6g}, b: {cz:.6g}, a: 0}}\n", 1)


UNITY_EDITOR_SCRIPT = """// Generated by AI Asset Studio: select a *_toon3d.mat plus the imported models (GLB/FBX/prefab), then menu
// Asset Studio > Apply Toon 3D Material writes <model>_Toon.prefab next to each one, with a toon copy of every material.
using System.IO;
using UnityEditor;
using UnityEngine;

public static class AssetStudioToon3D
{
    [MenuItem("Asset Studio/Apply Toon 3D Material")]
    static void Apply()
    {
        Material toon = null;
        foreach (var o in Selection.objects) if (o is Material m && m.shader.name == "AssetStudio/Toon3D") toon = m;
        if (toon == null) { Debug.LogWarning("Select a *_toon3d.mat from the export first."); return; }
        foreach (var o in Selection.objects)
        {
            if (o is Material) continue;
            var path = AssetDatabase.GetAssetPath(o);
            var go = o as GameObject;
            if (go == null) continue;
            var inst = (GameObject)PrefabUtility.InstantiatePrefab(go);
            var dir = Path.GetDirectoryName(path);
            foreach (var r in inst.GetComponentsInChildren<Renderer>())
            {
                var mats = r.sharedMaterials;
                for (int i = 0; i < mats.Length; i++) mats[i] = ToonFor(toon, mats[i], dir);
                r.sharedMaterials = mats;
            }
            var prefab = dir + "/" + Path.GetFileNameWithoutExtension(path) + "_Toon.prefab";
            PrefabUtility.SaveAsPrefabAsset(inst, prefab);
            Object.DestroyImmediate(inst);
        }
        AssetDatabase.SaveAssets();
    }

    static Material ToonFor(Material toon, Material src, string dir)
    {
        // glTFast materials use baseColorTexture/baseColorFactor, not the URP names.
        var name = src != null ? src.name : "default";
        var path = dir + "/" + name + "_Toon.mat";
        var m = AssetDatabase.LoadAssetAtPath<Material>(path);
        if (m == null)
        {
            m = new Material(toon);
            AssetDatabase.CreateAsset(m, path);
        }
        if (src != null)
        {
            if (src.HasProperty("_BaseMap")) m.SetTexture("_BaseMap", src.GetTexture("_BaseMap"));
            else if (src.HasProperty("_MainTex")) m.SetTexture("_BaseMap", src.GetTexture("_MainTex"));
            else if (src.HasProperty("baseColorTexture")) m.SetTexture("_BaseMap", src.GetTexture("baseColorTexture"));
            if (src.HasProperty("_BaseColor")) m.SetColor("_BaseColor", src.GetColor("_BaseColor"));
            else if (src.HasProperty("_Color")) m.SetColor("_BaseColor", src.GetColor("_Color"));
            else if (src.HasProperty("baseColorFactor")) m.SetColor("_BaseColor", src.GetColor("baseColorFactor"));
        }
        EditorUtility.SetDirty(m);
        return m;
    }
}
"""

README = {
    "godot": """- Toon 3D: Shaders/toon3d*.gdshader + Shaders/{a}_toon3d.tres (ShaderMaterial, viền qua next_pass cull_front). Mở
  {a}_toon.tscn / <model>_toon.tscn để xem: script gán bản toon cho từng surface và giữ texture gốc. Đổi lúc chạy:
  material.set_shader_parameter("rim_strength", 0.5); viền: material.next_pass.set_shader_parameter("width", 0.02).""",
    "unity": """- Toon 3D: Shaders/AssetStudio_Toon3D.shader (URP HLSL, pass viền inverted hull) + Shaders/{a}_toon3d.mat. Chọn
  .mat cùng các model đã import rồi menu Asset Studio > Apply Toon 3D Material để tạo <model>_Toon.prefab (giữ texture
  gốc). Cần Universal Render Pipeline; model hard-edge hở viền thì tăng Outline Smooth Normals.""",
}


def apply(files: Files, engine: str, src: Source, preset: ToonPreset | None = None) -> Files:
    if not src.glb:
        raise ShaderError("toon 3D material needs a model (.glb)")
    t = preset or ToonPreset()
    a = src.asset
    info = inspect_glb(src.glb[0][1])
    ramp_rel = f"Shaders/{a}_toon3d_ramp.png"
    out = list(files) + [(ramp_rel, ramp_png(t.bands, info.color))]
    if engine == "godot":
        out += [(f"Shaders/{n}", template_dir() / n) for n in GODOT_SHADERS]
        mat = f"Shaders/{a}_toon3d.tres"
        out.append((mat, godot_material(t, info, Path(ramp_rel).name).encode()))
        out += [(f"{Path(n).stem}_toon.tscn", godot_scene(n, mat).encode()) for n, _ in src.glb]
    else:
        from studio_core.export.pbr import MATERIAL_FILE_ID
        from studio_core.export.unity import guid, native_meta, texture_meta
        from studio_core.export.npc import script_meta
        from studio_core.export.shaders import shader_meta

        ramp_guid = guid(src.uid, a, ramp_rel)
        mat = f"Shaders/{a}_toon3d.mat"
        out += [(f"{ramp_rel}.meta", texture_meta(a, ramp_guid, None, (t.bands, 1), src.ppu, True).encode()),
                (UNITY_SHADER_REL, template_dir() / Path(UNITY_SHADER_REL).name),
                (f"{UNITY_SHADER_REL}.meta", shader_meta(UNITY_SHADER_GUID).encode()),
                (mat, unity_toon_material(t, info, f"{a}_toon3d", ramp_guid).encode()),
                (f"{mat}.meta", native_meta(guid(src.uid, a, mat), MATERIAL_FILE_ID).encode()),
                (UNITY_EDITOR_REL, UNITY_EDITOR_SCRIPT.encode()),
                (f"{UNITY_EDITOR_REL}.meta", script_meta(UNITY_EDITOR_GUID).encode())]
    readme = dict(files).get("README.txt")
    if isinstance(readme, bytes):
        note = README[engine].format(a=a)
        out = [(n, (d.decode().rstrip("\n") + "\n" + note + "\n").encode() if n == "README.txt" else d)
               for n, d in out]
    return out
