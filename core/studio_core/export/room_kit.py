"""Room kit export (op.room_kit). Godot 4: Rooms/<name>.tscn per room (TileMapLayer Ground / Shadow / Solid on the shared
TileSet, a disabled Plug_<side> layer per door, door markers, props, PlayerSpawn), room_generator.gd (class RoomGenerator,
joins rooms by their doors from a seed) and a demo scene. Unity: Rooms/<name>.prefab per room built by an editor script
from roomkit_<id>.json, the shared RoomData.cs / RoomGenerator.cs (fixed GUIDs) and a demo scene. Tiled and LDtk copies
of every room go under Tiled/."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from studio_core.contracts import BlobStore, StepRow
from studio_core.export.playable_scene import _script_meta, _text_meta, _n, _vec, godot_tileset, tile_map_data
from studio_core.export.sheet import SHARED_SCRIPT_NOTE, Cell, Layout, cs_namespace, stable_hex
from studio_core.export.source import Files, Source

DATA_GUID = "e2a5c7f19d3b4608b4f1a7c3d9e05b82"
GENERATOR_GUID = "7f4b1d9a3e6c4a25c8d0b2e5a1f73c96"
GENERATOR_GD_FILE = "room_generator.gd"


def room_kit_set(step: StepRow, blobs: BlobStore) -> tuple[dict[str, Any] | None, dict[str, Path]]:
    """op.room_kit's room_kit.json and its atlas / prop images, also when a gate passed it through."""
    from studio_core.ops.room_kit import KIT_FILE, KIT_TYPE

    for refs in step.outputs.values():
        for ref in refs:
            if (ref.name or "") != KIT_FILE:
                continue
            try:
                doc = json.loads(blobs.path(ref.hash).read_bytes())
                if doc.get("type") != KIT_TYPE or not doc.get("rooms"):
                    return None, {}
                files = {"atlas": blobs.path(doc["tileset"]["hash"])}
                for k in doc["prop_kinds"]:
                    files[f"prop:{k['name']}"] = blobs.path(k["hash"])
                missing = [k for k, p in files.items() if not Path(p).is_file()]
                if missing:
                    return None, {}
                return doc, files
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                return None, {}
    return None, {}


def source_from(doc: dict[str, Any], files: dict[str, Path], asset: str, uid: str) -> Source:
    src = Source(asset=asset, uid=uid)
    src.room_kit, src.room_kit_files = doc, files
    return src


def _doc(src: Source) -> dict[str, Any]:
    assert src.room_kit is not None
    return {**src.room_kit, "asset": src.asset}


def _uid(src: Source) -> str:
    return stable_hex("roomkit", src.uid, src.asset)[:8]


def _door_center(doc: dict[str, Any], room: dict[str, Any], side: str) -> tuple[float, float]:
    t = doc["tile"]
    cells = room["door_cells"][side]
    x0, x1 = min(c[0] for c in cells), max(c[0] for c in cells)
    y0, y1 = min(c[1] for c in cells), max(c[1] for c in cells)
    return (x0 + x1 + 1) / 2 * t, (y0 + y1 + 1) / 2 * t


def _plug_ids(room: dict[str, Any], side: str, cols: int, rows: int) -> list[int]:
    ids = [-1] * (cols * rows)
    for x, y, tid in room["plugs"][side]:
        ids[y * cols + x] = tid
    return ids


