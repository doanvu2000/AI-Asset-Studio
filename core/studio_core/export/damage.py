"""Damage states export (op.damage_debris): state sprites + hit-flash frames + debris pieces under Damage/, a JSON
index, a Unity prefab (intact sprite, inactive Pieces child with Rigidbody2D bodies) and a Godot scene whose script
swaps states, flashes on hit and breaks into RigidBody2D pieces."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from studio_core.contracts import BlobStore, StepRow
from studio_core.export.sheet import ident
from studio_core.export.source import Files, Source

SINGLE_SPRITE_ID = 21300000


def damage_set(step: StepRow, blobs: BlobStore
               ) -> tuple[list[tuple[str, Path, Path | None]], list[tuple[str, Path, dict[str, Any]]], tuple[int, int]]:
    """op.damage_debris's damage.json, also when a gate passed it through under another port name."""
    from studio_core.ops.damage import DAMAGE_FILE, DAMAGE_TYPE

    for refs in step.outputs.values():
        for ref in refs:
            if (ref.name or "") != DAMAGE_FILE:
                continue
            try:
                doc = json.loads(blobs.path(ref.hash).read_bytes())
                if doc.get("type") != DAMAGE_TYPE:
                    return [], [], (0, 0)
                states, seen = [], set()
                for s in doc["states"]:
                    name, path = ident(str(s["state"]), "state"), blobs.path(str(s["hash"]))
                    if name in seen or not path.is_file():
                        continue
                    seen.add(name)
                    hit = blobs.path(str(s["hit"]["hash"])) if s.get("hit") else None
                    states.append((name, path, hit if hit is not None and hit.is_file() else None))
                pieces = []
                for p in (doc.get("debris") or {}).get("pieces") or []:
                    path = blobs.path(str(p["hash"]))
                    if path.is_file():
                        pieces.append((ident(str(p["name"]), "piece"), path, p))
                w, h = (int(v) for v in doc["canvas"])
                return states, pieces, (w, h)
            except (OSError, ValueError, KeyError, TypeError):
                return [], [], (0, 0)
    return [], [], (0, 0)


def _names(src: Source) -> tuple[list[tuple[str, str, Path, str | None, Path | None]], list[tuple[str, str, Path,
                                                                                               dict[str, Any]]]]:
    a = src.asset
    states = [(s, f"{a}_{s}.png", p, f"{a}_{s}_hit.png" if h else None, h) for s, p, h in src.damage_states]
    pieces = [(n, f"{a}_{n}.png", p, info) for n, p, info in src.damage_pieces]
    return states, pieces


def index_json(src: Source) -> bytes:
    """Positions in engine units with y up (Unity); `offset_px` keeps the source pixels (y down) for anything else."""
    states, pieces = _names(src)
    ppu = src.ppu or 100.0
    doc = {"asset": src.asset, "ppu": ppu, "canvas": list(src.damage_canvas),
           "states": [{"state": s, "rank": i, "sprite": n, "hit": hn} for i, (s, n, _, hn, _) in enumerate(states)],
           "pieces": [{"name": n, "sprite": f, "offset_px": info.get("offset"),
                       "position": [round(info["offset"][0] / ppu, 5), round(-info["offset"][1] / ppu, 5)],
                       "pivot": info.get("pivot"), "dir": [info["dir"][0], -info["dir"][1]],
                       "mass": info.get("mass")} for n, f, _, info in pieces]}
    return json.dumps(doc, indent=1).encode()


def _f(v: float) -> str:
    return f"{v:.6g}"


def _go(go: int, comps: list[int], name: str, active: bool = True) -> str:
    lines = "".join(f"  - component: {{fileID: {c}}}\n" for c in comps)
    return f"""--- !u!1 &{go}
GameObject:
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {{fileID: 0}}
  m_PrefabInstance: {{fileID: 0}}
  m_PrefabAsset: {{fileID: 0}}
  serializedVersion: 6
  m_Component:
{lines}  m_Layer: 0
  m_Name: {name}
  m_TagString: Untagged
  m_Icon: {{fileID: 0}}
  m_NavMeshLayer: 0
  m_StaticEditorFlags: 0
  m_IsActive: {1 if active else 0}
"""


