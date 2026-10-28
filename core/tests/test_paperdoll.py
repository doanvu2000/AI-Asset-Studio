from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from studio_core.adapters.blender import paperdoll_math as pdm
from studio_core.adapters.blender.paperdoll import BlenderPaperdollExecutor
from studio_core.config import Settings
from studio_core.contracts import ExecutorRegistry, StepError
from studio_core.engine.kinds import CATALOG
from studio_core.engine.library import validate
from studio_core.engine.recipe import load_recipe_file
from studio_core.export.sheet import layout
from studio_core.ops import register
from studio_core.ops.executors import Sheet
from studio_core.ops.paperdoll import Paperdoll, cell_frames
from test_ops_executors import FakeCtx

RECIPE = Path(__file__).resolve().parents[1] / "studio_core" / "recipes" / "sprite-8dir-paperdoll.yaml"
ANGLES = [0, 45, 90, 135, 180, 225, 270, 315]
CELL = 16
PIVOT = (8.0, 14.0)
ORTHO = 2.0


def _close(a, b, tol=1e-6) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b))


# --- slot planning / fitting ---------------------------------------------------------------------------------------

def test_plan_equip_slots_and_errors() -> None:
    eq = pdm.plan_equip({"weapon": "sword", "shield": "round", "helmet": "none"}, set())
    assert [(e["name"], e["bone"], e["shape"]) for e in eq] == [("weapon", "hand_r", "sword"),
                                                                ("shield", "forearm_l", "round")]
    left = pdm.plan_equip({"weapon": "axe", "shield": "kite", "weapon_hand": "left"}, set())
    assert [e["bone"] for e in left] == ["hand_l", "forearm_r"] and left[1]["side"] == -1
    mesh = pdm.plan_equip({"weapon": "sword", "armor": "mesh"}, {"armor"})
    assert mesh[1]["mesh"] and mesh[1]["shape"] is None and mesh[0]["shape"] == "sword"
    assert pdm.plan_equip({}, set()) == []
    with pytest.raises(ValueError, match="needs a helmet mesh"):
        pdm.plan_equip({"helmet": "mesh"}, set())
    with pytest.raises(ValueError, match="unknown shape"):
        pdm.plan_equip({"weapon": "bazooka"}, set())
    with pytest.raises(ValueError):
        pdm.plan_equip({"weapon": "sword", "weapon_size": 9}, set())


RIG = {"height": 1.0,
       "bones": {"hand_r": [[-0.3, 0.0, 0.5], [-0.3, 0.0, 0.4]], "forearm_l": [[0.3, 0.0, 0.7], [0.3, 0.0, 0.5]],
                 "head": [[0.0, 0.0, 0.8], [0.0, 0.0, 1.0]], "spine": [[0.0, 0.0, 0.5], [0.0, 0.0, 0.8]]},
       "head_box": [[-0.1, -0.1, 0.8], [0.1, 0.1, 1.0]], "torso_box": [[-0.15, -0.08, 0.5], [0.15, 0.08, 0.8]]}


def test_fit_weapon_grips_hand_and_points_forward_up() -> None:
    item = pdm.plan_equip({"weapon": "sword"}, set())[0]
    lo, hi = (-0.1, -0.02, 0.0), (0.1, 0.02, 1.0)
    m = pdm.fit_matrix(item, lo, hi, RIG)
    grip = (0.0, 0.0, 0.1)
    assert _close(pdm.apply(m, grip), (-0.3, 0.0, 0.45))
    tip = pdm.apply(m, (0.0, 0.0, 1.0))
    v = tuple(t - g for t, g in zip(tip, pdm.apply(m, grip)))
    length = math.sqrt(sum(x * x for x in v))
    assert length == pytest.approx(0.55 * 0.9)
    want = pdm._norm(pdm.WEAPON_DIR[None])
    assert _close(tuple(x / length for x in v), want, 1e-6)
    flat = pdm.apply(m, (0.0, 1.0, 0.1))
    n = pdm._norm(tuple(f - g for f, g in zip(flat, pdm.apply(m, grip))))
    assert abs(pdm._dot(n, want)) < 1e-6 and n[1] < 0
    flipped = pdm.fit_matrix({**item, "flip": True}, lo, hi, RIG)
    assert _close(pdm.apply(flipped, (0.0, 0.0, 0.9)), (-0.3, 0.0, 0.45))