GENERATOR_GD = """class_name RoomGenerator
extends Node2D
## Room kit from AI Asset Studio: joins the exported rooms door to door into one map, the same map for the same seed.
## Rooms sit on a grid of room-sized cells. The start room goes first; each next room attaches to an open door through
## a room with the matching door that still leaves a door open, and a boss takes the last slot when one fits. A door
## with no matching door across it is closed with that room's Plug_<side> layer, so no door leads nowhere.
## Generation stops with a message in `report` when no door or no fitting room is left.

signal generated(info: Dictionary)

const DIRS := {"N": Vector2i(0, -1), "E": Vector2i(1, 0), "S": Vector2i(0, 1), "W": Vector2i(-1, 0)}
const OPPOSITE := {"N": "S", "E": "W", "S": "N", "W": "E"}

## Room kit json (<asset>_rooms.json), relative to the scene that holds this node or a res:// path.
@export var kit_path := ""
@export var room_count := 10
@export var map_seed := 0
@export var max_attempts_per_room := 40
@export var generate_on_ready := true
@export var camera: Camera2D

var kit: Dictionary = {}
var plan: Dictionary = {}
var report: Dictionary = {}


func _ready() -> void:
	if generate_on_ready:
		generate()


func generate(count: int = -1, new_seed: int = -1) -> Dictionary:
	clear()
	var path := _resolve(kit_path)
	var parsed: Variant = JSON.parse_string(FileAccess.get_file_as_string(path))
	if typeof(parsed) != TYPE_DICTIONARY:
		report = {"ok": false, "rooms": 0, "stopped": "cannot read the room kit at " + path}
		push_error(report["stopped"])
		return report
	kit = parsed
	var want := room_count if count < 0 else count
	var rng := RandomNumberGenerator.new()
	rng.seed = map_seed if new_seed < 0 else new_seed
	var result := _plan(want, rng)
	plan = result["plan"]
	_spawn_rooms(path.get_base_dir())
	report = {"ok": result["stopped"] == "", "rooms": plan.size(), "requested": want, "stopped": result["stopped"]}
	if result["stopped"] != "":
		push_warning("RoomGenerator: " + String(result["stopped"]))
	_fit_camera()
	generated.emit(report)
	return report


func clear() -> void:
	for child in get_children():
		remove_child(child)
		child.queue_free()
	plan = {}


func _resolve(path: String) -> String:
	if path.begins_with("res://") or path.begins_with("user://"):
		return path
	var base := ""
	if owner != null:
		base = owner.scene_file_path.get_base_dir()
	elif get_tree().current_scene != null:
		base = get_tree().current_scene.scene_file_path.get_base_dir()
	return base.path_join(path)


func _room_px() -> Vector2:
	return Vector2(float(kit["cols"]) * float(kit["tile"]), float(kit["rows"]) * float(kit["tile"]))


func _plan(want: int, rng: RandomNumberGenerator) -> Dictionary:
	var rooms: Array = kit["rooms"]
	var starts: Array[int] = []
	for i in rooms.size():
		if rooms[i]["kind"] == "start":
			starts.append(i)
	var placed := {}
	if starts.is_empty():
		return {"plan": placed, "stopped": "the kit has no start room"}
	placed[Vector2i.ZERO] = starts[rng.randi_range(0, starts.size() - 1)]
	var stopped := ""
	var tries := 0
	var limit := maxi(1, want) * max_attempts_per_room
	while placed.size() < want:
		tries += 1
		if tries > limit:
			stopped = "gave up after %d attempts with %d of %d rooms" % [limit, placed.size(), want]
			break
		var open_list := _open_doors(rooms, placed)
		if open_list.is_empty():
			stopped = "no open door left after %d of %d rooms" % [placed.size(), want]
			break
		var last := placed.size() == want - 1
		var slots: Array = _boss_slots(rooms, open_list) if last else []
		if slots.is_empty():
			slots = open_list
		var slot: Dictionary = slots[rng.randi_range(0, slots.size() - 1)]
		var cell: Vector2i = slot["cell"]
		var side: String = slot["side"]
		var target: Vector2i = cell + DIRS[side]
		var pick := _pick(rooms, placed, open_list, target, OPPOSITE[side], last, rng)
		if pick >= 0:
			placed[target] = pick
	return {"plan": placed, "stopped": stopped}


func _boss_slots(rooms: Array, open_list: Array) -> Array:
	var out := []
	for slot in open_list:
		for r in rooms:
			if r["kind"] == "boss" and OPPOSITE[slot["side"]] in r["doors"]:
				out.append(slot)
				break
	return out


func _open_doors(rooms: Array, placed: Dictionary) -> Array:
	var out := []
	for key in placed:
		var cell: Vector2i = key
		for side in rooms[placed[cell]]["doors"]:
			if not placed.has(cell + DIRS[side]):
				out.append({"cell": cell, "side": side})
	return out


func _pick(rooms: Array, placed: Dictionary, open_list: Array, target: Vector2i, need: String, last: bool,
		rng: RandomNumberGenerator) -> int:
	var closed := 0
	for o in open_list:
		var oc: Vector2i = o["cell"]
		if oc + DIRS[o["side"]] == target:
			closed += 1
	var others := open_list.size() - closed
	var pool: Array[int] = []
	var bosses: Array[int] = []
	for i in rooms.size():
		var r: Dictionary = rooms[i]
		if r["kind"] == "start" or not (need in r["doors"]):
			continue
		var fresh := 0
		for d in r["doors"]:
			if not placed.has(target + DIRS[d]):
				fresh += 1
		if not last and others + fresh == 0:
			continue
		if r["kind"] == "boss":
			if last:
				bosses.append(i)
			continue
		pool.append(i)
	if last and not bosses.is_empty():
		pool = bosses
	if pool.is_empty():
		return -1
	var total := 0.0
	for i in pool:
		total += maxf(0.0, float(rooms[i].get("weight", 1.0)))
	var roll := rng.randf() * maxf(total, 0.0001)
	for i in pool:
		roll -= maxf(0.0, float(rooms[i].get("weight", 1.0)))
		if roll <= 0.0:
			return i
	return pool[-1]


func _spawn_rooms(base_dir: String) -> void:
	var rooms: Array = kit["rooms"]
	var room_px := _room_px()
	var scenes := {}
	for key in plan:
		var cell: Vector2i = key
		var idx: int = plan[cell]
		var info: Dictionary = rooms[idx]
		if not scenes.has(idx):
			scenes[idx] = load(base_dir.path_join(info["scene"]))
		var room := (scenes[idx] as PackedScene).instantiate() as Node2D
		room.position = Vector2(cell) * room_px
		add_child(room)
		room.name = "%s_%d_%d" % [info["name"], cell.x, cell.y]
		for side in info["doors"]:
			var across: Vector2i = cell + DIRS[side]
			var linked: bool = plan.has(across) and OPPOSITE[side] in rooms[plan[across]]["doors"]
			if not linked:
				var plug := room.get_node_or_null("Plug_" + String(side)) as TileMapLayer
				if plug != null:
					plug.enabled = true


func _fit_camera() -> void:
	if camera == null or plan.is_empty():
		return
	var room_px := _room_px()
	var lo := Vector2i(1 << 30, 1 << 30)
	var hi := Vector2i(-(1 << 30), -(1 << 30))
	for key in plan:
		var c: Vector2i = key
		lo = Vector2i(mini(lo.x, c.x), mini(lo.y, c.y))
		hi = Vector2i(maxi(hi.x, c.x), maxi(hi.y, c.y))
	var bounds := Rect2(Vector2(lo) * room_px, Vector2(hi - lo + Vector2i.ONE) * room_px)
	camera.global_position = global_position + bounds.get_center()
	var view := get_viewport_rect().size
	var z := minf(view.x / bounds.size.x, view.y / bounds.size.y)
	camera.zoom = Vector2(z, z)
"""


