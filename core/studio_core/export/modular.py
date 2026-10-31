"""Modular kit export (blender.modular_kit). Unity: the per-piece FBX models (rig3d's FBX path), <asset>_modular.json
and an Editor script that builds Kit/<piece>.prefab (trim material + BoxColliders) and <asset>_SampleRoom.prefab on
import. Godot: per-piece OBJ meshes (OBJ imports as a Mesh resource, which a MeshLibrary can reference directly; a GLB
imports as a scene) with an .mtl pointing at the PBR maps, a MeshLibrary .tres and a GridMap sample-room scene."""
from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

from studio_core.adapters.blender import modular_math as mm
from studio_core.contracts import BlobStore, StepRow
from studio_core.export.sheet import cs_namespace
from studio_core.export.source import Files, Source

EDITOR_FILE = "AssetStudioModularKit.cs"
# Basis::get_orthogonal_index for a turn about +Y by 0/90/180/270 degrees (Godot's 24 orthogonal bases).
GRID_ORIENT = {0: 0, 90: 16, 180: 10, 270: 22}


def modular_set(step: StepRow, blobs: BlobStore) -> tuple[dict[str, Any] | None, list[tuple[str, Path]]]:
    """blender.modular_kit's modular.json and its OBJ files (name, path), also through a gate."""
    from studio_core.adapters.blender.modular import REPORT_FILE, REPORT_TYPE

    for refs in step.outputs.values():
        for ref in refs:
            if (ref.name or "") != REPORT_FILE:
                continue
            try:
                doc = json.loads(blobs.path(ref.hash).read_bytes())
                if doc.get("type") != REPORT_TYPE or not isinstance(doc.get("pieces"), list):
                    return None, []
                objs = [(str(e["obj"]), blobs.path(str(e["obj_hash"]))) for e in doc["pieces"]]
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                return None, []
            return doc, [(n, p) for n, p in objs if p.is_file()]
    return None, []


