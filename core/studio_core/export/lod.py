"""LOD chain export (blender.lod). Unity: the per-LOD FBX models, <asset>_lod.json and an Editor script that builds
<asset>_LOD.prefab (LODGroup + BoxCollider or convex MeshColliders) on import. Godot: a StaticBody3D scene with one
instanced GLB per LOD whose visibility ranges a small script applies, CollisionShape3D nodes and the optional impostor
Sprite3D. FBX meshes have no fileIDs the core can know before Unity imports them, hence the Editor script instead of a
hand-written prefab YAML (same route as export/destructible.py and export/modular.py)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from studio_core.contracts import BlobStore, StepRow
from studio_core.export.destructible import _f, _gd, _model, _points, _script_meta
from studio_core.export.sheet import SHARED_SCRIPT_NOTE, cs_namespace
from studio_core.export.source import Files, Source

REPORT_FILE = "lod.json"
REPORT_TYPE = "asset_studio.lod_chain"
EDITOR_GUID = "5d2f8a1c7b3e4096a8c1e4f7d0b29a63"
IMPOSTOR_GUID = "c4a91e6b2d7f4538b0e2a9d6f1c83e57"
EDITOR_FILE = "AssetStudioLod.cs"
IMPOSTOR_FILE = "AssetStudioImpostor.cs"
GODOT_SCRIPT = "asset_studio_lod.gd"
# Triangles of LOD0 above which the README flags the prop as heavy for mobile.
MOBILE_TRIS = 5000


def lod_set(step: StepRow, blobs: BlobStore) -> tuple[dict[str, Any] | None, dict[str, Path]]:
    """blender.lod's lod.json and the impostor atlas path (key "impostor"), also through a gate."""
    for refs in step.outputs.values():
        for ref in refs:
            if (ref.name or "") != REPORT_FILE:
                continue
            try:
                doc = json.loads(blobs.path(ref.hash).read_bytes())
            except (OSError, ValueError):
                return None, {}
            if not isinstance(doc, dict) or doc.get("type") != REPORT_TYPE or not isinstance(doc.get("lods"), list):
                return None, {}
            files: dict[str, Path] = {}
            atlas = (doc.get("impostor") or {}).get("atlas")
            for other in step.outputs.values():
                for r in other:
                    if atlas and (r.name or "") == atlas and blobs.path(r.hash).is_file():
                        files["impostor"] = blobs.path(r.hash)
            return doc, files
    return None, {}


def _models(doc: dict[str, Any], names: list[str], ext: str) -> list[str] | None:
    found = [_model(names, str(e.get(ext) or ""), f".{ext}") for e in doc["lods"]]
    return None if not found or not all(found) else found  # type: ignore[return-value]


def _summary(doc: dict[str, Any]) -> str:
    tris = " / ".join(str(e["tris"]) for e in doc["lods"])
    col = doc.get("collision") or {}
    ratio = col.get("volume_ratio")
    text = f"{len(doc['lods'])} LOD ({tris} tam giác), collision {col.get('mode', 'none')}"
    if ratio is not None:
        text += f" (thể tích {ratio:.2f}x mesh)"
    if doc["lods"][0]["tris"] > MOBILE_TRIS:
        text += f"; LOD0 > {MOBILE_TRIS} tam giác, nặng cho mobile"
    return text


def unity_json(doc: dict[str, Any], models: list[str], collision: str, atlas: str | None) -> bytes:
    heights = doc["transition_heights"]
    out: dict[str, Any] = {
        "asset": doc["asset"], "size": doc["size"],
        "lods": [{"model": f"Models/{m}", "tris": e["tris"], "height": heights[i]}
                 for i, (m, e) in enumerate(zip(models, doc["lods"]))],
        "collision": {"mode": (doc.get("collision") or {}).get("mode", "none"),
                      "model": f"Models/{collision}" if collision else ""}}
    imp = doc.get("impostor")
    if imp and atlas:
        out["impostor"] = {"atlas": f"Lod/{atlas}", "cols": imp["cols"], "views": imp["views"],
                           "scale": imp["ortho_scale"], "height": heights[len(doc["lods"])]}
    return json.dumps(out, indent=1).encode()