def test_fit_helmet_hat_and_armor_boxes() -> None:
    lo, hi = (-0.5, -0.5, 0.0), (0.5, 0.5, 0.5)
    helmet = pdm.fit_matrix({"slot": "helmet", "shape": "helmet"}, lo, hi, RIG)
    assert _close(pdm.apply(helmet, (0.0, 0.0, 0.5)), (0.0, 0.0, 1.006))
    assert pdm.apply(helmet, (0.5, 0.0, 0.5))[0] == pytest.approx(0.2 * 1.12 / 2)
    hat = pdm.fit_matrix({"slot": "helmet", "shape": "hat"}, lo, hi, RIG)
    assert _close(pdm.apply(hat, (0.0, 0.0, 0.0)), (0.0, 0.0, 1.0 - 0.35 * 0.2))
    armor = pdm.fit_matrix({"slot": "armor", "shape": "plate"}, (-0.5, -0.3, 0.0), (0.5, 0.3, 1.0), RIG)
    top = pdm.apply(armor, (0.5, 0.3, 1.0))
    assert _close(top, (0.15 * 1.08, 0.08 * 1.15, 0.8))
    assert pdm.apply(armor, (0.0, 0.0, 0.0))[2] == pytest.approx(0.8 - 0.3 * 0.8)
    shield = pdm.fit_matrix({"slot": "shield", "shape": "round", "side": 1, "bone": "forearm_l"}, (-0.5, -0.03, -0.5), (0.5, 0.03, 0.5),
                            RIG)
    face = pdm._norm(tuple(a - b for a, b in zip(pdm.apply(shield, (0, 1, 0)), pdm.apply(shield, (0, 0, 0)))))
    assert face[0] > 0 and face[1] < 0
    with pytest.raises(ValueError):
        pdm.fit_matrix({"slot": "cape"}, lo, hi, RIG)


# --- socket projection ---------------------------------------------------------------------------------------------

def test_projection_matches_worker_camera() -> None:
    assert pdm.to_cell((0, 0, 0), 37, 30, 50, PIVOT)[:2] == PIVOT
    assert _close(pdm.to_view((1, 0, 0), 0, 0), (1, 0, 0))
    assert _close(pdm.to_view((0, -1, 0), 0, 0), (0, 0, -1))
    # Yaw 90 shows the character's left side: its forward (-y) points to image left.
    assert _close(pdm.to_view((0, -1, 0), 90, 0), (-1, 0, 0))
    x, up, depth = pdm.to_view((0, 0, 1), 0, 30)
    assert _close((x, up, depth), (0, math.cos(math.radians(30)), -0.5))
    px = pdm.to_cell((0.1, 0, 0.5), 0, 0, 100, PIVOT)
    assert _close(px[:2], (PIVOT[0] + 10, PIVOT[1] - 50))


def _pose(hand_y: float = 0.0) -> dict[str, Any]:
    return {"sockets": {"hand_r": {"p": [-0.3, hand_y, 0.45], "d": [0, 0, -0.1]},
                        "head": {"p": [0, 0, 1.0], "d": [0, 0, 0.2]},
                        "chest": {"p": [0, 0, 0.7], "d": [0, 0, 0.3]}},
            "items": {"weapon": [-0.3, hand_y - 0.2, 0.6], "shield": [0.3, 0.1, 0.6]}}


def test_socket_frame_rot_depth_and_order() -> None:
    rec = pdm.socket_frame(_pose(), 0, 0, 100, PIVOT, 1.0)
    hand = next(s for s in rec["sockets"] if s["name"] == "hand_r")
    assert (hand["x"], hand["y"]) == (PIVOT[0] - 30, PIVOT[1] - 45)
    assert hand["rot"] == 90.0 and hand["scale"] == 1.0 and hand["z"] == "front"
    assert next(s for s in rec["sockets"] if s["name"] == "head")["rot"] == -90.0
    assert rec["order"] == ["shield", "weapon"]
    behind = pdm.socket_frame(_pose(0.2), 0, 0, 100, PIVOT, 1.0)
    assert next(s for s in behind["sockets"] if s["name"] == "hand_r")["z"] == "back"
    turned = pdm.socket_frame(_pose(), 180, 0, 100, PIVOT, 1.0)
    assert turned["order"] == ["weapon", "shield"]
    assert next(s for s in turned["sockets"] if s["name"] == "hand_r")["x"] == PIVOT[0] + 30


