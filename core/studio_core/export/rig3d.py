"""Engine export of rigged characters from blender.rig_export: Unity gets the FBX with a ModelImporter .meta,
Godot the GLB (it imports both formats, so shipping both would duplicate the character)."""
from __future__ import annotations

import io
import json
import struct
from collections.abc import Callable
from pathlib import Path, PureWindowsPath
from typing import Any

from PIL import Image

from studio_core.export.source import Files, Source

REPORT_FILE = "rig3d.json"
REPORT_TYPE = "asset_studio.rig3d"
# One enabled int32 per AvatarMaskBodyPart (13). An empty bodyMask crashes Unity 6's FBX import
# (ModelImporter::SplitAnimationClips -> RemovedMaskedCurve) on a Humanoid rig.
BODY_MASK_ALL = "01000000" * 13

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


_FBX_SCALAR = {"Y": "<h", "C": "<?", "I": "<i", "F": "<f", "D": "<d", "L": "<q"}
_IMAGE_MAGIC = ((b"\x89PNG", ".png"), (b"\xff\xd8\xff", ".jpg"))


def _fbx_nodes(b: bytes) -> list[tuple[str, list[Any], list[Any]]]:
    """Binary FBX node tree (name, properties, children); array properties are skipped, raw ones kept as bytes."""
    wide = struct.unpack_from("<I", b, 23)[0] >= 7500
    head = "<QQQ" if wide else "<III"
    hsize = struct.calcsize(head)

    def node(o: int) -> tuple[Any, int]:
        end, count, _ = struct.unpack_from(head, b, o)
        if end == 0:
            return None, o
        o += hsize
        name = b[o + 1:o + 1 + b[o]].decode("ascii", "replace")
        o += 1 + b[o]
        props: list[Any] = []
        for _ in range(count):
            t = chr(b[o])
            o += 1
            if t in _FBX_SCALAR:
                props.append(struct.unpack_from(_FBX_SCALAR[t], b, o)[0])
                o += struct.calcsize(_FBX_SCALAR[t])
            elif t in "fdlib":
                o += 12 + struct.unpack_from("<III", b, o)[2]
                props.append(None)
            else:
                n = struct.unpack_from("<I", b, o)[0]
                raw = b[o + 4:o + 4 + n]
                props.append(raw.decode("utf-8", "replace") if t == "S" else raw)
                o += 4 + n
        kids = []
        while o < end:
            k, o = node(o)
            if k is None:
                break
            kids.append(k)
        return (name, props, kids), end

    out, o = [], 27
    while o < len(b) - hsize:
        n, o = node(o)
        if n is None:
            break
        out.append(n)
    return out


def fbx_embedded_images(fbx: Path) -> list[tuple[str, bytes, bool]]:
    """Images embedded in a binary FBX: (file name its textures point at, bytes, used as a normal map)."""
    try:
        data = fbx.read_bytes()
        if not data.startswith(b"Kaydara FBX Binary"):
            return []
        top = {n[0]: n[2] for n in _fbx_nodes(data)}
        kinds = {p[0]: n for n, p, _ in top.get("Objects", []) if p}
        normal_tex = {p[1] for n, p, _ in top.get("Connections", []) if n == "C" and len(p) > 3
                      and kinds.get(p[1]) == "Texture" and any(k in str(p[3]) for k in ("Normal", "Bump"))}
        video_tex = {p[1]: p[2] for n, p, _ in top.get("Connections", []) if n == "C" and len(p) > 2
                     and kinds.get(p[1]) == "Video" and kinds.get(p[2]) == "Texture"}
        out, seen = [], set()
        for n, p, kids in top.get("Objects", []):
            if n != "Video" or not p:
                continue
            fields = {k[0]: k[1][0] for k in kids if k[1]}
            content, rel = fields.get("Content"), str(fields.get("RelativeFilename") or "")
            if not isinstance(content, bytes) or not content:
                continue
            name = PureWindowsPath(rel).name or f"texture_{len(out)}"
            if not Path(name).suffix:
                name += next((ext for magic, ext in _IMAGE_MAGIC if content.startswith(magic)), ".png")
            if name.lower() in seen:
                continue
            seen.add(name.lower())
            out.append((name, content, video_tex.get(p[0]) in normal_tex))
        return out
    except (OSError, struct.error, IndexError, ValueError):
        return []


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
            f"      heightFromFeet: 0\n      mirror: 0\n      bodyMask: {BODY_MASK_ALL}\n      curves: []\n      events: []\n"
            f"      transformMask: []\n      maskType: 3\n      maskSource: {{instanceID: 0}}\n"
            f"      additiveReferencePoseFrame: 0\n")


def unity_model_meta(g: str, rig: dict[str, Any] | None) -> str:
    """Fields Unity leaves out keep their defaults. animationType 3 = Humanoid with the avatar auto-mapped from bone
    names, 2 = Generic (anything without the rig report's humanoid flag). materialLocation 0 (legacy External):
    with the default (embedded materials) Unity leaves the FBX's embedded textures unused and the model imports
    white; External builds Materials/ wired to the textures in <model>.fbm/."""
    human = bool(rig and rig.get("humanoid"))
    clips = [c for c in (rig or {}).get("clips") or [] if isinstance(c, dict) and "name" in c and "last" in c]
    clip_block = "    clipAnimations:\n" + "".join(_clip_yaml(c) for c in clips) if clips else "    clipAnimations: []\n"
    return (f"fileFormatVersion: 2\nguid: {g}\nModelImporter:\n  serializedVersion: 22200\n"
            f"  externalObjects: {{}}\n  materials:\n    materialImportMode: 2\n    materialLocation: 0\n"
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
    from studio_core.export.unity import texture_meta

    for n, p in src.fbx:
        rel = f"Models/{n}"
        files += [(rel, p), (f"{rel}.meta", unity_model_meta(guid(rel), src.rig).encode())]
        # Unity does not extract embedded textures inside a package (and only on first import elsewhere); shipped
        # beside the FBX where its texture paths point, they are found either way.
        for img, data, is_normal in fbx_embedded_images(p):
            irel = f"Models/{Path(n).stem}.fbm/{img}"
            try:
                with Image.open(io.BytesIO(data)) as im:
                    size = im.size
            except OSError:
                continue
            files += [(irel, data), (f"{irel}.meta", texture_meta(src.asset, guid(irel), None, size, src.ppu, False,
                                                                 is_normal=is_normal, repeat=True).encode())]
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
