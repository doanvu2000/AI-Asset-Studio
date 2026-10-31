"""Playable scene export (op.playable_scene): a Godot 4 scene (TileMapLayer ground + solid with tile collision,
CharacterBody2D player on the 8-direction SpriteFrames, StaticBody2D props with traced colliders, Parallax2D,
Camera2D) and a Unity folder whose editor builder makes <asset>.unity + <asset>_SampleRoom.prefab (Grid, Tilemaps,
Rigidbody2D player on the exported Animator, props, follow camera), plus the Tiled / LDtk copies. `engine_check`
opens the export in the real engines and drives the player; `open_project` stages it for the editor GUI."""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from studio_core.contracts import BlobStore, StepRow
from studio_core.export.sheet import Cell, Layout, cs_namespace, stable_hex
from studio_core.export.source import Files, Source

GODOT_VIEW = (1280, 720)
PLAYTEST_SIZE = (960, 540)
PLAYTEST_TIMEOUT_S = 120.0


def playable_set(step: StepRow, blobs: BlobStore) -> tuple[dict[str, Any] | None, dict[str, Path]]:
    """op.playable_scene's playable_scene.json and its blobs, also when a gate passed it through."""
    from studio_core.ops.playable_scene import SCENE_FILE, SCENE_TYPE

    for refs in step.outputs.values():
        for ref in refs:
            if (ref.name or "") != SCENE_FILE:
                continue
            try:
                doc = json.loads(blobs.path(ref.hash).read_bytes())
                return (doc, scene_files(doc, blobs.path)) if doc.get("type") == SCENE_TYPE else (None, {})
            except (OSError, ValueError, KeyError, TypeError):
                return None, {}
    return None, {}


def scene_files(doc: dict[str, Any], path_of: Any) -> dict[str, Path]:
    files = {"atlas": path_of(doc["tileset"]["hash"]), "sheet": path_of(doc["player"]["sheet"])}
    for k in doc["prop_kinds"]:
        files[f"prop:{k['name']}"] = path_of(k["hash"])
    for b in doc["background"]:
        files[f"bg:{b['name']}"] = path_of(b["hash"])
    missing = [k for k, p in files.items() if not Path(p).is_file()]
    if missing:
        raise ValueError(f"playable scene blobs missing: {missing}")
    return files


def _uid(src: Source) -> str:
    return stable_hex("playable", src.uid, src.asset)[:8]


def _player_source(src: Source, engine_ppu: float) -> Source:
    p = src.playable["player"]  # type: ignore[index]
    return Source(asset=p["name"], uid=f"{src.uid}/player", sheet=src.playable_files["sheet"], meta=p["meta"],
                  clip_hint=p.get("clip_hint") or "anim", directional_rows=bool(p.get("directional_rows")),
                  ppu=engine_ppu, pixel=bool(p.get("pixel")))


def _n(v: float) -> str:
    s = f"{float(v):.6g}"
    return s if any(ch in s for ch in ".e") else f"{s}.0"


def _vec(x: float, y: float) -> str:
    return f"Vector2({_n(x)}, {_n(y)})"