def _room_tscn(doc: dict[str, Any], room: dict[str, Any]) -> str:
    cols, rows, t, side_view = doc["cols"], doc["rows"], doc["tile"], doc["style"] == "sideview"
    used = sorted({p["kind"] for p in room["props"]})
    ext = ['[ext_resource type="TileSet" path="../{}_tileset.tres" id="1_tiles"]'.format(doc["asset"])]
    for k in used:
        ext.append(f'[ext_resource type="Texture2D" path="../Props/{doc["prop_kinds"][k]["name"]}.png" id="p{k}"]')
    lines = [f"[gd_scene load_steps={len(ext) + 1} format=3]", "", *ext, "",
             f'[node name="{room["name"]}" type="Node2D"]', f'metadata/kind = "{room["kind"]}"',
             "metadata/doors = PackedStringArray({})".format(", ".join(f'"{s}"' for s in room["doors"])),
             f"metadata/size = {_vec(cols * t, rows * t)}"]
    for layer in room["layers"]:
        if all(v < 0 for v in layer["tiles"]):
            continue
        lines += ["", f'[node name="{layer["name"]}" type="TileMapLayer" parent="."]',
                  f"tile_map_data = {tile_map_data(layer['tiles'], cols)}", 'tile_set = ExtResource("1_tiles")']
        if not layer["collide"]:
            lines.append("collision_enabled = false")
    for side in room["doors"]:
        lines += ["", f'[node name="Plug_{side}" type="TileMapLayer" parent="."]',
                  f"tile_map_data = {tile_map_data(_plug_ids(room, side, cols, rows), cols)}",
                  'tile_set = ExtResource("1_tiles")', "enabled = false"]
    lines += ["", '[node name="Doors" type="Node2D" parent="."]']
    for side in room["doors"]:
        lines += ["", f'[node name="{side}" type="Marker2D" parent="Doors"]',
                  f"position = {_vec(*_door_center(doc, room, side))}"]
    lines += ["", '[node name="Props" type="Node2D" parent="."]']
    if not side_view:
        lines.append("y_sort_enabled = true")
    for n, p in enumerate(room["props"]):
        k = doc["prop_kinds"][p["kind"]]
        node = f"{k['name']}{n}"
        poly = ", ".join(_n(v) for xy in k["collider"] for v in xy)
        lines += ["", f'[node name="{node}" type="StaticBody2D" parent="Props"]', f"position = {_vec(*p['pos'])}",
                  "", f'[node name="Sprite" type="Sprite2D" parent="Props/{node}"]',
                  f'texture = ExtResource("p{p["kind"]}")', "centered = false",
                  f"offset = {_vec(-k['w'] / 2, -k['h'])}",
                  "", f'[node name="Collision" type="CollisionPolygon2D" parent="Props/{node}"]',
                  f"polygon = PackedVector2Array({poly})"]
    sx, sy = room["spawn"]
    lines += ["", '[node name="PlayerSpawn" type="Marker2D" parent="."]',
              f"position = {_vec((sx + 0.5) * t, (sy + 1) * t - 1 if side_view else (sy + 0.6) * t)}"]
    return "\n".join(lines) + "\n"


def _demo_tscn(doc: dict[str, Any]) -> str:
    a = doc["asset"]
    return "\n".join([
        "[gd_scene load_steps=2 format=3]", "", f'[ext_resource type="Script" path="{GENERATOR_GD_FILE}" id="1_gen"]', "",
        f'[node name="{a}Demo" type="Node2D"]', "", '[node name="Camera2D" type="Camera2D" parent="."]', "",
        '[node name="RoomGenerator" type="Node2D" parent="."]', 'script = ExtResource("1_gen")',
        f'kit_path = "{a}_rooms.json"', 'camera = NodePath("../Camera2D")']) + "\n"