def _transform(tr: int, go: int, pos: tuple[float, float], children: list[int], father: int) -> str:
    kids = "[]" if not children else "\n" + "".join(f"  - {{fileID: {c}}}\n" for c in children).rstrip("\n")
    return f"""--- !u!4 &{tr}
Transform:
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {{fileID: 0}}
  m_PrefabInstance: {{fileID: 0}}
  m_PrefabAsset: {{fileID: 0}}
  m_GameObject: {{fileID: {go}}}
  serializedVersion: 2
  m_LocalRotation: {{x: 0, y: 0, z: 0, w: 1}}
  m_LocalPosition: {{x: {_f(pos[0])}, y: {_f(pos[1])}, z: 0}}
  m_LocalScale: {{x: 1, y: 1, z: 1}}
  m_ConstrainProportionsScale: 0
  m_Children: {kids}
  m_Father: {{fileID: {father}}}
  m_LocalEulerAnglesHint: {{x: 0, y: 0, z: 0}}
"""


def _renderer(sr: int, go: int, tex_guid: str, size: tuple[float, float], order: int) -> str:
    return f"""--- !u!212 &{sr}
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
  m_SortingOrder: {order}
  m_MaskInteraction: 0
  m_Sprite: {{fileID: {SINGLE_SPRITE_ID}, guid: {tex_guid}, type: 3}}
  m_Color: {{r: 1, g: 1, b: 1, a: 1}}
  m_FlipX: 0
  m_FlipY: 0
  m_DrawMode: 0
  m_Size: {{x: {_f(size[0])}, y: {_f(size[1])}}}
  m_AdaptiveModeThreshold: 0.5
  m_SpriteTileMode: 0
  m_WasSpriteAssigned: 1
  m_SpriteSortPoint: 0
"""


def _body(rb: int, go: int, mass: float) -> str:
    return f"""--- !u!50 &{rb}
Rigidbody2D:
  serializedVersion: 5
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {{fileID: 0}}
  m_PrefabInstance: {{fileID: 0}}
  m_PrefabAsset: {{fileID: 0}}
  m_GameObject: {{fileID: {go}}}
  m_BodyType: 0
  m_Simulated: 1
  m_UseFullKinematicContacts: 0
  m_UseAutoMass: 0
  m_Mass: {_f(mass)}
  m_LinearDamping: 0
  m_AngularDamping: 0.05
  m_GravityScale: 1
  m_Material: {{fileID: 0}}
  m_Interpolate: 0
  m_SleepingMode: 1
  m_CollisionDetection: 0
  m_Constraints: 0
"""


def _box(bc: int, go: int, size: tuple[float, float]) -> str:
    return f"""--- !u!61 &{bc}
BoxCollider2D:
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {{fileID: 0}}
  m_PrefabInstance: {{fileID: 0}}
  m_PrefabAsset: {{fileID: 0}}
  m_GameObject: {{fileID: {go}}}
  m_Enabled: 1
  serializedVersion: 3
  m_Density: 1
  m_Material: {{fileID: 0}}
  m_IsTrigger: 0
  m_UsedByEffector: 0
  m_Offset: {{x: 0, y: 0}}
  m_AutoTiling: 0
  m_Size: {{x: {_f(size[0])}, y: {_f(size[1])}}}
  m_EdgeRadius: 0
"""


