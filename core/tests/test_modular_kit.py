from __future__ import annotations

import json
import re
import struct
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from studio_core.adapters.blender import modular_math as mm
from studio_core.adapters.blender import trim_math as tm
from studio_core.adapters.blender.modular import REPORT_FILE, BlenderModularKitExecutor, fallback_trim
from studio_core.config import Settings
from studio_core.contracts import StepError
from studio_core.engine.kinds import CATALOG
from studio_core.engine.library import validate
from studio_core.engine.recipe import load_recipe_file
from studio_core.ops.trim import preset_layout, strip_ranges
from test_ops_executors import FakeCtx

RECIPE = Path(__file__).resolve().parents[1] / "studio_core" / "recipes" / "modular-kit.yaml"


def test_dims_bounds_and_params() -> None:
    for grid, storey in ((1, 3), (2, 4), (4, 4)):
        d = mm.kit_dims(grid)
        assert d["storey"] == storey and d["storey"] % grid == 0 and d["door"][1] < d["storey"]
        for piece in mm.PIECES:
            lo, hi, snapped = mm.expected_bounds(piece, d)
            assert mm.check_bounds(lo, hi, piece, d)["ok"], (grid, piece)
            assert all(round((hi[i] - lo[i]) / grid, 6).is_integer() for i in snapped)
    d = mm.kit_dims(2)
    assert mm.check_bounds([0, 0, 0], [2.0004, d["thickness"], 4], "wall", d)["ok"]
    assert not mm.check_bounds([0, 0, 0], [2.001, d["thickness"], 4], "wall", d)["ok"]
    assert mm.kit_dims(2, 1)["storey"] == 2
    assert mm.normalize_params({"pieces": "wall, stair"})["pieces"] == ["wall", "stair"]
    assert mm.normalize_params(mm.normalize_params({"pieces": "stair;wall"}))["pieces"] == ["wall", "stair"]
    for bad in ({"grid": 3}, {"pieces": "wall,roof"}, {"max_tris": 10}, {"storey": 9}, {"bevel": 1}):
        with pytest.raises(ValueError):
            mm.normalize_params(bad)


def test_ends_match_and_room_on_grid() -> None:
    wall = [[x, y, z] for x in (0, 2) for y in (0, 0.2) for z in (0, 4)]
    assert mm.ends_match(wall, 2.0)["ok"]
    assert not mm.ends_match(wall + [[2, 0.1, 1]], 2.0)["ok"]
    d = mm.kit_dims(2)
    room = mm.sample_room(d, list(mm.PIECES))
    assert {r["piece"] for r in room} == set(mm.PIECES) - {"ceiling"}
    assert sum(r["piece"] == "corner_out" for r in room) == 4 and sum(r["piece"] == "floor" for r in room) == 8
    assert all(abs(c / 2 - round(c / 2)) < 1e-9 for r in room for c in r["pos"])
    assert mm.rot_z([1, 0, 0], 90) == pytest.approx([0, 1, 0])
    assert mm.to_godot([1, 2, 3]) == [1, 3, -2] and mm.to_unity([1, 2, 3]) == [-1, 3, -2]


def _trim() -> dict[str, Any]:
    return fallback_trim(2, 0)


def _chamfered_cube(s: float = 1.0, b: float = 0.1) -> list[tuple[list[list[float]], list[float]]]:
    """Faces (corners, normal) of a cube with bevelled edges: the 6 big faces, 12 edge faces at 45° and 8 corners."""
    faces = []
    for ax in range(3):
        for sign in (-1, 1):
            n = [0.0, 0.0, 0.0]
            n[ax] = sign
            o = [i for i in range(3) if i != ax]
            pts = []
            for a, c in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                p = [0.0, 0.0, 0.0]
                p[ax], p[o[0]], p[o[1]] = sign * s, a * (s - b), c * (s - b)
                pts.append(p)
            faces.append((pts, n))
    for ax in range(3):
        o = [i for i in range(3) if i != ax]
        for s1 in (-1, 1):
            for s2 in (-1, 1):
                n = [0.0, 0.0, 0.0]
                n[o[0]], n[o[1]] = s1 * 0.7071, s2 * 0.7071
                pts = []
                for t in (-1, 1):
                    for k in ((1, 0), (0, 1)) if t < 0 else ((0, 1), (1, 0)):
                        p = [0.0, 0.0, 0.0]
                        p[ax] = t * (s - b)
                        p[o[0]] = s1 * (s if k[0] else s - b)
                        p[o[1]] = s2 * (s if k[1] else s - b)
                        pts.append(p)
                faces.append((pts, n))
    return faces