def manifest(doc: dict[str, Any], ext: str) -> bytes:
    """What the Godot generator reads: kit size and, per room, kind / doors / weight and its scene path."""
    rooms = [{"name": r["name"], "kind": r["kind"], "doors": r["doors"], "weight": r["weight"],
              "scene": f"Rooms/{r['name']}.{ext}"} for r in doc["rooms"]]
    return json.dumps({"asset": doc["asset"], "style": doc["style"], "tile": doc["tile"], "cols": doc["cols"],
                       "rows": doc["rows"], "rooms": rooms}, indent=1).encode()


def godot_files(src: Source) -> tuple[Files, list[str]]:
    doc = _doc(src)
    a, side_view = src.asset, doc["style"] == "sideview"
    files: Files = [(f"Tiles/{a}_tiles.png", src.room_kit_files["atlas"]),
                    (f"{a}_tileset.tres", godot_tileset(doc, f"Tiles/{a}_tiles.png").encode()),
                    (GENERATOR_GD_FILE, GENERATOR_GD.encode()), (f"{a}_rooms.json", manifest(doc, "tscn")),
                    (f"{a}_demo.tscn", _demo_tscn(doc).encode())]
    for k in doc["prop_kinds"]:
        files.append((f"Props/{k['name']}.png", src.room_kit_files[f"prop:{k['name']}"]))
    for room in doc["rooms"]:
        files.append((f"Rooms/{room['name']}.tscn", _room_tscn(doc, room).encode()))
    notes = [f"- Rooms/*.tscn: {len(doc['rooms'])} phòng {doc['cols']}×{doc['rows']} ô ({doc['tile']}px, "
             f"{'side-view' if side_view else 'top-down'}). Mỗi phòng: TileMapLayer Ground / Shadow / Solid (va chạm theo "
             "ô), Doors/<N|E|S|W> (Marker2D giữa cửa), Plug_<hướng> (TileMapLayer tắt sẵn, bật để bịt cửa), Props, "
             "PlayerSpawn; metadata kind / doors / size.",
             f"- {GENERATOR_GD_FILE}: class RoomGenerator, ghép phòng theo cửa từ seed, không chồng, cửa không có phòng "
             "đối diện được bịt bằng Plug; hết chỗ ghép thì dừng và ghi lý do vào `report`. Dùng: "
             f"gen.generate(10, seed). {a}_demo.tscn là cảnh mẫu (đặt làm Main Scene rồi F5, tạo lại bằng generate()). "
             f"Nhiều bộ phòng trong một project dùng chung một {GENERATOR_GD_FILE} (cùng class_name)."]
    return files, notes


UNITY_DATA = cs_namespace("""using System;
using UnityEngine;

// Generated by AI Asset Studio: one room of a room kit (Rooms/*.prefab): its kind, the sides that have a door, the
// plug that closes each door nobody joins (plugs[i] closes doors[i]) and where the player starts.
public class RoomData : MonoBehaviour
{
    public string kind = "combat";
    public string[] doors = new string[0];
    public GameObject[] plugs = new GameObject[0];
    public float weight = 1f;
    public int cols, rows;
    public Vector2 spawn;

    public bool HasDoor(string side) { return Array.IndexOf(doors, side) >= 0; }

    public void Seal(string side)
    {
        int i = Array.IndexOf(doors, side);
        if (i >= 0 && i < plugs.Length && plugs[i] != null) plugs[i].SetActive(true);
    }
}
""")

