"""Destructible prop export (blender.fracture): Unity gets both FBX models (via rig3d's FBX path), a JSON of piece
masses, an Editor script that builds <asset>_Intact / <asset>_Fractured prefabs on import and the runtime
AssetStudioSwapOnHit; Godot gets RigidBody3D scenes with one convex CollisionShape3D per piece."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from studio_core.contracts import BlobStore, StepRow
from studio_core.export.sheet import cs_namespace
from studio_core.export.source import Files, Source

REPORT_FILE = "destructible.json"
REPORT_TYPE = "asset_studio.destructible"
# Fixed like the other shared scripts: a second destructible prop in one project shares the script identity, so
# deleting the duplicate .cs keeps every prefab's m_Script valid.
SWAP_GUID = "8c1d4e7f2a9b4c35b6e0d3f1a2c4e6b8"
EDITOR_GUID = "3e9a7c51d24f4b8e9a06c1f7b2d5e340"
SWAP_FILE = "AssetStudioSwapOnHit.cs"
PIECES_GUID = "b7f2c9d4e1a84f6c8d3e5a0b9c7f1e24"
PIECES_FILE = "AssetStudioFracturedPieces.cs"
EDITOR_FILE = "AssetStudioDestructible.cs"
GODOT_FRACTURED = "asset_studio_fractured.gd"
GODOT_INTACT = "asset_studio_destructible.gd"
BREAK_SPEED = 6.0
# Above this many triangles for the whole set the README flags the prop as heavy for mobile (#31 budget).
MOBILE_TRIS = 10000


def destructible_doc(step: StepRow, blobs: BlobStore) -> dict[str, Any] | None:
    """blender.fracture's destructible.json, also when a gate passed it through under another port name."""
    for refs in step.outputs.values():
        for ref in refs:
            if (ref.name or "") != REPORT_FILE:
                continue
            try:
                doc = json.loads(blobs.path(ref.hash).read_bytes())
            except (OSError, ValueError):
                return None
            ok = isinstance(doc, dict) and doc.get("type") == REPORT_TYPE and isinstance(doc.get("chunks"), list)
            return doc if ok else None
    return None


def _model(names: list[str], want: str, ext: str) -> str | None:
    """Exported file name of the report's model: the exporter may have renamed a clash (name_2.fbx)."""
    stem = Path(want).stem
    return next((n for n in names if n == want), None) or next(
        (n for n in names if Path(n).stem.startswith(stem) and n.endswith(ext)), None)


def _gd(v: Any) -> list[float]:
    """Blender Z-up -> glTF/Godot Y-up."""
    x, y, z = (float(c) for c in v)
    return [x, z, -y]


def _f(x: float) -> str:
    return f"{x:.5g}"


def _points(pts: list[Any]) -> str:
    return ", ".join(_f(c) for p in pts for c in _gd(p))


def unity_json(doc: dict[str, Any], intact: str, fractured: str) -> bytes:
    out = {"asset": doc["asset"], "intact_model": f"Models/{intact}", "fractured_model": f"Models/{fractured}",
           "total_mass": doc.get("total_mass", 0), "break_speed": BREAK_SPEED,
           "material": (doc.get("params") or {}).get("material", ""), "tris_total": doc.get("tris_total", 0),
           "chunks": [{"name": c["name"], "mass": c["mass"], "tris": c["tris"]} for c in doc["chunks"]]}
    return json.dumps(out, indent=1).encode()


def _script_meta(g: str) -> bytes:
    return (f"fileFormatVersion: 2\nguid: {g}\nMonoImporter:\n  externalObjects: {{}}\n  serializedVersion: 2\n"
            "  defaultReferences: []\n  executionOrder: 0\n  icon: {instanceID: 0}\n  userData: \n"
            "  assetBundleName: \n  assetBundleVariant: \n").encode()


def _budget_note(doc: dict[str, Any]) -> str:
    tris = int(doc.get("tris_total") or 0)
    heavy = tris > MOBILE_TRIS
    per = [int(c["tris"]) for c in doc["chunks"]]
    return (f"{len(per)} mảnh, tổng {tris} tam giác (mảnh nặng nhất {max(per)}; bản nguyên {doc.get('intact_tris')})"
            + (f" — nặng cho mobile (> {MOBILE_TRIS}), chạy lại với ngân sách nhỏ hơn hoặc ít mảnh hơn." if heavy
               else "."))


