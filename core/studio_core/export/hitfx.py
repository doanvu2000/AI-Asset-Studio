"""Hit VFX bound to attack frames. Per attack track (clip × direction) an impact frame — clip metadata, the procedural
rig's strike key, else the frame that ends the fastest silhouette change — and an impact point — the paper-doll hand_r
socket, else the silhouette pixel reaching farthest in the facing direction. Engines get a flipbook spawned there:
Unity AnimationEvents (OnHit) + AssetStudioHitFx.cs on the prefab, a Godot child node listening to frame_changed, and
a neutral <asset>_hits.json."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from studio_core.export.sheet import Cell, Layout, Track, cs_namespace, direction_vector, ident, layout
from studio_core.export.source import Files, Source

HITS_TYPE = "asset-studio/hits"
ATTACK_WORDS = ("attack", "slash", "strike", "punch", "kick", "stab", "smash", "shoot", "cast")
# blender.animate_dirs ATTACK_KEYS: the arm passes the strike key at t = 0.5 of the one-shot.
RIG_STRIKE_T = {"attack": 0.5}
SOCKET = "hand_r"
DEFAULT_SIZE = 0.5
ALPHA_ON = 16
# The legs move little in a swing; leaving them in lets a planted foot win "farthest along the facing".
LEG_SHARE = 0.35
REACH = 0.03
HEAD_MIN = 0.1
NECK = 0.8
SHOULDER = 0.35


@dataclass(frozen=True)
class Vfx:
    name: str
    sheet: Path
    meta: dict[str, Any]


@dataclass(frozen=True)
class HitOptions:
    vfx: Vfx
    # Track name (attack_S) -> impact frame chosen by the user.
    frames: dict[str, int] = field(default_factory=dict)
    # None = every attack-like clip.
    clips: tuple[str, ...] | None = None
    # VFX cell height as a share of the character frame height.
    size: float = DEFAULT_SIZE
    # Track name -> impact point (cell pixels) placed by the user.
    points: dict[str, tuple[float, float]] = field(default_factory=dict)


@dataclass
class Hit:
    track: str
    clip: str
    dir: str
    angle: float
    row: int
    frames: int
    fps: float
    frame: int
    auto_frame: int
    source: str
    point_source: str
    points: list[tuple[float, float]]

    @property
    def point(self) -> tuple[float, float]:
        return self.points[min(self.frame, len(self.points) - 1)]

    def as_dict(self) -> dict[str, Any]:
        return {"track": self.track, "clip": self.clip, "dir": self.dir, "angle": self.angle, "row": self.row,
                "frames": self.frames, "fps": self.fps, "frame": self.frame, "auto_frame": self.auto_frame,
                "source": self.source, "point_source": self.point_source, "point": list(self.point),
                "points": [list(p) for p in self.points]}


def is_attack(clip: str) -> bool:
    c = clip.lower()
    return any(w in c for w in ATTACK_WORDS)


def _clip_meta(meta: dict[str, Any], clip: str) -> dict[str, Any]:
    for c in meta.get("clips") or []:
        if isinstance(c, dict) and ident(str(c.get("name") or ""), "") == clip:
            return c
    return {}


def _masks(rgba: np.ndarray, cells: list[Cell]) -> list[np.ndarray]:
    return [rgba[c.y:c.y + c.h, c.x:c.x + c.w, 3] > ALPHA_ON for c in cells]


def _upper(mask0: np.ndarray, pivot_y: float) -> int:
    """First row of the legs: everything at or below it is ignored when looking for the strike."""
    rows = np.flatnonzero(mask0.any(1))
    if not len(rows):
        return mask0.shape[0]
    top = float(rows[0])
    return int(round(pivot_y - LEG_SHARE * max(1.0, pivot_y - top)))


def _facing(angle: float) -> tuple[float, float]:
    vx, vy = direction_vector(angle)
    # Sheet pixels run y-down; a sprite seen from above shows "toward the viewer" as down the screen, but only
    # foreshortened, so the vertical part counts half.
    return vx, -vy * 0.5


def motion_frame(masks: list[np.ndarray], loop: bool, angle: float, pivot: tuple[float, float]) -> int:
    """The frame that ends the biggest silhouette change (the swing lands there), nudged toward the frame reaching
    farthest along the facing."""
    n = len(masks)
    if n < 2:
        return 0
    cut = _upper(masks[0], pivot[1])
    up = [m[:cut] for m in masks]
    diff = np.zeros(n)
    for f in range(n):
        if f == 0 and not loop:
            continue
        diff[f] = float(np.logical_xor(up[f], up[f - 1]).sum())
    fx, fy = _facing(angle)
    reach = np.zeros(n)
    for f, m in enumerate(up):
        ys, xs = np.nonzero(m)
        if len(xs):
            reach[f] = float(((xs - pivot[0]) * fx + (ys - pivot[1]) * fy).max())
    score = diff / (diff.max() or 1.0)
    if np.ptp(reach) > 0:
        score = score + 0.5 * (reach - reach.min()) / np.ptp(reach)
    if not loop:
        score[0] = -1.0
    return int(score.argmax())


def _erode(m: np.ndarray, steps: int) -> np.ndarray:
    for _ in range(steps):
        p = np.pad(m, 1)
        m = m & p[:-2, 1:-1] & p[2:, 1:-1] & p[1:-1, :-2] & p[1:-1, 2:]
    return m


def _head_mask(m: np.ndarray, pivot_y: float) -> tuple[np.ndarray, int | None]:
    """The head: rows from the top down to the neck (where the run through the head's centre column narrows), within
    the head's columns, and the neck row. Left out, a big chibi head otherwise reaches farthest when facing W/NW/N."""
    head = np.zeros_like(m)
    rows = np.flatnonzero(m.any(1))
    if not len(rows):
        return head, None
    top = int(rows[0])
    span = max(1.0, pivot_y - top)
    cx = int(np.median(np.nonzero(m[top:top + max(2, int(0.08 * span))])[1]))
    y0 = top
    while y0 < m.shape[0] and not m[y0, cx]:
        y0 += 1
    runs: list[tuple[int, int]] = []
    for y in range(y0, min(m.shape[0], int(pivot_y))):
        if not m[y, cx]:
            break
        lo = cx - int(np.argmin(m[y, cx::-1])) + 1 if not m[y, :cx + 1].all() else 0
        hi = cx + int(np.argmin(m[y, cx:])) - 1 if not m[y, cx:].all() else m.shape[1] - 1
        runs.append((lo, hi))
    w = [hi - lo + 1 for lo, hi in runs]
    widest = 0
    for i, wi in enumerate(w):
        if y0 + i - top >= HEAD_MIN * span and widest and wi <= NECK * widest:
            # Past the jaw's curve: the neck is the narrowest row before the shoulders widen again.
            while i + 1 < len(w) and w[i + 1] <= w[i]:
                i += 1
            x0, x1 = min(r[0] for r in runs[:i]), max(r[1] for r in runs[:i])
            head[top:y0 + i, max(0, x0 - 2):x1 + 3] = True
            return head & m, y0 + i
        widest = max(widest, wi)
    return head, None


def silhouette_point(mask: np.ndarray, rest: np.ndarray, angle: float, pivot: tuple[float, float]
                     ) -> tuple[float, float]:
    """Mean of the few pixels reaching farthest along the facing, preferring the thick part the pose added over the
    rest frame (the swung arm / weapon) above the legs and away from the head; a lean or bob only adds thin slivers,
    which erosion drops."""
    floor = max(4, 0.002 * mask.size)
    cut = _upper(rest, pivot[1])
    fx, fy = _facing(angle)
    # Facing down the screen the head never leads, and a front view rarely shows a neck to find.
    head, neck = _head_mask(mask, pivot[1]) if fy <= 0.05 else (np.zeros_like(mask), None)
    m = mask.copy()
    m[cut:] = False
    body = m & ~head
    if body.sum() < floor and neck is not None:
        # A chibi head can reach the leg cut; then the shoulders just below it are the arm's side.
        body = mask & ~head
        body[max(cut, neck + int(SHOULDER * (pivot[1] - neck))):] = False
    if body.sum() >= floor:
        m = body
    added = m & ~rest
    pick = m
    for steps in (max(2, round(0.02 * m.shape[0])), 1, 0):
        core = _erode(added, steps)
        if core.sum() >= floor:
            pick = core
            break
    ys, xs = np.nonzero(pick)
    if not len(xs):
        ys, xs = np.nonzero(mask)
        if not len(xs):
            return round(pivot[0], 1), round(pivot[1], 1)
    cx, cy = float(xs.mean()), float(ys.mean())
    score = (xs - pivot[0]) * fx + (ys - pivot[1]) * fy + 0.35 * np.hypot(xs - cx, ys - cy)
    k = max(1, len(xs) // 100)
    top = np.argsort(score)[-k:]
    # The blow lands just past the silhouette edge, not on it.
    lead = REACH * mask.shape[0] / max(1e-6, float(np.hypot(fx, fy)))
    return round(float(xs[top].mean()) + fx * lead, 1), round(float(ys[top].mean()) + fy * lead, 1)


def _socket_points(sockets: dict[str, Any] | None, t: Track, socket: str) -> list[tuple[float, float]] | None:
    if not sockets:
        return None
    by_cell = {f.get("cell"): f for f in sockets.get("frames") or [] if isinstance(f, dict)}
    out = []
    for c in t.cells:
        s = next((s for s in (by_cell.get(c.name) or {}).get("sockets") or [] if s.get("name") == socket), None)
        if s is None:
            return None
        out.append((round(float(s["x"]), 1), round(float(s["y"]), 1)))
    return out


def _row(lay: Layout, meta: dict[str, Any], t: Track) -> int:
    return int(round(t.cells[0].y / (lay.frame_h + int(meta.get("padding") or 0))))


def analyze(sheet: Path, meta: dict[str, Any], clip_hint: str = "anim", directional_rows: bool = False,
            sockets: dict[str, Any] | None = None, clips: tuple[str, ...] | None = None,
            frames: dict[str, int] | None = None, points_in: dict[str, tuple[float, float]] | None = None
            ) -> tuple[Layout, list[Hit]]:
    from PIL import Image

    lay = layout(meta, clip_hint, directional_rows=directional_rows)
    tracks = [t for t in lay.tracks if t.dir and len(t.cells) > 1
              and (t.clip in clips if clips is not None else is_attack(t.clip))]
    if not tracks:
        return lay, []
    with Image.open(sheet) as im:
        rgba = np.asarray(im.convert("RGBA"))
    hits = []
    for t in tracks:
        n = len(t.cells)
        masks = _masks(rgba, t.cells)
        cm = _clip_meta(meta, t.clip)
        if isinstance(cm.get("impact"), (int, float)):
            auto, source = int(cm["impact"]), "meta"
        elif isinstance(cm.get("impact_t"), (int, float)):
            auto, source = int(float(cm["impact_t"]) * (n - 1) + 0.5), "meta"
        elif directional_rows and t.clip in RIG_STRIKE_T:
            auto, source = int(RIG_STRIKE_T[t.clip] * (n - 1) + 0.5), "rig"
        else:
            auto, source = motion_frame(masks, t.loop, t.angle, lay.pivot), "motion"
        auto = max(0, min(n - 1, auto))
        frame = auto
        user = (frames or {}).get(t.name)
        if isinstance(user, int) and 0 <= user < n:
            frame, source = user, "user"
        points = _socket_points(sockets, t, SOCKET)
        point_source = "socket" if points else "silhouette"
        if points is None:
            points = [silhouette_point(m, masks[0], t.angle, lay.pivot) for m in masks]
        pin = (points_in or {}).get(t.name)
        if pin is not None:
            points[frame] = (round(float(pin[0]), 1), round(float(pin[1]), 1))
            point_source = "user"
        hits.append(Hit(t.name, t.clip, t.dir, float(t.angle), _row(lay, meta, t), n, t.fps, frame, auto, source,
                        point_source, points))
    return lay, hits


def vfx_layout(v: Vfx) -> tuple[Layout, list[Cell], float, bool]:
    lay = layout(v.meta, v.name)
    lay.pivot = (lay.frame_w / 2, lay.frame_h / 2)
    cells = lay.tracks[0].cells if lay.tracks else lay.cells
    flip = v.meta.get("flipbook") if isinstance(v.meta.get("flipbook"), dict) else {}
    return lay, cells, float(flip.get("fps") or 24), bool(flip.get("loop"))


def vfx_scale(char: Layout, vfx: Layout, size: float) -> float:
    return round(max(0.01, size) * char.frame_h / max(1, vfx.frame_h), 4)


def hits_doc(src: Source, lay: Layout, hits: list[Hit], opts: HitOptions, vfx_rel: str) -> dict[str, Any]:
    vlay, cells, fps, loop = vfx_layout(opts.vfx)
    scale = vfx_scale(lay, vlay, opts.size)
    name = opts.vfx.name
    px, py = lay.pivot
    return {
        "type": HITS_TYPE, "version": 1, "asset": src.asset, "frame_w": lay.frame_w, "frame_h": lay.frame_h,
        "pivot": [px, py], "ppu": src.ppu,
        "coords": "point: pixels inside the cell, origin top-left, y down. offset: point - pivot in pixels, y up "
                  "(divide by ppu for world units). time: seconds from the clip start (frame / fps). scale: VFX "
                  "sprite scale so one VFX cell spans `size` of the character frame height.",
        "vfx": [{"name": name, "file": vfx_rel, "frames": len(cells), "fps": fps, "loop": loop,
                 "frame_w": vlay.frame_w, "frame_h": vlay.frame_h}],
        "hits": [{"track": h.track, "clip": h.clip, "dir": h.dir, "angle": h.angle, "frame": h.frame,
                  "time": round(h.frame / h.fps, 5), "point": list(h.point),
                  "offset": [round(h.point[0] - px, 2), round(py - h.point[1], 2)], "vfx": name, "scale": scale,
                  "size": opts.size, "source": h.source, "point_source": h.point_source} for h in hits],
    }


SCRIPT_FILE = "AssetStudioHitFx.cs"
# Fixed so several exported characters in one Unity project share one script identity.
SCRIPT_GUID = "5c0e7d1a9b2f4c63a8e4d17f0b9a6e25"
UNITY_SCRIPT = cs_namespace("""// Generated by AI Asset Studio: attack clips call OnHit on their impact frame; this spawns the bound flipbook VFX.
using System;
using System.Collections;
using System.Globalization;
using UnityEngine;

public class AssetStudioHitFx : MonoBehaviour
{
    [Serializable] public class Effect { public string name; public float fps = 24f; public Sprite[] sprites; }

    public SpriteRenderer body;
    public Effect[] effects = new Effect[0];
    public int sortingOffset = 10;
    /// <summary>Effect name and world-space impact point, raised on every hit (also when the effect has no sprites).</summary>
    public event Action<string, Vector3> Hit;

    int lastFrame = -1;

    void Awake()
    {
        if (body == null) body = GetComponent<SpriteRenderer>();
    }

    // AnimationEvent data: "<effect>|<x>|<y>" (local offset from the pivot, world units), floatParameter = scale.
    public void OnHit(AnimationEvent e)
    {
        // Every direction's clip in the blend tree carries the event; only the dominant one may spawn.
        if (e.animatorClipInfo.clip != null && e.animatorClipInfo.weight < 0.5f) return;
        if (lastFrame == Time.frameCount) return;
        lastFrame = Time.frameCount;
        var parts = (e.stringParameter ?? "").Split('|');
        float x = parts.Length > 1 ? Num(parts[1]) : 0f, y = parts.Length > 2 ? Num(parts[2]) : 0f;
        if (body != null && body.flipX) x = -x;
        var point = transform.TransformPoint(new Vector3(x, y, 0f));
        var fx = Array.Find(effects, f => f != null && f.name == parts[0]);
        if (fx != null && fx.sprites != null && fx.sprites.Length > 0)
            StartCoroutine(Play(fx, point, e.floatParameter > 0f ? e.floatParameter : 1f));
        Hit?.Invoke(parts[0], point);
    }

    static float Num(string s) => float.Parse(s, NumberStyles.Float, CultureInfo.InvariantCulture);

    IEnumerator Play(Effect fx, Vector3 point, float scale)
    {
        var go = new GameObject("HitFx_" + fx.name);
        go.transform.position = point;
        go.transform.localScale = Vector3.one * scale;
        var r = go.AddComponent<SpriteRenderer>();
        if (body != null)
        {
            r.sortingLayerID = body.sortingLayerID;
            r.sortingOrder = body.sortingOrder + sortingOffset;
        }
        var wait = new WaitForSeconds(1f / Mathf.Max(1f, fx.fps));
        foreach (var s in fx.sprites)
        {
            r.sprite = s;
            yield return wait;
        }
        Destroy(go);
    }
}
""")

GODOT_SCRIPT_FILE = "asset_studio_hitfx.gd"
GODOT_SCRIPT = """extends Node2D
## Child of an exported AnimatedSprite2D: when the parent shows an attack's impact frame (hits JSON), spawns that
## hit's VFX (SpriteFrames from `effects`, matched by resource file name) at the impact point and emits `hit`.

signal hit(vfx: String, global_point: Vector2)

@export var hits: JSON
@export var effects: Array[SpriteFrames] = []
@export var z_offset := 10

var _by_frame := {}
var _pivot := Vector2.ZERO
var _sprite: AnimatedSprite2D


func _ready() -> void:
	_sprite = get_parent() as AnimatedSprite2D
	if hits:
		var p: Array = hits.data.get("pivot", [0, 0])
		_pivot = Vector2(p[0], p[1])
		for h in hits.data.get("hits", []):
			_by_frame["%s:%d" % [h["track"], int(h["frame"])]] = h
	if _sprite:
		_sprite.frame_changed.connect(_on_frame)


func _effect(vfx_name: String) -> SpriteFrames:
	for e in effects:
		if e and e.resource_path.get_file().get_basename() == vfx_name:
			return e
	return effects[0] if effects.size() == 1 else null


func _on_frame() -> void:
	var h: Dictionary = _by_frame.get("%s:%d" % [_sprite.animation, _sprite.frame], {})
	if h.is_empty():
		return
	var p := Vector2(h["point"][0], h["point"][1]) - _pivot
	if _sprite.flip_h:
		p.x = -p.x
	var at := _sprite.to_global(p)
	var frames := _effect(String(h["vfx"]))
	if frames:
		var fx := AnimatedSprite2D.new()
		fx.name = "HitFx_" + String(h["vfx"])
		fx.top_level = true
		fx.sprite_frames = frames
		fx.z_index = _sprite.z_index + z_offset
		fx.scale = Vector2.ONE * float(h.get("scale", 1.0))
		fx.texture_filter = _sprite.texture_filter
		add_child(fx)
		fx.global_position = at
		fx.animation_finished.connect(fx.queue_free)
		fx.play(frames.get_animation_names()[0])
	hit.emit(String(h["vfx"]), at)
"""

_EVENTS = "  m_Events: []\n"


def unity_events(hit: Hit, doc_hit: dict[str, Any], ppu: float) -> str:
    dx, dy = doc_hit["offset"]
    return (f"  m_Events:\n  - time: {hit.frame / hit.fps:.6g}\n    functionName: OnHit\n"
            f"    data: {doc_hit['vfx']}|{dx / ppu:.6g}|{dy / ppu:.6g}\n"
            f"    objectReferenceParameter: {{fileID: 0}}\n    floatParameter: {doc_hit['scale']:.6g}\n"
            f"    intParameter: {hit.frame}\n    messageOptions: 0\n")


def _unity_behaviour(src: Source, vfx_asset: str, vfx_guid: str, cells: list[Cell], name: str, fps: float) -> str:
    from studio_core.export.unity import file_id

    a = src.asset
    sprites = "".join(f"    - {{fileID: {file_id('sprite', vfx_asset, c.name)}, guid: {vfx_guid}, type: 3}}\n"
                      for c in cells)
    return f"""--- !u!114 &{file_id("hitfx", a, "behaviour")}
MonoBehaviour:
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {{fileID: 0}}
  m_PrefabInstance: {{fileID: 0}}
  m_PrefabAsset: {{fileID: 0}}
  m_GameObject: {{fileID: {file_id("go", a)}}}
  m_Enabled: 1
  m_EditorHideFlags: 0
  m_Script: {{fileID: 11500000, guid: {SCRIPT_GUID}, type: 3}}
  m_Name:
  m_EditorClassIdentifier:
  body: {{fileID: {file_id("renderer", a)}}}
  effects:
  - name: {name}
    fps: {fps:.6g}
    sprites:
{sprites}  sortingOffset: 10
"""


def _patch_prefab(text: str, mb_id: int, behaviour: str) -> str:
    if f"&{mb_id}\n" in text:
        return text
    return text.replace("  m_Component:\n", f"  m_Component:\n  - component: {{fileID: {mb_id}}}\n", 1) + behaviour


def _unity(files: Files, src: Source, lay: Layout, hits: list[Hit], opts: HitOptions) -> tuple[Files, str]:
    from studio_core.export.paperdoll import _script_meta, _text_meta
    from studio_core.export.unity import file_id, guid, texture_meta

    a, u, name = src.asset, src.uid, opts.vfx.name
    vlay, cells, fps, _ = vfx_layout(opts.vfx)
    vfx_rel, json_rel = f"HitFx/{name}.png", f"{a}_hits.json"
    vfx_asset, vfx_guid = f"{a}_hitfx_{name}", guid(u, a, vfx_rel)
    doc = hits_doc(src, lay, hits, opts, vfx_rel)
    by_track = {h["track"]: h for h in doc["hits"]}
    clips = {f"Animations/{h.track}.anim": h for h in hits}
    behaviour = _unity_behaviour(src, vfx_asset, vfx_guid, cells, name, fps)
    mb = file_id("hitfx", a, "behaviour")
    out: Files = []
    for rel, data in files:
        if rel in clips and isinstance(data, bytes):
            h = clips[rel]
            data = data.decode().replace(_EVENTS, unity_events(h, by_track[h.track], src.ppu), 1).encode()
        elif rel in (f"{a}.prefab", f"{a}_paperdoll.prefab") and isinstance(data, bytes):
            data = _patch_prefab(data.decode(), mb, behaviour).encode()
        out.append((rel, data))
    out += [(vfx_rel, opts.vfx.sheet),
            (f"{vfx_rel}.meta", texture_meta(vfx_asset, vfx_guid, vlay, (vlay.width, vlay.height), src.ppu,
                                             src.is_pixel()).encode()),
            (json_rel, json.dumps(doc, indent=1).encode()), (f"{json_rel}.meta", _text_meta(guid(u, a, json_rel)).encode()),
            (SCRIPT_FILE, UNITY_SCRIPT.encode()), (f"{SCRIPT_FILE}.meta", _script_meta(SCRIPT_GUID).encode())]
    note = (f"- Hit VFX: clip {', '.join(sorted({h.clip for h in hits}))} có AnimationEvent OnHit ở khung trúng đòn "
            f"(mỗi hướng một khung/điểm, xem {json_rel}); {a}.prefab gắn {SCRIPT_FILE} — sinh flipbook {vfx_rel} tại "
            "điểm trúng (theo flipX), xong tự huỷ, và phát sự kiện C# Hit(tên, điểm thế giới) để gameplay trừ máu/rung "
            f"camera. Muốn VFX khác: kéo sprite khác vào effects. Xuất nhiều nhân vật vào cùng project: giữ 1 file "
            f"{SCRIPT_FILE}.")
    return out, note


def godot_scene(src: Source, vfx_res: str, json_rel: str) -> str:
    a = src.asset
    return "\n".join([
        "[gd_scene format=3]", "",
        f'[ext_resource type="PackedScene" path="{a}.tscn" id="1_body"]',
        f'[ext_resource type="Script" path="{GODOT_SCRIPT_FILE}" id="2_hitfx"]',
        f'[ext_resource type="JSON" path="{json_rel}" id="3_hits"]',
        f'[ext_resource type="SpriteFrames" path="{vfx_res}" id="4_vfx"]', "",
        f'[node name="{a}_hits" instance=ExtResource("1_body")]', "",
        '[node name="HitFx" type="Node2D" parent="."]', 'script = ExtResource("2_hitfx")',
        'hits = ExtResource("3_hits")', 'effects = Array[SpriteFrames]([ExtResource("4_vfx")])', ""])


def _godot(files: Files, src: Source, lay: Layout, hits: list[Hit], opts: HitOptions) -> tuple[Files, str]:
    from studio_core.export.godot import sprite_frames

    a, name = src.asset, opts.vfx.name
    vlay, cells, fps, loop = vfx_layout(opts.vfx)
    vfx_rel, vfx_res, json_rel = f"HitFx/{name}.png", f"HitFx/{name}.tres", f"{a}_hits.json"
    track = Track(name, "", 0.0, cells, fps, loop)
    flip = Layout(vlay.width, vlay.height, vlay.frame_w, vlay.frame_h, vlay.pivot, list(cells), [track])
    doc = hits_doc(src, lay, hits, opts, vfx_rel)
    out = list(files) + [(vfx_rel, opts.vfx.sheet), (vfx_res, sprite_frames(flip, f"{name}.png", None).encode()),
                         (json_rel, json.dumps(doc, indent=1).encode()),
                         (GODOT_SCRIPT_FILE, GODOT_SCRIPT.encode()),
                         (f"{a}_hits.tscn", godot_scene(src, vfx_res, json_rel).encode())]
    note = (f"- Hit VFX: {a}_hits.tscn = {a}.tscn + node con HitFx ({GODOT_SCRIPT_FILE}) nghe frame_changed: tới khung "
            f"trúng đòn của clip {', '.join(sorted({h.clip for h in hits}))} (mỗi hướng một khung/điểm, xem {json_rel}) "
            f"thì sinh AnimatedSprite2D {vfx_res} tại điểm trúng (theo flip_h), xong tự xoá, và emit signal "
            "hit(vfx, global_point) cho gameplay.")
    return out, note


def apply(files: Files, engine: str, src: Source, opts: HitOptions | None) -> Files:
    """Adds the hit data to an engine export; a sheet without attack tracks is left unchanged."""
    if opts is None or src.sheet is None or src.meta is None:
        return files
    sockets = (src.paperdoll or {}).get("sockets_doc")
    lay, hits = analyze(Path(src.sheet), src.meta, src.clip_hint, src.directional_rows, sockets, opts.clips,
                        opts.frames, opts.points)
    if not hits:
        return files
    files, note = (_godot if engine == "godot" else _unity)(files, src, lay, hits, opts)
    from studio_core.export.normal2d import append_readme

    return append_readme(files, note)


_VFX_NAME = re.compile(r"[^a-z0-9]+")


def vfx_name(prompt: str, fallback: str) -> str:
    words = _VFX_NAME.sub(" ", prompt.lower()).split()
    stop = {"a", "an", "the", "of", "into", "and", "with", "in", "on"}
    keep = [w for w in words if w not in stop][:3]
    return ident("_".join(keep), fallback)[:40] if keep else ident(fallback, "hit_vfx")