UNITY_GENERATOR = cs_namespace("""using System;
using System.Collections.Generic;
using UnityEngine;

// Generated by AI Asset Studio: joins RoomData prefabs door to door into one map, the same map for the same seed.
// Rooms sit on a grid of room-sized cells (north = +y). The start room goes first; each next room attaches to an open
// door through a room with the matching door that still leaves a door open, and a boss takes the last slot when one
// fits. A door with no matching door across it is closed with the room's plug, so no door leads nowhere.
// Generation stops with a message in `report` when no door or no fitting room is left.
public class RoomGenerator : MonoBehaviour
{
    public RoomData[] rooms;
    public int roomCount = 10;
    public int seed = 0;
    public int maxAttemptsPerRoom = 40;
    public bool generateOnStart = true;
    public Camera fitCamera;
    public string report = "";

    struct Slot { public Vector2Int cell; public string side; }

    static readonly Dictionary<string, Vector2Int> Dirs = new Dictionary<string, Vector2Int>
    {
        { "N", new Vector2Int(0, 1) }, { "E", new Vector2Int(1, 0) }, { "S", new Vector2Int(0, -1) }, { "W", new Vector2Int(-1, 0) }
    };
    static readonly Dictionary<string, string> Opposite = new Dictionary<string, string>
    {
        { "N", "S" }, { "E", "W" }, { "S", "N" }, { "W", "E" }
    };

    readonly Dictionary<Vector2Int, int> plan = new Dictionary<Vector2Int, int>();
    readonly List<Vector2Int> order = new List<Vector2Int>();
    readonly List<GameObject> spawned = new List<GameObject>();

    public IReadOnlyList<Vector2Int> Cells { get { return order; } }

    void Start() { if (generateOnStart) Generate(roomCount, seed); }

    [ContextMenu("Generate")]
    void GenerateNow() { Generate(roomCount, seed); }

    public string Generate(int count, int newSeed)
    {
        Clear();
        if (rooms == null || rooms.Length == 0)
        {
            report = "RoomGenerator: no room prefabs assigned";
            Debug.LogError(report);
            return report;
        }
        var stopped = BuildPlan(count, new System.Random(newSeed));
        SpawnRooms();
        report = plan.Count + " of " + count + " rooms" + (stopped.Length > 0 ? ", stopped: " + stopped : "");
        if (stopped.Length > 0) Debug.LogWarning("RoomGenerator: " + stopped);
        FitCamera();
        return report;
    }

    public void Clear()
    {
        foreach (var go in spawned)
        {
            if (go == null) continue;
            if (Application.isPlaying) Destroy(go); else DestroyImmediate(go);
        }
        spawned.Clear();
        plan.Clear();
        order.Clear();
    }

    void Place(Vector2Int cell, int room)
    {
        plan[cell] = room;
        order.Add(cell);
    }

    string BuildPlan(int want, System.Random rng)
    {
        var starts = new List<int>();
        for (int i = 0; i < rooms.Length; i++) if (rooms[i].kind == "start") starts.Add(i);
        if (starts.Count == 0) return "the kit has no start room";
        Place(Vector2Int.zero, starts[rng.Next(starts.Count)]);
        int limit = Mathf.Max(1, want) * maxAttemptsPerRoom;
        for (int tries = 1; plan.Count < want; tries++)
        {
            if (tries > limit) return "gave up after " + limit + " attempts with " + plan.Count + " of " + want + " rooms";
            var open = OpenDoors();
            if (open.Count == 0) return "no open door left after " + plan.Count + " of " + want + " rooms";
            bool last = plan.Count == want - 1;
            var slots = last ? BossSlots(open) : open;
            if (slots.Count == 0) slots = open;
            var slot = slots[rng.Next(slots.Count)];
            var target = slot.cell + Dirs[slot.side];
            int pick = Pick(open, target, Opposite[slot.side], last, rng);
            if (pick >= 0) Place(target, pick);
        }
        return "";
    }

    List<Slot> BossSlots(List<Slot> open)
    {
        var list = new List<Slot>();
        foreach (var o in open)
            foreach (var r in rooms)
                if (r.kind == "boss" && r.HasDoor(Opposite[o.side])) { list.Add(o); break; }
        return list;
    }

    List<Slot> OpenDoors()
    {
        var list = new List<Slot>();
        foreach (var cell in order)
            foreach (var side in rooms[plan[cell]].doors)
                if (!plan.ContainsKey(cell + Dirs[side])) list.Add(new Slot { cell = cell, side = side });
        return list;
    }

    int Pick(List<Slot> open, Vector2Int target, string need, bool last, System.Random rng)
    {
        int closed = 0;
        foreach (var o in open) if (o.cell + Dirs[o.side] == target) closed++;
        int others = open.Count - closed;
        var pool = new List<int>();
        var bosses = new List<int>();
        for (int i = 0; i < rooms.Length; i++)
        {
            var r = rooms[i];
            if (r.kind == "start" || !r.HasDoor(need)) continue;
            int fresh = 0;
            foreach (var d in r.doors) if (!plan.ContainsKey(target + Dirs[d])) fresh++;
            if (!last && others + fresh == 0) continue;
            if (r.kind == "boss")
            {
                if (last) bosses.Add(i);
                continue;
            }
            pool.Add(i);
        }
        if (last && bosses.Count > 0) pool = bosses;
        if (pool.Count == 0) return -1;
        float total = 0f;
        foreach (var i in pool) total += Mathf.Max(0f, rooms[i].weight);
        float roll = (float)rng.NextDouble() * Mathf.Max(total, 0.0001f);
        foreach (var i in pool)
        {
            roll -= Mathf.Max(0f, rooms[i].weight);
            if (roll <= 0f) return i;
        }
        return pool[pool.Count - 1];
    }

    void SpawnRooms()
    {
        foreach (var cell in order)
        {
            var info = rooms[plan[cell]];
            var room = Instantiate(info, transform);
            room.transform.localPosition = new Vector3(cell.x * info.cols, cell.y * info.rows, 0f);
            room.name = info.name + "_" + cell.x + "_" + cell.y;
            foreach (var side in info.doors)
            {
                var across = cell + Dirs[side];
                bool linked = plan.ContainsKey(across) && rooms[plan[across]].HasDoor(Opposite[side]);
                if (!linked) room.Seal(side);
            }
            spawned.Add(room.gameObject);
        }
    }

    void FitCamera()
    {
        if (fitCamera == null || order.Count == 0) return;
        var lo = new Vector2Int(int.MaxValue, int.MaxValue);
        var hi = new Vector2Int(int.MinValue, int.MinValue);
        foreach (var c in order)
        {
            lo = new Vector2Int(Mathf.Min(lo.x, c.x), Mathf.Min(lo.y, c.y));
            hi = new Vector2Int(Mathf.Max(hi.x, c.x), Mathf.Max(hi.y, c.y));
        }
        float w = (hi.x - lo.x + 1) * rooms[0].cols, h = (hi.y - lo.y + 1) * rooms[0].rows;
        var center = transform.position + new Vector3(lo.x * rooms[0].cols + w / 2f, lo.y * rooms[0].rows + h / 2f, -10f);
        fitCamera.orthographic = true;
        fitCamera.transform.position = center;
        fitCamera.orthographicSize = Mathf.Max(h / 2f, w / (2f * Mathf.Max(0.1f, fitCamera.aspect))) * 1.05f;
    }
}
""")

