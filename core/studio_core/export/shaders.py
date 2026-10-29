"""Sprite shader presets: hand-written Godot canvas_item / Unity URP 2D shaders from shader_templates/, one material per
preset with the export's parameters; with a single preset the sprite scene (Godot) or prefab (Unity) gets it assigned."""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from studio_core.export.source import Files, Source
from studio_core.paths import package_dir

MATERIAL_FILE_ID = 2100000
SHADER_FILE_ID = 4800000
RAMP_WIDTH = 256
UNITY_DEFAULT_SPRITE_MAT = "{fileID: 10754, guid: 0000000000000000f000000000000000, type: 0}"
_HEX = re.compile(r"^#?([0-9a-fA-F]{6})([0-9a-fA-F]{2})?$")


class ShaderError(ValueError):
    pass


@dataclass(frozen=True)
class Param:
    key: str
    godot: str
    unity: str
    default: float | str
    lo: float = 0.0
    hi: float = 1.0

    @property
    def is_color(self) -> bool:
        return isinstance(self.default, str)


@dataclass(frozen=True)
class Preset:
    id: str
    label: str
    unity: str
    params: tuple[Param, ...]
    hint: str = ""

    @property
    def shader_name(self) -> str:
        return f"AssetStudio/Sprite/{self.unity}"


PRESETS: dict[str, Preset] = {p.id: p for p in (
    Preset("hit_flash", "Hit flash", "HitFlash", (
        Param("color", "flash_color", "_FlashColor", "#ffffff"),
        Param("amount", "amount", "_Amount", 0.0),
        Param("speed", "speed", "_Speed", 0.0, 0, 20)),
        "tween amount 1 → 0 khi trúng đòn; speed > 0 tự nháy"),
    Preset("dissolve", "Dissolve", "Dissolve", (
        Param("progress", "progress", "_Progress", 0.0),
        Param("color", "edge_color", "_EdgeColor", "#ff8c1a"),
        Param("edge_width", "edge_width", "_EdgeWidth", 0.06, 0, 0.5),
        Param("noise_scale", "noise_scale", "_NoiseScale", 6.0, 1, 64),
        Param("speed", "speed", "_Speed", 0.0, 0, 20)),
        "tween progress 0 → 1 để tan biến (noise sinh trong shader); speed > 0 tự lặp"),
    Preset("outline_pulse", "Outline pulse", "OutlinePulse", (
        Param("color", "outline_color", "_OutlineColor", "#ffd94d"),
        Param("width", "width", "_Width", 1.0, 0, 8),
        Param("speed", "speed", "_Speed", 1.5, 0, 20),
        Param("min_alpha", "min_alpha", "_MinAlpha", 0.25)),
        "viền nhấp nháy theo alpha; mỗi khung cần viền trống ≥ width px, không thì lấn sang khung bên cạnh"),
    Preset("wind_sway", "Wind sway", "WindSway", (
        Param("strength", "strength", "_Strength", 0.06),
        Param("speed", "speed", "_Speed", 0.6, 0, 20)),
        "lắc theo gió, neo ở gốc node/pivot (chân): đỉnh lệch strength × chiều cao"),
    Preset("water_ripple", "Water ripple", "WaterRipple", (
        Param("color", "tint", "_Tint", "#ffffff"),
        Param("amplitude", "amplitude", "_Amplitude", 1.5, 0, 8),
        Param("wavelength", "wavelength", "_Wavelength", 12.0, 1, 128),
        Param("speed", "speed", "_Speed", 1.0, 0, 20)),
        "gợn nước ngang (lệch UV theo pixel) + tint"),
    Preset("palette_swap", "Palette swap", "PaletteSwap", (
        Param("color", "", "", "#d4af37"),
        Param("row", "row", "_Row", -1.0, -1, 255),
        Param("strength", "strength", "_Strength", 1.0)),
        "độ sáng mỗi pixel tra màu ở hàng row của ramp; đổi row là đổi bảng màu"),
)}


@dataclass
class ShaderChoice:
    preset: Preset
    values: dict[str, float | str] = field(default_factory=dict)


def template_dir() -> Path:
    return package_dir() / "export" / "shader_templates"