def tile_map_data(ids: list[int], cols: int) -> str:
    """TileMapLayer.tile_map_data: format 0 header, then per cell int16 x, y, source, atlas x, atlas y, alternative."""
    from studio_core.ops.playable_scene import ATLAS_COLS

    out = [0, 0]
    for i, tid in enumerate(ids):
        if tid < 0:
            continue
        for v in (i % cols, i // cols, 0, tid % ATLAS_COLS, tid // ATLAS_COLS, 0):
            out += [v & 0xFF, (v >> 8) & 0xFF]
    return "PackedByteArray(" + ", ".join(map(str, out)) + ")"


def godot_tileset(doc: dict[str, Any], atlas: str) -> str:
    from studio_core.ops.playable_scene import ATLAS_COLS

    t, h = doc["tile"], doc["tile"] / 2
    solid = set(doc["tileset"]["solid"])
    lines = ['[gd_resource type="TileSet" load_steps=3 format=3]', '',
             f'[ext_resource type="Texture2D" path="{atlas}" id="1_atlas"]', '',
             '[sub_resource type="TileSetAtlasSource" id="TileSetAtlasSource_1"]', 'texture = ExtResource("1_atlas")',
             f"texture_region_size = Vector2i({t}, {t})"]
    square = f"PackedVector2Array({_n(-h)}, {_n(-h)}, {_n(h)}, {_n(-h)}, {_n(h)}, {_n(h)}, {_n(-h)}, {_n(h)})"
    for tid in range(doc["tileset"]["count"]):
        k = f"{tid % ATLAS_COLS}:{tid // ATLAS_COLS}/0"
        lines.append(f"{k} = 0")
        if tid in solid:
            lines.append(f"{k}/physics_layer_0/polygon_0/points = {square}")
    lines += ['', '[resource]', f"tile_size = Vector2i({t}, {t})", "physics_layer_0/collision_layer = 1",
              'sources/0 = SubResource("TileSetAtlasSource_1")']
    return "\n".join(lines) + "\n"


PLAYER_GD = """extends CharacterBody2D
## Topdown: walk 8 ways. Sideview: run and jump (Space / Up / W). Arrows or WASD. `test_input` drives the player
## without a keyboard (the studio's engine check, AI agents).

@export var speed := 144.0
@export var sideview := false
@export var gravity := 0.0
@export var jump_speed := 0.0
var test_input := Vector2.ZERO
var facing := Vector2.DOWN

@onready var sprite: AnimatedSprite2D = get_node_or_null("Sprite")


func read_input() -> Vector2:
	if test_input != Vector2.ZERO:
		return test_input
	var v := Input.get_vector("ui_left", "ui_right", "ui_up", "ui_down")
	v.x += float(Input.is_physical_key_pressed(KEY_D)) - float(Input.is_physical_key_pressed(KEY_A))
	v.y += float(Input.is_physical_key_pressed(KEY_S)) - float(Input.is_physical_key_pressed(KEY_W))
	if Input.is_physical_key_pressed(KEY_SPACE):
		v.y = -1.0
	return v.limit_length(1.0)


func _physics_process(delta: float) -> void:
	var dir := read_input()
	if sideview:
		velocity.x = dir.x * speed
		velocity.y += gravity * delta
		if dir.y < -0.5 and is_on_floor():
			velocity.y = -jump_speed
	else:
		velocity = dir * speed
	move_and_slide()
	_animate(Vector2(dir.x, 0.0) if sideview else dir)


func _animate(dir: Vector2) -> void:
	if sprite == null or sprite.sprite_frames == null:
		return
	var moving := dir.length_squared() > 0.01
	if moving:
		facing = dir
	var clip := "walk" if moving and sprite.sprite_frames.has_animation("walk_S") else "idle"
	if sprite.has_method("face"):
		sprite.face(clip, facing)
"""

PLAYTEST_GD = """extends SceneTree
## Studio engine check: instance the scene, push the player for N physics frames, report the distance it travelled
## and save what the player's camera sees.


func _init() -> void:
	var args := OS.get_cmdline_user_args()
	var scene: PackedScene = load(args[0])
	if scene == null:
		printerr("PLAYTEST_FAIL cannot load ", args[0])
		quit(3)
		return
	var frames := int(args[2])
	var sideview := args.size() > 3 and args[3] == "sideview"
	var root := scene.instantiate()
	get_root().add_child(root)
	var player = root.find_child("Player", true, false)
	if player == null or not player is CharacterBody2D:
		printerr("PLAYTEST_FAIL no CharacterBody2D named Player")
		quit(4)
		return
	for i in 10:
		await physics_frame
	var start: Vector2 = player.global_position
	var last := start
	var travel := 0.0
	for i in frames:
		var dir := Vector2.RIGHT if i * 2 < frames else (Vector2.DOWN if not sideview else Vector2.LEFT)
		if sideview and i * 4 >= frames and i * 4 < frames + 4:
			dir = Vector2(1, -1)
		player.test_input = dir
		await physics_frame
		travel += player.global_position.distance_to(last)
		last = player.global_position
	player.test_input = Vector2.ZERO
	for i in 3:
		await process_frame
	var img := get_root().get_texture().get_image()
	img.save_png(args[1])
	print("PLAYTEST travel=%.2f dx=%.2f dy=%.2f floor=%s anim=%s" % [travel, last.x - start.x, last.y - start.y,
		str(player.is_on_floor()), str(player.get_node("Sprite").animation) if player.has_node("Sprite") else ""])
	quit(0)
"""


def godot_files(src: Source) -> tuple[Files, list[str]]:
    from studio_core.export import godot

    doc = src.playable
    assert doc is not None
    a, t, cols, rows = src.asset, doc["tile"], doc["cols"], doc["rows"]
    side = doc["style"] == "sideview"
    pl = doc["player"]
    files: Files = [(f"Tiles/{a}_tiles.png", src.playable_files["atlas"]),
                    (f"{a}_tileset.tres", godot_tileset(doc, f"Tiles/{a}_tiles.png").encode()),
                    ("player.gd", PLAYER_GD.encode())]
    for rel, data in godot.export(_player_source(src, 100.0)):
        files.append((f"Player/{rel}", data))
    ext = ['[ext_resource type="TileSet" path="{}" id="1_tiles"]'.format(f"{a}_tileset.tres"),
           '[ext_resource type="Script" path="player.gd" id="2_player"]',
           f'[ext_resource type="PackedScene" path="Player/{pl["name"]}.tscn" id="3_body"]']
    for i, k in enumerate(doc["prop_kinds"]):
        files.append((f"Props/{k['name']}.png", src.playable_files[f"prop:{k['name']}"]))
        ext.append(f'[ext_resource type="Texture2D" path="Props/{k["name"]}.png" id="p{i}"]')
    for i, b in enumerate(doc["background"]):
        files.append((f"Background/{b['name']}.png", src.playable_files[f"bg:{b['name']}"]))
        ext.append(f'[ext_resource type="Texture2D" path="Background/{b["name"]}.png" id="b{i}"]')
    col = pl["collider"]
    phys = doc["physics"]
    filt = "texture_filter = 1" if pl.get("pixel") else None
    lines = ["[gd_scene format=3]", "", *ext, "",
             '[sub_resource type="RectangleShape2D" id="RectangleShape2D_player"]',
             f"size = {_vec(col['w'], col['h'])}", "", f'[node name="{a}" type="Node2D"]']
    if filt:
        lines.append(filt)
    if doc["background"]:
        lines += ["", '[node name="Background" type="Node2D" parent="."]', "z_index = -10"]
        for i, b in enumerate(doc["background"]):
            k = rows * t / b["size"][1]
            w = b["size"][0] * k
            lines += ["", f'[node name="{b["name"]}" type="Parallax2D" parent="Background"]',
                      f"scroll_scale = {_vec(b['factor'], 1.0 if side else b['factor'])}",
                      f"repeat_size = {_vec(w, 0)}", "repeat_times = 3",
                      "", f'[node name="Sprite" type="Sprite2D" parent="Background/{b["name"]}"]',
                      f'texture = ExtResource("b{i}")', "centered = false", f"scale = {_vec(k, k)}"]
    for layer in doc["layers"]:
        if all(v < 0 for v in layer["tiles"]):
            continue
        lines += ["", f'[node name="{layer["name"]}" type="TileMapLayer" parent="."]',
                  f"tile_map_data = {tile_map_data(layer['tiles'], cols)}", 'tile_set = ExtResource("1_tiles")']
        if not layer["collide"]:
            lines.append("collision_enabled = false")
    lines += ["", '[node name="Actors" type="Node2D" parent="."]']
    if not side:
        lines.append("y_sort_enabled = true")
    for n, p in enumerate(doc["props"]):
        k = doc["prop_kinds"][p["kind"]]
        node = f"{k['name']}{n}"
        poly = ", ".join(_n(v) for xy in k["collider"] for v in xy)
        lines += ["", f'[node name="{node}" type="StaticBody2D" parent="Actors"]',
                  f"position = {_vec(*p['pos'])}",
                  "", f'[node name="Sprite" type="Sprite2D" parent="Actors/{node}"]',
                  f'texture = ExtResource("p{p["kind"]}")', "centered = false",
                  f"offset = {_vec(-k['w'] / 2, -k['h'])}",
                  "", f'[node name="Collision" type="CollisionPolygon2D" parent="Actors/{node}"]',
                  f"polygon = PackedVector2Array({poly})"]
    view = doc["camera"]["view_rows"] * t
    zoom = GODOT_VIEW[1] / view
    lines += ["", '[node name="Player" type="CharacterBody2D" parent="Actors"]', f"position = {_vec(*pl['pos'])}",
              'script = ExtResource("2_player")', f"speed = {_n(phys['speed'])}",
              f"sideview = {'true' if side else 'false'}", f"gravity = {_n(phys['gravity'])}",
              f"jump_speed = {_n(phys['jump'])}"]
    if not side:
        lines.append("motion_mode = 1")
    lines += ["", '[node name="Sprite" parent="Actors/Player" instance=ExtResource("3_body")]',
              "", '[node name="Collision" type="CollisionShape2D" parent="Actors/Player"]',
              f"position = {_vec(0, col['y'])}", 'shape = SubResource("RectangleShape2D_player")',
              "", '[node name="Camera2D" type="Camera2D" parent="Actors/Player"]', f"zoom = {_vec(zoom, zoom)}",
              "limit_left = 0", "limit_top = 0", f"limit_right = {cols * t}", f"limit_bottom = {rows * t}",
              "position_smoothing_enabled = true"]
    files.append((f"{a}.tscn", ("\n".join(lines) + "\n").encode()))
    notes = [f"- {a}.tscn: màn chơi thử {cols}×{rows} ô ({t}px, {'side-view' if side else 'top-down'}): TileMapLayer "
             "Ground (không va chạm) + Solid (va chạm theo ô), Actors (y-sort) gồm Player (CharacterBody2D + "
             f"player.gd, Camera2D bám theo) và prop StaticBody2D có CollisionPolygon2D vẽ theo alpha. "
             "Phím mũi tên / WASD, Space hoặc lên để nhảy (side-view). Đặt làm Main Scene rồi F5.",
             f"- Player/: nhân vật {pl['name']} (SpriteFrames 8 hướng + asset_studio_sprite.gd chọn hướng).",
             f"- {a}_tileset.tres + Tiles/{a}_tiles.png: TileSet dùng chung cho cả hai layer, ô tường có polygon va "
             "chạm (physics layer 0)."]
    if doc["background"]:
        notes.append("- Background/: Parallax2D theo thứ tự xa → gần (scroll_scale nhỏ = xa).")
    return files, notes


def _script_meta(g: str) -> bytes:
    return (f"fileFormatVersion: 2\nguid: {g}\nMonoImporter:\n  externalObjects: {{}}\n  serializedVersion: 2\n"
            "  defaultReferences: []\n  executionOrder: 0\n  icon: {instanceID: 0}\n  userData: \n"
            "  assetBundleName: \n  assetBundleVariant: \n").encode()


def _text_meta(g: str) -> bytes:
    return (f"fileFormatVersion: 2\nguid: {g}\nTextScriptImporter:\n  externalObjects: {{}}\n  userData: \n"
            "  assetBundleName: \n  assetBundleVariant: \n").encode()


UNITY_RUNTIME = {
    "PlayerController": """// Generated by AI Asset Studio. Topdown: walk 8 ways; sideview: run + jump (Space / Up / W). Arrows or WASD.
// testInput drives the player without a keyboard (the studio's engine check, AI agents).
using UnityEngine;

[RequireComponent(typeof(Rigidbody2D))]
public class PlayerController : MonoBehaviour
{
    public float speed = 4.5f;
    public bool sideview;
    public float jumpSpeed = 14f;
    public Animator animator;
    public SpriteRenderer body;
    public bool hasWalk;
    public Vector2 testInput;
    Rigidbody2D rb;
    Vector2 facing = Vector2.down;
    string clip = "";
    readonly RaycastHit2D[] hits = new RaycastHit2D[4];

    void Awake() { rb = GetComponent<Rigidbody2D>(); }

    public Vector2 ReadInput()
    {
        if (testInput != Vector2.zero) return testInput;
#if ENABLE_INPUT_SYSTEM && !ENABLE_LEGACY_INPUT_MANAGER
        var k = UnityEngine.InputSystem.Keyboard.current;
        if (k == null) return Vector2.zero;
        var v = new Vector2((k.dKey.isPressed || k.rightArrowKey.isPressed ? 1 : 0) - (k.aKey.isPressed || k.leftArrowKey.isPressed ? 1 : 0),
            (k.wKey.isPressed || k.upArrowKey.isPressed || k.spaceKey.isPressed ? 1 : 0) - (k.sKey.isPressed || k.downArrowKey.isPressed ? 1 : 0));
#else
        var v = new Vector2(Input.GetAxisRaw("Horizontal"), Input.GetAxisRaw("Vertical"));
        if (Input.GetKey(KeyCode.Space)) v.y = 1f;
#endif
        return Vector2.ClampMagnitude(v, 1f);
    }

    void FixedUpdate() { Tick(); }

    void LateUpdate()
    {
        if (!sideview && body != null) body.sortingOrder = Mathf.RoundToInt(-transform.position.y * 10f);
    }

    public bool Grounded()
    {
        return rb.Cast(Vector2.down, hits, 0.06f) > 0;
    }

    public void Tick()
    {
        if (rb == null) rb = GetComponent<Rigidbody2D>();
        var input = ReadInput();
        if (sideview)
        {
            var v = rb.linearVelocity;
            v.x = input.x * speed;
            if (input.y > 0.5f && Grounded() && v.y <= 0.01f) v.y = jumpSpeed;
            rb.linearVelocity = v;
            Animate(new Vector2(input.x, 0f));
        }
        else
        {
            rb.linearVelocity = input * speed;
            Animate(input);
        }
    }

    void Animate(Vector2 dir)
    {
        if (animator == null || animator.runtimeAnimatorController == null) return;
        bool moving = dir.sqrMagnitude > 0.01f;
        if (moving) facing = dir.normalized;
        animator.SetFloat("MoveX", facing.x);
        animator.SetFloat("MoveY", facing.y);
        var want = moving && hasWalk ? "walk" : "idle";
        if (want != clip && animator.HasState(0, Animator.StringToHash(want)))
        {
            animator.Play(want);
            clip = want;
        }
    }
}
""",
    "FollowCamera": """// Generated by AI Asset Studio: follows the player, kept inside the level bounds.
using UnityEngine;

public class FollowCamera : MonoBehaviour
{
    public Transform target;
    public Vector2 min;
    public Vector2 max;
    public float smooth = 10f;

    void LateUpdate()
    {
        if (target == null) return;
        var cam = GetComponent<Camera>();
        float h = cam.orthographicSize, w = h * cam.aspect;
        var p = Vector3.Lerp(transform.position, target.position, 1f - Mathf.Exp(-smooth * Time.deltaTime));
        p.x = max.x - min.x <= 2f * w ? (min.x + max.x) / 2f : Mathf.Clamp(p.x, min.x + w, max.x - w);
        p.y = max.y - min.y <= 2f * h ? (min.y + max.y) / 2f : Mathf.Clamp(p.y, min.y + h, max.y - h);
        p.z = -10f;
        transform.position = p;
    }
}
""",
    "ParallaxLayer": """// Generated by AI Asset Studio: factor 0 stays on screen, 1 scrolls with the level.
using UnityEngine;

public class ParallaxLayer : MonoBehaviour
{
    public float factor = 0.3f;
    public Transform cam;
    float startX;

    void Start()
    {
        startX = transform.position.x;
        if (cam == null && Camera.main != null) cam = Camera.main.transform;
    }

    void LateUpdate()
    {
        if (cam == null) return;
        var p = transform.position;
        p.x = startX + cam.position.x * (1f - factor);
        transform.position = p;
    }
}
""",
}

UNITY_BUILDER = """// Generated by AI Asset Studio: builds __ASSET__.unity and __ASSET___SampleRoom.prefab (Grid + Tilemaps, player,
// props, follow camera) from playable___ID__.json once the folder is imported; menu Asset Studio > Playable Scene
// __ASSET__ (__ID__) rebuilds and opens it.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Tilemaps;
using Runtime = AssetStudio.Playable__ID__;

public class PlayableSceneBuilder__ID__ : AssetPostprocessor
{
    const string Json = "playable___ID__.json";

    [Serializable] class PlayerDoc { public string prefab; public float x, y, colW, colH, colY, speed, gravityScale, jump, scale; public bool walk; }
    [Serializable] class PropDoc { public string sprite; public float x, y; public float[] poly; }
    [Serializable] class BgDoc { public string sprite; public float factor, scale, width; }
    [Serializable] class Doc
    {
        public string id, asset, style, tiles;
        public int cols, rows, tileCount, viewRows;
        public int[] solid, ground, solidLayer;
        public PlayerDoc player;
        public PropDoc[] props;
        public BgDoc[] background;
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
        return AssetDatabase.FindAssets("playable___ID__ t:TextAsset").Select(AssetDatabase.GUIDToAssetPath)
            .FirstOrDefault(p => p.EndsWith("/" + Json));
    }

    public static void BuildAll()
    {
        var p = Find();
        if (p != null) Build(p, true);
    }

    [MenuItem("Asset Studio/Playable Scene __ASSET__ (__ID__)")]
    public static void Open()
    {
        var p = Find();
        if (p == null || !Build(p, false)) return;
        var doc = JsonUtility.FromJson<Doc>(AssetDatabase.LoadAssetAtPath<TextAsset>(p).text);
        EditorSceneManager.OpenScene(Dir(p) + "/" + doc.asset + ".unity", OpenSceneMode.Single);
        var view = SceneView.lastActiveSceneView;
        if (view != null)
        {
            view.in2DMode = true;
            view.LookAt(new Vector3(doc.cols / 2f, doc.rows / 2f, 0f), Quaternion.identity, Mathf.Max(doc.cols, doc.rows) / 2f);
        }
    }

    static string Dir(string p) { return Path.GetDirectoryName(p).Replace('\\\\', '/'); }

    static int Order(float y) { return Mathf.RoundToInt(-y * 10f); }

    static Sprite LoadSprite(string path)
    {
        return AssetDatabase.LoadAllAssetsAtPath(path).OfType<Sprite>().FirstOrDefault();
    }

    public static bool Build(string jsonPath, bool selfTest)
    {
        var text = AssetDatabase.LoadAssetAtPath<TextAsset>(jsonPath);
        if (text == null) return false;
        var doc = JsonUtility.FromJson<Doc>(text.text);
        var dir = Dir(jsonPath);
        bool side = doc.style == "sideview";
        var sprites = AssetDatabase.LoadAllAssetsAtPath(dir + "/" + doc.tiles).OfType<Sprite>().ToDictionary(s => s.name);
        var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(dir + "/" + doc.player.prefab);
        if (sprites.Count < doc.tileCount || prefab == null)
        {
            Debug.LogWarning("Asset Studio: " + doc.asset + " is not imported yet");
            return false;
        }
        // NewScene(Single) unloads unreferenced assets, so it must come before the Tile objects are made.
        var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
        if (!AssetDatabase.IsValidFolder(dir + "/Tiles/Assets")) AssetDatabase.CreateFolder(dir + "/Tiles", "Assets");
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

        var root = new GameObject(doc.asset + "_SampleRoom");
        var grid = new GameObject("Grid");
        grid.transform.SetParent(root.transform, false);
        grid.AddComponent<Grid>();
        Layer(grid.transform, "Ground", doc.ground, tiles, doc, side ? -5 : -1000, false);
        Layer(grid.transform, "Solid", doc.solidLayer, tiles, doc, side ? 0 : -999, true);
        float levelW = doc.cols, levelH = doc.rows;
        for (int i = 0; i < doc.background.Length; i++)
        {
            var b = doc.background[i];
            var sprite = LoadSprite(dir + "/" + b.sprite);
            if (sprite == null) continue;
            var layer = new GameObject("Background" + i);
            layer.transform.SetParent(root.transform, false);
            layer.AddComponent<Runtime.ParallaxLayer>().factor = b.factor;
            for (int k = -1; k <= Mathf.CeilToInt(levelW / Mathf.Max(0.01f, b.width)); k++)
            {
                var go = new GameObject("Tile" + k);
                go.transform.SetParent(layer.transform, false);
                go.transform.localPosition = new Vector3(k * b.width, 0f, 0f);
                go.transform.localScale = new Vector3(b.scale, b.scale, 1f);
                var sr = go.AddComponent<SpriteRenderer>();
                sr.sprite = sprite;
                sr.sortingOrder = -2000 + i;
            }
        }
        var props = new GameObject("Props");
        props.transform.SetParent(root.transform, false);
        for (int i = 0; i < doc.props.Length; i++)
        {
            var p = doc.props[i];
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
            go.isStatic = true;
        }
        var pd = doc.player;
        var player = new GameObject("Player");
        player.transform.SetParent(root.transform, false);
        player.transform.localPosition = new Vector3(pd.x, pd.y, 0f);
        var rb = player.AddComponent<Rigidbody2D>();
        rb.gravityScale = pd.gravityScale;
        rb.freezeRotation = true;
        rb.interpolation = RigidbodyInterpolation2D.Interpolate;
        rb.collisionDetectionMode = CollisionDetectionMode2D.Continuous;
        var box = player.AddComponent<BoxCollider2D>();
        box.size = new Vector2(pd.colW, pd.colH);
        box.offset = new Vector2(0f, pd.colY);
        var ctrl = player.AddComponent<Runtime.PlayerController>();
        ctrl.speed = pd.speed;
        ctrl.sideview = side;
        ctrl.jumpSpeed = pd.jump;
        ctrl.hasWalk = pd.walk;
        var body = (GameObject)PrefabUtility.InstantiatePrefab(prefab, player.transform);
        body.name = "Body";
        body.transform.localScale = new Vector3(pd.scale, pd.scale, 1f);
        ctrl.animator = body.GetComponent<Animator>();
        ctrl.body = body.GetComponent<SpriteRenderer>();
        if (ctrl.body != null) ctrl.body.sortingOrder = side ? 10 : Order(pd.y);
        var camGo = new GameObject("Main Camera");
        camGo.tag = "MainCamera";
        camGo.transform.SetParent(root.transform, false);
        camGo.transform.localPosition = new Vector3(pd.x, pd.y, -10f);
        var cam = camGo.AddComponent<Camera>();
        cam.orthographic = true;
        cam.orthographicSize = doc.viewRows / 2f;
        cam.clearFlags = CameraClearFlags.SolidColor;
        cam.backgroundColor = new Color(0.23f, 0.26f, 0.33f);
        var follow = camGo.AddComponent<Runtime.FollowCamera>();
        follow.target = player.transform;
        follow.min = Vector2.zero;
        follow.max = new Vector2(levelW, levelH);

        PrefabUtility.SaveAsPrefabAssetAndConnect(root, dir + "/" + doc.asset + "_SampleRoom.prefab", InteractionMode.AutomatedAction);
        EditorSceneManager.SaveScene(scene, dir + "/" + doc.asset + ".unity");
        Debug.Log("Asset Studio: built " + doc.asset + ".unity (" + doc.cols + "x" + doc.rows + ", " + doc.props.Length + " props)");
        if (selfTest) SelfTest(doc, ctrl, rb);
        return true;
    }

    static void Layer(Transform grid, string name, int[] ids, Tile[] tiles, Doc doc, int order, bool collide)
    {
        if (ids.All(v => v < 0)) return;
        var go = new GameObject(name);
        go.transform.SetParent(grid, false);
        var tm = go.AddComponent<Tilemap>();
        go.AddComponent<TilemapRenderer>().sortingOrder = order;
        for (int i = 0; i < ids.Length; i++)
            if (ids[i] >= 0) tm.SetTile(new Vector3Int(i % doc.cols, doc.rows - 1 - i / doc.cols, 0), tiles[ids[i]]);
        if (collide) go.AddComponent<TilemapCollider2D>();
    }

    [Serializable] class TestReport { public string id; public bool ok; public float travel, dx, dy; public int steps; }

    // Edit-mode physics: drive the controller and step Physics2D by hand, as play mode would at 50 Hz.
    static void SelfTest(Doc doc, Runtime.PlayerController ctrl, Rigidbody2D rb)
    {
        var mode = Physics2D.simulationMode;
        Physics2D.simulationMode = SimulationMode2D.Script;
        Physics2D.SyncTransforms();
        const float dt = 0.02f;
        int steps = 60;
        for (int i = 0; i < 10; i++) Physics2D.Simulate(dt);
        var start = rb.position;
        var last = start;
        float travel = 0f;
        bool side = doc.style == "sideview";
        for (int i = 0; i < steps; i++)
        {
            ctrl.testInput = i < steps / 2 ? Vector2.right : (side ? Vector2.left : Vector2.down);
            if (side && i == steps / 4) ctrl.testInput = new Vector2(1f, 1f);
            ctrl.Tick();
            Physics2D.Simulate(dt);
            travel += (rb.position - last).magnitude;
            last = rb.position;
        }
        ctrl.testInput = Vector2.zero;
        Physics2D.simulationMode = mode;
        var rep = new TestReport { id = doc.id, ok = travel > 0.5f, travel = travel, dx = last.x - start.x, dy = last.y - start.y, steps = steps };
        var outDir = Path.Combine(Directory.GetCurrentDirectory(), "Logs");
        Directory.CreateDirectory(outDir);
        File.WriteAllText(Path.Combine(outDir, "asset_studio_playtest_" + doc.id + ".json"), JsonUtility.ToJson(rep));
        if (rep.ok) Debug.Log("ASPLAY travel=" + travel.ToString("0.00") + " units");
        else Debug.LogError("Asset Studio: player did not move in the self test (travel " + travel.ToString("0.00") + ")");
    }
}
"""


def unity_doc(src: Source) -> dict[str, Any]:
    doc = src.playable
    assert doc is not None
    t, rows = doc["tile"], doc["rows"]
    pl, col, phys = doc["player"], doc["player"]["collider"], doc["physics"]

    def ux(x: float) -> float:
        return round(x / t, 4)

    def uy(y: float) -> float:
        return round(rows - y / t, 4)

    props = []
    for p in doc["props"]:
        k = doc["prop_kinds"][p["kind"]]
        props.append({"sprite": f"Props/{k['name']}.png", "x": ux(p["pos"][0]), "y": uy(p["pos"][1]),
                      "poly": [round(v / t * (-1 if i % 2 else 1), 4) for xy in k["collider"]
                               for i, v in enumerate(xy)]})
    bgs = []
    for b in doc["background"]:
        scale = rows / (b["size"][1] / t)
        bgs.append({"sprite": f"Background/{b['name']}.png", "factor": b["factor"], "scale": round(scale, 4),
                    "width": round(b["size"][0] / t * scale, 4)})
    return {"id": _uid(src), "asset": src.asset, "style": doc["style"], "tiles": f"Tiles/{src.asset}_tiles.png",
            "cols": doc["cols"], "rows": rows, "tileCount": doc["tileset"]["count"],
            "viewRows": doc["camera"]["view_rows"], "solid": doc["tileset"]["solid"],
            "ground": doc["layers"][0]["tiles"], "solidLayer": doc["layers"][1]["tiles"],
            "player": {"prefab": f"Player/{pl['name']}.prefab", "x": ux(pl["pos"][0]), "y": uy(pl["pos"][1]),
                       "colW": round(col["w"] / t, 4), "colH": round(col["h"] / t, 4),
                       "colY": round(-col["y"] / t, 4), "speed": round(phys["speed"] / t, 4),
                       "gravityScale": round(phys["gravity"] / t / 9.81, 4), "jump": round(phys["jump"] / t, 4),
                       "scale": 1.0, "walk": bool(pl.get("walk"))},
            "props": props, "background": bgs}


def unity_files(src: Source) -> tuple[Files, list[str]]:
    from studio_core.export import unity
    from studio_core.ops.playable_scene import ATLAS_COLS

    doc = src.playable
    assert doc is not None
    a, t = src.asset, doc["tile"]
    uid = _uid(src)
    pixel = bool(doc["player"].get("pixel"))
    files: Files = []
    for rel, data in unity.export(_player_source(src, float(t))):
        files.append((f"Player/{rel}", data))
    count = doc["tileset"]["count"]
    rows_px = -(-count // ATLAS_COLS) * t
    tiles = Layout(width=ATLAS_COLS * t, height=rows_px, frame_w=t, frame_h=t, pivot=(t / 2, t / 2),
                   cells=[Cell(f"tile_{i}", (i % ATLAS_COLS) * t, (i // ATLAS_COLS) * t, t, t) for i in range(count)])
    rel = f"Tiles/{a}_tiles.png"
    files += [(rel, src.playable_files["atlas"]),
              (f"{rel}.meta", unity.texture_meta(f"{a}_tiles", unity.guid(src.uid, a, rel), tiles,
                                                  (tiles.width, rows_px), t, pixel).encode())]
    for k in doc["prop_kinds"]:
        rel = f"Props/{k['name']}.png"
        lay = Layout(width=k["w"], height=k["h"], frame_w=k["w"], frame_h=k["h"], pivot=(k["w"] / 2, float(k["h"])),
                     cells=[Cell(k["name"], 0, 0, k["w"], k["h"])])
        files += [(rel, src.playable_files[f"prop:{k['name']}"]),
                  (f"{rel}.meta", unity.texture_meta(k["name"], unity.guid(src.uid, a, rel), lay, (k["w"], k["h"]), t,
                                                      pixel).encode())]
    for b in doc["background"]:
        rel = f"Background/{b['name']}.png"
        w, h = b["size"]
        lay = Layout(width=w, height=h, frame_w=w, frame_h=h, pivot=(0.0, float(h)), cells=[Cell(b["name"], 0, 0, w, h)])
        files += [(rel, src.playable_files[f"bg:{b['name']}"]),
                  (f"{rel}.meta", unity.texture_meta(b["name"], unity.guid(src.uid, a, rel), lay, (w, h), t,
                                                      pixel).encode())]
    ns = f"AssetStudio.Playable{uid}"
    for cls, code in UNITY_RUNTIME.items():
        rel = f"Scripts/{cls}.cs"
        files += [(rel, cs_namespace(code, ns).encode()), (f"{rel}.meta", _script_meta(unity.guid(src.uid, a, rel)))]
    builder = UNITY_BUILDER.replace("__ID__", uid).replace("__ASSET__", a)
    rel = f"Editor/PlayableSceneBuilder{uid}.cs"
    files += [(rel, cs_namespace(builder).encode()), (f"{rel}.meta", _script_meta(unity.guid(src.uid, a, rel)))]
    rel = f"playable_{uid}.json"
    files += [(rel, json.dumps(unity_doc(src), indent=1).encode()),
              (f"{rel}.meta", _text_meta(unity.guid(src.uid, a, rel)))]
    notes = [f"- Mở xong project, Editor/PlayableSceneBuilder{uid}.cs tự dựng {a}.unity + {a}_SampleRoom.prefab: Grid "
             "với Tilemap Ground + Solid (TilemapCollider2D), Player (Rigidbody2D + BoxCollider2D + "
             "PlayerController, thân là Player/<nhân vật>.prefab có Animator MoveX/MoveY), prop SpriteRenderer + "
             f"PolygonCollider2D, Main Camera bám theo. Menu Asset Studio > Playable Scene {a} ({uid}) dựng lại và "
             "mở scene; bấm Play, phím mũi tên / WASD, Space nhảy (side-view).",
             f"- Scripts/: PlayerController, FollowCamera, ParallaxLayer (namespace {ns}, chạy cả Input Manager cũ "
             "lẫn Input System)."]
    return files, notes


def extra_files(src: Source) -> Files:
    """Tiled / LDtk copies next to the engine files, with the tile atlas they point at."""
    doc = src.playable
    assert doc is not None
    from studio_core.ops.playable_scene import ldtk_project, tiled_tmx

    a = src.asset
    files: Files = [(f"Tiled/{a}.tmx", tiled_tmx(doc, a).encode()), (f"Tiled/{a}.ldtk", ldtk_project(doc, a).encode()),
                    (f"Tiled/{a}_tiles.png", src.playable_files["atlas"])]
    files += [(f"Tiled/Props/{k['name']}.png", src.playable_files[f"prop:{k['name']}"]) for k in doc["prop_kinds"]]
    files += [(f"Tiled/Background/{b['name']}.png", src.playable_files[f"bg:{b['name']}"]) for b in doc["background"]]
    return files


def source_from(doc: dict[str, Any], files: dict[str, Path], asset: str, uid: str) -> Source:
    src = Source(asset=asset, uid=uid)
    src.playable, src.playable_files = doc, files
    return src


def _write(root: Path, files: Files) -> None:
    if root.exists():
        shutil.rmtree(root)
    for rel, data in files:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, Path):
            shutil.copyfile(data, p)
        else:
            p.write_bytes(data)


def _readme(files: Files, notes: list[str], asset: str, engine: str) -> Files:
    head = f"{asset} — màn chơi thử xuất từ AI Asset Studio cho {engine}.\n\n"
    return [*files, ("README.txt", (head + "\n".join(notes) + "\n").encode())]


async def engine_check(ctx: Any, settings: Any, scene: dict[str, Any], engine: str, frames: int) -> dict[str, Any]:
    """Exports the scene for one engine into the step's job dir and opens it there: Godot imports it, screenshots the
    whole level and plays it for `frames` physics frames; Unity builds the scene, self-tests the controller and
    renders the sample room. Missing engines are skipped, not failed."""
    from studio_core.export import preview as pv

    if settings is None:
        return {"skipped": "no settings", "ok": None}
    avail = await asyncio.to_thread(pv.availability, settings)
    if not avail[engine]["available"]:
        return {"skipped": avail[engine]["reason"], "ok": None}
    files_map = await asyncio.to_thread(scene_files, scene, ctx.blobs.path)
    asset = scene["asset"]
    src = source_from(scene, files_map, asset, f"check/{ctx.run_id}")
    name = f"{asset}_{_uid(src)}"
    export_dir = Path(ctx.job_dir) / engine / name
    work = Path(ctx.job_dir) / engine / "work"
    files, notes = godot_files(src) if engine == "godot" else unity_files(src)
    await asyncio.to_thread(_write, export_dir, _readme(files, notes, asset, engine))
    started = time.monotonic()
    if engine == "godot":
        res = await _check_godot(export_dir, work, Path(settings.godot_exe), ctx.cancelled, frames, scene)
    else:
        res = await _check_unity(export_dir, work, settings, ctx.cancelled, _uid(src))
    res["seconds"] = round(time.monotonic() - started, 1)
    return res


async def _check_godot(export_dir: Path, work: Path, exe: Path, cancel: asyncio.Event, frames: int,
                       scene: dict[str, Any]) -> dict[str, Any]:
    from studio_core.export import preview as pv

    over = await pv.preview_godot(export_dir, work, exe, cancel)
    out: dict[str, Any] = {"errors": list(over.errors), "warnings": list(over.warnings), "target": over.target,
                           "images": []}
    if over.png is not None:
        out["images"].append((f"{scene['asset']}-godot-level.png", over.png))
    project = work / "project"
    if not (project / export_dir.name).is_dir():
        out["ok"] = False
        return out
    (project / "_asset_studio_playtest.gd").write_text(PLAYTEST_GD, encoding="utf-8")
    shot = work / "playtest.png"
    res = f"res://{export_dir.name}/{scene['asset']}.tscn"
    run = await pv.run_proc([str(exe), "--path", str(project), "--rendering-driver", "opengl3", "--rendering-method",
                             "gl_compatibility", "--audio-driver", "Dummy",
                             "--resolution", f"{PLAYTEST_SIZE[0]}x{PLAYTEST_SIZE[1]}", "--position", "-32000,-32000",
                             "--script", "res://_asset_studio_playtest.gd", "--", res, str(shot), str(frames),
                             scene["style"]], project, None, PLAYTEST_TIMEOUT_S, cancel)
    errors, warnings = pv.godot_issues(run.output)
    m = re.search(r"PLAYTEST travel=([-\d.]+) dx=([-\d.]+) dy=([-\d.]+) floor=(\w+)", run.output)
    travel = float(m.group(1)) if m else 0.0
    out["errors"] = pv._dedupe(out["errors"] + errors + ([f"Godot playtest {run.stopped}"] if run.stopped else []))
    out["warnings"] = pv._dedupe(out["warnings"] + warnings)
    out["moved"] = {"travel_px": travel, "dx": float(m.group(2)) if m else 0.0, "dy": float(m.group(3)) if m else 0.0,
                    "tiles": round(travel / scene["tile"], 2)}
    if shot.is_file():
        out["images"].append((f"{scene['asset']}-godot-play.png", shot))
    out["ok"] = bool(m) and travel >= scene["tile"] and not out["errors"]
    if not m:
        out["errors"].append("playtest printed no result")
        out["log_tail"] = pv._tail(run.output)
    return out


async def _check_unity(export_dir: Path, work: Path, settings: Any, cancel: asyncio.Event, uid: str
                       ) -> dict[str, Any]:
    from studio_core.export import preview as pv

    project = Path(settings.unity_project)
    report = project / "Logs" / f"asset_studio_playtest_{uid}.json"
    if report.is_file():
        report.unlink()
    res = await pv.preview_unity(export_dir, work, Path(settings.unity_exe), project, cancel)
    # Offline machines log Unity's cloud-config curl failure mid-line, sometimes glued to an import line of our folder.
    noise = ("Curl error", "Failed to connect to config.uca.cloud.unity3d.com")
    out: dict[str, Any] = {"errors": [e for e in res.errors if not any(n in e for n in noise)],
                           "warnings": list(res.warnings), "target": res.target,
                           "images": [(f"{export_dir.name}-unity.png", res.png)] if res.png else [],
                           "project": str(project)}
    rep: dict[str, Any] = {}
    if report.is_file():
        try:
            rep = json.loads(report.read_text(encoding="utf-8-sig"))
        except ValueError:
            rep = {}
    out["moved"] = {"travel_units": rep.get("travel"), "dx": rep.get("dx"), "dy": rep.get("dy"),
                    "tiles": round(float(rep.get("travel") or 0), 2)}
    if not rep:
        out["errors"].append("Unity self test wrote no report")
        out["log_tail"] = res.log_tail
    out["ok"] = res.png is not None and bool(rep.get("ok")) and not out["errors"]
    return out


def godot_gui_exe(console: Path) -> Path:
    gui = console.with_name(console.name.replace("_console", ""))
    return gui if gui.is_file() else console


def godot_project(export_dir: Path, dst: Path, asset: str) -> Path:
    """A standalone Godot project around an export folder, main scene set, so the editor opens straight on it."""
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    shutil.copytree(export_dir, dst / export_dir.name)
    main = f"res://{export_dir.name}/{asset}.tscn"
    (dst / "project.godot").write_text(
        'config_version=5\n\n[application]\n\nconfig/name="{}"\nrun/main_scene="{}"\n\n[display]\n\n'
        "window/size/viewport_width={}\nwindow/size/viewport_height={}\nwindow/stretch/mode=\"canvas_items\"\n\n"
        '[rendering]\n\nrenderer/rendering_method="gl_compatibility"\n'.format(asset, main, *GODOT_VIEW),
        encoding="utf-8")
    return dst


def _spawn_detached(args: list[str], cwd: Path, env: dict[str, str] | None = None) -> None:
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    subprocess.Popen(args, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True, env=env)


def open_project(settings: Any, export_dir: Path, engine: str, asset: str, launch: bool = True) -> dict[str, Any]:
    """Godot: a project next to the export, editor opened on the scene. Unity: the folder staged into the configured
    project (like the engine preview) and the editor started with the builder's Open method; a project already open
    in Unity only gets the files (the builder runs on import, menu Asset Studio > Playable Scene opens it)."""
    from studio_core.export import preview as pv

    avail = pv.availability(settings)
    if not avail[engine]["available"]:
        raise RuntimeError(avail[engine]["reason"] or f"{engine} unavailable")
    if engine == "godot":
        proj = godot_project(export_dir, export_dir.parent.parent / "godot_projects" / export_dir.name, asset)
        exe = godot_gui_exe(Path(settings.godot_exe))
        if launch:
            _spawn_detached([str(exe), "--editor", "--path", str(proj), f"res://{export_dir.name}/{asset}.tscn"], proj)
        return {"engine": engine, "project": str(proj), "scene": f"{export_dir.name}/{asset}.tscn", "launched": launch,
                "note": "Godot mở project; F5 để chơi"}
    project = Path(settings.unity_project)
    locked = pv.project_locked(project)
    rel = pv._unity_stage(export_dir, project)
    uid = next((p.stem.removeprefix("playable_") for p in export_dir.glob("playable_*.json")), "")
    launched = False
    if launch and not locked:
        _spawn_detached([str(settings.unity_exe), "-projectPath", str(project), "-executeMethod",
                         f"AssetStudio.PlayableSceneBuilder{uid}.Open"], project, pv.unity_env())
        launched = True
    note = ("Unity đang mở project này: đã chép file, scene tự dựng khi Unity import xong — menu Asset Studio > "
            f"Playable Scene {asset}" if locked else "Unity đang khởi động, scene mở sau khi import xong")
    return {"engine": engine, "project": str(project), "scene": f"{rel}/{asset}.unity", "launched": launched,
            "note": note}