def unity_prefab(src: Source, guids: dict[str, str]) -> str:
    """Root = first state's sprite; child Pieces (inactive) holds one Rigidbody2D + BoxCollider2D body per piece at
    its place in the intact prop, so SetActive(true) on Pieces (and disabling the root renderer) is the break."""
    from studio_core.export.unity import HEADER, file_id

    a, ppu = src.asset, src.ppu or 100.0
    states, pieces = _names(src)
    w, h = src.damage_canvas
    root, rtr, rsr = (file_id("damage", a, k) for k in ("go", "transform", "renderer"))
    pgo, ptr = file_id("damage", a, "pieces_go"), file_id("damage", a, "pieces_transform")
    ids = [tuple(file_id("damage", a, n, k) for k in ("go", "tr", "sr", "rb", "bc")) for n, *_ in pieces]
    docs = [_go(root, [rtr, rsr], f"{a}_damage"), _transform(rtr, root, (0, 0), [ptr], 0),
            _renderer(rsr, root, guids[states[0][1]], (w / ppu, h / ppu), 0),
            _go(pgo, [ptr], "Pieces", active=False), _transform(ptr, pgo, (0, 0), [i[1] for i in ids], rtr)]
    for (name, f, _, info), (go, tr, sr, rb, bc) in zip(pieces, ids):
        pw, ph = info["rect"][2] / ppu, info["rect"][3] / ppu
        pos = (info["offset"][0] / ppu, -info["offset"][1] / ppu)
        docs += [_go(go, [tr, sr, rb, bc], name), _transform(tr, go, pos, [], ptr),
                 _renderer(sr, go, guids[f], (pw, ph), 1), _body(rb, go, max(0.05, float(info.get("mass") or 0))),
                 _box(bc, go, (pw * 0.8, ph * 0.8))]
    return HEADER + "".join(docs)


def unity_files(src: Source) -> tuple[Files, list[str]]:
    from studio_core.export.unity import guid, prefab_meta, texture_meta

    states, pieces = _names(src)
    files: Files = []
    guids: dict[str, str] = {}
    sprites = [(n, p) for _, n, p, _, _ in states] + [(hn, hp) for *_, hn, hp in states if hn and hp] \
        + [(f, p) for _, f, p, _ in pieces]
    for name, p in sprites:
        rel = f"Damage/{name}"
        guids[name] = guid(src.uid, src.asset, rel)
        files += [(rel, p), (f"{rel}.meta", texture_meta(src.asset, guids[name], None, _image_size(p), src.ppu,
                                                         src.pixel is True).encode())]
    files.append((f"Damage/{src.asset}_damage.json", index_json(src)))
    prefab_rel = f"Damage/{src.asset}_damage.prefab"
    files += [(prefab_rel, unity_prefab(src, guids).encode()),
              (f"{prefab_rel}.meta", prefab_meta(guid(src.uid, src.asset, prefab_rel)).encode())]
    note = (f"- Damage/: {len(states)} trạng thái {src.asset}_<trạng thái>.png (cùng khung, pivot giữa, đổi "
            "SpriteRenderer.sprite theo máu), mỗi trạng thái có _hit.png chớp trắng (đổi sprite 1–2 frame khi trúng "
            f"đòn), {len(pieces)} mảnh vỡ {src.asset}_piece_<n>.png. {src.asset}_damage.prefab: gốc hiện trạng thái "
            "đầu; con Pieces (đang tắt) có sẵn từng mảnh đúng chỗ + Rigidbody2D + BoxCollider2D — khi vỡ: tắt "
            "SpriteRenderer gốc, Pieces.SetActive(true), AddForce theo dir trong "
            f"{src.asset}_damage.json (y hướng lên, position tính theo PPU).")
    return files, [note]


GODOT_SCRIPT_FILE = "asset_studio_damage.gd"
GODOT_SCRIPT = """extends Node2D
## Damage states: set_state(i) / next_state() swap the visible States child, hit() flashes it white,
## break_apart() hides the states and throws the frozen Pieces bodies outward (`break` is a GDScript keyword).

signal broken

@export var impulse := 240.0
@export var spin := 6.0
@export var flash_time := 0.08

var state := 0


func set_state(index: int) -> void:
	var states := $States.get_children()
	state = clampi(index, 0, states.size() - 1)
	for i in states.size():
		states[i].visible = i == state


func next_state() -> void:
	if state >= $States.get_child_count() - 1:
		break_apart()
	else:
		set_state(state + 1)


func hit() -> void:
	var sprite := $States.get_child(state) as Sprite2D
	if sprite == null or not sprite.has_meta("hit"):
		return
	sprite.texture = sprite.get_meta("hit")
	await get_tree().create_timer(flash_time).timeout
	if is_instance_valid(sprite):
		sprite.texture = sprite.get_meta("base")


func break_apart() -> void:
	if $Pieces.visible:
		return
	$States.visible = false
	$Pieces.visible = true
	for body in $Pieces.get_children():
		if not body is RigidBody2D:
			continue
		for child in body.get_children():
			if child is CollisionShape2D:
				child.set_deferred("disabled", false)
		body.freeze = false
		var dir: Vector2 = body.get_meta("dir", Vector2.UP)
		body.linear_velocity = (dir + Vector2.UP * 0.6).normalized() * impulse * randf_range(0.7, 1.2)
		body.angular_velocity = randf_range(-spin, spin)
	broken.emit()
"""


