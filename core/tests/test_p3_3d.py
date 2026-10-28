from __future__ import annotations

import asyncio
import json
import struct
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from studio_core.adapters.blender import BlenderCleanupExecutor, BlenderGameReadyExecutor, BlenderRenderDirsExecutor
from studio_core.adapters.blender import cleanup_math as cm
from studio_core.config import Settings
from studio_core.contracts import ExecutorRegistry, Lane, StepError
from studio_core.engine.gates import register_gates
from studio_core.engine.recipe import load_recipes
from studio_core.ops import register as register_ops
from studio_core.ops import glb

from test_ops_executors import FakeCtx


def make_glb(textured: bool = True, magic: bytes = b"glTF", tris: int = 1) -> bytes:
    positions = struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0)
    indices = struct.pack(f"<{3 * tris}H", *([0, 1, 2] * tris))
    indices += b"\0" * (-len(indices) % 4)
    png = b"\x89PNG\r\n\x1a\nfake"
    png += b"\0" * (-len(png) % 4)
    binary = positions + indices + (png if textured else b"")
    doc: dict[str, Any] = {
        "asset": {"version": "2.0", "generator": "test"},
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": 36},
                        {"buffer": 0, "byteOffset": 36, "byteLength": len(indices)}],
        "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3",
                       "min": [0, 0, 0], "max": [1, 1, 0]},
                      {"bufferView": 1, "componentType": 5123, "count": 3 * tris, "type": "SCALAR"}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1, "material": 0}]}],
        "materials": [{}],
        "nodes": [{"mesh": 0}],
    }
    if textured:
        doc["bufferViews"].append({"buffer": 0, "byteOffset": 36 + len(indices), "byteLength": len(png)})
        doc["images"] = [{"bufferView": 2, "mimeType": "image/png"}]
        doc["textures"] = [{"source": 0}]
    js = json.dumps(doc).encode()
    js += b" " * (-len(js) % 4)
    body = struct.pack("<II", len(js), glb.CHUNK_JSON) + js + struct.pack("<II", len(binary), glb.CHUNK_BIN) + binary
    return struct.pack("<4sII", magic, 2, 12 + len(body)) + body


# cleanup parameter logic

@pytest.mark.parametrize("spec,want", [(None, None), ("none", None), ("auto", None), ("X90", ("x", 90.0)),
                                       ("y-90", ("y", -90.0)), ("z180", ("z", 180.0))])
def test_parse_upright(spec: Any, want: Any) -> None:
    assert cm.parse_upright(spec) == want


@pytest.mark.parametrize("bad", ["x45", "w90", "up", "x"])
def test_parse_upright_rejects(bad: str) -> None:
    with pytest.raises(ValueError):
        cm.parse_upright(bad)


def test_decimate_ratio() -> None:
    assert cm.decimate_ratio(100_000, 30_000) == pytest.approx(0.3)
    assert cm.decimate_ratio(20_000, 30_000) is None
    assert cm.decimate_ratio(30_000, 30_000) is None
    assert cm.decimate_ratio(100_000, 0) is None
    assert cm.decimate_ratio(100_000, None) is None


def test_height_scale_and_merge() -> None:
    assert cm.height_scale(2.0, 1.0) == 0.5
    assert cm.height_scale(2.0, 0) == 1.0
    assert cm.height_scale(0.0, 1.0) == 1.0
    assert cm.merge_distance(True) == cm.DEFAULT_MERGE
    assert cm.merge_distance(False) == 0.0
    assert cm.merge_distance(-1) == 0.0


def test_normalize_params() -> None:
    p = cm.normalize_params({"target_faces": "30000", "upright": "X-90"})
    assert p == {"target_faces": 30000, "height": 1.0, "origin": "feet", "upright": "x-90",
                 "merge_by_distance": cm.DEFAULT_MERGE, "smooth_angle": 40.0}
    assert cm.normalize_params({"height": None})["height"] == 0.0
    with pytest.raises(ValueError):
        cm.normalize_params({"origin": "top"})
    with pytest.raises(ValueError):
        cm.normalize_params({"upright": "sideways"})


class FakeBlender:
    version = "5.2"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, cmd: str, params: dict[str, Any], on_event: Any = None, timeout: float = 0) -> Any:
        self.calls.append((cmd, params))
        on_event({"value": 1, "max": 1, "label": "export"})
        Path(params["out"]).write_bytes(make_glb())
        return {"faces_before": 100, "faces_after": 50, "out": params["out"]}


