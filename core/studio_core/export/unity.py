"""Unity drop-in folder: sliced sprite sheet (.meta written by hand), AnimationClips, a 2D blend-tree Animator, prefab."""
from __future__ import annotations

import hashlib
from pathlib import Path

from studio_core.export.collider import for_sheet, unity_physics_shape
from studio_core.export.sheet import Layout, Track, direction_vector, layout, stable_hex
from studio_core.export.source import Files, Source

TEXTURE_FILE_ID = 2800000
CLIP_FILE_ID = 7400000
CONTROLLER_FILE_ID = 9100000
_RESERVED_IDS = {0, TEXTURE_FILE_ID, CLIP_FILE_ID, CONTROLLER_FILE_ID, 21300000}
HEADER = "%YAML 1.1\n%TAG !u! tag:unity3d.com,2011:\n"


def guid(uid: str, asset: str, rel: str) -> str:
    """Seeded by the source step id too: two runs exporting the same asset name must not share GUIDs in one project."""
    return stable_hex("unity", uid, asset, rel) if uid else stable_hex("unity", asset, rel)


def file_id(*parts: str) -> int:
    """Deterministic signed 64-bit local id, the same width Unity uses for internalID/fileID."""
    n = int.from_bytes(hashlib.blake2b("\x1f".join(parts).encode(), digest_size=8).digest(), "little", signed=True)
    return n + 1 if n in _RESERVED_IDS else n


def _f(v: float) -> str:
    return f"{v:.6g}"


def _platform(target: str, max_size: int) -> str:
    return f"""  - serializedVersion: 4
    buildTarget: {target}
    maxTextureSize: {max_size}
    resizeAlgorithm: 0
    textureFormat: -1
    textureCompression: 0
    compressionQuality: 50
    crunchedCompression: 0
    allowsAlphaSplitting: 0
    overridden: 0
    ignorePlatformSupport: 0
    androidETC2FallbackOverride: 0
    forceMaximumCompressionQuality_BC6H_BC7: 0
"""


def _sprite_entry(asset: str, name: str, x: int, y: int, w: int, h: int, pivot: tuple[float, float],
                  physics: str = "      physicsShape: []\n") -> str:
    return f"""    - serializedVersion: 2
      name: {name}
      rect:
        serializedVersion: 2
        x: {x}
        y: {y}
        width: {w}
        height: {h}
      alignment: 9
      pivot: {{x: {_f(pivot[0])}, y: {_f(pivot[1])}}}
      border: {{x: 0, y: 0, z: 0, w: 0}}
      customData:
      outline: []
{physics}      tessellationDetail: 0
      bones: []
      spriteID: {stable_hex("sprite", asset, name)}
      internalID: {file_id("sprite", asset, name)}
      vertices: []
      indices:
      edges: []
      weights: []
"""


def _border(b: tuple[int, int, int, int] | None) -> str:
    """Unity orders a sprite border (left, bottom, right, top); slices store (left, top, right, bottom)."""
    left, top, right, bottom = b or (0, 0, 0, 0)
    return f"{{x: {left}, y: {bottom}, z: {right}, w: {top}}}"