def _box(lo: list[float], hi: list[float], conv: Any) -> tuple[list[float], list[float]]:
    pts = [conv([x, y, z]) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
    a = [min(p[i] for p in pts) for i in range(3)]
    b = [max(p[i] for p in pts) for i in range(3)]
    return [round((a[i] + b[i]) / 2, 5) for i in range(3)], [round(b[i] - a[i], 5) for i in range(3)]


def _material_rel(src: Source) -> str | None:
    if not src.pbr:
        return None
    from studio_core.export.pbr import _prefix

    return f"PBR/{_prefix(src, src.pbr[0][0])}"


def _vec(v: list[float]) -> dict[str, float]:
    return {"x": v[0], "y": v[1], "z": v[2]}


def unity_json(doc: dict[str, Any], models: dict[str, str], material: str | None) -> bytes:
    pieces = []
    for e in doc["pieces"]:
        boxes = [_box(lo, hi, mm.to_unity) for lo, hi in e.get("colliders") or []]
        pieces.append({"name": e["name"], "piece": e["piece"], "model": f"Models/{models[e['piece']]}",
                       "tris": e["tris"], "colliders": [{"center": _vec(c), "size": _vec(s)} for c, s in boxes]})
    names = {e["piece"]: e["name"] for e in doc["pieces"]}
    room = [{"piece": names[r["piece"]], "position": _vec(mm.to_unity(r["pos"])), "yaw": -float(r["rot"])}
            for r in doc.get("room") or [] if r["piece"] in names]
    out = {"asset": doc["asset"], "grid": doc["dims"]["grid"], "storey": doc["dims"]["storey"],
           "material": f"{material}.mat" if material else "", "pieces": pieces, "room": room}
    return json.dumps(out, indent=1).encode()


def _summary(doc: dict[str, Any]) -> str:
    d = doc["dims"]
    tris = ", ".join(f"{e['piece']} {e['tris']}" for e in doc["pieces"])
    return (f"lưới {d['grid']:g} m, tầng cao {d['storey']:g} m, tường dày {d['thickness']:g} m; pivot góc dưới-trái "
            f"của ô, tường chạy theo trục X, mặt ngoài ở phía trước. Tam giác: {tris}. Kích thước đúng bội lưới: "
            f"{'có' if doc.get('all_bounds_ok') else 'KHÔNG'}; đầu mảnh khớp tường kề: "
            f"{'có' if doc.get('all_ends_ok') else 'KHÔNG'}.")


def unity_files(src: Source) -> tuple[Files, list[str]]:
    doc = src.modular or {}
    fbx = {Path(n).stem: n for n, _ in src.fbx}
    models = {e["piece"]: fbx.get(Path(str(e.get("fbx") or "")).stem) for e in doc.get("pieces") or []}
    if not doc or not all(models.values()):
        return [], []
    a = str(doc["asset"])
    mat = _material_rel(src)
    from studio_core.export.unity import guid

    rel = f"Editor/{EDITOR_FILE}"
    script, ns = editor_script(src.uid, a)
    files: Files = [(f"{a}_modular.json", unity_json(doc, models, mat)),  # type: ignore[arg-type]
                    (rel, script.encode()), (f"{rel}.meta", _script_meta(guid(src.uid, a, rel)))]
    note = (f"- Modular kit: {_summary(doc)} Khi import xong, Editor/{EDITOR_FILE} ({ns}) tạo Kit/<mảnh>.prefab (gắn "
            f"{mat or 'material của FBX'}.mat + BoxCollider theo hình mảnh) và {a}_SampleRoom.prefab ghép phòng mẫu; "
            f"chạy lại: menu Asset Studio > Build Modular Kit Prefabs ({a}). Bật Snap (Grid Snapping) theo cỡ lưới để "
            "xếp mảnh. Mỗi lần xuất có namespace riêng nên nhiều kit chung 1 project không trùng script.")
    return files, [note]


def _script_meta(g: str) -> bytes:
    return (f"fileFormatVersion: 2\nguid: {g}\nMonoImporter:\n  externalObjects: {{}}\n  serializedVersion: 2\n"
            "  defaultReferences: []\n  executionOrder: 0\n  icon: {instanceID: 0}\n  userData: \n"
            "  assetBundleName: \n  assetBundleVariant: \n").encode()


def mtl(material: str | None) -> str:
    lines = ["newmtl trim", "Kd 1 1 1", "Ns 0"]
    if material:
        lines += [f"map_Kd ../{material}_albedo.png", f"map_Ns ../{material}_roughness.png",
                  f"map_bump ../{material}_normal.png"]
    return "\n".join(lines) + "\n"


def obj_with_mtl(path: Path, mtl_name: str) -> bytes:
    body = path.read_bytes()
    return f"mtllib {mtl_name}\nusemtl trim\n".encode() + body


def _f(x: float) -> str:
    return f"{x:.6g}"


def mesh_library(doc: dict[str, Any], objs: dict[str, str]) -> str:
    ext, subs, props = [], [], []
    for i, e in enumerate(doc["pieces"]):
        ext.append(f'[ext_resource type="ArrayMesh" path="Kit/{objs[e["piece"]]}" id="{i + 1}_{e["piece"]}"]')
        shapes = []
        for k, (lo, hi) in enumerate(e.get("colliders") or []):
            c, s = _box(lo, hi, mm.to_godot)
            sid = f"Box_{i}_{k}"
            subs.append(f'[sub_resource type="BoxShape3D" id="{sid}"]\nsize = Vector3({_f(s[0])}, {_f(s[1])}, '
                        f"{_f(s[2])})\n")
            at = ", ".join(_f(v) for v in c)
            shapes += [f'SubResource("{sid}")', f"Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, {at})"]
        props += [f'item/{i}/name = "{e["piece"]}"', f'item/{i}/mesh = ExtResource("{i + 1}_{e["piece"]}")',
                  f"item/{i}/mesh_transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0)",
                  f"item/{i}/mesh_cast_shadow = 1", f"item/{i}/shapes = [{', '.join(shapes)}]",
                  f"item/{i}/navigation_mesh_transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0)",
                  f"item/{i}/navigation_layers = 1"]
    return (f'[gd_resource type="MeshLibrary" load_steps={len(ext) + len(subs) + 1} format=3]\n\n'
            + "\n".join(ext) + "\n\n" + "\n".join(subs) + "\n[resource]\n" + "\n".join(props) + "\n")


LAYER_OF = {"floor": "Floors", "ceiling": "Ceilings", "pillar": "Props"}


def grid_layers(doc: dict[str, Any]) -> dict[str, list[int]]:
    """GridMap `data.cells` per child GridMap: a GridMap holds one item per cell, and a floor tile shares its cell
    with the wall or pillar standing on it, so each kind goes to its own map (a clash overflows to a new one).
    Per cell: the 64-bit key (x, y, z as int16, x lowest) as two int32, then item | orientation << 16."""
    g = float(doc["dims"]["grid"])
    index = {e["piece"]: i for i, e in enumerate(doc["pieces"])}
    layers: dict[str, list[int]] = {}
    taken: dict[str, set[tuple[int, int, int]]] = {}
    for r in doc.get("room") or []:
        if r["piece"] not in index:
            continue
        cell = tuple(round(c / g) for c in mm.to_godot(r["pos"]))
        name, k = LAYER_OF.get(r["piece"], "Walls"), 2
        base = name
        while cell in taken.get(name, set()):
            name, k = f"{base}{k}", k + 1
        taken.setdefault(name, set()).add(cell)
        lo, hi = struct.unpack("<ii", struct.pack("<hhhh", *cell, 0))
        layers.setdefault(name, []).extend([lo, hi, index[r["piece"]] | GRID_ORIENT[int(r["rot"]) % 360] << 16])
    return layers


def room_scene(doc: dict[str, Any], lib: str) -> str:
    g = _f(float(doc["dims"]["grid"]))
    nodes = []
    for name, cells in grid_layers(doc).items():
        nodes.append(f'[node name="{name}" type="GridMap" parent="."]\nmesh_library = ExtResource("1_lib")\n'
                     f"cell_size = Vector3({g}, {g}, {g})\ncell_center_x = false\ncell_center_y = false\n"
                     f'cell_center_z = false\ndata = {{\n"cells": PackedInt32Array({", ".join(map(str, cells))})\n}}\n')
    return ("[gd_scene load_steps=2 format=3]\n\n"
            f'[ext_resource type="MeshLibrary" path="{lib}" id="1_lib"]\n\n'
            f'[node name="{doc["asset"]}_room" type="Node3D"]\n\n' + "\n".join(nodes))


def godot_files(src: Source) -> tuple[Files, list[str]]:
    doc = src.modular or {}
    objs = {Path(n).stem: (n, p) for n, p in src.modular_obj}
    pieces = doc.get("pieces") or []
    if not doc or not all(Path(str(e.get("obj") or "")).stem in objs for e in pieces):
        return [], []
    a = str(doc["asset"])
    mat = _material_rel(src)
    mtl_name = f"{a}_kit.mtl"
    files: Files = [(f"Kit/{mtl_name}", mtl(mat).encode())]
    names: dict[str, str] = {}
    for e in pieces:
        n, p = objs[Path(str(e["obj"])).stem]
        names[e["piece"]] = n
        files.append((f"Kit/{n}", obj_with_mtl(p, mtl_name)))
    lib = f"{a}_kit.tres"
    files += [(lib, mesh_library(doc, names).encode()), (f"{a}_room.tscn", room_scene(doc, lib).encode())]
    note = (f"- Modular kit: {_summary(doc)} Kit/*.obj là mesh từng mảnh (material từ {mtl_name} trỏ vào bộ map "
            f"PBR); {lib} là MeshLibrary (mỗi mảnh 1 item + BoxShape3D) cho GridMap — đặt cell_size = "
            f"{doc['dims']['grid']:g} và tắt cell_center_x/y/z vì pivot ở góc ô; {a}_room.tscn là phòng mẫu "
            "(GridMap riêng cho sàn, tường, cột vì mỗi ô GridMap chỉ giữ 1 mảnh). Models/*.glb là từng mảnh + "
            "phòng mẫu dạng scene để xem nhanh.")
    return files, [note]


EDITOR_SCRIPT = """// Generated by AI Asset Studio: builds Kit/<piece>.prefab (trim material + BoxColliders) and
// <asset>_SampleRoom.prefab next to <asset>_modular.json once the FBX pieces are imported;
// menu Asset Studio > Build Modular Kit Prefabs reruns it.
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEngine;

public class AssetStudioModularKit : AssetPostprocessor
{
    [System.Serializable] class Box { public Vector3 center; public Vector3 size; }
    [System.Serializable] class Piece { public string name; public string model; public Box[] colliders; }
    [System.Serializable] class Place { public string piece; public Vector3 position; public float yaw; }
    [System.Serializable] class Doc
    {
        public string asset;
        public string material;
        public Piece[] pieces;
        public Place[] room;
    }

    const string JsonName = "__JSON__";

    static void OnPostprocessAllAssets(string[] imported, string[] deleted, string[] moved, string[] movedFrom)
    {
        foreach (var p in imported)
        {
            if (!string.Equals(Path.GetFileName(p), JsonName, System.StringComparison.OrdinalIgnoreCase)) continue;
            var path = p;
            EditorApplication.delayCall += () => Build(path);
        }
    }

    [MenuItem("Asset Studio/Build Modular Kit Prefabs (__ASSET__)")]
    public static void BuildAll()
    {
        foreach (var g in AssetDatabase.FindAssets(Path.GetFileNameWithoutExtension(JsonName) + " t:TextAsset"))
        {
            var p = AssetDatabase.GUIDToAssetPath(g);
            if (string.Equals(Path.GetFileName(p), JsonName, System.StringComparison.OrdinalIgnoreCase)) Build(p);
        }
    }

    public static bool Build(string jsonPath)
    {
        var text = AssetDatabase.LoadAssetAtPath<TextAsset>(jsonPath);
        if (text == null) return false;
        var doc = JsonUtility.FromJson<Doc>(text.text);
        var dir = Path.GetDirectoryName(jsonPath).Replace('\\\\', '/');
        var mat = string.IsNullOrEmpty(doc.material) ? null
            : AssetDatabase.LoadAssetAtPath<Material>(dir + "/" + doc.material);
        if (!AssetDatabase.IsValidFolder(dir + "/Kit")) AssetDatabase.CreateFolder(dir, "Kit");
        var prefabs = new Dictionary<string, GameObject>();
        foreach (var piece in doc.pieces)
        {
            var model = AssetDatabase.LoadAssetAtPath<GameObject>(dir + "/" + piece.model);
            if (model == null)
            {
                Debug.LogWarning("Asset Studio: " + piece.model + " is not imported yet");
                return false;
            }
            var go = (GameObject)PrefabUtility.InstantiatePrefab(model);
            go.name = piece.name;
            if (mat != null)
                foreach (var r in go.GetComponentsInChildren<Renderer>())
                {
                    var mats = r.sharedMaterials;
                    for (int i = 0; i < mats.Length; i++) mats[i] = mat;
                    r.sharedMaterials = mats;
                }
            foreach (var b in piece.colliders)
            {
                var col = go.AddComponent<BoxCollider>();
                col.center = b.center;
                col.size = b.size;
            }
            go.isStatic = true;
            prefabs[piece.name] = PrefabUtility.SaveAsPrefabAsset(go, dir + "/Kit/" + piece.name + ".prefab");
            Object.DestroyImmediate(go);
        }
        var room = new GameObject(doc.asset + "_SampleRoom");
        foreach (var p in doc.room)
        {
            if (!prefabs.TryGetValue(p.piece, out var prefab)) continue;
            var inst = (GameObject)PrefabUtility.InstantiatePrefab(prefab, room.transform);
            inst.transform.localPosition = p.position;
            inst.transform.localRotation = Quaternion.Euler(0f, p.yaw, 0f);
        }
        PrefabUtility.SaveAsPrefabAsset(room, dir + "/" + doc.asset + "_SampleRoom.prefab");
        Object.DestroyImmediate(room);
        AssetDatabase.SaveAssets();
        Debug.Log("Asset Studio: built " + doc.pieces.Length + " modular pieces + " + doc.asset + "_SampleRoom");
        return true;
    }
}
"""


def editor_script(uid: str, asset: str) -> tuple[str, str]:
    """The builder in a namespace of its own per export: two kits in one project must not define one class twice."""
    from studio_core.export.unity import guid

    ns = f"AssetStudio.Modular{guid(uid, asset, f'Editor/{EDITOR_FILE}')[:8]}"
    body = EDITOR_SCRIPT.replace("__JSON__", f"{asset}_modular.json").replace("__ASSET__", asset)
    return cs_namespace(body, ns), ns
