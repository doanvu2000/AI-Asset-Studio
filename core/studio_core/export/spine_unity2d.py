"""Spine 4.2 rig -> Unity cutout rig: bone GameObject hierarchy prefab with a SpriteRenderer per attachment, AnimationClips
(Euler / position / scale curves by bone path, active + sorting-order float curves) and an Animator controller. Mesh
attachments are baked into one sprite bound to the slot's bone (no SpriteSkin, so weights and deform are lost)."""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from studio_core.export.damage import _go, _renderer
from studio_core.export.sheet import ident
from studio_core.export.source import Files
from studio_core.export.spine_skeleton2d import Clip, Key, Rig, _f, image_files, png_bytes
from studio_core.export.unity import (CLIP_FILE_ID, CONTROLLER_FILE_ID, HEADER, file_id, guid, native_meta,
                                      prefab_meta, texture_meta)

INF = "Infinity"
THIRD = "0.33333334"
IDLE_NAMES = ("idle", "stand")


@dataclass
class Sprite:
    rel: str
    png: bytes
    size: tuple[int, int]
    center: tuple[float, float] | None


def _baked_mesh(rig: Rig, part, rel: str) -> Sprite:
    from studio_core.ops.spine_runtime import _draw_mesh_px

    g, b = rig.geometry(part), rig.bones[part.bone]
    local = (g.world - b.p) @ np.linalg.pinv(b.m).T
    (x0, y0), (x1, y1) = local.min(axis=0), local.max(axis=0)
    size = (max(1, math.ceil(round(x1 - x0, 6))), max(1, math.ceil(round(y1 - y0, 6))))
    dst = np.stack([local[:, 0] - x0, y1 - local[:, 1]], axis=1)
    layer = _draw_mesh_px(rig.images.get(part.image), {"uvs": part.att["uvs"], "triangles": g.triangles}, dst, size)
    return Sprite(rel, png_bytes(layer.convert("RGBA")), size, (x0 + size[0] / 2, y1 - size[1] / 2))


def sprites(rig: Rig) -> list[Sprite]:
    """One Sprite per part (regions that share an atlas region share the PNG)."""
    names = image_files(rig)
    out: list[Sprite] = []
    cache: dict[str, Sprite] = {}
    for part in rig.parts:
        if part.kind == "mesh":
            stem = names[part.image][:-4]
            out.append(_baked_mesh(rig, part, f"Textures/{stem}_{part.node}.png"))
            continue
        rel = f"Textures/{names[part.image]}"
        if rel not in cache:
            img = rig.region_image(part)
            cache[rel] = Sprite(rel, png_bytes(img), img.size, None)
        out.append(cache[rel])
    if any(p.kind == "mesh" for p in rig.parts):
        rig.warn("mesh attachments are baked to a static sprite on the slot bone for Unity (weights / deform dropped)")
    return out


def _xform(tr: int, go: int, pos: tuple[float, float], rot: float, scale: tuple[float, float], children: list[int],
           father: int) -> str:
    h = math.radians(rot) / 2
    kids = "[]" if not children else "\n" + "".join(f"  - {{fileID: {c}}}\n" for c in children).rstrip("\n")
    return f"""--- !u!4 &{tr}
Transform:
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {{fileID: 0}}
  m_PrefabInstance: {{fileID: 0}}
  m_PrefabAsset: {{fileID: 0}}
  m_GameObject: {{fileID: {go}}}
  serializedVersion: 2
  m_LocalRotation: {{x: 0, y: 0, z: {_f(math.sin(h))}, w: {_f(math.cos(h))}}}
  m_LocalPosition: {{x: {_f(pos[0])}, y: {_f(pos[1])}, z: 0}}
  m_LocalScale: {{x: {_f(scale[0])}, y: {_f(scale[1])}, z: 1}}
  m_ConstrainProportionsScale: 0
  m_Children: {kids}
  m_Father: {{fileID: {father}}}
  m_LocalEulerAnglesHint: {{x: 0, y: 0, z: {_f(rot)}}}
"""