def unity_files(src: Source) -> tuple[Files, list[str]]:
    doc = getattr(src, "lod", None) or {}
    files_by_key: dict[str, Path] = getattr(src, "lod_files", None) or {}
    names = [n for n, _ in src.fbx]
    models = _models(doc, names, "fbx") if doc else None
    col = doc.get("collision") or {}
    collision = _model(names, str(doc.get("collision_fbx") or ""), ".fbx") if col.get("mode") in ("hull", "multi_hull") else ""
    if not models or (col.get("mode") in ("hull", "multi_hull") and not collision):
        return [], []
    a = str(doc["asset"])
    atlas = (doc.get("impostor") or {}).get("atlas")
    has_atlas = bool(atlas and "impostor" in files_by_key)
    files: Files = [(f"{a}_lod.json", unity_json(doc, models, collision or "", atlas if has_atlas else None)),
                    (f"Lod/{IMPOSTOR_FILE}", IMPOSTOR_SCRIPT.encode()),
                    (f"Lod/{IMPOSTOR_FILE}.meta", _script_meta(IMPOSTOR_GUID)),
                    (f"Editor/{EDITOR_FILE}", EDITOR_SCRIPT.encode()),
                    (f"Editor/{EDITOR_FILE}.meta", _script_meta(EDITOR_GUID))]
    if has_atlas:
        files.append((f"Lod/{atlas}", files_by_key["impostor"]))
    note = (f"- LOD: {_summary(doc)}. Khi import xong, Editor/{EDITOR_FILE} tự tạo {a}_LOD.prefab (LODGroup, ngưỡng "
            f"{' / '.join(f'{h:g}' for h in doc['transition_heights'])}" +
            (", BoxCollider" if col.get("mode") == "box" else ", MeshCollider convex theo từng hull" if collision else "") +
            (", impostor 8 góc là LOD cuối" if has_atlas else "") +
            f"); chạy lại: menu Asset Studio > Build LOD Prefabs. Nhập nhiều prop vào một project thì giữ một bản "
            f"Editor/{EDITOR_FILE} và Lod/{IMPOSTOR_FILE} (cùng GUID)" + SHARED_SCRIPT_NOTE + ".")
    return files, [note]


def godot_scene(doc: dict[str, Any], models: list[str], collision_ok: bool, atlas: str | None) -> str:
    a = str(doc["asset"])
    n = len(doc["lods"])
    dist = doc["distances_m"]
    spans = [(0.0 if i == 0 else dist[i - 1], dist[i]) for i in range(n)]
    res = [f'[ext_resource type="Script" path="{GODOT_SCRIPT}" id="1_script"]']
    res += [f'[ext_resource type="PackedScene" path="Models/{m}" id="{i + 2}_lod{i}"]' for i, m in enumerate(models)]
    imp = doc.get("impostor")
    if imp and atlas:
        res.append(f'[ext_resource type="Texture2D" path="Lod/{atlas}" id="{n + 2}_atlas"]')
    col = doc.get("collision") or {}
    subs, shapes = [], []
    if col.get("mode") == "box" and collision_ok:
        sx, sy, sz = (float(c) for c in col["box"]["size"])
        subs.append(f'[sub_resource type="BoxShape3D" id="Shape_box"]\nsize = Vector3({_f(sx)}, {_f(sz)}, {_f(sy)})\n')
        x, y, z = _gd(col["box"]["center"])
        shapes.append(f'[node name="Collision" type="CollisionShape3D" parent="."]\n'
                      f"transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, {_f(x)}, {_f(y)}, {_f(z)})\n"
                      'shape = SubResource("Shape_box")\n')
    elif col.get("mode") in ("hull", "multi_hull") and collision_ok:
        for i, h in enumerate(col.get("hulls") or []):
            subs.append(f'[sub_resource type="ConvexPolygonShape3D" id="Shape_{i:02d}"]\n'
                        f"points = PackedVector3Array({_points(h['points'])})\n")
            shapes.append(f'[node name="Collision{i:02d}" type="CollisionShape3D" parent="."]\n'
                          f'shape = SubResource("Shape_{i:02d}")\n')
    nodes = [f'[node name="LOD{i}" parent="." instance=ExtResource("{i + 2}_lod{i}")]\n' for i in range(n)]
    ranges = ", ".join(f"{_f(b)}, {_f(e)}" for b, e in spans)
    if imp and atlas:
        cx, cy, cz = _gd(imp["center"])
        cell = int(imp["res"])
        nodes.append(
            f'[node name="Impostor" type="Sprite3D" parent="."]\n'
            f"transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, {_f(cx)}, {_f(cy)}, {_f(cz)})\n"
            f"visibility_range_begin = {_f(dist[n - 1])}\nvisibility_range_end = {_f(dist[n])}\n"
            f"billboard = 2\nshaded = false\nalpha_cut = 1\npixel_size = {_f(float(imp['ortho_scale']) / cell)}\n"
            f'texture = ExtResource("{n + 2}_atlas")\nregion_enabled = true\n'
            f"region_rect = Rect2(0, 0, {cell}, {cell})\n")
        ranges_extra = f"impostor_cols = {int(imp['cols'])}\nimpostor_views = {int(imp['views'])}\n" \
                       f"impostor_cell = Vector2i({cell}, {cell})\n"
    else:
        ranges_extra = ""
    return ("[gd_scene format=3]\n\n" + "\n".join(res) + "\n\n" + "\n".join(subs) + ("\n" if subs else "") +
            f'[node name="{a}_lod" type="StaticBody3D"]\nscript = ExtResource("1_script")\n'
            f"ranges = PackedVector2Array({ranges})\n{ranges_extra}\n" + "\n".join(nodes) + "\n" + "\n".join(shapes))