def catalog() -> list[dict[str, Any]]:
    """What the app shows: the editable colour/speed of each preset next to its id."""
    out = []
    for p in PRESETS.values():
        by_key = {q.key: q.default for q in p.params}
        out.append({"id": p.id, "label": p.label, "hint": p.hint, "color": by_key.get("color"),
                    "speed": by_key.get("speed"), "params": {q.key: q.default for q in p.params}})
    return out


def applies_to(src: Source) -> bool:
    return src.sheet is not None or bool(src.ui) or bool(src.rarity)


def parse_color(v: Any) -> tuple[float, float, float, float]:
    m = _HEX.match(str(v).strip()) if isinstance(v, str) else None
    if m is None:
        raise ShaderError(f"invalid colour '{v}' (use #rrggbb or #rrggbbaa)")
    h = m.group(1) + (m.group(2) or "ff")
    r, g, b, a = (int(h[i:i + 2], 16) / 255 for i in range(0, 8, 2))
    return r, g, b, a


def resolve(names: list[str] | None, params: dict[str, dict[str, Any]] | None = None) -> list[ShaderChoice]:
    out: list[ShaderChoice] = []
    for n in dict.fromkeys(names or []):
        if n not in PRESETS:
            raise ShaderError(f"unknown shader preset '{n}' (use {', '.join(PRESETS)})")
        out.append(ShaderChoice(PRESETS[n]))
    chosen = {c.preset.id: c for c in out}
    for name, values in (params or {}).items():
        if name not in PRESETS:
            raise ShaderError(f"unknown shader preset '{name}' in shader_params")
        spec = {q.key: q for q in PRESETS[name].params}
        for key, v in (values or {}).items():
            q = spec.get(key)
            if q is None:
                raise ShaderError(f"shader '{name}' has no parameter '{key}' (use {', '.join(spec)})")
            if q.is_color:
                parse_color(v)
            elif isinstance(v, bool) or not isinstance(v, (int, float)) or not q.lo <= float(v) <= q.hi:
                raise ShaderError(f"shader '{name}': {key} must be a number in [{q.lo:g}, {q.hi:g}]")
            if name in chosen:
                chosen[name].values[key] = v if q.is_color else float(v)
    return out


def _value(c: ShaderChoice, q: Param) -> float | str:
    return c.values.get(q.key, q.default)


def ramp_rows(src: Source, c: ShaderChoice) -> list[tuple[str, str]]:
    """A rarity set gives one palette row per tier (low to high); anything else one row from the chosen colour."""
    rows = [(t, src.rarity_colors[t]) for t, _ in src.rarity if src.rarity_colors.get(t)]
    color = str(_value(c, next(q for q in c.preset.params if q.key == "color")))
    return rows or [("color", color)]


def ramp_png(rows: list[tuple[str, str]]) -> bytes:
    import numpy as np
    from PIL import Image

    from studio_core.ops.rarity import ramp_lut

    lines = [ramp_lut(tuple(round(v * 255) for v in parse_color(h)[:3]), RAMP_WIDTH) for _, h in rows]
    img = Image.fromarray(np.clip(np.round(np.stack(lines) * 255), 0, 255).astype(np.uint8), "RGB")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _row(c: ShaderChoice, rows: int) -> float:
    r = float(_value(c, next(q for q in c.preset.params if q.key == "row")))
    return float(rows - 1) if r < 0 else min(r, rows - 1)


def _num(v: float) -> str:
    s = f"{float(v):.6g}"
    return s if any(ch in s for ch in ".e") else f"{s}.0"


def godot_material(c: ShaderChoice, ramp: str | None, rows: int) -> str:
    ext = [f'[ext_resource type="Shader" path="{c.preset.id}.gdshader" id="1_shader"]']
    props = ["resource_local_to_scene = true", 'shader = ExtResource("1_shader")']
    for q in c.preset.params:
        if not q.godot:
            continue
        v = _value(c, q)
        if q.key == "row":
            v = _row(c, rows)
        if q.is_color:
            props.append(f"shader_parameter/{q.godot} = Color({', '.join(_num(x) for x in parse_color(v))})")
        else:
            props.append(f"shader_parameter/{q.godot} = {_num(float(v))}")
    if ramp:
        ext.append(f'[ext_resource type="Texture2D" path="{ramp}" id="2_ramp"]')
        props += ['shader_parameter/ramp = ExtResource("2_ramp")', f"shader_parameter/rows = {_num(rows)}"]
    return ('[gd_resource type="ShaderMaterial" format=3]\n\n' + "\n".join(ext) + "\n\n[resource]\n"
            + "\n".join(props) + "\n")