# --- executor + ops + export ---------------------------------------------------------------------------------------

CLIPS = [{"name": "idle", "frames": 2}, {"name": "attack", "frames": 3}]


class FakeBlender:
    version = "fake"

    def __init__(self) -> None:
        self.req: dict[str, Any] = {}

    async def call(self, cmd: str, params: dict[str, Any], on_event=None, timeout: float = 0) -> dict[str, Any]:
        assert cmd == "paperdoll_dirs"
        self.req = params
        out = Path(params["out_dir"])
        out.mkdir(parents=True, exist_ok=True)
        frames, layers = [], []
        names = [e["name"] for e in params["equip"]]
        for layer in [None, *names]:
            for c in params["clips"]:
                for fi in range(c["frames"]):
                    for a in ANGLES:
                        f = out / f"{layer}_{c['name']}_{a}_{fi}.png"
                        img = Image.new("RGBA", (CELL, CELL), (0, 0, 0, 0))
                        color = (200, 50, 50, 255) if layer is None else (50, 50, 200 - 40 * names.index(layer), 255)
                        img.putpixel((fi, a // 45), color)
                        img.save(f)
                        rec = {"layer": layer, "clip": c["name"], "angle": float(a), "frame": fi, "file": str(f)}
                        (frames if layer is None else layers).append(rec)
        poses = {c["name"]: [_pose(0.05 * fi) for fi in range(c["frames"])] for c in params["clips"]}
        return {"frames": frames[::-1], "layers": layers[::-1], "pivot": list(PIVOT), "res": CELL,
                "frames_per_dir": 3, "elevation": 30, "ortho_scale": ORTHO, "weights": "distance", "height": 1.0,
                "engine": "BLENDER_EEVEE", "total_s": 0.1, "supersample": 1, "poses": poses,
                "equip": [{k: v for k, v in e.items() if k != "glb"} for e in params["equip"]]}


async def _put_json(ctx: FakeCtx, doc: Any, name: str):
    return await ctx.blobs.put_bytes(json.dumps(doc).encode(), name=name, mime="application/json")


async def _doll(ctx: FakeCtx, tmp_path: Path, **params: Any):
    mesh = await ctx.blobs.put_bytes(b"glb", name="hero_clean.glb", mime="model/gltf-binary")
    joints = await _put_json(ctx, {"joints": {f"j{i}": [1, 2] for i in range(19)}}, "joints.json")
    front = await _put_json(ctx, {"width": 512, "height": 512, "pivot": [256, 500], "ortho_scale": 1.0}, "render.json")
    sword = await ctx.blobs.put_bytes(b"glb2", name="sword.glb", mime="model/gltf-binary")
    fake = FakeBlender()
    ex = BlenderPaperdollExecutor(Settings(data_dir=tmp_path / "d"), fake)  # type: ignore[arg-type]
    res = await ex.run(ctx, {"anims": "idle, attack", "idle_frames": 2, "attack_frames": 3, "count": 8, **params},
                       {"mesh": [mesh], "joints": [joints], "front": [front], "weapon": [sword], "shield": [],
                        "helmet": [], "armor": []})
    return fake, res


async def test_executor_orders_frames_and_layers(tmp_path: Path) -> None:
    ctx = FakeCtx(tmp_path)
    fake, res = await _doll(ctx, tmp_path, weapon="sword", shield="round")
    assert [e["name"] for e in fake.req["equip"]] == ["weapon", "shield"]
    assert fake.req["equip"][0]["mesh"] and Path(fake.req["equip"][0]["glb"]).read_bytes() == b"glb2"
    assert fake.req["clips"] == [{"name": "idle", "frames": 2, "fps": 6, "loop": True},
                                 {"name": "attack", "frames": 3, "fps": 12, "loop": False}]
    n = 5 * 8
    assert len(res.outputs["images"]) == n and len(res.outputs["layers"]) == 2 * n
    assert res.outputs["images"][0].name == "idle_dir_000_f00.png"
    assert res.outputs["images"][2].name == "idle_dir_045_f00.png"
    assert res.outputs["layers"][n].name == "shield_idle_dir_000_f00.png"
    rig = json.loads(ctx.blobs.path(res.outputs["meta"][0].hash).read_bytes())
    assert rig["clips"][1]["name"] == "attack" and len(rig["poses"]["attack"]) == 3 and rig["body_height"] == 1.0
    with pytest.raises(StepError, match="needs a helmet mesh"):
        (tmp_path / "x").mkdir()
        await _doll(FakeCtx(tmp_path / "x"), tmp_path / "x", helmet="mesh")


async def _pipeline(tmp_path: Path):
    ctx = FakeCtx(tmp_path)
    _, doll = await _doll(ctx, tmp_path, weapon="sword", helmet="helmet")
    sheet = await Sheet().run(ctx, {}, {"images": doll.outputs["images"], "pivot": doll.outputs["meta"]})
    res = await Paperdoll().run(ctx, {}, {"layers": doll.outputs["layers"], "rig": doll.outputs["meta"],
                                          "sheet": sheet.outputs["sheet"], "sheet_meta": sheet.outputs["meta"]})
    return ctx, doll, sheet, res


def _img(ctx: FakeCtx, ref) -> Image.Image:
    with Image.open(ctx.blobs.path(ref.hash)) as im:
        return im.convert("RGBA")


async def test_layer_sheets_share_character_cells(tmp_path: Path) -> None:
    ctx, _, sheet, res = await _pipeline(tmp_path)
    meta = json.loads(ctx.blobs.path(sheet.outputs["meta"][0].hash).read_bytes())
    lay = layout(meta, directional_rows=True)
    body = _img(ctx, sheet.outputs["sheet"][0])
    assert [r.name for r in res.outputs["sheets"]] == ["weapon_sheet.png", "helmet_sheet.png"]
    for ref in res.outputs["sheets"]:
        layer = _img(ctx, ref)
        assert layer.size == body.size
        for t, i, c in cell_frames(lay):
            dot = (c.x + i, c.y + ANGLES.index(round(t.angle)))
            assert body.getpixel(dot)[3] == 255 and layer.getpixel(dot)[3] == 255, (t.name, i)
        assert int((np.asarray(layer)[..., 3] > 0).sum()) == len(lay.cells)
    doc = json.loads(ctx.blobs.path(res.outputs["sockets"][0].hash).read_bytes())
    assert [f["cell"] for f in doc["frames"]] == [c.name for c in lay.cells]
    assert doc["frames"][0]["anim"] == "idle_S" and doc["layers"] == ["weapon", "helmet"]
    f0 = doc["frames"][0]
    k = CELL / ORTHO
    hand = next(s for s in f0["sockets"] if s["name"] == "hand_r")
    want = pdm.to_cell((-0.3, 0.0, 0.45), 0, 30, k, PIVOT)
    assert (hand["x"], hand["y"]) == (round(want[0], 2), round(want[1], 2))
    for port in ("preview", "socket_preview"):
        assert _img(ctx, res.outputs[port][0]).width == 8 * CELL
    doll = json.loads(ctx.blobs.path(res.outputs["doll"][0].hash).read_bytes())
    assert doll["sheet"] == sheet.outputs["sheet"][0].hash and [la["slot"] for la in doll["layers"]] == ["weapon",
                                                                                                         "helmet"]


async def test_paperdoll_rejects_mismatched_layers(tmp_path: Path) -> None:
    ctx, doll, sheet, _ = await _pipeline(tmp_path)
    with pytest.raises(StepError, match="layer frames"):
        await Paperdoll().run(ctx, {}, {"layers": doll.outputs["layers"][:-1], "rig": doll.outputs["meta"],
                                        "sheet": sheet.outputs["sheet"], "sheet_meta": sheet.outputs["meta"]})


async def test_export_unity_and_godot(tmp_path: Path) -> None:
    from test_export_engines import _row

    from studio_core.export import godot, unity
    from studio_core.export.paperdoll import SCRIPT_GUID
    from studio_core.store.bundle import engine_ready, engine_source

    ctx, _, sheet, res = await _pipeline(tmp_path)
    step = _row("approve", "gate.approve", {"preview": res.outputs["preview"], "sheet": sheet.outputs["sheet"],
                                            "layers": res.outputs["sheets"], "sockets": res.outputs["sockets"],
                                            "doll": res.outputs["doll"]})
    assert engine_ready(step, ctx.blobs)
    src = engine_source(step, [], ctx.blobs, "Hero")
    assert src.sheet == ctx.blobs.path(sheet.outputs["sheet"][0].hash)
    assert [n for n, _ in src.paperdoll_layers] == ["weapon", "helmet"]

    u = dict(unity.export(src))
    assert {"Hero.png", "Hero.prefab", "Hero_weapon_sheet.png", "Hero_helmet_sheet.png.meta", "Hero_sockets.json",
            "Hero_sockets.json.meta", "AssetStudioPaperdoll.cs", "Hero_paperdoll.prefab"} <= set(u)
    body_meta, layer_meta = u["Hero.png.meta"].decode(), u["Hero_weapon_sheet.png.meta"].decode()
    names = lambda m: [ln.split(": ", 1)[1] for ln in m.splitlines() if ln.startswith("      name: ")]  # noqa: E731
    assert names(body_meta) == names(layer_meta) and "idle_S_0" in names(layer_meta)
    prefab = u["Hero_paperdoll.prefab"].decode()
    assert SCRIPT_GUID in prefab and prefab.count("SpriteRenderer:") == 3 and prefab.count("MonoBehaviour:") == 1
    assert "m_Name: hand_r" in prefab and "m_Name: Sockets" in prefab and "socketRoot: {fileID:" in prefab
    assert prefab.count("    - {fileID: ") == 2 * 40
    assert "m_Controller:" in prefab and "public class AssetStudioPaperdoll" in u["AssetStudioPaperdoll.cs"].decode()
    assert "Paper-doll" in u["README.txt"].decode()

    g = dict(godot.export(src))
    assert {"Hero.tres", "Hero.tscn", "Hero_weapon.tres", "Hero_helmet_sheet.png", "Hero_paperdoll.tscn",
            "asset_studio_paperdoll.gd", "asset_studio_sprite.gd", "Hero_sockets.json"} <= set(g)
    assert g["Hero_weapon.tres"].decode().count("AtlasTexture") == g["Hero.tres"].decode().count("AtlasTexture")
    scene = g["Hero_paperdoll.tscn"].decode()
    assert 'instance=ExtResource("1_body")' in scene and scene.count('type="AnimatedSprite2D"') == 2
    assert scene.count('type="Marker2D"') == len(pdm.SOCKETS) and 'type="JSON"' in scene
    assert g["asset_studio_paperdoll.gd"].decode().startswith('extends "asset_studio_sprite.gd"')


# --- recipe ------------------------------------------------------------------------------------------------------

def test_recipe_lints_and_kinds_registered() -> None:
    r = load_recipe_file(RECIPE)
    assert [s.id for s in r.steps][:2] == ["front", "doll"] and r.step("layers").kind == "op.paperdoll"
    assert validate(RECIPE.read_text(encoding="utf-8"), None)[1] == []
    reg = ExecutorRegistry()
    register(reg)
    assert reg.resolve("op.paperdoll")
    for kind in ("blender.paperdoll", "op.paperdoll"):
        assert CATALOG[kind]["label"] and isinstance(CATALOG[kind]["params"], dict)
    assert set(CATALOG["blender.paperdoll"]["in"]) >= set(pdm.SLOTS)
    inputs = r.coerce_inputs({"mesh": "a" * 8, "joints": "b" * 8})
    params = r.resolve_params(r.step("doll"), inputs)
    assert [e["name"] for e in pdm.plan_equip(params, set())] == ["weapon", "helmet"]
    assert inputs["weapon_mesh"] is None