def godot_files(src: Source) -> tuple[Files, list[str]]:
    doc = getattr(src, "lod", None) or {}
    files_by_key: dict[str, Path] = getattr(src, "lod_files", None) or {}
    names = [n for n, _ in src.glb]
    models = _models(doc, names, "glb") if doc else None
    if not models:
        return [], []
    col = doc.get("collision") or {}
    ok = col.get("mode") != "none"
    a = str(doc["asset"])
    atlas = (doc.get("impostor") or {}).get("atlas")
    has_atlas = bool(atlas and "impostor" in files_by_key)
    files: Files = [(f"{a}_lod.tscn", godot_scene(doc, models, ok, atlas if has_atlas else None).encode()),
                    (GODOT_SCRIPT, GODOT_SCRIPT_BODY.encode())]
    if has_atlas:
        files.append((f"Lod/{atlas}", files_by_key["impostor"]))
    dist = " / ".join(f"{d:g}" for d in doc["distances_m"])
    note = (f"- LOD: {a}_lod.tscn = StaticBody3D, mỗi LOD một GLB, {GODOT_SCRIPT} đặt visibility_range_begin/end cho mọi "
            f"GeometryInstance3D theo khoảng cách (m) {dist} (suy từ ngưỡng chiều cao màn hình, FOV 60°)"
            f"{', CollisionShape3D ' + col['mode'] if ok else ''}"
            f"{', impostor Sprite3D billboard 8 góc ở cuối' if has_atlas else ''}. {_summary(doc)}.")
    return files, [note]