def test_bevelled_cube_faces_map_into_their_strip() -> None:
    trim = _trim()
    for name in ("cap", "frame", "wall"):
        strip = next(s for s in trim["strips"] if s["name"] == name)
        for pts, n in _chamfered_cube():
            for along in (False, True):
                uv = tm.face_uv(pts, n, strip, along_long=along)
                assert tm.uv_in_strip(uv, strip) and uv[:, 1].max() > uv[:, 1].min()
    assert tm.resolve_strip(trim, "base")["name"] == "base"
    general = {"strips": strip_ranges(preset_layout("general", "x")["strips"])}
    assert tm.resolve_strip(general, "wall")["name"] == "panel"
    assert tm.resolve_strip(general, "base")["name"] == "baseboard"
    assert tm.resolve_strip({"strips": [{"name": "a", "px": 64, "y0": 0}, {"name": "b", "px": 512, "y0": 64}]},
                            "floor")["name"] == "b"


def test_u_snaps_to_whole_repeats_per_cell() -> None:
    strip = {"name": "wall", "px": 512, "repeats": 8, "v0": 0.75, "v1": 1.0, "v0_safe": 0.751, "v1_safe": 0.999}
    pts = [[0, 0, 0], [2, 0, 0], [2, 0, 4], [0, 0, 4]]
    uv = tm.face_uv(pts, [0, -1, 0], strip, span=(0, 4), cell_m=2)
    # 128 px/m over a 2 m cell = 256 px = exactly one of 8 repeats; u at x=2 lands on a period boundary.
    assert uv[1, 0] == pytest.approx(0.125) and uv[2, 1] == pytest.approx(0.999)
    assert tm.u_scale(1067, 2, 8) * 2 == pytest.approx(1.0)
    back = tm.face_uv(pts, [0, 1, 0], strip, span=(0, 4), cell_m=2)
    assert back[1, 0] == pytest.approx(-0.125)


def fake_report(asset: str = "kit") -> dict[str, Any]:
    d = mm.kit_dims(2)
    pieces = []
    for p in mm.PIECES:
        lo, hi, _ = mm.expected_bounds(p, d)
        pieces.append({"piece": p, "name": f"{asset}_{p}", "tris": 100, "verts": 50, "bounds_min": lo,
                       "bounds_max": hi, "bounds": mm.check_bounds(lo, hi, p, d), "uv_outside": 0,
                       "colliders": [[lo, hi]], "parts": ["wall"]})
    return {"asset": asset, "dims": d, "params": mm.normalize_params({}), "pieces": pieces,
            "room": mm.sample_room(d, list(mm.PIECES)), "max_tris": 100, "tris_budget": 2000,
            "all_bounds_ok": True, "all_ends_ok": True, "uv_outside": 0, "seconds": 1.0}


class FakeBlender:
    version = "fake"

    def __init__(self, report: dict[str, Any] | None = None) -> None:
        self.req: dict[str, Any] = {}
        self.report = report or fake_report()

    async def call(self, cmd: str, params: dict[str, Any], on_event=None, timeout: float = 0) -> dict[str, Any]:
        assert cmd == "script" and params["unrestricted"] is True and "glb_in" not in params
        self.req = params
        p = params["params"]
        out = Path(p["out_dir"])
        for e in self.report["pieces"]:
            for ext, data in ((".glb", b"glTF"), (".fbx", b"Kaydara FBX Binary  \x00"), (".obj", b"v 0 0 0\n")):
                (out / f"{p['asset']}_{e['piece']}{ext}").write_bytes(data + e["piece"].encode())
        (out / f"{p['asset']}_room.glb").write_bytes(b"glTFroom")
        Path(p["report"]).write_text(json.dumps(self.report), encoding="utf-8")
        Path(params["out"]).write_bytes(b"glTFkit")
        return {"out": params["out"]}