def test_cleanup_executor(tmp_path: Path) -> None:
    async def go() -> None:
        ctx = FakeCtx(tmp_path)
        src = await ctx.blobs.put_bytes(make_glb(), name="raw.glb", mime="model/gltf-binary")
        fake = FakeBlender()
        ex = BlenderCleanupExecutor(Settings(data_dir=tmp_path), fake)  # type: ignore[arg-type]
        assert ex.lane == Lane.BLENDER and ex.cacheable({})
        res = await ex.run(ctx, {"target_faces": 50, "upright": "none"}, {"mesh": [src]})
        cmd, params = fake.calls[0]
        assert cmd == "cleanup" and params["target_faces"] == 50 and params["origin"] == "feet"
        assert res.outputs["meshes"][0].name == "raw_clean.glb"
        assert res.meta["faces_after"] == 50 and "out" not in res.meta
        with pytest.raises(StepError, match="blender_bad_params"):
            await ex.run(ctx, {"upright": "x45"}, {"mesh": [src]})
        with pytest.raises(StepError, match="blender_bad_input"):
            await ex.run(ctx, {}, {"mesh": []})

    asyncio.run(go())


# glb validation

def test_inspect_glb() -> None:
    r = glb.inspect_glb(make_glb(tris=2))
    assert r["meshes"] == 1 and r["primitives"] == 1 and r["vertices"] == 3 and r["triangles"] == 2
    assert r["textures"] == 1 and r["embedded_images"] == 1
    assert r["bounds"]["size"] == [1, 1, 0]
    assert glb.problems(r) == []
    assert glb.problems(r, max_triangles=1) == ["2 triangles > max 1"]
    assert glb.problems(glb.inspect_glb(make_glb(textured=False))) == ["no textures"]
    assert glb.problems(glb.inspect_glb(make_glb(textured=False)), require_textures=False) == []


@pytest.mark.parametrize("data,msg", [(b"short", "too small"), (make_glb(magic=b"glTX"), "bad magic"),
                                      (make_glb()[:-4], "header length")])
def test_parse_glb_rejects(data: bytes, msg: str) -> None:
    with pytest.raises(glb.GlbError, match=msg):
        glb.parse_glb(data)


def _ops_registry() -> ExecutorRegistry:
    reg = ExecutorRegistry()
    register_ops(reg)
    return reg


def test_glb_validate_executor(tmp_path: Path) -> None:
    async def go() -> None:
        ctx = FakeCtx(tmp_path)
        ex = _ops_registry().resolve("op.glb_validate")
        assert ex.lane == Lane.CPU
        good = await ctx.blobs.put_bytes(make_glb(), name="m.glb")
        res = await ex.run(ctx, {}, {"mesh": [good]})
        assert res.outputs["meshes"] == [good] and res.meta["triangles"] == 1
        bare = await ctx.blobs.put_bytes(make_glb(textured=False), name="b.glb")
        with pytest.raises(StepError) as e:
            await ex.run(ctx, {"require_textures": True}, {"mesh": [bare]})
        assert e.value.code == "glb_invalid" and e.value.detail["problems"] == ["no textures"]
        junk = await ctx.blobs.put_bytes(b"not a glb at all, really", name="j.glb")
        with pytest.raises(StepError) as e:
            await ex.run(ctx, {}, {"mesh": [junk]})
        assert e.value.code == "glb_invalid"

    asyncio.run(go())


def test_sheet_pivot_bottom(tmp_path: Path) -> None:
    async def go() -> None:
        ctx = FakeCtx(tmp_path)
        import io
        buf = io.BytesIO()
        Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(buf, "PNG")
        ref = await ctx.blobs.put_bytes(buf.getvalue(), name="f.png")
        sheet = _ops_registry().resolve("op.sheet")
        res = await sheet.run(ctx, {"cols": 8, "pivot": "bottom"}, {"images": [ref] * 2})
        assert res.meta["pivot"] == [50, 94]
        info = await ctx.blobs.put_bytes(json.dumps({"pivot": [256, 416.4], "width": 512, "height": 512}).encode())
        res = await sheet.run(ctx, {"cols": 8}, {"images": [ref] * 2, "pivot": [info]})
        assert res.meta["pivot"] == [50, 81]
        junk = await ctx.blobs.put_bytes(b"{}")
        with pytest.raises(StepError, match="bad pivot"):
            await sheet.run(ctx, {"cols": 8}, {"images": [ref], "pivot": [junk]})

    asyncio.run(go())


# recipes

