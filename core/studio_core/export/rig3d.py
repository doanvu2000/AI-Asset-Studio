"""Engine export of rigged characters from blender.rig_export: Unity gets the FBX with a ModelImporter .meta,
Godot the GLB (it imports both formats, so shipping both would duplicate the character)."""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from studio_core.export.source import Files, Source

REPORT_FILE = "rig3d.json"
REPORT_TYPE = "asset_studio.rig3d"

UNITY_README = ("- Models/{fbx}: nhân vật có rig, .meta đặt sẵn Rig = {rig}{avatar}; mỗi anim một clip ({clips}), "
                "clip lặp đã bật Loop Time. Kéo FBX vào scene, gán Animator Controller dùng các clip này.")
GODOT_README = ("- Models/{glb}: nhân vật có rig (xương tên chuẩn Humanoid) + anim {clips}. Muốn dùng chung anim "
                "Humanoid: Advanced Import > Skeleton3D > Retarget > Bone Map = SkeletonProfileHumanoid (tự khớp tên). "
                "Anim lặp ({loops}): bật Loop Mode trong Advanced Import > Animation.")


def collect(outputs: dict[str, list[Any]], path: Callable[[str], Path], unique: Callable[[str], str],
            filename: Callable[[Any], str]) -> tuple[list[tuple[str, Path]], dict[str, Any] | None]:
    fbx: list[tuple[str, Path]] = []
    rig = None
    seen: set[str] = set()
    for refs in outputs.values():
        for r in refs:
            name = r.name or ""
            if name.lower().endswith(".fbx") and r.hash not in seen:
                seen.add(r.hash)
                fbx.append((unique(filename(r)), path(r.hash)))
            elif name == REPORT_FILE and rig is None:
                try:
                    doc = json.loads(path(r.hash).read_bytes())
                    rig = doc if isinstance(doc, dict) and doc.get("type") == REPORT_TYPE else None
                except (OSError, ValueError):
                    rig = None
    return fbx, rig


def fbx_twin(src: Source, glb_name: str) -> bool:
    """The rigged GLB beside an FBX of the same stem: Unity takes the FBX (no glTFast needed)."""
    return any(Path(n).stem == Path(glb_name).stem for n, _ in src.fbx)


def _clip_yaml(c: dict[str, Any]) -> str:
    return (f"    - serializedVersion: 16\n      name: {c['name']}\n      takeName: {c.get('take') or c['name']}\n"
            f"      internalID: 0\n      firstFrame: {int(c.get('first', 0))}\n      lastFrame: {int(c['last'])}\n"
            f"      wrapMode: 0\n      orientationOffsetY: 0\n      level: 0\n      cycleOffset: 0\n      loop: 0\n"
            f"      hasAdditiveReferencePose: 0\n      loopTime: {1 if c.get('loop') else 0}\n      loopBlend: 0\n"
            f"      loopBlendOrientation: 0\n      loopBlendPositionY: 0\n      loopBlendPositionXZ: 0\n"
            f"      keepOriginalOrientation: 0\n      keepOriginalPositionY: 1\n      keepOriginalPositionXZ: 0\n"
            f"      heightFromFeet: 0\n      mirror: 0\n      bodyMask: \n      curves: []\n      events: []\n"
            f"      transformMask: []\n      maskType: 3\n      maskSource: {{instanceID: 0}}\n"
            f"      additiveReferencePoseFrame: 0\n")


def unity_model_meta(g: str, rig: dict[str, Any] | None) -> str:
    """Fields Unity leaves out keep their defaults. animationType 3 = Humanoid with the avatar auto-mapped from bone
    names, 2 = Generic (anything without the rig report's humanoid flag)."""
    human = bool(rig and rig.get("humanoid"))
    clips = [c for c in (rig or {}).get("clips") or [] if isinstance(c, dict) and "name" in c and "last" in c]
    clip_block = "    clipAnimations:\n" + "".join(_clip_yaml(c) for c in clips) if clips else "    clipAnimations: []\n"
    return (f"fileFormatVersion: 2\nguid: {g}\nModelImporter:\n  serializedVersion: 22200\n"
            f"  externalObjects: {{}}\n  materials:\n    materialImportMode: 2\n"
            f"  animations:\n    resampleCurves: 1\n    animationCompression: 1\n{clip_block}    isReadable: 0\n"
            f"  meshes:\n    globalScale: 1\n    useFileScale: 1\n"
            f"  importAnimation: 1\n  autoGenerateAvatarMappingIfUnspecified: 1\n"
            f"  animationType: {3 if human else 2}\n  avatarSetup: 1\n  userData: \n  assetBundleName: \n"
            f"  assetBundleVariant: \n")


def unity_files(src: Source, guid: Callable[[str], str]) -> tuple[Files, list[str]]:
    files: Files = []
    notes: list[str] = []
    human = bool(src.rig and src.rig.get("humanoid"))
    clips = ", ".join(c["name"] for c in (src.rig or {}).get("clips") or []) or "theo take trong FBX"
    for n, p in src.fbx:
        rel = f"Models/{n}"
        files += [(rel, p), (f"{rel}.meta", unity_model_meta(guid(rel), src.rig).encode())]
        notes.append(UNITY_README.format(fbx=n, rig="Humanoid" if human else "Generic", clips=clips,
                                         avatar=" (Avatar tự tạo từ tên xương)" if human else ""))
    return files, notes


def godot_note(src: Source) -> list[str]:
    if not src.rig:
        return []
    glb = str(src.rig.get("glb") or "")
    if not any(n == glb or Path(n).stem == Path(glb).stem for n, _ in src.glb):
        return []
    clips = (src.rig.get("clips") or [])
    return [GODOT_README.format(glb=glb, clips=", ".join(c["name"] for c in clips),
                                loops=", ".join(c["name"] for c in clips if c.get("loop")) or "không có")]