def unity_files(src: Source) -> tuple[Files, list[str]]:
    doc = src.destructible or {}
    names = [n for n, _ in src.fbx]
    intact = _model(names, str(doc.get("intact_fbx") or ""), ".fbx")
    fractured = _model(names, str(doc.get("fractured_fbx") or ""), ".fbx")
    if not doc or not intact or not fractured:
        return [], []
    a = str(doc["asset"])
    files: Files = [(f"{a}_destructible.json", unity_json(doc, intact, fractured)),
                    (f"Destructible/{SWAP_FILE}", SWAP_SCRIPT.encode()),
                    (f"Destructible/{SWAP_FILE}.meta", _script_meta(SWAP_GUID)),
                    (f"Destructible/{PIECES_FILE}", PIECES_SCRIPT.encode()),
                    (f"Destructible/{PIECES_FILE}.meta", _script_meta(PIECES_GUID)),
                    (f"Editor/{EDITOR_FILE}", EDITOR_SCRIPT.encode()),
                    (f"Editor/{EDITOR_FILE}.meta", _script_meta(EDITOR_GUID))]
    note = (f"- Phá huỷ: Models/{intact} (bản nguyên) + Models/{fractured} (mảnh {a}_chunkNN). Khi import xong, "
            f"Editor/{EDITOR_FILE} tự tạo {a}_Intact.prefab (Rigidbody {doc.get('total_mass')} kg, MeshCollider convex, "
            f"AssetStudioSwapOnHit: va chạm ≥ {BREAK_SPEED:g} m/s hoặc gọi Break() thì thay bằng bản vỡ) và "
            f"{a}_Fractured.prefab (mỗi mảnh Rigidbody theo {a}_destructible.json + MeshCollider convex; "
            "AssetStudioFracturedPieces cho mảnh bỏ qua va chạm với nhau vì hull convex của mảnh lõm chồng lên mảnh bên "
            "cạnh — bật piecesCollide nếu prop toàn mảnh lồi); chạy lại: menu "
            f"Asset Studio > Build Destructible Prefabs. {_budget_note(doc)} Nhập nhiều prop vào một project thì giữ "
            f"một bản Destructible/{SWAP_FILE} và Editor/{EDITOR_FILE} (cùng GUID).")
    return files, [note]


def fractured_scene(doc: dict[str, Any], glb: str) -> str:
    a = str(doc["asset"])
    subs, nodes = [], []
    for i, c in enumerate(doc["chunks"]):
        subs.append(f'[sub_resource type="ConvexPolygonShape3D" id="Shape_{i:02d}"]\npoints = '
                    f'PackedVector3Array({_points(c["hull"])})\n')
        x, y, z = _gd(c["center"])
        nodes.append(f'[node name="{c["name"]}" type="RigidBody3D" parent="."]\n'
                     f"transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, {_f(x)}, {_f(y)}, {_f(z)})\n"
                     f"mass = {_f(max(float(c['mass']), 0.001))}\n\n"
                     f'[node name="Collision" type="CollisionShape3D" parent="{c["name"]}"]\n'
                     f'shape = SubResource("Shape_{i:02d}")\n')
    return ("[gd_scene format=3]\n\n"
            f'[ext_resource type="PackedScene" path="{glb}" id="1_model"]\n'
            f'[ext_resource type="Script" path="{GODOT_FRACTURED}" id="2_script"]\n\n' + "\n".join(subs) + "\n"
            f'[node name="{a}_fractured" type="Node3D"]\nscript = ExtResource("2_script")\n\n'
            '[node name="Model" parent="." instance=ExtResource("1_model")]\n\n' + "\n".join(nodes))