def texture_meta(asset: str, tex_guid: str, lay: Layout | None, size: tuple[int, int], ppu: float, pixel: bool,
                 normal_guid: str | None = None, is_normal: bool = False, repeat: bool = False,
                 border: tuple[int, int, int, int] | None = None, shapes: dict[str, list] | None = None) -> str:
    max_size = 32
    while max_size < max(size) and max_size < 16384:
        max_size *= 2
    sprites = lay is not None and not is_normal
    entries, table, secondary = "", "", " []"
    if sprites and lay is not None:
        # Unity's rect origin is the texture's bottom-left; the sheet layout is top-left.
        for c in lay.cells:
            physics = unity_physics_shape((shapes or {}).get(c.name) or [], c.w, c.h)
            entries += _sprite_entry(asset, c.name, c.x, lay.height - c.y - c.h, c.w, c.h, lay.pivot_norm, physics)
        table = "".join(f"      {c.name}: {file_id('sprite', asset, c.name)}\n" for c in lay.cells)
        if normal_guid:
            secondary = f"\n    - texture: {{fileID: {TEXTURE_FILE_ID}, guid: {normal_guid}, type: 3}}\n      name: _NormalMap"
    return f"""fileFormatVersion: 2
guid: {tex_guid}
TextureImporter:
  internalIDToNameTable: []
  externalObjects: {{}}
  serializedVersion: 13
  mipmaps:
    mipMapMode: 0
    enableMipMap: {1 if repeat and not pixel else 0}
    sRGBTexture: {0 if is_normal else 1}
    linearTexture: 0
    fadeOut: 0
    borderMipMap: 0
    mipMapsPreserveCoverage: 0
    alphaTestReferenceValue: 0.5
    mipMapFadeDistanceStart: 1
    mipMapFadeDistanceEnd: 3
  bumpmap:
    convertToNormalMap: 0
    externalNormalMap: 0
    heightScale: 0.25
    normalMapFilter: 0
    flipGreenChannel: 0
  isReadable: 0
  streamingMipmaps: 0
  streamingMipmapsPriority: 0
  vTOnly: 0
  ignoreMipmapLimit: 0
  grayScaleToAlpha: 0
  generateCubemap: 6
  cubemapConvolution: 0
  seamlessCubemap: 0
  textureFormat: 1
  maxTextureSize: {max_size}
  textureSettings:
    serializedVersion: 2
    filterMode: {0 if pixel else 1}
    aniso: 1
    mipBias: 0
    wrapU: {0 if repeat else 1}
    wrapV: {0 if repeat else 1}
    wrapW: {0 if repeat else 1}
  nPOTScale: 0
  lightmap: 0
  compressionQuality: 50
  spriteMode: {2 if sprites else (0 if is_normal or repeat else 1)}
  spriteExtrude: 1
  spriteMeshType: {0 if sprites or border else 1}
  alignment: 9
  spritePivot: {{x: {_f(lay.pivot_norm[0]) if lay else 0.5}, y: {_f(lay.pivot_norm[1]) if lay else 0.5}}}
  spritePixelsToUnits: {_f(ppu)}
  spriteBorder: {_border(border)}
  spriteGenerateFallbackPhysicsShape: 0
  alphaUsage: 1
  alphaIsTransparency: {0 if is_normal or repeat else 1}
  spriteTessellationDetail: -1
  textureType: {1 if is_normal else (0 if repeat else 8)}
  textureShape: 1
  singleChannelComponent: 0
  flipbookRows: 1
  flipbookColumns: 1
  maxTextureSizeSet: 0
  compressionQualitySet: 0
  textureFormatSet: 0
  ignorePngGamma: 0
  applyGammaDecoding: 0
  swizzle: 50462976
  cookieLightType: 0
  platformSettings:
{_platform("DefaultTexturePlatform", max_size)}{_platform("Standalone", max_size)}  spriteSheet:
    serializedVersion: 2
    sprites:{" []" if not entries else chr(10) + entries.rstrip(chr(10))}
    outline: []
    customData:
    physicsShape: []
    bones: []
    spriteID: {stable_hex("sheet", asset, tex_guid)}
    internalID: 0
    vertices: []
    indices:
    edges: []
    weights: []
    secondaryTextures:{secondary}
    spriteCustomMetadata:
      entries: []
    nameFileIdTable:{" {}" if not table else chr(10) + table.rstrip(chr(10))}
  mipmapLimitGroupName:
  pSDRemoveMatte: 0
  userData:
  assetBundleName:
  assetBundleVariant:
"""


def native_meta(g: str, main_id: int) -> str:
    return (f"fileFormatVersion: 2\nguid: {g}\nNativeFormatImporter:\n  externalObjects: {{}}\n"
            f"  mainObjectFileID: {main_id}\n  userData: \n  assetBundleName: \n  assetBundleVariant: \n")


def prefab_meta(g: str) -> str:
    return (f"fileFormatVersion: 2\nguid: {g}\nPrefabImporter:\n  externalObjects: {{}}\n  userData: \n"
            f"  assetBundleName: \n  assetBundleVariant: \n")