def godot_assign(scene: str, material: str) -> str:
    """Adds the material to the scene's root node (the sprite); instanced scenes such as *_body.tscn inherit it."""
    lines = scene.split("\n")
    last_ext = max((i for i, ln in enumerate(lines) if ln.startswith("[ext_resource")), default=0)
    lines.insert(last_ext + 1, f'[ext_resource type="ShaderMaterial" path="{material}" id="9_material"]')
    root = next(i for i, ln in enumerate(lines) if ln.startswith("[node "))
    lines.insert(root + 1, 'material = ExtResource("9_material")')
    out = "\n".join(lines)
    return re.sub(r"load_steps=(\d+)", lambda m: f"load_steps={int(m.group(1)) + 1}", out, count=1)


def unity_shader_rel(p: Preset) -> str:
    return f"Shaders/AssetStudio_{p.unity}.shader"


def shader_meta(g: str) -> str:
    return (f"fileFormatVersion: 2\nguid: {g}\nShaderImporter:\n  externalObjects: {{}}\n  defaultTextures: []\n"
            "  nonModifiableTextures: []\n  preprocessorOverride: 0\n  userData: \n  assetBundleName: \n"
            "  assetBundleVariant: \n")


def _unity_color(v: float | str) -> str:
    r, g, b, a = parse_color(v)
    return f"{{r: {r:.6g}, g: {g:.6g}, b: {b:.6g}, a: {a:.6g}}}"


def unity_material(c: ShaderChoice, name: str, shader_guid: str, ramp_guid: str | None, rows: int) -> str:
    floats, colors = [], []
    for q in c.preset.params:
        if not q.unity:
            continue
        v = _value(c, q)
        if q.key == "row":
            v = _row(c, rows)
        if q.is_color:
            colors.append(f"    - {q.unity}: {_unity_color(v)}\n")
        else:
            floats.append(f"    - {q.unity}: {float(v):.6g}\n")
    envs = "    - _MainTex:\n        m_Texture: {fileID: 0}\n        m_Scale: {x: 1, y: 1}\n        m_Offset: {x: 0, y: 0}\n"
    if ramp_guid:
        floats.append(f"    - _Rows: {rows}\n")
        envs += (f"    - _Ramp:\n        m_Texture: {{fileID: 2800000, guid: {ramp_guid}, type: 3}}\n"
                 "        m_Scale: {x: 1, y: 1}\n        m_Offset: {x: 0, y: 0}\n")
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
  m_Shader: {{fileID: {SHADER_FILE_ID}, guid: {shader_guid}, type: 3}}
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
{envs}    m_Ints: []
    m_Floats:
{"".join(floats)}    m_Colors:{(chr(10) + "".join(colors)) if colors else " []" + chr(10)}  m_BuildTextureStacks: []
  m_AllowLocking: 1