EDITOR_SCRIPT = cs_namespace("""// Generated by AI Asset Studio: builds <asset>_LOD.prefab next to <asset>_lod.json once its FBX models are imported;
// menu Asset Studio > Build LOD Prefabs rebuilds them.
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;

public class AssetStudioLod : AssetPostprocessor
{
    [System.Serializable] class Level { public string model; public int tris; public float height; }
    [System.Serializable] class Shape { public string mode; public string model; }
    [System.Serializable] class Imp { public string atlas; public int cols; public int views; public float scale; public float height; }
    [System.Serializable] class Doc { public string asset; public Level[] lods; public Shape collision; public Imp impostor; }

    static void OnPostprocessAllAssets(string[] imported, string[] deleted, string[] moved, string[] movedFrom)
    {
        foreach (var p in imported)
        {
            if (!p.EndsWith("_lod.json")) continue;
            var path = p;
            EditorApplication.delayCall += () => Build(path);
        }
    }

    [MenuItem("Asset Studio/Build LOD Prefabs")]
    public static void BuildAll()
    {
        foreach (var g in AssetDatabase.FindAssets("_lod t:TextAsset"))
        {
            var p = AssetDatabase.GUIDToAssetPath(g);
            if (p.EndsWith("_lod.json")) Build(p);
        }
    }

    static Bounds Combined(Renderer[] renderers)
    {
        var b = renderers[0].bounds;
        foreach (var r in renderers) b.Encapsulate(r.bounds);
        return b;
    }

    static Renderer AddImpostor(GameObject root, Doc doc, string dir, Bounds b)
    {
        var go = new GameObject("Impostor");
        go.transform.SetParent(root.transform, false);
        go.transform.position = b.center;
        go.transform.localScale = new Vector3(doc.impostor.scale, doc.impostor.scale, 1f);
        go.AddComponent<MeshFilter>().sharedMesh = Resources.GetBuiltinResource<Mesh>("Quad.fbx");
        var urp = Shader.Find("Universal Render Pipeline/Unlit");
        var shader = urp != null ? urp : Shader.Find("Unlit/Transparent");
        if (shader == null)
        {
            Debug.LogWarning("Asset Studio: no unlit transparent shader found, impostor of " + doc.asset + " skipped");
            Object.DestroyImmediate(go);
            return null;
        }
        var mat = new Material(shader);
        var atlas = AssetDatabase.LoadAssetAtPath<Texture2D>(dir + "/" + doc.impostor.atlas);
        mat.mainTexture = atlas;
        if (urp != null)
        {
            mat.SetFloat("_Surface", 1f);
            mat.SetFloat("_Blend", 0f);
            mat.SetFloat("_SrcBlend", (float)UnityEngine.Rendering.BlendMode.SrcAlpha);
            mat.SetFloat("_DstBlend", (float)UnityEngine.Rendering.BlendMode.OneMinusSrcAlpha);
            mat.SetFloat("_ZWrite", 0f);
            mat.EnableKeyword("_SURFACE_TYPE_TRANSPARENT");
            mat.SetOverrideTag("RenderType", "Transparent");
            mat.renderQueue = 3000;
        }
        var matPath = dir + "/" + doc.asset + "_impostor.mat";
        AssetDatabase.DeleteAsset(matPath);
        AssetDatabase.CreateAsset(mat, matPath);
        var mr = go.AddComponent<MeshRenderer>();
        mr.sharedMaterial = mat;
        mr.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
        var imp = go.AddComponent<AssetStudioImpostor>();
        imp.cols = doc.impostor.cols;
        imp.views = doc.impostor.views;
        return mr;
    }

    static void AddCollision(GameObject root, Doc doc, string dir, Bounds b)
    {
        if (doc.collision.mode == "box")
        {
            var box = root.AddComponent<BoxCollider>();
            box.center = b.center;
            box.size = b.size;
        }
        else if (!string.IsNullOrEmpty(doc.collision.model))
        {
            var holder = new GameObject("Collision");
            holder.transform.SetParent(root.transform, false);
            foreach (var mesh in AssetDatabase.LoadAllAssetsAtPath(dir + "/" + doc.collision.model).OfType<Mesh>())
            {
                var part = new GameObject(mesh.name);
                part.transform.SetParent(holder.transform, false);
                var mc = part.AddComponent<MeshCollider>();
                mc.sharedMesh = mesh;
                mc.convex = true;
            }
        }
    }

    public static bool Build(string jsonPath)
    {
        var text = AssetDatabase.LoadAssetAtPath<TextAsset>(jsonPath);
        if (text == null) return false;
        var doc = JsonUtility.FromJson<Doc>(text.text);
        var dir = Path.GetDirectoryName(jsonPath).Replace('\\\\', '/');
        var root = new GameObject(doc.asset + "_LOD");
        var levels = new List<LOD>();
        for (int i = 0; i < doc.lods.Length; i++)
        {
            var model = AssetDatabase.LoadAssetAtPath<GameObject>(dir + "/" + doc.lods[i].model);
            if (model == null)
            {
                Debug.LogWarning("Asset Studio: " + doc.lods[i].model + " is not imported yet");
                Object.DestroyImmediate(root);
                return false;
            }
            var go = (GameObject)PrefabUtility.InstantiatePrefab(model);
            go.name = "LOD" + i;
            go.transform.SetParent(root.transform, false);
            levels.Add(new LOD(doc.lods[i].height, go.GetComponentsInChildren<Renderer>()));
        }
        var bounds = Combined(levels[0].renderers);
        if (!string.IsNullOrEmpty(doc.impostor.atlas))
        {
            var imp = AddImpostor(root, doc, dir, bounds);
            if (imp != null) levels.Add(new LOD(doc.impostor.height, new[] { imp }));
        }
        AddCollision(root, doc, dir, bounds);
        var group = root.AddComponent<LODGroup>();
        group.fadeMode = LODFadeMode.None;
        group.SetLODs(levels.ToArray());
        group.RecalculateBounds();
        PrefabUtility.SaveAsPrefabAsset(root, dir + "/" + doc.asset + "_LOD.prefab");
        Object.DestroyImmediate(root);
        return true;
    }
}
""")