def test_p3_recipes_load(tmp_path: Path) -> None:
    recipes = load_recipes(Settings().recipes_dir)
    assert {"prop-3d", "sprite-8dir-3d", "sprite-8dir-glb"} <= recipes.keys()

    reg = _ops_registry()
    register_gates(reg)
    settings = Settings(data_dir=tmp_path)
    reg.register(BlenderRenderDirsExecutor(settings, object()))  # type: ignore[arg-type]
    reg.register(BlenderCleanupExecutor(settings, object()))  # type: ignore[arg-type]
    reg.register(BlenderGameReadyExecutor(settings, object()))  # type: ignore[arg-type]
    reg.register_prefix("comfy.", lambda kind: type("C", (), {"kind": kind, "lane": Lane.GPU})())  # type: ignore[arg-type, return-value]
    for rid in ("prop-3d", "sprite-8dir-3d", "sprite-8dir-glb"):
        for s in recipes[rid].steps:
            reg.resolve(s.kind)

    r5 = recipes["sprite-8dir-3d"]
    assert [s.id for s in r5.steps] == ["concept", "pick", "mesh", "cleanup", "render", "dirs", "ndirs",
                                       "down", "ndown", "fx", "nfx", "sheet", "nsheet"]
    vals = r5.coerce_inputs({"prompt": "knight", "mirror": "true", "res": 128})
    render = r5.resolve_params(r5.step("render"), vals)
    assert render["mirror"] is True and render["res"] == 128 and render["elevation"] == 30
    assert r5.step("sheet").bindings["pivot"].source == "render"
    mesh = r5.resolve_params(r5.step("mesh"), vals)
    assert mesh["186.target_face_count"] == 30000
    assert r5.step("mesh").bindings["image"].source == "pick"
    assert {k: str(v) for k, v in r5.outputs.items()} == {"final": "sheet.sheet", "meta": "sheet.meta",
                                                           "frames": "fx.images", "normal_sheet": "nsheet.sheet",
                                                           "normal_meta": "nsheet.meta"}

    r4 = recipes["prop-3d"]
    v4 = r4.coerce_inputs({"prompt": "barrel", "texture": 1024})
    assert r4.resolve_params(r4.step("mesh"), v4)["288.value"] == 1024
    assert r4.resolve_params(r4.step("turntable"), v4) == {"count": 16, "res": 256, "elevation": 20}
    assert str(r4.outputs["final"]) == "approve.meshes"
    with pytest.raises(ValueError):
        r4.coerce_inputs({"prompt": "barrel", "texture": 3000})


def test_render_dirs_emits_pivot_meta(tmp_path: Path) -> None:
    class FakeRender:
        version = "5.2"

        async def call(self, cmd: str, params: dict[str, Any], on_event: Any = None, timeout: float = 0) -> Any:
            out = Path(params["out_dir"])
            frames = []
            for a in (0, 45):
                f = out / f"dir_{a:03d}.png"
                Image.new("RGBA", (8, 8)).save(f)
                frames.append({"angle": a, "file": str(f)})
            return {"frames": frames, "ortho_scale": 1.5, "engine": "BLENDER_EEVEE", "res": 8, "supersample": 2,
                    "elevation": 30, "radius": 0.5, "height": 1.0, "verts": 3, "total_s": 0.1, "pivot": [4, 6.5]}

    async def go() -> None:
        ctx = FakeCtx(tmp_path)
        src = await ctx.blobs.put_bytes(make_glb(), name="m.glb")
        ex = BlenderRenderDirsExecutor(Settings(data_dir=tmp_path), FakeRender())  # type: ignore[arg-type]
        res = await ex.run(ctx, {"count": 2}, {"mesh": [src]})
        assert len(res.outputs["images"]) == 2
        info = json.loads(ctx.blobs.path(res.outputs["meta"][0].hash).read_bytes())
        assert info["pivot"] == [4, 6.5] and info["width"] == 8 and info["angles"] == [0, 45]

    asyncio.run(go())


JOINTS = ("head_top neck pelvis shoulder_l elbow_l wrist_l hand_tip_l shoulder_r elbow_r wrist_r hand_tip_r "
          "hip_l knee_l ankle_l foot_tip_l hip_r knee_r ankle_r foot_tip_r").split()


class FakeAnimBlender:
    version = "5.2"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, cmd: str, params: dict[str, Any], on_event: Any = None, timeout: float = 0) -> Any:
        self.calls.append((cmd, params))
        frames = []
        for f in range(params["frames"]):
            for a in (0, 45, 90, 135, 180, 225, 270, 315):
                p = Path(params["out_dir"]) / f"dir_{a:03d}_f{f:02d}.png"
                Image.new("RGBA", (8, 8), (a, f, 0, 255)).save(p)
                frames.append({"angle": a, "frame": f, "file": str(p)})
        return {"frames": frames, "frames_per_dir": params["frames"], "res": 512, "elevation": 30,
                "ortho_scale": 2.0, "pivot": [256, 400], "weights": "distance", "engine": "BLENDER_EEVEE",
                "total_s": 1.0}


