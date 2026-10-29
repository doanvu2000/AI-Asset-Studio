"""Particle VFX export (op.vfx_particles): Unity prefabs (empty root ParticleSystem that plays/destroys the preset, one
child ParticleSystem per emitter, TrailRenderer for the projectile) with Built-in particle materials an Editor script
switches to URP Particles/Unlit when URP is active; Godot GPUParticles2D scenes (+ GPUParticles3D variants) with
ParticleProcessMaterial, a player script and a demo scene. Both read the same engine-neutral preset JSON."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from studio_core.export.sheet import cs_namespace
from studio_core.export.source import Files, Source

MATERIAL_FILE_ID = 2100000
BUILTIN = "0000000000000000f000000000000000"
# unity_builtin_extra ids of Legacy Shaders/Particles/Additive and /Alpha Blended (read from Editor 6000.3 .mat files).
LEGACY_SHADER = {"add": 200, "alpha": 203}
EDITOR_FILE = "AssetStudioVfx.cs"
# Fixed so several exported packs in one project share one script identity.
EDITOR_GUID = "6a2f9c4e1b7d4e08a3c5f2d1e9b84c71"
GODOT_SCRIPT_FILE = "asset_studio_vfx.gd"
GRAVITY = 9.81
DEMO_COLS = 4


def vfx_set(step: Any, blobs: Any) -> tuple[dict[str, Any] | None, dict[str, Path]]:
    """vfx_particles.json and its textures, also when a gate passed it through under another port name."""
    from studio_core.ops.vfx_particles import VFX_FILE, VFX_TYPE

    for refs in step.outputs.values():
        for ref in refs:
            if (ref.name or "") != VFX_FILE:
                continue
            try:
                doc = json.loads(blobs.path(ref.hash).read_bytes())
                if doc.get("type") != VFX_TYPE or not doc.get("presets"):
                    return None, {}
                files = {k: blobs.path(str(t["hash"])) for k, t in doc["textures"].items()}
                if not all(p.is_file() for p in files.values()):
                    return None, {}
                return doc, files
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                return None, {}
    return None, {}


def _order(emitters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from studio_core.ops.vfx_particles import draw_order

    return draw_order(emitters)


def _f(v: float) -> str:
    return f"{float(v):.6g}"


def _rgba(h: str) -> tuple[float, float, float, float]:
    s = h.lstrip("#")
    v = [int(s[i:i + 2], 16) / 255 for i in range(0, len(s), 2)]
    return v[0], v[1], v[2], v[3] if len(v) > 3 else 1.0


def _life(em: dict[str, Any]) -> tuple[float, float]:
    return float(em["lifetime"][0]), float(em["lifetime"][1])


def _amount(em: dict[str, Any]) -> int:
    if int(em["burst"]) > 0:
        return int(em["burst"])
    return max(1, math.ceil(float(em["rate"]) * _life(em)[1]))


def _span(preset: dict[str, Any]) -> float:
    """Seconds until the last particle of a one-shot preset dies."""
    return max(float(em["delay"]) + (0.0 if int(em["burst"]) else float(preset["duration"])) + _life(em)[1]
               for em in preset["emitters"])


def _extent(preset: dict[str, Any]) -> float:
    from studio_core.ops.vfx_particles import extent, simulate

    return extent(preset, simulate(preset))


def _tex_name(a: str, key: str) -> str:
    return f"{a}_{key}.png"


def _keys(points: list[tuple[float, float]], indent: str) -> str:
    """AnimationCurve keys with linear tangents (Unity has no per-key linear flag in YAML, the slopes do it)."""
    out = []
    for i, (t, v) in enumerate(points):
        ins = (v - points[i - 1][1]) / max(t - points[i - 1][0], 1e-6) if i > 0 else 0.0
        outs = (points[i + 1][1] - v) / max(points[i + 1][0] - t, 1e-6) if i + 1 < len(points) else 0.0
        out.append(f"{indent}- serializedVersion: 3\n{indent}  time: {_f(t)}\n{indent}  value: {_f(v)}\n"
                   f"{indent}  inSlope: {_f(ins)}\n{indent}  outSlope: {_f(outs)}\n{indent}  tangentMode: 0\n"
                   f"{indent}  weightedMode: 0\n{indent}  inWeight: 0.33333334\n{indent}  outWeight: 0.33333334\n")
    return "".join(out)


def _curve(points: list[tuple[float, float]], ind: str) -> str:
    return (f"{ind}serializedVersion: 2\n{ind}m_Curve:\n{_keys(points, ind)}{ind}m_PreInfinity: 2\n"
            f"{ind}m_PostInfinity: 2\n{ind}m_RotationOrder: 4\n")


def _minmax(name: str, ind: str, lo: float, hi: float | None = None,
            curve: list[tuple[float, float]] | None = None) -> str:
    """MinMaxCurve: constant (hi None), random between two constants, or curve × 1."""
    flat = [(0.0, 1.0), (1.0, 1.0)]
    if curve is not None:
        state, scalar, min_scalar, max_c = 1, 1.0, 1.0, curve
    elif hi is None or abs(hi - lo) < 1e-9:
        state, scalar, min_scalar, max_c = 0, lo, lo, flat
    else:
        state, scalar, min_scalar, max_c = 3, hi, lo, flat
    i2 = ind + "  "
    return (f"{ind}{name}:\n{i2}serializedVersion: 2\n{i2}minMaxState: {state}\n{i2}scalar: {_f(scalar)}\n"
            f"{i2}minScalar: {_f(min_scalar)}\n{i2}maxCurve:\n{_curve(max_c, i2 + '  ')}"
            f"{i2}minCurve:\n{_curve(flat, i2 + '  ')}")


def _color(c: tuple[float, ...]) -> str:
    return f"{{r: {_f(c[0])}, g: {_f(c[1])}, b: {_f(c[2])}, a: {_f(c[3])}}}"


def _gradient(keys: list[list[Any]], ind: str) -> str:
    keys = keys[:8]
    cols = [_rgba(h) for _, h in keys]
    ts = [float(t) for t, _ in keys]
    slots = [(cols[i][0], cols[i][1], cols[i][2], cols[i][3]) if i < len(keys) else (0, 0, 0, 0) for i in range(8)]
    lines = [f"{ind}serializedVersion: 2"]
    lines += [f"{ind}key{i}: {_color(slots[i])}" for i in range(8)]
    lines += [f"{ind}ctime{i}: {int(round(ts[i] * 65535)) if i < len(ts) else 0}" for i in range(8)]
    lines += [f"{ind}atime{i}: {int(round(ts[i] * 65535)) if i < len(ts) else 0}" for i in range(8)]
    lines += [f"{ind}m_Mode: 0", f"{ind}m_ColorSpace: -1", f"{ind}m_NumColorKeys: {len(keys)}",
              f"{ind}m_NumAlphaKeys: {len(keys)}"]
    return "\n".join(lines) + "\n"


def _minmax_gradient(name: str, ind: str, keys: list[list[Any]] | None) -> str:
    i2 = ind + "  "
    white = [[0, "#ffffffff"], [1, "#ffffffff"]]
    return (f"{ind}{name}:\n{i2}serializedVersion: 2\n{i2}minMaxState: {1 if keys else 0}\n"
            f"{i2}minColor: {{r: 1, g: 1, b: 1, a: 1}}\n{i2}maxColor: {{r: 1, g: 1, b: 1, a: 1}}\n"
            f"{i2}maxGradient:\n{_gradient(keys or white, i2 + '  ')}{i2}minGradient:\n{_gradient(white, i2 + '  ')}")


def _shape(em: dict[str, Any]) -> str:
    sh = em["shape"]
    kind = sh.get("type", "point")
    radius = float(sh.get("radius", 0.0))
    rot = "{x: 0, y: 0, z: 0}"
    thickness = 1.0
    angle = 25.0
    if kind == "sphere":
        t = 0
    elif kind == "circle":
        t, thickness = 10, float(sh.get("thickness", 1.0))
    elif kind == "cone":
        # Cone axis is local +Z; -90° about X turns it to +Y (up), its base disc lies flat on XZ.
        t, angle, rot = 4, float(sh.get("angle", 0.0)), "{x: -90, y: 0, z: 0}"
    else:
        t, radius = 0, 0.0001
    return f"""  ShapeModule:
    serializedVersion: 6
    enabled: 1
    type: {t}
    angle: {_f(angle)}
    length: 5
    boxThickness: {{x: 0, y: 0, z: 0}}
    radiusThickness: {_f(thickness)}
    donutRadius: 0.2
    m_Position: {{x: 0, y: 0, z: 0}}
    m_Rotation: {rot}
    m_Scale: {{x: 1, y: 1, z: 1}}
    placementMode: 0
    radius:
      value: {_f(max(radius, 0.0001))}
      mode: 0
      spread: 0
{_minmax("speed", "      ", 1.0)}    arc:
      value: 360
      mode: 0
      spread: 0
{_minmax("speed", "      ", 1.0)}"""


def unity_particle_system(fid: int, go: int, em: dict[str, Any] | None, preset: dict[str, Any], *, root: bool,
                          demo: bool) -> str:
    """One ParticleSystem; `em` None = the root that only owns duration, looping and the destroy-on-finish action."""
    loop = bool(preset["loop"])
    if em is None:
        length = float(preset["duration"]) if loop else _span(preset)
        stop = 2 if root and not loop and not demo else 0
        head = (f"  lengthInSec: {_f(length)}\n  simulationSpeed: 1\n  stopAction: {stop}\n  cullingMode: 0\n"
                f"  ringBufferMode: 0\n  ringBufferLoopRange: {{x: 0, y: 1}}\n  emitterVelocityMode: 1\n"
                f"  looping: {1 if loop or demo else 0}\n  prewarm: 0\n  playOnAwake: 1\n  useUnscaledTime: 0\n"
                f"  autoRandomSeed: 1\n{_minmax('startDelay', '  ', 0.0)}  moveWithTransform: 0\n"
                f"  moveWithCustomTransform: {{fileID: 0}}\n  scalingMode: 0\n  randomSeed: 0\n")
        modules = ("  InitialModule:\n    serializedVersion: 3\n    enabled: 1\n    maxNumParticles: 1\n"
                   "  EmissionModule:\n    enabled: 0\n    serializedVersion: 4\n"
                   f"{_minmax('rateOverTime', '    ', 0.0)}    m_BurstCount: 0\n    m_Bursts: []\n"
                   "  ShapeModule:\n    serializedVersion: 6\n    enabled: 0\n")
        return _ps_doc(fid, go, head + modules)
    lo, hi = _life(em)
    burst = int(em["burst"])
    length = float(preset["duration"])
    head = (f"  lengthInSec: {_f(length)}\n  simulationSpeed: 1\n  stopAction: 0\n  cullingMode: 0\n"
            f"  ringBufferMode: 0\n  ringBufferLoopRange: {{x: 0, y: 1}}\n  emitterVelocityMode: 1\n"
            f"  looping: {1 if loop or demo else 0}\n  prewarm: {1 if loop else 0}\n  playOnAwake: 1\n"
            f"  useUnscaledTime: 0\n  autoRandomSeed: 1\n{_minmax('startDelay', '  ', float(em['delay']))}"
            f"  moveWithTransform: {1 if em['space'] == 'world' else 0}\n  moveWithCustomTransform: {{fileID: 0}}\n"
            f"  scalingMode: 0\n  randomSeed: 0\n")
    rot = [math.radians(float(v)) for v in em["rotation"]]
    spin = [math.radians(float(v)) for v in em["spin"]]
    initial = (f"  InitialModule:\n    serializedVersion: 3\n    enabled: 1\n"
               f"{_minmax('startLifetime', '    ', lo, hi)}"
               f"{_minmax('startSpeed', '    ', float(em['speed'][0]), float(em['speed'][1]))}"
               f"{_minmax_gradient('startColor', '    ', None)}"
               f"{_minmax('startSize', '    ', float(em['size'][0]), float(em['size'][1]))}"
               f"{_minmax('startRotation', '    ', rot[0], rot[1])}"
               f"    randomizeRotationDirection: 0\n    gravitySource: 0\n"
               f"    maxNumParticles: {_amount(em) * (3 if loop or demo else 1) + 8}\n"
               f"    customEmitterVelocity: {{x: 0, y: 0, z: 0}}\n    size3D: 0\n    rotation3D: 0\n"
               f"{_minmax('gravityModifier', '    ', float(em['gravity']) / GRAVITY)}")
    bursts = (f"    m_BurstCount: 1\n    m_Bursts:\n    - serializedVersion: 2\n      time: 0\n"
              f"{_minmax('countCurve', '      ', float(burst))}      cycleCount: 1\n      repeatInterval: 0.01\n"
              f"      probability: 1\n") if burst else "    m_BurstCount: 0\n    m_Bursts: []\n"
    emission = (f"  EmissionModule:\n    enabled: 1\n    serializedVersion: 4\n"
                f"{_minmax('rateOverTime', '    ', float(em['rate']))}{_minmax('rateOverDistance', '    ', 0.0)}"
                f"{bursts}")
    size = (f"  SizeModule:\n    enabled: 1\n"
            f"{_minmax('curve', '    ', 1.0, curve=[(float(t), float(v)) for t, v in em['size_curve']])}"
            f"    separateAxes: 0\n")
    rotation = (f"  RotationModule:\n    enabled: {1 if any(spin) else 0}\n{_minmax('curve', '    ', spin[0], spin[1])}"
                f"    separateAxes: 0\n")
    color = f"  ColorModule:\n    enabled: 1\n{_minmax_gradient('gradient', '    ', em['colors'])}"
    from studio_core.ops.vfx_particles import SHEETS

    cols, rows = SHEETS.get(em["texture"], (1, 1))
    uv = ""
    if cols * rows > 1:
        # startFrame is stored normalised over the sheet (0..0.9999 = any tile); frameOverTime 0 keeps that tile.
        uv = (f"  UVModule:\n    serializedVersion: 2\n    enabled: 1\n    mode: 0\n    timeMode: 0\n    fps: 30\n"
              f"{_minmax('frameOverTime', '    ', 0.0)}{_minmax('startFrame', '    ', 0.0, 0.9999)}"
              f"    speedRange: {{x: 0, y: 1}}\n    tilesX: {cols}\n    tilesY: {rows}\n    animationType: 0\n"
              f"    rowIndex: 0\n    cycles: 1\n    uvChannelMask: -1\n    rowMode: 1\n    flipU: 0\n    flipV: 0\n")
    drag = ""
    if float(em["drag"]) > 0:
        drag = (f"  ClampVelocityModule:\n    enabled: 1\n{_minmax('magnitude', '    ', 1000.0)}"
                f"    separateAxis: 0\n    inWorldSpace: 0\n    multiplyDragByParticleSize: 0\n"
                f"    multiplyDragByParticleVelocity: 0\n    dampen: 0\n{_minmax('drag', '    ', float(em['drag']))}")
    return _ps_doc(fid, go, head + initial + _shape(em) + emission + size + rotation + color + uv + drag)


def _ps_doc(fid: int, go: int, body: str) -> str:
    return (f"--- !u!198 &{fid}\nParticleSystem:\n  m_ObjectHideFlags: 0\n  m_CorrespondingSourceObject: {{fileID: 0}}\n"
            f"  m_PrefabInstance: {{fileID: 0}}\n  m_PrefabAsset: {{fileID: 0}}\n  m_GameObject: {{fileID: {go}}}\n"
            f"  serializedVersion: 8\n{body}")


def _renderer_common(go: int, enabled: bool, mat_guid: str | None, order: int) -> str:
    mats = f"\n  - {{fileID: {MATERIAL_FILE_ID}, guid: {mat_guid}, type: 2}}" if mat_guid else " []"
    return (f"  m_ObjectHideFlags: 0\n  m_CorrespondingSourceObject: {{fileID: 0}}\n  m_PrefabInstance: {{fileID: 0}}\n"
            f"  m_PrefabAsset: {{fileID: 0}}\n  m_GameObject: {{fileID: {go}}}\n  m_Enabled: {1 if enabled else 0}\n"
            f"  m_CastShadows: 0\n  m_ReceiveShadows: 0\n  m_DynamicOccludee: 1\n  m_StaticShadowCaster: 0\n"
            f"  m_MotionVectors: 1\n  m_LightProbeUsage: 0\n  m_ReflectionProbeUsage: 0\n  m_RenderingLayerMask: 1\n"
            f"  m_RendererPriority: 0\n  m_Materials:{mats}\n  m_SortingLayerID: 0\n  m_SortingLayer: 0\n"
            f"  m_SortingOrder: {order}\n  m_MaskInteraction: 0\n")


def unity_renderer(fid: int, go: int, em: dict[str, Any] | None, mat_guid: str | None, order: int) -> str:
    stretched = em is not None and em["align"] == "velocity"
    return (f"--- !u!199 &{fid}\nParticleSystemRenderer:\n  serializedVersion: 7\n"
            f"{_renderer_common(go, em is not None, mat_guid, order)}"
            f"  m_RenderMode: {1 if stretched else 0}\n  m_MeshDistribution: 0\n  m_SortMode: 0\n"
            f"  m_MinParticleSize: 0\n  m_MaxParticleSize: 2\n  m_CameraVelocityScale: 0\n  m_VelocityScale: 0\n"
            f"  m_LengthScale: 1\n  m_SortingFudge: 0\n  m_NormalDirection: 1\n  m_ShadowBias: 0\n"
            f"  m_RenderAlignment: 0\n  m_Pivot: {{x: 0, y: 0, z: 0}}\n  m_Flip: {{x: 0, y: 0, z: 0}}\n"
            f"  m_EnableGPUInstancing: 1\n  m_ApplyActiveColorSpace: 1\n  m_AllowRoll: 1\n"
            f"  m_UseCustomVertexStreams: 0\n  m_VertexStreams: 00010304\n  m_Mesh: {{fileID: 0}}\n")


def unity_trail(fid: int, go: int, trail: dict[str, Any], mat_guid: str) -> str:
    return (f"--- !u!96 &{fid}\nTrailRenderer:\n  serializedVersion: 4\n{_renderer_common(go, True, mat_guid, -1)}"
            f"  m_Time: {_f(trail['time'])}\n  m_PreviewTimeScale: 1\n  m_Parameters:\n    serializedVersion: 3\n"
            f"    widthMultiplier: {_f(trail['width'])}\n    widthCurve:\n{_curve([(0.0, 1.0), (1.0, 0.0)], '      ')}"
            f"    colorGradient:\n{_gradient(trail['colors'], '      ')}    numCornerVertices: 0\n"
            f"    numCapVertices: 2\n    alignment: 0\n    textureMode: 0\n    textureScale: {{x: 1, y: 1}}\n"
            f"    shadowBias: 0.5\n    generateLightingData: 0\n  m_MinVertexDistance: 0.05\n  m_Autodestruct: 0\n"
            f"  m_Emitting: 1\n  m_ApplyActiveColorSpace: 1\n")


def _go(fid: int, comps: list[int], name: str) -> str:
    c = "".join(f"  - component: {{fileID: {x}}}\n" for x in comps)
    return (f"--- !u!1 &{fid}\nGameObject:\n  m_ObjectHideFlags: 0\n  m_CorrespondingSourceObject: {{fileID: 0}}\n"
            f"  m_PrefabInstance: {{fileID: 0}}\n  m_PrefabAsset: {{fileID: 0}}\n  serializedVersion: 6\n"
            f"  m_Component:\n{c}  m_Layer: 0\n  m_Name: {name}\n  m_TagString: Untagged\n  m_Icon: {{fileID: 0}}\n"
            f"  m_NavMeshLayer: 0\n  m_StaticEditorFlags: 0\n  m_IsActive: 1\n")


def _tr(fid: int, go: int, pos: tuple[float, float], children: list[int], father: int) -> str:
    ch = "".join(f"\n  - {{fileID: {c}}}" for c in children) if children else " []"
    return (f"--- !u!4 &{fid}\nTransform:\n  m_ObjectHideFlags: 0\n  m_CorrespondingSourceObject: {{fileID: 0}}\n"
            f"  m_PrefabInstance: {{fileID: 0}}\n  m_PrefabAsset: {{fileID: 0}}\n  m_GameObject: {{fileID: {go}}}\n"
            f"  serializedVersion: 2\n  m_LocalRotation: {{x: 0, y: 0, z: 0, w: 1}}\n"
            f"  m_LocalPosition: {{x: {_f(pos[0])}, y: {_f(pos[1])}, z: 0}}\n  m_LocalScale: {{x: 1, y: 1, z: 1}}\n"
            f"  m_ConstrainProportionsScale: 0\n  m_Children:{ch}\n  m_Father: {{fileID: {father}}}\n"
            f"  m_LocalEulerAnglesHint: {{x: 0, y: 0, z: 0}}\n")


def _preset_docs(a: str, preset: dict[str, Any], mats: dict[str, str], key: str, pos: tuple[float, float],
                 father: int, demo: bool) -> tuple[list[str], int]:
    """YAML documents of one preset (root + emitter children); returns them and the root transform id."""
    from studio_core.export.unity import file_id

    name = preset["name"]
    ids = {k: file_id("vfx", a, key, name, k) for k in ("go", "tr", "ps", "r", "trail")}
    emitters = _order(preset["emitters"])
    kids = [{k: file_id("vfx", a, key, name, em["name"], k) for k in ("go", "tr", "ps", "r")} for em in emitters]
    trail = preset.get("trail")
    trail = trail if trail and "soft_dot|add" in mats else None
    comps = [ids["tr"], ids["ps"], ids["r"]] + ([ids["trail"]] if trail else [])
    docs = [_go(ids["go"], comps, f"{a}_{name}"), _tr(ids["tr"], ids["go"], pos, [k["tr"] for k in kids], father),
            unity_particle_system(ids["ps"], ids["go"], None, preset, root=father == 0, demo=demo),
            unity_renderer(ids["r"], ids["go"], None, None, 0)]
    if trail and "soft_dot|add" in mats:
        docs.append(unity_trail(ids["trail"], ids["go"], trail, mats["soft_dot|add"]))
    for i, (em, k) in enumerate(zip(emitters, kids)):
        docs += [_go(k["go"], [k["tr"], k["ps"], k["r"]], em["name"]), _tr(k["tr"], k["go"], (0, 0), [], ids["tr"]),
                 unity_particle_system(k["ps"], k["go"], em, preset, root=False, demo=demo),
                 unity_renderer(k["r"], k["go"], em, mats[f"{em['texture']}|{em['blend']}"], i)]
    return docs, ids["tr"]


def legacy_material(name: str, blend: str, tex_guid: str) -> str:
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
  m_Shader: {{fileID: {LEGACY_SHADER[blend]}, guid: {BUILTIN}, type: 0}}
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
    m_Floats:
    - _InvFade: 1
    m_Colors:
    - _TintColor: {{r: 0.5, g: 0.5, b: 0.5, a: 0.5}}
  m_BuildTextureStacks: []
  m_AllowLocking: 1
"""