IMPOSTOR_SCRIPT = cs_namespace("""// Generated by AI Asset Studio: faces the camera and shows the impostor atlas cell that matches the viewing angle.
using UnityEngine;

public class AssetStudioImpostor : MonoBehaviour
{
    public int cols = 4;
    public int views = 8;
    Renderer rend;
    MaterialPropertyBlock block;

    void LateUpdate()
    {
        var cam = Camera.main;
        if (cam == null) return;
        if (rend == null)
        {
            rend = GetComponent<Renderer>();
            block = new MaterialPropertyBlock();
        }
        var toCam = cam.transform.position - transform.position;
        var flat = new Vector3(toCam.x, 0f, toCam.z);
        if (flat.sqrMagnitude < 1e-6f) return;
        transform.rotation = Quaternion.LookRotation(-flat);
        var d = transform.parent != null ? transform.parent.InverseTransformDirection(toCam) : toCam;
        var idx = Mathf.RoundToInt(Mathf.Atan2(-d.x, d.z) * Mathf.Rad2Deg / (360f / views));
        idx = ((idx % views) + views) % views;
        var rows = Mathf.CeilToInt(views / (float)cols);
        var row = idx / cols;
        var col = idx % cols;
        var prop = rend.sharedMaterial != null && rend.sharedMaterial.HasProperty("_BaseMap") ? "_BaseMap_ST" : "_MainTex_ST";
        block.SetVector(prop, new Vector4(1f / cols, 1f / rows, col / (float)cols, 1f - (row + 1f) / rows));
        rend.SetPropertyBlock(block);
    }
}
""")

GODOT_SCRIPT_BODY = """# Generated by AI Asset Studio: applies the LOD distance ranges and picks the impostor cell for the viewing angle.
extends StaticBody3D

@export var ranges := PackedVector2Array()
@export var impostor_cols := 4
@export var impostor_views := 8
@export var impostor_cell := Vector2i(256, 256)

var _impostor: Sprite3D


func _ready() -> void:
	for i in ranges.size():
		var lod := get_node_or_null("LOD%d" % i)
		if lod == null:
			continue
		for g in lod.find_children("*", "GeometryInstance3D", true, false):
			g.visibility_range_begin = ranges[i].x
			g.visibility_range_end = ranges[i].y
	_impostor = get_node_or_null("Impostor") as Sprite3D
	set_process(_impostor != null)


func _process(_delta: float) -> void:
	var cam := get_viewport().get_camera_3d()
	if cam == null:
		return
	var d := global_transform.affine_inverse() * cam.global_position
	var idx := posmod(roundi(rad_to_deg(atan2(d.x, d.z)) / (360.0 / impostor_views)), impostor_views)
	var row := floori(float(idx) / impostor_cols)
	_impostor.region_rect = Rect2((idx % impostor_cols) * impostor_cell.x, row * impostor_cell.y,
			impostor_cell.x, impostor_cell.y)
"""