def test_animate_dirs_executor_orders_direction_major(tmp_path: Path) -> None:
    from studio_core.adapters.blender import BlenderAnimateDirsExecutor

    async def go() -> None:
        ctx = FakeCtx(tmp_path)
        mesh = await ctx.blobs.put_bytes(make_glb(), name="m.glb", mime="model/gltf-binary")
        joints = await ctx.blobs.put_bytes(json.dumps({"joints": {j: [10, 20] for j in JOINTS}}).encode(),
                                           name="result.json", mime="application/json")
        front = await ctx.blobs.put_bytes(json.dumps({"pivot": [256, 480], "width": 512, "height": 512,
                                                      "ortho_scale": 1.3}).encode(), name="render.json",
                                          mime="application/json")
        fake = FakeAnimBlender()
        ex = BlenderAnimateDirsExecutor(Settings(data_dir=tmp_path), fake)  # type: ignore[arg-type]
        res = await ex.run(ctx, {"frames": 3}, {"mesh": [mesh], "joints": [joints], "front": [front]})
        cmd, params = fake.calls[0]
        assert cmd == "animate_dirs" and params["image_w"] == 512 and params["ortho_scale"] == 1.3
        names = [r.name for r in res.outputs["images"]]
        assert names[:4] == ["dir_000_f00.png", "dir_000_f01.png", "dir_000_f02.png", "dir_045_f00.png"]
        assert len(names) == 24 and res.meta["weights"] == "distance"
        bad = await ctx.blobs.put_bytes(b'{"joints": {"neck": [1, 2]}}', name="j.json", mime="application/json")
        with pytest.raises(StepError, match="19-joint"):
            await ex.run(ctx, {"frames": 3}, {"mesh": [mesh], "joints": [bad], "front": [front]})

    asyncio.run(go())


def test_game_ready_params() -> None:
    p = cm.game_ready_params({"lods": "1,0.4,0.1", "bake": "normal"})
    assert p["mode"] == "decimate" and p["lods"] == [1.0, 0.4, 0.1] and p["bake"] == ["normal"]
    assert cm.game_ready_params({"bake": ""})["bake"] == []
    for bad in ({"mode": "quad"}, {"lods": "0.5,1"}, {"bake": "height"}, {"texture_size": 300}, {"target_faces": 10}):
        with pytest.raises(ValueError):
            cm.game_ready_params(bad)


class FakeGameReady:
    version = "5.2"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, cmd: str, params: dict[str, Any], on_event: Any = None, timeout: float = 0) -> Any:
        self.calls.append((cmd, params))
        on_event({"value": 1, "max": 6, "label": "import"})
        on_event({"label": "bake normal"})
        lods = []
        for i, r in enumerate(params["lods"]):
            f = f"{params['out_prefix']}_lod{i}.glb"
            Path(f).write_bytes(make_glb())
            lods.append({"file": f, "faces": int(params["target_faces"] * r)})
        maps = {}
        for name in params["bake"]:
            f = str(Path(params["out_prefix"]).with_name(f"{name}.png"))
            Path(f).write_bytes(b"\x89PNG")
            maps[name] = f
        combined = f"{params['out_prefix']}_lods.glb"
        Path(combined).write_bytes(make_glb())
        return {"lods": lods, "maps": maps, "remesh": "voxel+decimate", "combined": combined}


def test_game_ready_executor(tmp_path: Path) -> None:
    async def go() -> None:
        ctx = FakeCtx(tmp_path)
        src = await ctx.blobs.put_bytes(make_glb(), name="axe_clean.glb", mime="model/gltf-binary")
        fake = FakeGameReady()
        ex = BlenderGameReadyExecutor(Settings(data_dir=tmp_path), fake)  # type: ignore[arg-type]
        res = await ex.run(ctx, {"target_faces": 2000}, {"mesh": [src]})
        cmd, params = fake.calls[0]
        assert cmd == "game_ready" and params["mode"] == "remesh" and params["lods"] == [1.0, 0.5, 0.25]
        assert params["name"] == "axe"
        assert [m.name for m in res.outputs["meshes"]] == ["axe_lod0.glb", "axe_lod1.glb", "axe_lod2.glb", "axe_lods.glb"]
        assert sorted(m.name for m in res.outputs["maps"]) == ["axe_ao.png", "axe_normal.png"]
        assert res.meta["lods"][1] == {"faces": 1000} and res.meta["maps"] == ["ao", "normal"]
        off = await ex.run(ctx, {"mode": "off"}, {"mesh": [src]})
        assert off.outputs["meshes"] == [src] and len(fake.calls) == 1
        with pytest.raises(StepError, match="blender_bad_params"):
            await ex.run(ctx, {"mode": "quad"}, {"mesh": [src]})

    asyncio.run(go())