def intact_scene(doc: dict[str, Any], glb: str, fractured_tscn: str) -> str:
    a = str(doc["asset"])
    return ("[gd_scene format=3]\n\n"
            f'[ext_resource type="PackedScene" path="{glb}" id="1_model"]\n'
            f'[ext_resource type="Script" path="{GODOT_INTACT}" id="2_script"]\n'
            f'[ext_resource type="PackedScene" path="{fractured_tscn}" id="3_fractured"]\n\n'
            '[sub_resource type="ConvexPolygonShape3D" id="Shape_intact"]\n'
            f'points = PackedVector3Array({_points(doc.get("intact_hull") or [])})\n\n'
            f'[node name="{a}_intact" type="RigidBody3D"]\n'
            f"mass = {_f(max(float(doc.get('total_mass') or 1), 0.001))}\n"
            'script = ExtResource("2_script")\nfractured = ExtResource("3_fractured")\n'
            f"break_speed = {BREAK_SPEED:g}\n\n"
            '[node name="Collision" type="CollisionShape3D" parent="."]\nshape = SubResource("Shape_intact")\n\n'
            '[node name="Model" parent="." instance=ExtResource("1_model")]\n')


def godot_files(src: Source) -> tuple[Files, list[str]]:
    doc = src.destructible or {}
    names = [n for n, _ in src.glb]
    intact = _model(names, str(doc.get("intact_glb") or ""), ".glb")
    fractured = _model(names, str(doc.get("fractured_glb") or ""), ".glb")
    if not doc or not intact or not fractured or not doc.get("intact_hull"):
        return [], []
    a = str(doc["asset"])
    frac_tscn = f"{a}_fractured.tscn"
    files: Files = [(frac_tscn, fractured_scene(doc, f"Models/{fractured}").encode()),
                    (f"{a}_intact.tscn", intact_scene(doc, f"Models/{intact}", frac_tscn).encode()),
                    (GODOT_FRACTURED, GODOT_FRACTURED_SCRIPT.encode()), (GODOT_INTACT, GODOT_INTACT_SCRIPT.encode())]
    note = (f"- Phá huỷ: {a}_intact.tscn = RigidBody3D ({doc.get('total_mass')} kg, CollisionShape3D convex) + "
            f"{GODOT_INTACT}: va chạm ≥ {BREAK_SPEED:g} m/s hoặc gọi break_apart() thì thay bằng {frac_tscn} — mỗi mảnh "
            f"{a}_chunkNN là RigidBody3D (mass theo thể tích) + CollisionShape3D convex, {GODOT_FRACTURED} gắn mesh từ "
            f"Models/{fractured} vào đúng body khi _ready (mảnh không va nhau, pieces_collide để bật). {_budget_note(doc)}")
    return files, [note]


SWAP_SCRIPT = cs_namespace("""// Generated by AI Asset Studio: swaps an intact prop for its pre-fractured prefab on a hard hit or Break().
using UnityEngine;

[RequireComponent(typeof(Rigidbody))]
public class AssetStudioSwapOnHit : MonoBehaviour
{
    [Tooltip("Pre-fractured prefab: every piece has its own Rigidbody + convex MeshCollider")]
    public GameObject fractured;
    [Tooltip("Relative impact speed (m/s) that breaks the prop; 0 = only Break() from code")]
    public float breakSpeed = 6f;
    [Tooltip("Outward speed (m/s) added to every piece, away from the impact point")]
    public float burst = 2.5f;
    [Tooltip("Seconds before the pieces are destroyed; 0 = keep them")]
    public float despawnAfter = 10f;
    bool broken;

    void OnCollisionEnter(Collision c)
    {
        if (breakSpeed > 0f && c.relativeVelocity.magnitude >= breakSpeed)
            BreakAt(c.contactCount > 0 ? c.GetContact(0).point : transform.position);
    }

    public void Break()
    {
        BreakAt(transform.position);
    }

    public void BreakAt(Vector3 point)
    {
        if (broken || fractured == null) return;
        broken = true;
        var body = GetComponent<Rigidbody>();
        var v = GetVelocity(body);
        var go = Instantiate(fractured, transform.position, transform.rotation);
        go.transform.localScale = transform.lossyScale;
        foreach (var rb in go.GetComponentsInChildren<Rigidbody>())
        {
            var r = rb.GetComponent<Renderer>();
            var dir = (r != null ? r.bounds.center : rb.position) - point;
            SetVelocity(rb, v + (dir.sqrMagnitude > 1e-6f ? dir.normalized * burst : Vector3.zero));
            rb.angularVelocity = body.angularVelocity;
        }
        if (despawnAfter > 0f) Destroy(go, despawnAfter);
        Destroy(gameObject);
    }

    static Vector3 GetVelocity(Rigidbody rb)
    {
#if UNITY_6000_0_OR_NEWER
        return rb.linearVelocity;
#else
        return rb.velocity;
#endif
    }

    static void SetVelocity(Rigidbody rb, Vector3 v)
    {
#if UNITY_6000_0_OR_NEWER
        rb.linearVelocity = v;
#else
        rb.velocity = v;
#endif
    }
}
""")