async def _kit(ctx: FakeCtx, tmp_path: Path, fake: FakeBlender | None = None, trim: bool = True, **params: Any):
    fake = fake or FakeBlender()
    inputs: dict[str, Any] = {}
    if trim:
        doc = {"strips": strip_ranges(preset_layout("modular", "x")["strips"])}
        inputs["trim"] = [await ctx.blobs.put_bytes(json.dumps(doc).encode(), name="trim.json")]
        inputs["maps"] = [await ctx.blobs.put_bytes(b"png" + r.encode(), name=f"kit_{r}.png", mime="image/png")
                          for r in ("albedo", "normal", "roughness", "ao")]
    ex = BlenderModularKitExecutor(Settings(data_dir=tmp_path / "d"), fake)  # type: ignore[arg-type]
    return fake, await ex.run(ctx, {"asset": "kit", **params}, inputs)


async def test_executor_stages_and_collects(tmp_path: Path) -> None:
    ctx = FakeCtx(tmp_path)
    fake, res = await _kit(ctx, tmp_path, grid=2)
    p = fake.req["params"]
    assert "modular_bpy.run(params)" in fake.req["code"] and p["grid"] == 2 and len(p["pieces"]) == 10
    assert set(p["maps"]) == {"albedo", "normal", "roughness"} and Path(p["trim"]).is_file()
    assert [r.name for r in res.outputs["meshes"]][:2] == ["kit_wall.glb", "kit_wall_window.glb"]
    assert len(res.outputs["fbx"]) == len(res.outputs["obj"]) == 10 and res.outputs["room"][0].name == "kit_room.glb"
    rep = json.loads(ctx.blobs.path(res.outputs["report"][0].hash).read_bytes())
    assert res.outputs["report"][0].name == REPORT_FILE and rep["type"] == "asset_studio.modular_kit"
    assert rep["pieces"][0]["obj"] == "kit_wall.obj" and rep["pieces"][0]["obj_hash"]
    assert res.meta["warnings"] == [] and res.meta["bounds_ok"] and res.meta["trimmed"]
    bad = fake_report()
    bad["pieces"][0]["tris"] = 5000
    bad["pieces"][1]["bounds"]["ok"] = False
    bad.update(all_bounds_ok=False, uv_outside=3)
    _, res = await _kit(ctx, tmp_path, FakeBlender(bad), trim=False)
    w = " ".join(res.meta["warnings"])
    assert "wall" in w and "wall_window" in w and "3 mặt" in w and not res.meta["trimmed"]
    with pytest.raises(StepError, match="grid"):
        await _kit(ctx, tmp_path, grid=3)