# Unity's stretched billboard lays the texture's X axis along the velocity (rendered in Editor 6000.3); the
# neutral spark is a vertical streak for Godot's align_y, so Unity gets it turned a quarter.
VELOCITY_ALIGNED = ("spark",)


def _rotated(path: Path) -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    with Image.open(path) as im:
        im.transpose(Image.Transpose.ROTATE_90).save(buf, "PNG")
    return buf.getvalue()


def _texture_meta(src: Source, g: str, path: Path) -> str:
    from studio_core.export.pbr import _image_size
    from studio_core.export.unity import texture_meta

    # Default texture with mipmaps (particles shrink a lot), clamped so sheet tiles and soft edges do not bleed.
    meta = texture_meta(src.asset, g, None, _image_size(path), src.ppu, False, repeat=True)
    for k in ("wrapU", "wrapV", "wrapW"):
        meta = meta.replace(f"{k}: 0", f"{k}: 1", 1)
    return meta.replace("alphaIsTransparency: 0", "alphaIsTransparency: 1", 1)


EDITOR_SCRIPT = cs_namespace("""// Generated by AI Asset Studio: the exported particle materials use Built-in Legacy particle shaders; with URP active
// they are switched to Universal Render Pipeline/Particles/Unlit (and back) when <asset>_vfx.json is imported, or via
// menu Asset Studio > Fit VFX Materials To Render Pipeline.
using System.IO;
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;

public class AssetStudioVfx : AssetPostprocessor
{
    const string UrpShader = "Universal Render Pipeline/Particles/Unlit";

    static void OnPostprocessAllAssets(string[] imported, string[] deleted, string[] moved, string[] movedFrom)
    {
        foreach (var p in imported)
            if (p.EndsWith("_vfx.json")) Fit(Path.GetDirectoryName(p).Replace('\\\\', '/'));
    }

    [MenuItem("Asset Studio/Fit VFX Materials To Render Pipeline")]
    public static void FitAll()
    {
        int n = 0;
        foreach (var g in AssetDatabase.FindAssets("_vfx t:TextAsset"))
        {
            var p = AssetDatabase.GUIDToAssetPath(g);
            if (p.EndsWith("_vfx.json")) n += Fit(Path.GetDirectoryName(p).Replace('\\\\', '/'));
        }
        Debug.Log("Asset Studio: " + n + " VFX materials switched");
    }

    public static int Fit(string dir)
    {
        var matDir = dir + "/Materials";
        if (!AssetDatabase.IsValidFolder(matDir)) return 0;
        bool urp = GraphicsSettings.currentRenderPipeline != null;
        var urpShader = Shader.Find(UrpShader);
        int n = 0;
        foreach (var g in AssetDatabase.FindAssets("t:Material", new[] { matDir }))
        {
            var m = AssetDatabase.LoadAssetAtPath<Material>(AssetDatabase.GUIDToAssetPath(g));
            if (m == null) continue;
            bool add = m.name.EndsWith("_add");
            bool isUrp = m.shader != null && m.shader.name == UrpShader;
            if (urp && urpShader != null && !isUrp)
            {
                var tex = m.HasProperty("_MainTex") ? m.GetTexture("_MainTex") : null;
                m.shader = urpShader;
                m.SetTexture("_BaseMap", tex);
                m.SetColor("_BaseColor", Color.white);
                m.SetFloat("_Surface", 1f);
                m.SetFloat("_Blend", add ? 2f : 0f);
                m.SetFloat("_SrcBlend", (float)BlendMode.SrcAlpha);
                m.SetFloat("_DstBlend", (float)(add ? BlendMode.One : BlendMode.OneMinusSrcAlpha));
                m.SetFloat("_SrcBlendAlpha", (float)BlendMode.One);
                m.SetFloat("_DstBlendAlpha", (float)(add ? BlendMode.One : BlendMode.OneMinusSrcAlpha));
                m.SetFloat("_ZWrite", 0f);
                m.EnableKeyword("_SURFACE_TYPE_TRANSPARENT");
                m.SetOverrideTag("RenderType", "Transparent");
                m.SetShaderPassEnabled("DepthOnly", false);
                m.renderQueue = (int)RenderQueue.Transparent;
            }
            else if (!urp && (m.shader == null || isUrp || m.shader.name == "Hidden/InternalErrorShader"))
            {
                var tex = m.HasProperty("_BaseMap") ? m.GetTexture("_BaseMap") : null;
                m.shader = Shader.Find(add ? "Legacy Shaders/Particles/Additive" : "Legacy Shaders/Particles/Alpha Blended");
                if (tex != null) m.SetTexture("_MainTex", tex);
                m.renderQueue = -1;
            }
            else continue;
            EditorUtility.SetDirty(m);
            n++;
        }
        if (n > 0) AssetDatabase.SaveAssets();
        return n;
    }
}
""")