"""


README_GODOT = """- Shaders/: shader preset cho sprite (canvas_item, chạy cả Forward+ lẫn Compatibility). Mỗi preset một
  ShaderMaterial Shaders/{asset}_<preset>.tres (Local to Scene: mỗi instance một bản, đổi tham số không lây sang
  con khác) — gán vào CanvasItem > Material. Đổi lúc chạy: material.set_shader_parameter("amount", 1.0).{assigned}"""
README_UNITY = """- Shaders/: shader preset URP 2D (AssetStudio/Sprite/<Tên>, kiểu Sprite-Unlit, tương thích SRP Batcher, flip và
  màu SpriteRenderer, 2D Animation skinning). Cần Universal Render Pipeline — Built-in RP sẽ hiện màu hồng. Mỗi preset
  một Shaders/{asset}_<preset>.mat, kéo vào Material của SpriteRenderer. Đổi lúc chạy:
  GetComponent<SpriteRenderer>().material.SetFloat("_Amount", 1f) (renderer.material tự tách bản riêng).{assigned}"""


def _notes(engine: str, a: str, chosen: list[ShaderChoice], assigned: str | None, rows: list[tuple[str, str]] | None
           ) -> str:
    tail = f"\n  Đã gán sẵn {assigned} cho sprite chính." if assigned else ""
    text = (README_GODOT if engine == "godot" else README_UNITY).format(asset=a, assigned=tail)
    for c in chosen:
        text += f"\n  · {c.preset.id}: {c.preset.hint}."
        if c.preset.id == "palette_swap" and rows:
            names = ", ".join(f"{i} = {t}" for i, (t, _) in enumerate(rows))
            text += f" Shaders/{a}_ramp.png {len(rows)} hàng ({names})."
    return text


def apply(files: Files, engine: str, src: Source, chosen: list[ShaderChoice]) -> Files:
    if not chosen:
        return files
    if not applies_to(src):
        raise ShaderError("shader presets need a sprite (sprite sheet, UI kit or rarity set)")
    a = src.asset
    out = list(files)
    names = dict(out)
    rows: list[tuple[str, str]] | None = None
    ramp_rel = f"Shaders/{a}_ramp.png"
    swap = next((c for c in chosen if c.preset.id == "palette_swap"), None)
    if swap is not None:
        rows = ramp_rows(src, swap)
        out.append((ramp_rel, ramp_png(rows)))
    n_rows = len(rows or [])
    assigned = None
    if engine == "godot":
        for c in chosen:
            out.append((f"Shaders/{c.preset.id}.gdshader", template_dir() / f"{c.preset.id}.gdshader"))
            mat = f"Shaders/{a}_{c.preset.id}.tres"
            ramp = Path(ramp_rel).name if c is swap else None
            out.append((mat, godot_material(c, ramp, n_rows).encode()))
        scene = f"{a}.tscn"
        if len(chosen) == 1 and isinstance(names.get(scene), bytes):
            assigned = f"Shaders/{a}_{chosen[0].preset.id}.tres"
            out = [(n, godot_assign(d.decode(), assigned).encode() if n == scene and isinstance(d, bytes) else d)
                   for n, d in out]
    else:
        from studio_core.export.unity import SPRITE_LIT_MAT, guid, native_meta, texture_meta

        ramp_guid = None
        if swap is not None:
            ramp_guid = guid(src.uid, a, ramp_rel)
            out.append((f"{ramp_rel}.meta", texture_meta(a, ramp_guid, None, (RAMP_WIDTH, n_rows), src.ppu,
                                                         True).encode()))
        for c in chosen:
            # Per-export GUID: a fixed one collides when two exports land in one project and Unity re-rolls both.
            rel = unity_shader_rel(c.preset)
            shader_guid = guid(src.uid, a, rel)
            out += [(rel, template_dir() / Path(rel).name), (f"{rel}.meta", shader_meta(shader_guid).encode())]
            mat = f"Shaders/{a}_{c.preset.id}.mat"
            out += [(mat, unity_material(c, f"{a}_{c.preset.id}", shader_guid, ramp_guid if c is swap else None,
                                         n_rows).encode()),
                    (f"{mat}.meta", native_meta(guid(src.uid, a, mat), MATERIAL_FILE_ID).encode())]
        prefab = f"{a}.prefab"
        if len(chosen) == 1 and isinstance(names.get(prefab), bytes):
            mat = f"Shaders/{a}_{chosen[0].preset.id}.mat"
            assigned = mat
            ref = f"{{fileID: {MATERIAL_FILE_ID}, guid: {guid(src.uid, a, mat)}, type: 2}}"
            out = [(n, d.decode().replace(UNITY_DEFAULT_SPRITE_MAT, ref).replace(SPRITE_LIT_MAT, ref).encode()
                    if n == prefab and isinstance(d, bytes) else d) for n, d in out]
    note = _notes(engine, a, chosen, assigned, rows)
    readme = names.get("README.txt")
    if isinstance(readme, bytes):
        out = [(n, (d.decode().rstrip("\n") + "\n" + note + "\n").encode() if n == "README.txt" else d)
               for n, d in out]
    return out