def _animator(an: int, go: int, ctrl_guid: str) -> str:
    return f"""--- !u!95 &{an}
Animator:
  serializedVersion: 7
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {{fileID: 0}}
  m_PrefabInstance: {{fileID: 0}}
  m_PrefabAsset: {{fileID: 0}}
  m_GameObject: {{fileID: {go}}}
  m_Enabled: 1
  m_Avatar: {{fileID: 0}}
  m_Controller: {{fileID: {CONTROLLER_FILE_ID}, guid: {ctrl_guid}, type: 2}}
  m_CullingMode: 0
  m_UpdateMode: 0
  m_ApplyRootMotion: 0
  m_LinearVelocityBlending: 0
  m_StabilizeFeet: 0
  m_AnimatePhysics: 0
  m_WarningMessage:
  m_HasTransformHierarchy: 1
  m_AllowConstantClipSamplingOptimization: 1
  m_KeepAnimatorStateOnDisable: 0
  m_WriteDefaultValuesOnDisable: 0
"""


def prefab_text(rig: Rig, asset: str, sprs: list[Sprite], sprite_guids: list[str], ppu: float,
                ctrl_guid: str | None) -> str:
    fid = lambda *k: file_id("spine", asset, *k)  # noqa: E731
    root_go, root_tr, an = fid("root", "go"), fid("root", "tr"), fid("root", "animator")
    kids: dict[int, list[int]] = {fid("bone", i, "tr"): [] for i in range(len(rig.bones))}
    kids[root_tr] = []
    docs: list[str] = []
    for i, b in enumerate(rig.bones):
        go, tr = fid("bone", i, "go"), fid("bone", i, "tr")
        father = fid("bone", b.parent, "tr") if b.parent >= 0 else root_tr
        kids[father].append(tr)
        docs += [_go(go, [tr], b.node), ("tr", tr, go, (b.x / ppu, b.y / ppu), b.rotation, (b.sx, b.sy), father)]
    for i, part in enumerate(rig.parts):
        go, tr, sr = (fid("part", i, k) for k in ("go", "tr", "sr"))
        bone_tr = fid("bone", part.bone, "tr")
        kids[bone_tr].append(tr)
        spr, att = sprs[i], part.att
        if spr.center is not None:
            pos, rot, scale = (spr.center[0] / ppu, spr.center[1] / ppu), 0.0, (1.0, 1.0)
        else:
            w, h = float(att.get("width", spr.size[0])), float(att.get("height", spr.size[1]))
            pos = (att.get("x", 0.0) / ppu, att.get("y", 0.0) / ppu)
            rot = float(att.get("rotation", 0.0))
            scale = (att.get("scaleX", 1.0) * w / spr.size[0], att.get("scaleY", 1.0) * h / spr.size[1])
        rend = _renderer(sr, go, sprite_guids[i], (spr.size[0] / ppu, spr.size[1] / ppu), part.slot)
        rend = rend.replace("m_Color: {r: 1, g: 1, b: 1, a: 1}",
                            f"m_Color: {{r: {_f(part.color[0])}, g: {_f(part.color[1])}, b: {_f(part.color[2])}, "
                            f"a: {_f(part.color[3])}}}")
        docs += [_go(go, [tr, sr], part.node, part.visible), ("tr", tr, go, pos, rot, scale, bone_tr), rend]
    out = [HEADER, _go(root_go, [root_tr, *([an] if ctrl_guid else [])], ident(asset, "spine")),
           _xform(root_tr, root_go, (0, 0), 0.0, (1, 1), kids[root_tr], 0)]
    for d in docs:
        if isinstance(d, tuple):
            _, tr, go, pos, rot, scale, father = d
            out.append(_xform(tr, go, pos, rot, scale, kids[tr], father))
        else:
            out.append(d)
    if ctrl_guid:
        out.append(_animator(an, root_go, ctrl_guid))
    return "".join(out)