PIECES_SCRIPT = cs_namespace("""// Generated by AI Asset Studio: keeps the pieces of a fractured prop from colliding with each other.
using UnityEngine;

public class AssetStudioFracturedPieces : MonoBehaviour
{
    [Tooltip("Convex hulls of concave pieces overlap their neighbours at spawn; colliding they blast each other apart")]
    public bool piecesCollide = false;

    void Awake()
    {
        if (piecesCollide) return;
        var cols = GetComponentsInChildren<Collider>();
        for (int i = 0; i < cols.Length; i++)
            for (int j = i + 1; j < cols.Length; j++)
                Physics.IgnoreCollision(cols[i], cols[j]);
    }
}
""")

EDITOR_SCRIPT = cs_namespace("""// Generated by AI Asset Studio: builds <asset>_Intact / <asset>_Fractured prefabs next to <asset>_destructible.json
// once its FBX models are imported; menu Asset Studio > Build Destructible Prefabs rebuilds them.
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEngine;

public class AssetStudioDestructible : AssetPostprocessor
{
    [System.Serializable] class Chunk { public string name; public float mass; }
    [System.Serializable] class Doc
    {
        public string asset;
        public string intact_model;
        public string fractured_model;
        public float total_mass;
        public float break_speed;
        public Chunk[] chunks;
    }

    static void OnPostprocessAllAssets(string[] imported, string[] deleted, string[] moved, string[] movedFrom)
    {
        foreach (var p in imported)
        {
            if (!p.EndsWith("_destructible.json")) continue;
            var path = p;
            EditorApplication.delayCall += () => Build(path);
        }
    }

    [MenuItem("Asset Studio/Build Destructible Prefabs")]
    public static void BuildAll()
    {
        foreach (var g in AssetDatabase.FindAssets("_destructible t:TextAsset"))
        {
            var p = AssetDatabase.GUIDToAssetPath(g);
            if (p.EndsWith("_destructible.json")) Build(p);
        }
    }

    static T Ensure<T>(GameObject go) where T : Component
    {
        var c = go.GetComponent<T>();
        return c != null ? c : go.AddComponent<T>();
    }

    static void AddConvex(GameObject root)
    {
        foreach (var mf in root.GetComponentsInChildren<MeshFilter>())
        {
            var col = Ensure<MeshCollider>(mf.gameObject);
            col.sharedMesh = mf.sharedMesh;
            col.convex = true;
        }
    }

    public static bool Build(string jsonPath)
    {
        var text = AssetDatabase.LoadAssetAtPath<TextAsset>(jsonPath);
        if (text == null) return false;
        var doc = JsonUtility.FromJson<Doc>(text.text);
        var dir = Path.GetDirectoryName(jsonPath).Replace('\\\\', '/');
        var intactModel = AssetDatabase.LoadAssetAtPath<GameObject>(dir + "/" + doc.intact_model);
        var fracturedModel = AssetDatabase.LoadAssetAtPath<GameObject>(dir + "/" + doc.fractured_model);
        if (intactModel == null || fracturedModel == null)
        {
            Debug.LogWarning("Asset Studio: models of " + jsonPath + " are not imported yet");
            return false;
        }
        var mass = new Dictionary<string, float>();
        foreach (var c in doc.chunks) mass[c.name] = c.mass;

        var frac = (GameObject)PrefabUtility.InstantiatePrefab(fracturedModel);
        frac.name = doc.asset + "_Fractured";
        AddConvex(frac);
        foreach (var mf in frac.GetComponentsInChildren<MeshFilter>())
        {
            var rb = Ensure<Rigidbody>(mf.gameObject);
            rb.mass = mass.TryGetValue(mf.gameObject.name, out var m) && m > 0f ? m : 1f;
            rb.interpolation = RigidbodyInterpolation.Interpolate;
        }
        Ensure<AssetStudioFracturedPieces>(frac);
        var fracPrefab = PrefabUtility.SaveAsPrefabAsset(frac, dir + "/" + doc.asset + "_Fractured.prefab");
        Object.DestroyImmediate(frac);

        var intact = (GameObject)PrefabUtility.InstantiatePrefab(intactModel);
        intact.name = doc.asset + "_Intact";
        AddConvex(intact);
        var body = Ensure<Rigidbody>(intact);
        body.mass = doc.total_mass > 0f ? doc.total_mass : 1f;
        var swap = Ensure<AssetStudioSwapOnHit>(intact);
        swap.fractured = fracPrefab;
        swap.breakSpeed = doc.break_speed;
        PrefabUtility.SaveAsPrefabAsset(intact, dir + "/" + doc.asset + "_Intact.prefab");
        Object.DestroyImmediate(intact);
        AssetDatabase.SaveAssets();
        Debug.Log("Asset Studio: built " + doc.asset + "_Intact / _Fractured (" + doc.chunks.Length + " pieces)");
        return true;
    }
}
""")