UNITY_BUILDER = r"""// Generated by AI Asset Studio: builds the room prefabs (Rooms/*.prefab) and __ASSET___RoomKit.unity (a RoomGenerator
// wired to them) from roomkit___ID__.json once the folder is imported; menu Asset Studio > Room Kit __ASSET__ (__ID__)
// rebuilds and opens it.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Tilemaps;

public class RoomKitBuilder__ID__ : AssetPostprocessor
{
    const string Json = "roomkit___ID__.json";

    [Serializable] class PlugDoc { public string side; public int[] cells; }
    [Serializable] class PropDoc { public string sprite; public float x, y; public float[] poly; }
    [Serializable] class RoomDoc
    {
        public string name, kind;
        public string[] doors;
        public float weight, spawnX, spawnY;
        public int[] ground, shadowLayer, solidLayer;
        public PlugDoc[] plugs;
        public PropDoc[] props;
    }
    [Serializable] class Doc
    {
        public string id, asset, style, tiles;
        public int cols, rows, tileCount;
        public int[] solid;
        public RoomDoc[] rooms;
    }

    static void OnPostprocessAllAssets(string[] imported, string[] deleted, string[] moved, string[] movedFrom)
    {
        foreach (var p in imported)
        {
            if (!p.EndsWith("/" + Json)) continue;
            var path = p;
            EditorApplication.delayCall += () => Build(path, false);
        }
    }

    static string Find()
    {
        return AssetDatabase.FindAssets("roomkit___ID__ t:TextAsset").Select(AssetDatabase.GUIDToAssetPath)
            .FirstOrDefault(p => p.EndsWith("/" + Json));
    }

    public static void BuildAll()
    {
        var p = Find();
        if (p != null) Build(p, false);
    }

    [MenuItem("Asset Studio/Room Kit __ASSET__ (__ID__)")]
    public static void Open()
    {
        var p = Find();
        if (p == null || !Build(p, true)) return;
        var doc = JsonUtility.FromJson<Doc>(AssetDatabase.LoadAssetAtPath<TextAsset>(p).text);
        EditorSceneManager.OpenScene(Dir(p) + "/" + doc.asset + "_RoomKit.unity", OpenSceneMode.Single);
    }

    static string Dir(string p) { return Path.GetDirectoryName(p).Replace('\\', '/'); }

    static int Order(float y) { return Mathf.RoundToInt(-y * 10f); }

    static Sprite LoadSprite(string path)
    {
        return AssetDatabase.LoadAllAssetsAtPath(path).OfType<Sprite>().FirstOrDefault();
    }

    public static bool Build(string jsonPath, bool open)
    {
        var text = AssetDatabase.LoadAssetAtPath<TextAsset>(jsonPath);
        if (text == null) return false;
        var doc = JsonUtility.FromJson<Doc>(text.text);
        var dir = Dir(jsonPath);
        bool side = doc.style == "sideview";
        var sprites = AssetDatabase.LoadAllAssetsAtPath(dir + "/" + doc.tiles).OfType<Sprite>().ToDictionary(s => s.name);
        if (sprites.Count < doc.tileCount)
        {
            Debug.LogWarning("Asset Studio: " + doc.asset + " is not imported yet");
            return false;
        }
        // NewScene(Single) unloads unreferenced assets, so it must come before the Tile objects are made.
        var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
        if (!AssetDatabase.IsValidFolder(dir + "/Tiles/Assets")) AssetDatabase.CreateFolder(dir + "/Tiles", "Assets");
        if (!AssetDatabase.IsValidFolder(dir + "/Rooms")) AssetDatabase.CreateFolder(dir, "Rooms");
        var solid = new HashSet<int>(doc.solid);
        var tiles = new Tile[doc.tileCount];
        for (int i = 0; i < doc.tileCount; i++)
        {
            var path = dir + "/Tiles/Assets/tile_" + i + ".asset";
            var t = AssetDatabase.LoadAssetAtPath<Tile>(path);
            if (t == null)
            {
                t = ScriptableObject.CreateInstance<Tile>();
                AssetDatabase.CreateAsset(t, path);
            }
            t.sprite = sprites["tile_" + i];
            t.colliderType = solid.Contains(i) ? Tile.ColliderType.Grid : Tile.ColliderType.None;
            EditorUtility.SetDirty(t);
            tiles[i] = t;
        }
        AssetDatabase.SaveAssets();

        var prefabs = new List<RoomData>();
        foreach (var rd in doc.rooms)
        {
            var root = new GameObject(rd.name);
            var grid = new GameObject("Grid");
            grid.transform.SetParent(root.transform, false);
            grid.AddComponent<Grid>();
            Layer(grid.transform, "Ground", rd.ground, tiles, doc, side ? -5 : -1000, false);
            Layer(grid.transform, "Shadow", rd.shadowLayer, tiles, doc, side ? -4 : -999, false);
            Layer(grid.transform, "Solid", rd.solidLayer, tiles, doc, side ? 0 : -998, true);
            var plugs = new List<GameObject>();
            foreach (var pd in rd.plugs) plugs.Add(Plug(grid.transform, pd, tiles, doc));
            var props = new GameObject("Props");
            props.transform.SetParent(root.transform, false);
            for (int i = 0; i < rd.props.Length; i++)
            {
                var p = rd.props[i];
                var go = new GameObject(Path.GetFileNameWithoutExtension(p.sprite) + i);
                go.transform.SetParent(props.transform, false);
                go.transform.localPosition = new Vector3(p.x, p.y, 0f);
                var sr = go.AddComponent<SpriteRenderer>();
                sr.sprite = LoadSprite(dir + "/" + p.sprite);
                sr.sortingOrder = side ? 5 : Order(p.y);
                var poly = go.AddComponent<PolygonCollider2D>();
                var pts = new Vector2[p.poly.Length / 2];
                for (int k = 0; k < pts.Length; k++) pts[k] = new Vector2(p.poly[2 * k], p.poly[2 * k + 1]);
                poly.points = pts;
            }
            var data = root.AddComponent<RoomData>();
            data.kind = rd.kind;
            data.doors = rd.doors;
            data.plugs = plugs.ToArray();
            data.weight = rd.weight;
            data.cols = doc.cols;
            data.rows = doc.rows;
            data.spawn = new Vector2(rd.spawnX, rd.spawnY);
            var saved = PrefabUtility.SaveAsPrefabAsset(root, dir + "/Rooms/" + rd.name + ".prefab");
            UnityEngine.Object.DestroyImmediate(root);
            prefabs.Add(saved.GetComponent<RoomData>());
        }

        var genGo = new GameObject("RoomGenerator");
        var gen = genGo.AddComponent<RoomGenerator>();
        gen.rooms = prefabs.ToArray();
        var camGo = new GameObject("Main Camera");
        camGo.tag = "MainCamera";
        var cam = camGo.AddComponent<Camera>();
        cam.orthographic = true;
        cam.clearFlags = CameraClearFlags.SolidColor;
        cam.backgroundColor = new Color(0.07f, 0.07f, 0.1f);
        camGo.transform.position = new Vector3(doc.cols / 2f, doc.rows / 2f, -10f);
        cam.orthographicSize = doc.rows / 2f;
        gen.fitCamera = cam;
        EditorSceneManager.SaveScene(scene, dir + "/" + doc.asset + "_RoomKit.unity");
        Debug.Log("Asset Studio: built " + doc.rooms.Length + " room prefabs and " + doc.asset + "_RoomKit.unity");
        return true;
    }

    static void Layer(Transform grid, string name, int[] ids, Tile[] tiles, Doc doc, int order, bool collide)
    {
        if (ids == null || ids.All(v => v < 0)) return;
        var go = new GameObject(name);
        go.transform.SetParent(grid, false);
        var tm = go.AddComponent<Tilemap>();
        go.AddComponent<TilemapRenderer>().sortingOrder = order;
        for (int i = 0; i < ids.Length; i++)
            if (ids[i] >= 0) tm.SetTile(new Vector3Int(i % doc.cols, doc.rows - 1 - i / doc.cols, 0), tiles[ids[i]]);
        if (collide) go.AddComponent<TilemapCollider2D>();
    }

    static GameObject Plug(Transform grid, PlugDoc p, Tile[] tiles, Doc doc)
    {
        var go = new GameObject("Plug_" + p.side);
        go.transform.SetParent(grid, false);
        var tm = go.AddComponent<Tilemap>();
        go.AddComponent<TilemapRenderer>().sortingOrder = 50;
        for (int k = 0; k + 2 < p.cells.Length; k += 3)
            tm.SetTile(new Vector3Int(p.cells[k], doc.rows - 1 - p.cells[k + 1], 0), tiles[p.cells[k + 2]]);
        go.AddComponent<TilemapCollider2D>();
        go.SetActive(false);
        return go;
    }
}
"""