def _slopes(keys: list[tuple[float, tuple[float, ...], bool]]) -> list[tuple[tuple, tuple]]:
    n, dim = len(keys), len(keys[0][1])
    zero, inf = (0.0,) * dim, (INF,) * dim

    def seg(a: int, b: int) -> tuple[float, ...]:
        dt = keys[b][0] - keys[a][0]
        return tuple((y - x) / dt for x, y in zip(keys[a][1], keys[b][1])) if dt > 1e-9 else zero

    out = []
    for i in range(n):
        out_s = (inf if keys[i][2] else seg(i, i + 1)) if i + 1 < n else zero
        in_s = (inf if keys[i - 1][2] else seg(i - 1, i)) if i > 0 else out_s
        out.append((in_s, out_s))
    return out


def _s(v: float | str) -> str:
    return v if isinstance(v, str) else _f(v)


def _vec_entries(keys: list[tuple[float, tuple[float, float, float], bool]]) -> str:
    out = []
    for (t, v, _), (si, so) in zip(keys, _slopes(keys)):
        def xyz(c) -> str:
            return f"{{x: {_s(c[0])}, y: {_s(c[1])}, z: {_s(c[2])}}}"
        out.append(f"""      - serializedVersion: 3
        time: {_f(t)}
        value: {xyz(v)}
        inSlope: {xyz(si)}
        outSlope: {xyz(so)}
        tangentMode: 0
        weightedMode: 0
        inWeight: {xyz((THIRD,) * 3)}
        outWeight: {xyz((THIRD,) * 3)}
""")
    return "".join(out)


def _vec_curves(items: list[tuple[str, list[tuple[float, tuple[float, float, float], bool]]]]) -> str:
    if not items:
        return " []\n"
    return "\n" + "".join(f"""  - curve:
      serializedVersion: 2
      m_Curve:
{_vec_entries(keys)}      m_PreInfinity: 2
      m_PostInfinity: 2
      m_RotationOrder: 4
    path: {path}
""" for path, keys in items)


def _float_curves(items: list[tuple[str, str, int, list[tuple[float, float]]]]) -> str:
    if not items:
        return " []\n"
    out = []
    for path, attr, class_id, keys in items:
        entries = "".join(f"""      - serializedVersion: 3
        time: {_f(t)}
        value: {_f(v)}
        inSlope: {INF}
        outSlope: {INF}
        tangentMode: 103
        weightedMode: 0
        inWeight: 0
        outWeight: 0
""" for t, v in keys)
        out.append(f"""  - serializedVersion: 2
    curve:
      serializedVersion: 2
      m_Curve:
{entries}      m_PreInfinity: 2
      m_PostInfinity: 2
      m_RotationOrder: 4
    attribute: {attr}
    path: {path}
    classID: {class_id}
    script: {{fileID: 0}}
    flags: 0
""")
    return "\n" + "".join(out)


def _bone_path(rig: Rig, i: int) -> str:
    return rig.bones[i].path


def _vec3(group: str, keys: list[Key], ppu: float) -> list[tuple[float, tuple[float, float, float], bool]]:
    out = []
    for t, v, held in keys:
        if group == "rotation":
            out.append((t, (0.0, 0.0, v[0]), held))
        elif group == "position":
            out.append((t, (v[0] / ppu, v[1] / ppu, 0.0), held))
        else:
            out.append((t, (v[0], v[1], 1.0), held))
    return out