GODOT_FRACTURED_SCRIPT = """# Generated by AI Asset Studio: moves each *_chunkNN mesh of the imported GLB under the RigidBody3D of the same name.
extends Node3D

@export var despawn_after := 10.0
## Convex hulls of concave pieces overlap their neighbours at spawn; colliding they would blast each other apart.
@export var pieces_collide := false


func _ready() -> void:
	var bodies := get_children().filter(func(n): return n is RigidBody3D)
	if not pieces_collide:
		for i in bodies.size():
			for j in range(i + 1, bodies.size()):
				bodies[i].add_collision_exception_with(bodies[j])
	var model := get_node_or_null("Model")
	if model != null:
		for body in bodies:
			var mesh := model.find_child(String(body.name), true, false)
			if mesh is Node3D:
				mesh.reparent(body, true)
		model.queue_free()
	if despawn_after > 0.0:
		get_tree().create_timer(despawn_after).timeout.connect(queue_free)
"""

GODOT_INTACT_SCRIPT = """# Generated by AI Asset Studio: an intact prop that swaps to its pre-fractured scene on a hard hit or break_apart().
extends RigidBody3D

@export var fractured: PackedScene
@export var break_speed := 6.0
@export var burst := 2.5

var _broken := false
var _last_velocity := Vector3.ZERO


func _ready() -> void:
	contact_monitor = true
	max_contacts_reported = 4
	body_entered.connect(_on_body_entered)


func _physics_process(_delta: float) -> void:
	_last_velocity = linear_velocity


func _on_body_entered(other: Node) -> void:
	var other_velocity := Vector3.ZERO
	if other is RigidBody3D:
		other_velocity = other.linear_velocity
	if break_speed > 0.0 and (_last_velocity - other_velocity).length() >= break_speed:
		break_apart()


func break_apart(point: Vector3 = Vector3.INF) -> void:
	if _broken or fractured == null:
		return
	_broken = true
	# Bodies cannot be added while the physics server is flushing the contact that triggered this.
	_spawn.call_deferred(global_position if point == Vector3.INF else point)


func _spawn(point: Vector3) -> void:
	var pieces := fractured.instantiate() as Node3D
	get_parent().add_child(pieces)
	pieces.global_transform = global_transform
	for body in pieces.get_children():
		if body is RigidBody3D:
			var dir: Vector3 = body.global_position - point
			var push := dir.normalized() * burst if dir.length() > 0.001 else Vector3.ZERO
			body.linear_velocity = _last_velocity + push
			body.angular_velocity = angular_velocity
	queue_free()
"""