async def test_export_unity_and_godot(tmp_path: Path) -> None:
    from test_export_engines import _row

    from studio_core.export import godot, unity
    from studio_core.ops.trim import TrimSheet
    from studio_core.store.bundle import engine_ready, engine_source
    from test_trim_decal import _layout_ref, _noise, _png

    ctx = FakeCtx(tmp_path)
    lay = preset_layout("modular", "x")
    ref = await _layout_ref(ctx, lay)
    imgs = [await ctx.blobs.put_bytes(_png(_noise(256, 128, i)), name=f"{i}.png", mime="image/png")
            for i in range(len(lay["strips"]))]
    sheet = await TrimSheet().run(ctx, {"size": 1024, "asset": "kit"}, {"images": imgs, "layout": [ref]})
    _, res = await _kit(ctx, tmp_path)
    step = _row("approve", "gate.approve", {**{k: res.outputs[k] for k in ("meshes", "room", "fbx", "obj", "report")},
                                            "maps": sheet.outputs["maps"], "material": sheet.outputs["material"],
                                            "trim": sheet.outputs["trim"]})
    assert engine_ready(step, ctx.blobs)
    src = engine_source(step, [], ctx.blobs, "kit")
    assert src.modular and len(src.modular_obj) == 10 and len(src.fbx) == 10
    u = dict(unity.export(src))
    assert {"Models/kit_wall.fbx", "kit_modular.json", "Editor/AssetStudioModularKit.cs", "PBR/kit.mat",
            "Models/kit_room.glb"} <= set(u)
    assert "Models/kit_wall.glb" not in u
    doc = json.loads(u["kit_modular.json"])
    assert doc["material"] == "PBR/kit.mat" and doc["pieces"][0]["model"] == "Models/kit_wall.fbx"
    wall = doc["pieces"][0]["colliders"][0]
    t = mm.kit_dims(2)["thickness"]
    # Blender box x 0..2, y 0..t, z 0..4 -> Unity x -2..0, y 0..4, z -t..0.
    assert wall["center"] == {"x": -1.0, "y": 2.0, "z": round(-t / 2, 5)} and wall["size"]["y"] == 4.0
    door = next(r for r in doc["room"] if r["piece"] == "kit_wall_door")
    assert door["position"] == {"x": -2, "y": 0, "z": 0}
    assert {r["yaw"] for r in doc["room"]} >= {0.0, -90.0, -180.0}
    editor = u["Editor/AssetStudioModularKit.cs"].decode()
    assert "namespace AssetStudio" in editor and "BoxCollider" in editor and "_SampleRoom.prefab" in editor

    g = dict(godot.export(src))
    assert {"Kit/kit_wall.obj", "Kit/kit_kit.mtl", "kit_kit.tres", "kit_room.tscn", "PBR/kit.tres"} <= set(g)
    assert g["Kit/kit_wall.obj"].startswith(b"mtllib kit_kit.mtl\nusemtl trim\nv 0 0 0")
    assert "map_Kd ../PBR/kit_albedo.png" in g["Kit/kit_kit.mtl"].decode()
    lib = g["kit_kit.tres"].decode()
    assert lib.count('type="ArrayMesh"') == 10 and 'item/9/name = "railing"' in lib
    assert lib.count('type="BoxShape3D"') == 10 and 'path="Kit/kit_stair.obj"' in lib
    room = g["kit_room.tscn"].decode()
    assert 'name="Floors" type="GridMap"' in room and 'name="Walls" type="GridMap"' in room
    assert "cell_center_x = false" in room
    decoded = []
    for block in re.findall(r"PackedInt32Array\(([^)]*)\)", room):
        cells = [int(c) for c in block.split(", ")]
        assert len(cells) % 3 == 0
        layer = set()
        for i in range(0, len(cells), 3):
            x, y, z, _ = struct.unpack("<hhhh", struct.pack("<ii", cells[i], cells[i + 1]))
            assert (x, y, z) not in layer
            layer.add((x, y, z))
            decoded.append((x, y, z, cells[i + 2] & 0xFFFF, cells[i + 2] >> 16))
    names = [e["piece"] for e in src.modular["pieces"]]
    assert len(decoded) == len(src.modular["room"])
    # North wall with the window: Blender pivot (4, 4) turned 180° -> Godot cell (2, 0, -2), orientation 10.
    assert (2, 0, -2, names.index("wall_window"), 10) in decoded
    assert any(o == 16 for *_, o in decoded) and all(o in (0, 16, 10, 22) for *_, o in decoded)


def test_recipe_loads_and_lints() -> None:
    r = load_recipe_file(RECIPE)
    assert [s.id for s in r.steps] == ["layout", "strips", "sheet", "kit", "room_view", "approve"]
    _, issues = validate(RECIPE.read_text(encoding="utf-8"), None)
    assert issues == []
    assert CATALOG["blender.modular_kit"]["in"] == {"trim": "json", "maps": "image[]"}
    assert np.isclose(CATALOG["blender.modular_kit"]["params"]["bevel"]["max"], 0.05)