def _unity_doc(src: Source, a: str) -> dict[str, Any]:
    doc = {k: v for k, v in (src.vfx or {}).items() if k not in ("textures",)}
    doc["textures"] = {k: {**{kk: vv for kk, vv in t.items() if kk != "hash"}, "file": f"Textures/{_tex_name(a, k)}"}
                       for k, t in (src.vfx or {}).get("textures", {}).items()}
    return doc


def unity_files(src: Source) -> tuple[Files, list[str]]:
    from studio_core.export.destructible import _script_meta
    from studio_core.export.paperdoll import _text_meta
    from studio_core.export.unity import HEADER, guid, native_meta, prefab_meta

    doc, a, u = src.vfx or {}, src.asset, src.uid
    files: Files = []
    tex_guids: dict[str, str] = {}
    for key, path in src.vfx_files.items():
        rel = f"VFX/Textures/{_tex_name(a, key)}"
        tex_guids[key] = guid(u, a, rel)
        data: bytes | Path = _rotated(path) if key in VELOCITY_ALIGNED else path
        files += [(rel, data), (f"{rel}.meta", _texture_meta(src, tex_guids[key], path).encode())]
    used = {(em["texture"], em["blend"]) for p in doc["presets"] for em in p["emitters"]}
    if any(p.get("trail") for p in doc["presets"]) and "soft_dot" in tex_guids:
        used.add(("soft_dot", "add"))
    mats: dict[str, str] = {}
    for tex, blend in sorted(used):
        if tex not in tex_guids:
            continue
        rel = f"VFX/Materials/{a}_{tex}_{blend}.mat"
        mats[f"{tex}|{blend}"] = guid(u, a, rel)
        files += [(rel, legacy_material(f"{a}_{tex}_{blend}", blend, tex_guids[tex]).encode()),
                  (f"{rel}.meta", native_meta(mats[f"{tex}|{blend}"], MATERIAL_FILE_ID).encode())]
    demo_docs: list[str] = []
    from studio_core.export.unity import file_id

    demo_go, demo_tr = file_id("vfx", a, "demo", "go"), file_id("vfx", a, "demo", "tr")
    demo_kids: list[int] = []
    spacing = 2 * max(_extent(p) for p in doc["presets"]) * 1.15
    for i, p in enumerate(doc["presets"]):
        docs, _ = _preset_docs(a, p, mats, "prefab", (0, 0), 0, False)
        rel = f"VFX/Prefabs/{a}_{p['name']}.prefab"
        files += [(rel, (HEADER + "".join(docs)).encode()), (f"{rel}.meta", prefab_meta(guid(u, a, rel)).encode())]
        pos = ((i % DEMO_COLS) * spacing, -(i // DEMO_COLS) * spacing)
        d_docs, root_tr = _preset_docs(a, p, mats, "demo", pos, demo_tr, True)
        demo_docs += d_docs
        demo_kids.append(root_tr)
    demo = [_go(demo_go, [demo_tr], f"{a}_vfx_demo"), _tr(demo_tr, demo_go, (0, 0), demo_kids, 0)] + demo_docs
    demo_rel = f"VFX/{a}_vfx_demo.prefab"
    json_rel = f"VFX/{a}_vfx.json"
    files += [(demo_rel, (HEADER + "".join(demo)).encode()), (f"{demo_rel}.meta", prefab_meta(guid(u, a, demo_rel)).encode()),
              (json_rel, json.dumps(_unity_doc(src, a), indent=1, ensure_ascii=False).encode()),
              (f"{json_rel}.meta", _text_meta(guid(u, a, json_rel)).encode()),
              (f"Editor/{EDITOR_FILE}", EDITOR_SCRIPT.encode()), (f"Editor/{EDITOR_FILE}.meta", _script_meta(EDITOR_GUID))]
    names = ", ".join(p["name"] for p in doc["presets"])
    note = (f"- VFX/Prefabs/{a}_<preset>.prefab ({names}): gốc là ParticleSystem rỗng giữ thời lượng — Instantiate là "
            "chạy (play on awake), hiệu ứng một lần tự huỷ khi xong (Stop Action = Destroy), loop thì Stop() để tắt; mỗi "
            "lớp hạt là 1 ParticleSystem con (Texture Sheet Animation cho texture nhiều ô"
            + (", TrailRenderer cho đạn" if any(p.get("trail") for p in doc["presets"]) else "") + "). "
            f"{a}_vfx_demo.prefab bày mọi preset lặp lại để xem. Material ở VFX/Materials dùng shader Legacy Particles "
            f"(Built-in); khi project dùng URP, Editor/{EDITOR_FILE} tự đổi sang Universal Render Pipeline/Particles/Unlit "
            "lúc import (chạy lại: menu Asset Studio > Fit VFX Materials To Render Pipeline). "
            f"{a}_vfx.json là preset trung lập (đơn vị thế giới, y lên) để dựng lại ở engine khác.")
    return files, [note]


def _gd(v: float) -> str:
    return f"{float(v):.6g}"


def _gdf(v: float) -> str:
    """Curve tangents must parse as floats ("0" is an int to Godot and the curve fails to load)."""
    t = _gd(v)
    return t if any(c in t for c in ".en") else t + ".0"


def _sub_gradient(rid: str, keys: list[list[Any]]) -> str:
    offs = ", ".join(_gd(t) for t, _ in keys)
    cols = ", ".join(", ".join(_gd(c) for c in _rgba(h)) for _, h in keys)
    return (f'[sub_resource type="Gradient" id="{rid}"]\noffsets = PackedFloat32Array({offs})\n'
            f"colors = PackedColorArray({cols})\n\n"
            f'[sub_resource type="GradientTexture1D" id="{rid}_tex"]\ngradient = SubResource("{rid}")\n\n')


def _sub_curve(rid: str, points: list[list[Any]]) -> str:
    pts = [(float(t), float(v)) for t, v in points]
    data = []
    for i, (t, v) in enumerate(pts):
        left = (v - pts[i - 1][1]) / max(t - pts[i - 1][0], 1e-6) if i > 0 else 0.0
        right = (pts[i + 1][1] - v) / max(pts[i + 1][0] - t, 1e-6) if i + 1 < len(pts) else 0.0
        data.append(f"Vector2({_gd(t)}, {_gd(v)}), {_gdf(left)}, {_gdf(right)}, {1 if i > 0 else 0}, "
                    f"{1 if i + 1 < len(pts) else 0}")
    return (f'[sub_resource type="Curve" id="{rid}"]\n_data = [{", ".join(data)}]\npoint_count = {len(pts)}\n\n'
            f'[sub_resource type="CurveTexture" id="{rid}_tex"]\ncurve = SubResource("{rid}")\n\n')


def _ppm(rid: str, em: dict[str, Any], unit: float, two_d: bool, cell_px: int) -> str:
    """ParticleProcessMaterial; 2D works in pixels with y down, 3D in metres with y up."""
    up = -1.0 if two_d else 1.0
    sh = em["shape"]
    kind, radius = sh.get("type", "point"), float(sh.get("radius", 0.0)) * unit
    lines = [f'[sub_resource type="ParticleProcessMaterial" id="{rid}"]']
    if two_d:
        lines.append("particle_flag_disable_z = true")
    if em["align"] == "velocity":
        lines.append("particle_flag_align_y = true")
    spread = 180.0
    if kind == "sphere":
        lines += ["emission_shape = 1", f"emission_sphere_radius = {_gd(max(radius, 0.01))}"]
    elif kind == "circle":
        inner = radius * (1 - float(sh.get("thickness", 1.0)))
        lines += ["emission_shape = 6", "emission_ring_axis = Vector3(0, 0, 1)", "emission_ring_height = 0.0",
                  f"emission_ring_radius = {_gd(max(radius, 0.01))}", f"emission_ring_inner_radius = {_gd(inner)}"]
    elif kind == "cone":
        spread = float(sh.get("angle", 0.0))
        ext = f"Vector3({_gd(radius)}, 0, 0)" if two_d else f"Vector3({_gd(radius)}, 0, {_gd(radius)})"
        lines += ["emission_shape = 3", f"emission_box_extents = {ext}"]
    lo, hi = _life(em)
    s0, s1 = float(em["speed"][0]) * unit, float(em["speed"][1]) * unit
    size_scale = unit / cell_px if two_d else 1.0
    lines += [f"lifetime_randomness = {_gd(max(0.0, 1 - lo / hi) if hi > 0 else 0)}",
              f"direction = Vector3(0, {_gd(up)}, 0)", f"spread = {_gd(spread)}",
              f"initial_velocity_min = {_gd(s0)}", f"initial_velocity_max = {_gd(s1)}",
              f"gravity = Vector3(0, {_gd(-up * float(em['gravity']) * unit)}, 0)",
              f"angle_min = {_gd(em['rotation'][0])}", f"angle_max = {_gd(em['rotation'][1])}",
              f"angular_velocity_min = {_gd(em['spin'][0])}", f"angular_velocity_max = {_gd(em['spin'][1])}",
              f"scale_min = {_gd(float(em['size'][0]) * size_scale)}",
              f"scale_max = {_gd(float(em['size'][1]) * size_scale)}",
              f'scale_curve = SubResource("{rid}_size_tex")', f'color_ramp = SubResource("{rid}_color_tex")']
    if float(em["drag"]) > 0:
        # Godot damping is a constant deceleration (units/s²); this matches linear drag at the mean start speed.
        damp = float(em["drag"]) * (s0 + s1) / 2 * 0.5
        lines += [f"damping_min = {_gd(damp)}", f"damping_max = {_gd(damp)}"]
    from studio_core.ops.vfx_particles import SHEETS

    if SHEETS.get(em["texture"], (1, 1)) != (1, 1):
        lines += ["anim_offset_max = 1.0"]
    return "\n".join(lines) + "\n\n"


def _godot_emitter_node(em: dict[str, Any], preset: dict[str, Any], rid: str, tex_id: str, two_d: bool,
                        vis: float) -> str:
    lo, hi = _life(em)
    loop = bool(preset["loop"])
    burst = int(em["burst"]) > 0
    typ = "GPUParticles2D" if two_d else "GPUParticles3D"
    lines = [f'[node name="{em["name"]}" type="{typ}" parent="."]']
    if two_d:
        lines += [f'material = SubResource("{rid}_canvas")', f'texture = ExtResource("{tex_id}")',
                  f"visibility_rect = Rect2({_gd(-vis)}, {_gd(-vis)}, {_gd(2 * vis)}, {_gd(2 * vis)})"]
    else:
        lines += [f'draw_pass_1 = SubResource("{rid}_quad")',
                  f"visibility_aabb = AABB({_gd(-vis)}, {_gd(-vis)}, {_gd(-vis)}, {_gd(2 * vis)}, {_gd(2 * vis)}, "
                  f"{_gd(2 * vis)})"]
    lines += [f"amount = {_amount(em)}", f"lifetime = {_gd(hi)}",
              f"one_shot = {'true' if burst and not loop else 'false'}",
              f"local_coords = {'false' if em['space'] == 'world' else 'true'}",
              f'process_material = SubResource("{rid}")']
    if burst:
        lines.append("explosiveness = 1.0")
    if loop:
        lines.append(f"preprocess = {_gd(hi)}")
    if float(em["delay"]) > 0:
        lines += ["emitting = false", f"metadata/delay = {_gd(em['delay'])}"]
    return "\n".join(lines) + "\n\n"


def godot_preset_scene(a: str, preset: dict[str, Any], tex_res: dict[str, str], ppu: float, cell_px: int,
                       two_d: bool, script_res: str) -> str:
    from studio_core.ops.vfx_particles import SHEETS

    emitters = _order(preset["emitters"])
    unit = ppu if two_d else 1.0
    vis = _extent(preset) * unit * 1.3
    textures = sorted({em["texture"] for em in emitters})
    ext = [f'[ext_resource type="Script" path="{script_res}" id="1_script"]']
    ext += [f'[ext_resource type="Texture2D" path="{tex_res[t]}" id="tex_{t}"]' for t in textures]
    subs, nodes = [], []
    for i, em in enumerate(emitters):
        rid = f"e{i}"
        cols, rows = SHEETS.get(em["texture"], (1, 1))
        subs.append(_sub_gradient(f"{rid}_color", em["colors"]) + _sub_curve(f"{rid}_size", em["size_curve"])
                    + _ppm(rid, em, unit, two_d, cell_px))
        # StandardMaterial3D has no particles_animation flag: billboard_mode 3 alone enables the anim fields.
        anim = (("particles_animation = true\n" if two_d else "") + f"particles_anim_h_frames = {cols}\n"
                f"particles_anim_v_frames = {rows}\nparticles_anim_loop = false\n") if cols * rows > 1 else ""
        if two_d:
            subs.append(f'[sub_resource type="CanvasItemMaterial" id="{rid}_canvas"]\n'
                        f"blend_mode = {1 if em['blend'] == 'add' else 0}\n{anim}\n")
        else:
            subs.append(f'[sub_resource type="StandardMaterial3D" id="{rid}_mat"]\ntransparency = 1\n'
                        f"blend_mode = {1 if em['blend'] == 'add' else 0}\nshading_mode = 0\n"
                        f"vertex_color_use_as_albedo = true\nalbedo_texture = ExtResource(\"tex_{em['texture']}\")\n"
                        f"billboard_mode = 3\nbillboard_keep_scale = true\n{anim}\n"
                        f'[sub_resource type="QuadMesh" id="{rid}_quad"]\nmaterial = SubResource("{rid}_mat")\n\n')
        nodes.append(_godot_emitter_node(em, preset, rid, f"tex_{em['texture']}", two_d, vis))
    root_type = "Node2D" if two_d else "Node3D"
    root = (f'[node name="{a}_{preset["name"]}" type="{root_type}"]\nscript = ExtResource("1_script")\n'
            f"one_shot = {'false' if preset['loop'] else 'true'}\n"
            f"duration = {_gd(float(preset['duration']) if preset['loop'] else _span(preset))}\n\n")
    return "[gd_scene format=3]\n\n" + "\n".join(ext) + "\n\n" + "".join(subs) + root + "".join(nodes)


GODOT_SCRIPT = """extends Node
## Asset Studio particle preset: (re)starts every GPUParticles child, honouring its `delay` metadata. One-shot presets
## emit `finished` after `duration`; auto_free frees the node then, loop_demo replays it (demo scene).

signal finished

@export var one_shot := true
@export var duration := 1.0
@export var auto_free := false
@export var loop_demo := false
## Demo only: circles the node so world-space trails show.
@export var demo_orbit := 0.0

var _home := Vector2.ZERO
var _t := 0.0


func _ready() -> void:
	var p = get("position")
	if p is Vector2:
		_home = p
	play()


func play() -> void:
	for c in get_children():
		if c is GPUParticles2D or c is GPUParticles3D:
			var d: float = c.get_meta("delay", 0.0)
			if d > 0.0:
				c.emitting = false
				get_tree().create_timer(d, false).timeout.connect(c.restart)
			else:
				c.restart()
	if one_shot:
		get_tree().create_timer(duration, false).timeout.connect(_done)


func _done() -> void:
	finished.emit()
	if loop_demo:
		play()
	elif auto_free:
		queue_free()


func _process(delta: float) -> void:
	if demo_orbit > 0.0 and get("position") is Vector2:
		_t += delta * 3.0
		set("position", _home + Vector2(cos(_t), sin(_t)) * demo_orbit)
"""


def godot_demo_scene(a: str, presets: list[dict[str, Any]], ppu: float) -> str:
    spacing = 2 * max(_extent(p) for p in presets) * ppu * 1.15
    rows = math.ceil(len(presets) / DEMO_COLS)
    cols = min(DEMO_COLS, len(presets))
    ext, nodes = [], []
    for i, p in enumerate(presets):
        ext.append(f'[ext_resource type="PackedScene" path="Presets/{a}_{p["name"]}.tscn" id="{i + 1}_{p["name"]}"]')
        x, y = (i % DEMO_COLS) * spacing, (i // DEMO_COLS) * spacing
        extra = "loop_demo = true\n" if not p["loop"] else ""
        if p.get("trail"):
            extra += f"demo_orbit = {_gd(spacing * 0.2)}\n"
        nodes.append(f'[node name="{p["name"]}" parent="." instance=ExtResource("{i + 1}_{p["name"]}")]\n'
                     f"position = Vector2({_gd(x)}, {_gd(y)})\n{extra}")
    cx, cy = (cols - 1) * spacing / 2, (rows - 1) * spacing / 2
    # Fits the grid into the preview window (640 px) and a 1152x648 default window alike.
    zoom = min(600 / (cols * spacing), 600 / (rows * spacing))
    cam = (f'[node name="Camera2D" type="Camera2D" parent="."]\nposition = Vector2({_gd(cx)}, {_gd(cy)})\n'
           f"zoom = Vector2({_gd(zoom)}, {_gd(zoom)})\n")
    return ("[gd_scene format=3]\n\n" + "\n".join(ext) + f'\n\n[node name="{a}_vfx_demo" type="Node2D"]\n\n'
            + cam + "\n" + "\n".join(nodes))


def godot_files(src: Source) -> tuple[Files, list[str]]:
    doc, a = src.vfx or {}, src.asset
    cell = int(doc.get("texture_size") or 128)
    files: Files = []
    tex2d, tex3d = {}, {}
    for key, path in src.vfx_files.items():
        rel = _tex_name(a, key)
        files.append((f"VFX/Textures/{rel}", path))
        tex2d[key] = tex3d[key] = f"../Textures/{rel}"
    for p in doc["presets"]:
        files.append((f"VFX/Presets/{a}_{p['name']}.tscn",
                      godot_preset_scene(a, p, tex2d, src.ppu, cell, True, f"../{GODOT_SCRIPT_FILE}").encode()))
        files.append((f"VFX/Presets3D/{a}_{p['name']}_3d.tscn",
                      godot_preset_scene(a, p, tex3d, src.ppu, cell, False, f"../{GODOT_SCRIPT_FILE}").encode()))
    doc_out = {k: v for k, v in doc.items() if k != "textures"}
    doc_out["textures"] = {k: {**{kk: vv for kk, vv in t.items() if kk != "hash"}, "file": f"Textures/{_tex_name(a, k)}"}
                           for k, t in doc.get("textures", {}).items()}
    files += [(f"VFX/{GODOT_SCRIPT_FILE}", GODOT_SCRIPT.encode()),
              (f"VFX/{a}_vfx_demo.tscn", godot_demo_scene(a, doc["presets"], src.ppu).encode()),
              (f"VFX/{a}_vfx.json", json.dumps(doc_out, indent=1, ensure_ascii=False).encode())]
    note = (f"- VFX/Presets/{a}_<preset>.tscn: Node2D + {GODOT_SCRIPT_FILE} + mỗi lớp hạt 1 GPUParticles2D "
            "(ParticleProcessMaterial, màu/cỡ theo đời hạt, CanvasItemMaterial cộng sáng, particles_anim cho texture "
            "nhiều ô). Thêm scene vào cây là chạy; preset một lần phát signal finished, bật auto_free để tự xoá; gọi "
            f"play() để chạy lại. Presets3D/ cùng preset bằng GPUParticles3D (billboard, mét). {a}_vfx_demo.tscn bày "
            f"mọi preset lặp lại. Đơn vị 2D = mét × {src.ppu:g} px.")
    return files, [note]