def clip_text(rig: Rig, clip: Clip, name: str, ppu: float) -> str:
    curves: dict[str, list] = {"rotation": [], "position": [], "scale": []}
    for bi, groups in clip.bones.items():
        for group, keys in groups.items():
            curves[group].append((_bone_path(rig, bi), _vec3(group, keys, ppu)))
    floats = []
    for pi, vis in clip.visible.items():
        floats.append((_part_path(rig, pi), "m_IsActive", 1, [(t, 1.0 if v else 0.0) for t, v in vis]))
    if clip.order:
        for pi, part in enumerate(rig.parts):
            floats.append((_part_path(rig, pi), "m_SortingOrder", 212, [(t, float(pos[part.slot]))
                                                                        for t, pos in clip.order]))
    stop = max(clip.length, 0.01)
    return f"""{HEADER}--- !u!74 &{CLIP_FILE_ID}
AnimationClip:
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {{fileID: 0}}
  m_PrefabInstance: {{fileID: 0}}
  m_PrefabAsset: {{fileID: 0}}
  m_Name: {name}
  serializedVersion: 7
  m_Legacy: 0
  m_Compressed: 0
  m_UseHighQualityCurve: 1
  m_RotationCurves: []
  m_CompressedRotationCurves: []
  m_EulerCurves:{_vec_curves(curves["rotation"])}  m_PositionCurves:{_vec_curves(curves["position"])}  m_ScaleCurves:{_vec_curves(curves["scale"])}  m_FloatCurves:{_float_curves(floats)}  m_PPtrCurves: []
  m_SampleRate: {_f(rig.fps)}
  m_WrapMode: 0
  m_Bounds:
    m_Center: {{x: 0, y: 0, z: 0}}
    m_Extent: {{x: 0, y: 0, z: 0}}
  m_ClipBindingConstant:
    genericBindings: []
    pptrCurveMapping: []
  m_AnimationClipSettings:
    serializedVersion: 2
    m_AdditiveReferencePoseClip: {{fileID: 0}}
    m_AdditiveReferencePoseTime: 0
    m_StartTime: 0
    m_StopTime: {_f(stop)}
    m_OrientationOffsetY: 0
    m_Level: 0
    m_CycleOffset: 0
    m_HasAdditiveReferencePose: 0
    m_LoopTime: {1 if clip.loop else 0}
    m_LoopBlend: 0
    m_LoopBlendOrientation: 0
    m_LoopBlendPositionY: 0
    m_LoopBlendPositionXZ: 0
    m_KeepOriginalOrientation: 0
    m_KeepOriginalPositionY: 1
    m_KeepOriginalPositionXZ: 0
    m_HeightFromFeet: 0
    m_Mirror: 0
  m_EditorCurves: []
  m_EulerEditorCurves: []
  m_HasGenericRootTransform: 0
  m_HasMotionFloatCurves: 0
  m_Events: []
"""


def _part_path(rig: Rig, pi: int) -> str:
    part = rig.parts[pi]
    return f"{rig.bones[part.bone].path}/{part.node}"


def controller_text(asset: str, names: list[str], clip_guids: list[str]) -> str:
    sm = file_id("spine", asset, "sm")
    default = next((i for i, n in enumerate(names) if n.lower() in IDLE_NAMES), 0)
    docs, states = [], []
    for i, (n, g) in enumerate(zip(names, clip_guids)):
        sid = file_id("spine", asset, "state", n)
        docs.append(f"""--- !u!1102 &{sid}
AnimatorState:
  serializedVersion: 6
  m_ObjectHideFlags: 1
  m_CorrespondingSourceObject: {{fileID: 0}}
  m_PrefabInstance: {{fileID: 0}}
  m_PrefabAsset: {{fileID: 0}}
  m_Name: {n}
  m_Speed: 1
  m_CycleOffset: 0
  m_Transitions: []
  m_StateMachineBehaviours: []
  m_Position: {{x: 50, y: 50, z: 0}}
  m_IKOnFeet: 0
  m_WriteDefaultValues: 1
  m_Mirror: 0
  m_SpeedParameterActive: 0
  m_MirrorParameterActive: 0
  m_CycleOffsetParameterActive: 0
  m_TimeParameterActive: 0
  m_Motion: {{fileID: {CLIP_FILE_ID}, guid: {g}, type: 2}}
  m_Tag:
  m_SpeedParameter:
  m_MirrorParameter:
  m_CycleOffsetParameter:
  m_TimeParameter:
""")
        states.append(f"""  - serializedVersion: 1
    m_State: {{fileID: {sid}}}
    m_Position: {{x: {300 + 260 * (i % 3)}, y: {60 + 70 * (i // 3)}, z: 0}}
""")
    docs.append(f"""--- !u!91 &{CONTROLLER_FILE_ID}
AnimatorController:
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {{fileID: 0}}
  m_PrefabInstance: {{fileID: 0}}
  m_PrefabAsset: {{fileID: 0}}
  m_Name: {asset}
  serializedVersion: 5
  m_AnimatorParameters: []
  m_AnimatorLayers:
  - serializedVersion: 5
    m_Name: Base Layer
    m_StateMachine: {{fileID: {sm}}}
    m_Mask: {{fileID: 0}}
    m_Motions: []
    m_Behaviours: []
    m_BlendingMode: 0
    m_SyncedLayerIndex: -1
    m_DefaultWeight: 0
    m_IKPass: 0
    m_SyncedLayerAffectsTiming: 0
    m_Controller: {{fileID: {CONTROLLER_FILE_ID}}}
""")
    docs.append(f"""--- !u!1107 &{sm}
AnimatorStateMachine:
  serializedVersion: 6
  m_ObjectHideFlags: 1
  m_CorrespondingSourceObject: {{fileID: 0}}
  m_PrefabInstance: {{fileID: 0}}
  m_PrefabAsset: {{fileID: 0}}
  m_Name: Base Layer
  m_ChildStates:
{"".join(states)}  m_ChildStateMachines: []
  m_AnyStateTransitions: []
  m_EntryTransitions: []
  m_StateMachineTransitions: {{}}
  m_StateMachineBehaviours: []
  m_AnyStatePosition: {{x: 50, y: 20, z: 0}}
  m_EntryPosition: {{x: 50, y: 120, z: 0}}
  m_ExitPosition: {{x: 800, y: 120, z: 0}}
  m_ParentStateMachinePosition: {{x: 800, y: 20, z: 0}}
  m_DefaultState: {{fileID: {file_id("spine", asset, "state", names[default])}}}
""")
    return HEADER + "".join(docs)