def anim_clip(asset: str, tex_guid: str, t: Track) -> str:
    refs = [f"{{fileID: {file_id('sprite', asset, c.name)}, guid: {tex_guid}, type: 3}}" for c in t.cells]
    keys = "".join(f"    - time: {_f(i / t.fps)}\n      value: {r}\n" for i, r in enumerate(refs))
    mapping = "".join(f"    - {r}\n" for r in refs)
    return f"""{HEADER}--- !u!74 &{CLIP_FILE_ID}
AnimationClip:
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {{fileID: 0}}
  m_PrefabInstance: {{fileID: 0}}
  m_PrefabAsset: {{fileID: 0}}
  m_Name: {t.name}
  serializedVersion: 7
  m_Legacy: 0
  m_Compressed: 0
  m_UseHighQualityCurve: 1
  m_RotationCurves: []
  m_CompressedRotationCurves: []
  m_EulerCurves: []
  m_PositionCurves: []
  m_ScaleCurves: []
  m_FloatCurves: []
  m_PPtrCurves:
  - serializedVersion: 2
    curve:
{keys}    attribute: m_Sprite
    path:
    classID: 212
    script: {{fileID: 0}}
    flags: 2
  m_SampleRate: {_f(t.fps)}
  m_WrapMode: 0
  m_Bounds:
    m_Center: {{x: 0, y: 0, z: 0}}
    m_Extent: {{x: 0, y: 0, z: 0}}
  m_ClipBindingConstant:
    genericBindings:
    - serializedVersion: 2
      path: 0
      attribute: 0
      script: {{fileID: 0}}
      typeID: 212
      customType: 23
      isPPtrCurve: 1
      isIntCurve: 0
      isSerializeReferenceCurve: 0
    pptrCurveMapping:
{mapping}  m_AnimationClipSettings:
    serializedVersion: 2
    m_AdditiveReferencePoseClip: {{fileID: 0}}
    m_AdditiveReferencePoseTime: 0
    m_StartTime: 0
    m_StopTime: {_f(len(t.cells) / t.fps)}
    m_OrientationOffsetY: 0
    m_Level: 0
    m_CycleOffset: 0
    m_HasAdditiveReferencePose: 0
    m_LoopTime: {1 if t.loop else 0}
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


def controller(asset: str, name: str, lay: Layout, clip_guids: dict[str, str]) -> str:
    sm_id = file_id("sm", asset)
    clips = lay.clips()
    default = "idle" if "idle" in clips else clips[0]
    docs, states = [], []
    for i, clip in enumerate(clips):
        tree_id, state_id = file_id("tree", asset, clip), file_id("state", asset, clip)
        children = ""
        for j, t in enumerate(x for x in lay.tracks if x.clip == clip):
            vx, vy = direction_vector(t.angle)
            children += f"""  - serializedVersion: 2
    m_Motion: {{fileID: {CLIP_FILE_ID}, guid: {clip_guids[t.name]}, type: 2}}
    m_Threshold: {j}
    m_Position: {{x: {_f(vx)}, y: {_f(vy)}}}
    m_TimeScale: 1
    m_CycleOffset: 0
    m_DirectBlendParameter: Blend
    m_Mirror: 0
"""
        docs.append(f"""--- !u!206 &{tree_id}
BlendTree:
  m_ObjectHideFlags: 1
  m_CorrespondingSourceObject: {{fileID: 0}}
  m_PrefabInstance: {{fileID: 0}}
  m_PrefabAsset: {{fileID: 0}}
  m_Name: {clip}
  m_Childs:
{children}  m_BlendParameter: MoveX
  m_BlendParameterY: MoveY
  m_MinThreshold: 0
  m_MaxThreshold: 1
  m_UseAutomaticThresholds: 1
  m_NormalizedBlendValues: 0
  m_BlendType: 1
""")
        docs.append(f"""--- !u!1102 &{state_id}
AnimatorState:
  serializedVersion: 6
  m_ObjectHideFlags: 1
  m_CorrespondingSourceObject: {{fileID: 0}}
  m_PrefabInstance: {{fileID: 0}}
  m_PrefabAsset: {{fileID: 0}}
  m_Name: {clip}
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
  m_Motion: {{fileID: {tree_id}}}
  m_Tag:
  m_SpeedParameter:
  m_MirrorParameter:
  m_CycleOffsetParameter:
  m_TimeParameter:
""")
        states.append(f"""  - serializedVersion: 1
    m_State: {{fileID: {state_id}}}
    m_Position: {{x: {300 + 260 * (i % 3)}, y: {60 + 70 * (i // 3)}, z: 0}}
""")
    params = "".join(f"""  - m_Name: {p}
    m_Type: 1
    m_DefaultFloat: {-1 if p == "MoveY" else 0}
    m_DefaultInt: 0
    m_DefaultBool: 0
    m_Controller: {{fileID: {CONTROLLER_FILE_ID}}}
""" for p in ("MoveX", "MoveY"))
    docs.append(f"""--- !u!91 &{CONTROLLER_FILE_ID}
AnimatorController:
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {{fileID: 0}}
  m_PrefabInstance: {{fileID: 0}}
  m_PrefabAsset: {{fileID: 0}}
  m_Name: {name}
  serializedVersion: 5
  m_AnimatorParameters:
{params}  m_AnimatorLayers:
  - serializedVersion: 5
    m_Name: Base Layer
    m_StateMachine: {{fileID: {sm_id}}}
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
    docs.append(f"""--- !u!1107 &{sm_id}
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
  m_DefaultState: {{fileID: {file_id("state", asset, default)}}}
""")
    return HEADER + "".join(docs)


def prefab(asset: str, name: str, lay: Layout, tex_guid: str, ppu: float, ctrl_guid: str | None) -> str:
    go, tr, sr, an = (file_id(k, asset) for k in ("go", "transform", "renderer", "animator"))
    first = lay.tracks[0].cells[0] if lay.tracks else lay.cells[0]
    comps = f"  - component: {{fileID: {tr}}}\n  - component: {{fileID: {sr}}}\n"
    animator = ""
    if ctrl_guid:
        comps += f"  - component: {{fileID: {an}}}\n"
        animator = f"""--- !u!95 &{an}
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
    return f"""{HEADER}--- !u!1 &{go}
GameObject:
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {{fileID: 0}}
  m_PrefabInstance: {{fileID: 0}}
  m_PrefabAsset: {{fileID: 0}}
  serializedVersion: 6
  m_Component:
{comps}  m_Layer: 0
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
  m_LocalRotation: {{x: 0, y: 0, z: 0, w: 1}}
  m_LocalPosition: {{x: 0, y: 0, z: 0}}
  m_LocalScale: {{x: 1, y: 1, z: 1}}
  m_ConstrainProportionsScale: 0
  m_Children: []
  m_Father: {{fileID: 0}}
  m_LocalEulerAnglesHint: {{x: 0, y: 0, z: 0}}
--- !u!212 &{sr}
SpriteRenderer:
  serializedVersion: 2
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {{fileID: 0}}
  m_PrefabInstance: {{fileID: 0}}
  m_PrefabAsset: {{fileID: 0}}
  m_GameObject: {{fileID: {go}}}
  m_Enabled: 1
  m_CastShadows: 0
  m_ReceiveShadows: 0
  m_DynamicOccludee: 1
  m_StaticShadowCaster: 0
  m_MotionVectors: 1
  m_LightProbeUsage: 1
  m_ReflectionProbeUsage: 1
  m_RayTracingMode: 0
  m_RayTraceProcedural: 0
  m_RenderingLayerMask: 1
  m_RendererPriority: 0
  m_Materials:
  - {{fileID: 10754, guid: 0000000000000000f000000000000000, type: 0}}
  m_StaticBatchInfo:
    firstSubMesh: 0
    subMeshCount: 0
  m_StaticBatchRoot: {{fileID: 0}}
  m_ProbeAnchor: {{fileID: 0}}
  m_LightProbeVolumeOverride: {{fileID: 0}}
  m_SortingLayerID: 0
  m_SortingLayer: 0
  m_SortingOrder: 0
  m_MaskInteraction: 0
  m_Sprite: {{fileID: {file_id("sprite", asset, first.name)}, guid: {tex_guid}, type: 3}}
  m_Color: {{r: 1, g: 1, b: 1, a: 1}}
  m_FlipX: 0
  m_FlipY: 0
  m_DrawMode: 0
  m_Size: {{x: {_f(first.w / ppu)}, y: {_f(first.h / ppu)}}}
  m_AdaptiveModeThreshold: 0.5
  m_SpriteTileMode: 0
  m_WasSpriteAssigned: 1
  m_SpriteSortPoint: 0
{animator}"""


README_HEAD = ("{asset} — xuất từ AI Asset Studio cho Unity 6. Kéo cả thư mục này vào Assets/.\n"
               "GUID trong các file .meta cố định theo bước (step) đã tạo ra asset: xuất lại cùng một run giữ "
               "nguyên GUID nên tham chiếu trong scene/prefab vẫn đúng. Run mới sinh GUID mới, nên import lại sẽ "
               "là asset thay thế (gán lại tham chiếu) chứ không cập nhật asset cũ.\n\n")
README_SHEET = """- {asset}.png: sprite sheet đã cắt sẵn (Sprite Mode Multiple), tên khung <clip>_<hướng>_<số>,
  hướng S, SW, W, NW, N, NE, E, SE (S = quay mặt xuống màn hình). PPU {ppu}, pivot ở chân.
- Animations/<clip>_<hướng>.anim + {asset}.controller: mỗi clip là một state blend tree 2D
  (Simple Directional), tham số MoveX/MoveY = hướng nhìn. Animator.Play("<clip>") để đổi clip.
- {asset}.prefab: SpriteRenderer + Animator gắn sẵn. Sprite không cần script hay package thêm."""
README_FLIPBOOK = """- {asset}.png: flipbook đã cắt sẵn (Sprite Mode Multiple), tên khung <clip>_<số>. PPU {ppu}, pivot giữa ô.
- Animations/<clip>.anim + {asset}.controller: một clip đọc các ô theo hàng, fps lúc tạo, loop theo chế độ
  (oneshot không lặp).
- {asset}.prefab: SpriteRenderer + Animator gắn sẵn, tự phát khi vào scene. Không cần script hay package thêm."""


README_TEXTURES = """- Textures/: texture liền mạch, import sẵn Wrap Mode = Repeat, có mipmap (bản pixel art: Point, không mipmap);
  *_normal.png đã đặt Texture Type = Normal map. Kéo vào ô Base Map / Normal Map của material, chỉnh Tiling để lặp.
  tileset.png (nếu có): các biến thể xếp một hàng, mỗi ô vuông cạnh = chiều cao ảnh, dùng làm Tile Palette."""


README_UI = """- UI/: mỗi phần tử một sprite (Sprite Mode Single, Mesh Type Full Rect){sliced}.{states}"""
README_UI_SLICED = """ — viền 9-slice đã đặt sẵn
  (Sprite Editor > Border). Image component chọn Image Type = Sliced là kéo giãn giữ nguyên góc và khung"""
README_UI_STATES = """
  Nút có thêm <tên>_hover/_pressed/_disabled cùng viền: Button > Transition = Sprite Swap, gán Highlighted/Pressed/
  Disabled Sprite tương ứng."""


README_WANG = """- Textures/{asset}_wang.png: bộ Wang góc 16 ô (4×4), đã cắt sẵn 16 sprite tên wang_<số>_<góc NW NE SW SE>
  (a = vật liệu 1, b = vật liệu 2, vd wang_06_abba). Đặt ô sao cho hai ô cạnh nhau cùng nhãn ở góc chung là liền
  mạch; Textures/{asset}_wang.json ghi nhãn góc/cạnh từng ô (index = nw*8 + ne*4 + sw*2 + se).
  {asset}_wang_normal.png (nếu có) gắn sẵn làm _NormalMap."""


def wang_sprites(size: tuple[int, int]) -> Layout:
    from studio_core.export.sheet import Cell
    from studio_core.ops.tile import wang_layout

    c = size[0] // 4
    return Layout(width=size[0], height=size[1], frame_w=c, frame_h=c, pivot=(c / 2, c / 2),
                  cells=[Cell(t["name"], t["col"] * c, t["row"] * c, c, c) for t in wang_layout()])


def _wang_json() -> bytes:
    from studio_core.export.godot import _wang_doc

    return _wang_doc()


def _image_size(p: Path) -> tuple[int, int]:
    from PIL import Image

    with Image.open(p) as im:
        return im.size


def export(src: Source) -> Files:
    a, u = src.asset, src.uid
    files: Files = []
    notes: list[str] = []
    if src.sheet is not None and src.meta is not None:
        lay = layout(src.meta, src.clip_hint, directional_rows=src.directional_rows)
        col = for_sheet(src, lay)
        pixel = src.is_pixel()
        notes.append((README_SHEET if lay.facing else README_FLIPBOOK).format(asset=a, ppu=_f(src.ppu)))
        tex_rel, normal_rel = f"{a}.png", f"{a}_normal.png"
        tex_guid = guid(u, a, tex_rel)
        normal_guid = guid(u, a, normal_rel) if src.normal is not None else None
        size = (lay.width, lay.height)
        files += [(tex_rel, src.sheet),
                  (f"{tex_rel}.meta", texture_meta(a, tex_guid, lay, size, src.ppu, pixel, normal_guid,
                                                   shapes=col.shapes if col else None).encode())]
        if col is not None:
            foot = " Pivot đặt ở hàng pixel thấp nhất (chân)." if lay.facing else ""
            notes.append("- Collider: mỗi sprite có Custom Physics Shape vẽ theo alpha (Sprite Editor > Custom Physics "
                         f"Shape) — thêm PolygonCollider2D là tự lấy viền.{foot}")
        if src.normal is not None and normal_guid:
            files += [(normal_rel, src.normal),
                      (f"{normal_rel}.meta",
                       texture_meta(a, normal_guid, None, size, src.ppu, pixel, is_normal=True).encode())]
            notes.append(f"- {normal_rel}: normal map, đã gắn làm secondary texture _NormalMap (URP 2D Light).")
        ctrl_guid = None
        if lay.tracks:
            clip_guids = {t.name: guid(u, a, f"Animations/{t.name}.anim") for t in lay.tracks}
            for t in lay.tracks:
                rel = f"Animations/{t.name}.anim"
                files += [(rel, anim_clip(a, tex_guid, t).encode()),
                          (f"{rel}.meta", native_meta(clip_guids[t.name], CLIP_FILE_ID).encode())]
            ctrl_rel = f"{a}.controller"
            ctrl_guid = guid(u, a, ctrl_rel)
            files += [(ctrl_rel, controller(a, a, lay, clip_guids).encode()),
                      (f"{ctrl_rel}.meta", native_meta(ctrl_guid, CONTROLLER_FILE_ID).encode())]
        if lay.cells:
            pf_rel = f"{a}.prefab"
            files += [(pf_rel, prefab(a, a, lay, tex_guid, src.ppu, ctrl_guid).encode()),
                      (f"{pf_rel}.meta", prefab_meta(guid(u, a, pf_rel)).encode())]
    if src.spine_json is not None and src.spine_atlas is not None:
        stem = Path(src.spine_json[0]).stem
        files += [(f"Spine/{stem}.json", src.spine_json[1]), (f"Spine/{stem}.atlas.txt", src.spine_atlas[1])]
        files += [(f"Spine/{n}", p) for n, p in src.spine_pages]
        notes.append(f"- Spine/: {stem}.json + {stem}.atlas.txt + ảnh trang — cần package spine-unity 4.2 "
                     "(tự tạo SkeletonData khi import).")
    from studio_core.export import rig3d

    rig_files, rig_notes = rig3d.unity_files(src, lambda rel: guid(u, a, rel))
    files += rig_files
    notes += rig_notes
    glbs = [(n, p) for n, p in src.glb if not rig3d.fbx_twin(src, n)]
    for n, p in glbs:
        files.append((f"Models/{n}", p))
    if glbs:
        notes.append("- Models/*.glb: cần package glTFast (com.unity.cloud.gltfast) để import.")
    if any(n.endswith("_lods.glb") for n, _ in src.glb):
        files.append(("Editor/AssetStudioLodGroup.cs", LOD_GROUP_SCRIPT.encode()))
        notes.append("- Models/*_lods.glb: mọi LOD chung 1 bộ texture; chọn file rồi menu Asset Studio > Make LOD "
                     "Group tạo prefab có LODGroup (node *_LOD0, *_LOD1…).")
    from studio_core.export.godot import wang_file

    wang_n = next((f"Textures/{wang_file(a, n)}" for n, _, role in src.textures
                   if role == "atlas" and Path(n).stem == "wang_normal"), None)
    for n, p, role in src.textures:
        rel = f"Textures/{wang_file(a, n)}"
        point = role == "pixel" or src.pixel is True
        if role == "atlas" and Path(n).stem == "wang":
            size = _image_size(p)
            normal_guid = guid(u, a, wang_n) if wang_n else None
            files += [(rel, p), (f"{rel}.meta", texture_meta(a, guid(u, a, rel), wang_sprites(size), size, src.ppu,
                                                             point, normal_guid).encode()),
                      (f"Textures/{a}_wang.json", _wang_json())]
            notes.append(README_WANG.format(asset=a))
            continue
        files += [(rel, p), (f"{rel}.meta", texture_meta(a, guid(u, a, rel), None, _image_size(p), src.ppu, point,
                                                         is_normal=role == "normal" or rel == wang_n,
                                                         repeat=role != "atlas").encode())]
    if src.textures:
        notes.append(README_TEXTURES)
    for n, p, b, _ in src.ui:
        rel = f"UI/{n}.png"
        files += [(rel, p), (f"{rel}.meta", texture_meta(a, guid(u, a, rel), None, _image_size(p), src.ppu,
                                                         src.pixel is True, border=b).encode())]
    if src.ui:
        notes.append(README_UI.format(sliced=README_UI_SLICED if any(any(b) for _, _, b, _ in src.ui) else "",
                                      states=README_UI_STATES if any(s for *_, s in src.ui) else ""))
    files += [(f"Audio/{n}", p) for n, p in src.audio]
    if src.audio:
        notes.append("- Audio/: Unity tự import thành AudioClip. File *_loop nối liền đầu-cuối: bật Loop ở AudioSource "
                     "(nhạc dài nên đặt Load Type = Streaming).")
    if src.pbr:
        from studio_core.export.pbr import unity_files

        pbr_files, pbr_notes = unity_files(src)
        files += pbr_files
        notes += pbr_notes
    if src.rarity:
        from studio_core.export.rarity import unity_files as rarity_files

        r_files, r_notes = rarity_files(src)
        files += r_files
        notes += r_notes
    if src.damage_states:
        from studio_core.export.damage import unity_files as damage_files

        d_files, d_notes = damage_files(src)
        files += d_files
        notes += d_notes
    if src.soundscape and src.audio:
        from studio_core.export.soundscape import unity_files as soundscape_files

        s_files, s_notes = soundscape_files(src)
        files += s_files
        notes += s_notes
    if src.luts:
        from studio_core.export.lut import unity_files as lut_files

        l_files, l_notes = lut_files(src)
        files += l_files
        notes += l_notes
    if not files:
        return files
    files.append(("README.txt", (README_HEAD.format(asset=a) + "\n".join(notes) + "\n").encode()))
    return files


LOD_GROUP_SCRIPT = """// Generated by AI Asset Studio: builds a LODGroup prefab from a GLB whose child meshes are named *_LOD0, *_LOD1...
using System.Linq;
using UnityEditor;
using UnityEngine;

public static class AssetStudioLodGroup
{
    [MenuItem("Asset Studio/Make LOD Group")]
    static void Make()
    {
        var src = Selection.activeObject as GameObject;
        if (src == null) { Debug.LogWarning("Select an imported *_lods.glb first."); return; }
        var go = (GameObject)PrefabUtility.InstantiatePrefab(src);
        var renderers = go.GetComponentsInChildren<Renderer>()
            .Select(r => (r, i: LodIndex(r.name)))
            .Where(x => x.i >= 0).OrderBy(x => x.i).ToList();
        if (renderers.Count == 0) { Object.DestroyImmediate(go); Debug.LogWarning("No *_LODn meshes found."); return; }
        var lods = renderers.Select((x, k) => new LOD(Mathf.Pow(0.5f, k + 1) * 0.6f, new[] { x.r })).ToArray();
        go.AddComponent<LODGroup>().SetLODs(lods);
        var path = AssetDatabase.GetAssetPath(src).Replace(".glb", "_LODGroup.prefab");
        PrefabUtility.SaveAsPrefabAsset(go, path);
        Object.DestroyImmediate(go);
        Debug.Log("Saved " + path);
    }

    static int LodIndex(string name)
    {
        var i = name.LastIndexOf("_LOD");
        return i >= 0 && int.TryParse(name.Substring(i + 4), out var n) ? n : -1;
    }
}
"""