def unity_doc(src: Source) -> dict[str, Any]:
    from studio_core.ops.playable_scene import layer_tiles

    doc = src.room_kit
    assert doc is not None
    t, rows = doc["tile"], doc["rows"]
    rooms = []
    for r in doc["rooms"]:
        props = []
        for p in r["props"]:
            k = doc["prop_kinds"][p["kind"]]
            props.append({"sprite": f"Props/{k['name']}.png", "x": round(p["pos"][0] / t, 4),
                          "y": round(rows - p["pos"][1] / t, 4),
                          "poly": [round(v / t * (-1 if i % 2 else 1), 4) for xy in k["collider"]
                                   for i, v in enumerate(xy)]})
        sx, sy = r["spawn"]
        rooms.append({"name": r["name"], "kind": r["kind"], "doors": r["doors"], "weight": r["weight"],
                      "spawnX": sx + 0.5, "spawnY": round(rows - sy - 0.5, 4),
                      "ground": layer_tiles(r, "Ground"), "shadowLayer": layer_tiles(r, "Shadow"),
                      "solidLayer": layer_tiles(r, "Solid"),
                      "plugs": [{"side": s, "cells": [v for cell in r["plugs"][s] for v in cell]} for s in r["doors"]],
                      "props": props})
    return {"id": _uid(src), "asset": src.asset, "style": doc["style"], "tiles": f"Tiles/{src.asset}_tiles.png",
            "cols": doc["cols"], "rows": rows, "tileCount": doc["tileset"]["count"], "solid": doc["tileset"]["solid"],
            "rooms": rooms}