def unity_files(rig: Rig, asset: str, uid: str = "", ppu: float = 100.0) -> tuple[Files, list[str]]:
    files: Files = []
    sprs = sprites(rig)
    guids: dict[str, str] = {}
    for s in sprs:
        if s.rel not in guids:
            guids[s.rel] = guid(uid, asset, s.rel)
            files += [(s.rel, s.png), (f"{s.rel}.meta", texture_meta(asset, guids[s.rel], None, s.size, ppu,
                                                                     False).encode())]
    clips = rig.clips()
    clip_names: list[str] = []
    clip_guids: list[str] = []
    for clip in clips:
        base = ident(clip.name, "clip")
        name, n = base, 2
        while name in clip_names:
            name, n = f"{base}_{n}", n + 1
        rel = f"Animations/{asset}_{name}.anim"
        clip_names.append(name)
        clip_guids.append(guid(uid, asset, rel))
        files += [(rel, clip_text(rig, clip, name, ppu).encode()),
                  (f"{rel}.meta", native_meta(clip_guids[-1], CLIP_FILE_ID).encode())]
    ctrl_guid = None
    if clips:
        ctrl_rel = f"{asset}.controller"
        ctrl_guid = guid(uid, asset, ctrl_rel)
        files += [(ctrl_rel, controller_text(asset, clip_names, clip_guids).encode()),
                  (f"{ctrl_rel}.meta", native_meta(ctrl_guid, CONTROLLER_FILE_ID).encode())]
    prefab_rel = f"{asset}.prefab"
    prefab_guid = guid(uid, asset, prefab_rel)
    files += [(prefab_rel, prefab_text(rig, asset, sprs, [guids[s.rel] for s in sprs], ppu, ctrl_guid).encode()),
              (f"{prefab_rel}.meta", prefab_meta(prefab_guid).encode())]
    note = (f"- {asset}.prefab: cây GameObject {len(rig.bones)} xương (Transform theo setup pose Spine, 1 đơn vị Spine = "
            f"1/{ppu:g} đơn vị Unity) + {len(rig.parts)} SpriteRenderer con theo xương (Textures/, sorting order = thứ tự "
            f"slot); {len(clips)} AnimationClip trong Animations/ (rotate / translate / scale theo path xương, "
            f"active + sorting order cho attachment / draw order) và {asset}.controller. Mesh được bake thành sprite "
            "tĩnh gắn xương slot, không dùng SpriteSkin.")
    return files, [note]