def _num(v: float) -> str:
    s = f"{float(v):.6g}"
    return s if any(ch in s for ch in ".e") else f"{s}.0"


def godot_scene(src: Source) -> str:
    states, pieces = _names(src)
    ext = [f'[ext_resource type="Script" path="{GODOT_SCRIPT_FILE}" id="1_script"]']
    subs: list[str] = []
    nodes = [f'[node name="{src.asset}_damage" type="Node2D"]', 'script = ExtResource("1_script")', '',
             '[node name="States" type="Node2D" parent="."]']
    for i, (s, n, _, hn, _) in enumerate(states):
        ext.append(f'[ext_resource type="Texture2D" path="Damage/{n}" id="s{i}"]')
        nodes += ['', f'[node name="{s}" type="Sprite2D" parent="States"]']
        if i:
            nodes.append("visible = false")
        nodes += [f'texture = ExtResource("s{i}")', f'metadata/base = ExtResource("s{i}")']
        if hn:
            ext.append(f'[ext_resource type="Texture2D" path="Damage/{hn}" id="h{i}"]')
            nodes.append(f'metadata/hit = ExtResource("h{i}")')
    nodes += ['', '[node name="Pieces" type="Node2D" parent="."]', "visible = false"]
    for k, (name, f, _, info) in enumerate(pieces):
        ext.append(f'[ext_resource type="Texture2D" path="Damage/{f}" id="p{k}"]')
        pw, ph = info["rect"][2], info["rect"][3]
        subs += [f'[sub_resource type="RectangleShape2D" id="RectangleShape2D_{k}"]',
                 f"size = Vector2({_num(pw * 0.8)}, {_num(ph * 0.8)})", '']
        ox, oy = info["offset"]
        dx, dy = info["dir"]
        nodes += ['', f'[node name="{name}" type="RigidBody2D" parent="Pieces"]',
                  f"position = Vector2({_num(ox)}, {_num(oy)})", f"mass = {_num(max(0.05, float(info.get('mass') or 0)))}",
                  "freeze = true", f"metadata/dir = Vector2({_num(dx)}, {_num(dy)})",
                  '', f'[node name="Sprite" type="Sprite2D" parent="Pieces/{name}"]', f'texture = ExtResource("p{k}")',
                  '', f'[node name="Shape" type="CollisionShape2D" parent="Pieces/{name}"]',
                  f'shape = SubResource("RectangleShape2D_{k}")', "disabled = true"]
    return "\n".join(['[gd_scene format=3]', '', *ext, '', *subs, *nodes]) + "\n"


def godot_files(src: Source) -> tuple[Files, list[str]]:
    states, pieces = _names(src)
    files: Files = [(f"Damage/{n}", p) for _, n, p, _, _ in states]
    files += [(f"Damage/{hn}", hp) for *_, hn, hp in states if hn and hp]
    files += [(f"Damage/{f}", p) for _, f, p, _ in pieces]
    files += [(f"Damage/{src.asset}_damage.json", index_json(src)),
              (f"{src.asset}_damage.tscn", godot_scene(src).encode()), (GODOT_SCRIPT_FILE, GODOT_SCRIPT.encode())]
    note = (f"- {src.asset}_damage.tscn: Node2D + {GODOT_SCRIPT_FILE}; States/ có {len(states)} Sprite2D (chỉ trạng "
            "thái đầu hiện), Pieces/ có từng mảnh RigidBody2D đúng chỗ, đang freeze + ẩn. Gọi set_state(i) hoặc "
            "next_state() khi mất máu, hit() chớp trắng, break_apart() để vỡ (tín hiệu broken). Ảnh trong Damage/, "
            f"{src.asset}_damage.json ghi offset/pivot/hướng văng từng mảnh.")
    return files, [note]


def _image_size(p: Path) -> tuple[int, int]:
    from PIL import Image

    with Image.open(p) as im:
        return im.size