def unity_files(src: Source) -> tuple[Files, list[str]]:
    from studio_core.export import unity
    from studio_core.ops.playable_scene import ATLAS_COLS

    doc = src.room_kit
    assert doc is not None
    a, t, uid = src.asset, doc["tile"], _uid(src)
    pixel = t <= 16
    count = doc["tileset"]["count"]
    rows_px = -(-count // ATLAS_COLS) * t
    tiles = Layout(width=ATLAS_COLS * t, height=rows_px, frame_w=t, frame_h=t, pivot=(t / 2, t / 2),
                   cells=[Cell(f"tile_{i}", (i % ATLAS_COLS) * t, (i // ATLAS_COLS) * t, t, t) for i in range(count)])
    rel = f"Tiles/{a}_tiles.png"
    files: Files = [(rel, src.room_kit_files["atlas"]),
                    (f"{rel}.meta", unity.texture_meta(f"{a}_tiles", unity.guid(src.uid, a, rel), tiles,
                                                        (tiles.width, rows_px), t, pixel).encode())]
    for k in doc["prop_kinds"]:
        rel = f"Props/{k['name']}.png"
        lay = Layout(width=k["w"], height=k["h"], frame_w=k["w"], frame_h=k["h"], pivot=(k["w"] / 2, float(k["h"])),
                     cells=[Cell(k["name"], 0, 0, k["w"], k["h"])])
        files += [(rel, src.room_kit_files[f"prop:{k['name']}"]),
                  (f"{rel}.meta", unity.texture_meta(k["name"], unity.guid(src.uid, a, rel), lay, (k["w"], k["h"]), t,
                                                      pixel).encode())]
    for name, code, g in (("RoomData", UNITY_DATA, DATA_GUID), ("RoomGenerator", UNITY_GENERATOR, GENERATOR_GUID)):
        files += [(f"Scripts/{name}.cs", code.encode()), (f"Scripts/{name}.cs.meta", _script_meta(g))]
    rel = f"Editor/RoomKitBuilder{uid}.cs"
    builder = UNITY_BUILDER.replace("__ID__", uid).replace("__ASSET__", a)
    files += [(rel, cs_namespace(builder).encode()), (f"{rel}.meta", _script_meta(unity.guid(src.uid, a, rel)))]
    rel = f"roomkit_{uid}.json"
    files += [(rel, json.dumps(unity_doc(src), indent=1).encode()),
              (f"{rel}.meta", _text_meta(unity.guid(src.uid, a, rel)))]
    notes = [f"- Mở xong project, Editor/RoomKitBuilder{uid}.cs tự dựng Rooms/*.prefab ({len(doc['rooms'])} phòng "
             f"{doc['cols']}×{doc['rows']} ô: Grid + Tilemap Ground / Shadow / Solid, Plug_<hướng> tắt sẵn, Props, "
             f"component RoomData) và {a}_RoomKit.unity (RoomGenerator đã gán sẵn các prefab). Menu Asset Studio > "
             f"Room Kit {a} ({uid}) dựng lại và mở scene; bấm Play hoặc chuột phải component > Generate.",
             "- Scripts/RoomData.cs + RoomGenerator.cs (namespace AssetStudio, GUID cố định): RoomGenerator.Generate(count, "
             "seed) ghép phòng theo cửa, không chồng, cửa không có phòng đối diện được bịt bằng plug; hết chỗ ghép thì "
             "dừng và ghi lý do vào `report`. Nhiều bộ phòng trong một project dùng chung một bản" + SHARED_SCRIPT_NOTE + "."]
    return files, notes


def extra_files(src: Source) -> Files:
    """Tiled / LDtk copy of every room next to the engine files, with the atlas and props they point at."""
    from studio_core.ops.room_kit import ldtk_with_doors, tmx_with_doors

    doc = _doc(src)
    files: Files = [(f"Tiled/{doc['asset']}_tiles.png", src.room_kit_files["atlas"])]
    for r in doc["rooms"]:
        files += [(f"Tiled/{r['name']}.tmx", tmx_with_doors(doc, r).encode()),
                  (f"Tiled/{r['name']}.ldtk", ldtk_with_doors(doc, r).encode())]
    files += [(f"Tiled/Props/{k['name']}.png", src.room_kit_files[f"prop:{k['name']}"]) for k in doc["prop_kinds"]]
    return files
