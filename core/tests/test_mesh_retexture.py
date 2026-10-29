import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from studio_core.adapters.blender import retex_math as rm
from studio_core.adapters.blender.retexture import (BlenderRetexApplyExecutor, BlenderViewsRenderExecutor, map_roles,
                                                    mesh_ext)
from studio_core.adapters.comfy.retexture import RetexPaintExecutor, composite
from studio_core.config import Settings
from studio_core.contracts import StepError, StepResult
from studio_core.engine.kinds import CATALOG
from studio_core.engine.library import validate
from studio_core.engine.recipe import load_recipe_file
from studio_core.ops.pbr import Pbr
from studio_core.ops.retexture import REPORT_FILE, ProjectBake, load_views, save_views
from test_ops_executors import FakeCtx, put_img

RECIPES = Path(__file__).resolve().parents[1] / "studio_core" / "recipes"
FACES = [  # outward normal, quad corners
    ((1, 0, 0), [(1, -1, -1), (1, 1, -1), (1, 1, 1), (1, -1, 1)]),
    ((-1, 0, 0), [(-1, 1, -1), (-1, -1, -1), (-1, -1, 1), (-1, 1, 1)]),
    ((0, 1, 0), [(1, 1, -1), (-1, 1, -1), (-1, 1, 1), (1, 1, 1)]),
    ((0, -1, 0), [(-1, -1, -1), (1, -1, -1), (1, -1, 1), (-1, -1, 1)]),
    ((0, 0, 1), [(-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)]),
    ((0, 0, -1), [(-1, 1, -1), (1, 1, -1), (1, -1, -1), (-1, -1, -1)]),
]
FACE = {"+x": 0, "-x": 1, "+y": 2, "-y": 3, "+z": 4, "-z": 5}


def cube(mirror: bool = False) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Unit cube triangle soup; UVs one face per cell of a 3×2 atlas, or every face on the same cell."""
    pos, nrm, uv = [], [], []
    for i, (n, quad) in enumerate(FACES):
        c = 0 if mirror else i
        u0, v0, m = (c % 3) / 3, (c // 3) / 2, 0.01
        u1, v1 = u0 + 1 / 3, v0 + 1 / 2
        q_uv = [(u0 + m, v0 + m), (u1 - m, v0 + m), (u1 - m, v1 - m), (u0 + m, v1 - m)]
        for a, b, c2 in ((0, 1, 2), (0, 2, 3)):
            pos.append([np.array(quad[k]) * 0.5 for k in (a, b, c2)])
            nrm.append([n] * 3)
            uv.append([q_uv[k] for k in (a, b, c2)])
    return np.array(pos, float), np.array(nrm, float), np.array(uv, float)


def face_texels(uv: np.ndarray, face: int, size: int) -> np.ndarray:
    tri, _, _, _ = rm.raster(rm.uv_pixels(uv, size), np.zeros(uv.shape[:2]), size, size)
    return (tri == 2 * face) | (tri == 2 * face + 1)


def test_cameras_and_raster_frame_the_cube():
    pos, nrm, _ = cube()
    cams = rm.cameras(pos, 6, 128)
    assert [c.name for c in cams] == ["front", "right", "left", "back", "top", "bottom"]
    front = rm.render_view(cams[0], pos, nrm)
    hit = front.tri >= 0
    ys, xs = np.nonzero(hit)
    assert abs((xs.min() + xs.max()) / 2 - 63.5) <= 1 and 0.2 < hit.mean() < 0.6
    # Front camera sits at -Y: the -Y face is nearest and faces it squarely.
    assert set(np.unique(front.tri[64, 40:88])) <= {2 * FACE["-y"], 2 * FACE["-y"] + 1}
    assert front.cos[64, 64] > 0.95
    top = rm.render_view(cams[4], pos, nrm)
    assert set(np.unique(top.tri[top.tri >= 0])) <= {2 * FACE["+z"], 2 * FACE["+z"] + 1}
    d = rm.depth_image(front)
    assert d[0, 0] == 0 and d[64, 64] > 200


def test_uv_overlap_detects_stacked_islands():
    _, _, uv = cube()
    ov, cov = rm.uv_overlap(uv, 256)
    assert ov < 0.005 and 0.8 < cov < 1.0
    _, _, stacked = cube(mirror=True)
    ov2, _ = rm.uv_overlap(stacked, 256)
    assert ov2 > 0.9


def test_blend_weight_prefers_square_views():
    w = rm.blend_weight(np.array([1.0, 0.5, 0.0]), 4.0)
    assert w[0] == 1 and abs(w[1] - 0.0625) < 1e-9 and w[2] == 0


def _bake_views(n: int, size: int = 128, tex: int = 128):
    pos, nrm, uv = cube()
    cams = rm.cameras(pos, n, size)
    surf = rm.uv_surface(pos, nrm, uv, tex)
    acc = rm.Accumulator(surf, 4.0)
    colors = {}
    for i, cam in enumerate(cams):
        v = rm.render_view(cam, pos, nrm)
        col = np.zeros(3)
        col[i % 3] = 1.0
        colors[cam.name] = col
        img = np.ones((size, size, 3)) * col
        acc.add(rm.visibility(surf, v), img)
    return acc, colors, uv


def test_six_views_paint_every_visible_texel():
    acc, _, _ = _bake_views(6)
    res = rm.finish_texture(acc)
    assert res.texel_unpainted_pct == 0.0
    assert res.painted[res.covered].all()
    pos, nrm, uv = cube()
    assert rm.visible_unpainted(acc.surf, acc.painted(), pos, nrm, uv, 12, 96) == 0.0


def test_four_views_leave_only_the_bottom_unpainted():
    acc, _, uv = _bake_views(4)
    res = rm.finish_texture(acc)
    bottom = face_texels(uv, FACE["-z"], 128)
    top = face_texels(uv, FACE["+z"], 128)
    assert not res.painted[bottom].any() and res.painted[top].all()
    assert abs(res.texel_unpainted_pct - 100 / 6) < 1.5
    pos, nrm, _ = cube()
    seen_bottom = rm.visible_unpainted(acc.surf, acc.painted(), pos, nrm, uv, 12, 96)
    assert 3 < seen_bottom < 20
    # Holes are filled from neighbours, not left black.
    assert res.texture[bottom].mean() > 0.05


def test_square_view_dominates_its_face():
    acc, colors, uv = _bake_views(6)
    tex = rm.finish_texture(acc).texture
    front = tex[face_texels(uv, FACE["-y"], 128)].mean(0)
    right = tex[face_texels(uv, FACE["+x"], 128)].mean(0)
    assert np.argmax(front) == np.argmax(colors["front"]) and front.max() > 0.95
    assert np.argmax(right) == np.argmax(colors["right"]) and right.max() > 0.95


def test_known_projection_marks_what_earlier_views_painted():
    pos, nrm, uv = cube()
    cams = rm.cameras(pos, 8, 128)
    surf = rm.uv_surface(pos, nrm, uv, 128)
    acc = rm.Accumulator(surf)
    front = rm.render_view(cams[0], pos, nrm)
    acc.add(rm.visibility(surf, front), np.ones((128, 128, 3)) * [1.0, 0.0, 0.0])
    fr = rm.render_view(cams[1], pos, nrm)
    rgb, known = rm.known_projection(fr, uv, acc, 0.45, 0.4, fr.cos * fr.edge)
    obj = fr.tri >= 0
    seen_front = np.isin(fr.tri, [2 * FACE["-y"], 2 * FACE["-y"] + 1])
    assert known[seen_front].mean() > 0.75 and not known[obj & ~seen_front].any()
    assert np.allclose(rgb[known], [1, 0, 0], atol=1e-3)
    # The same face is repainted when the new view sees it far more squarely than the first one did.
    _, again = rm.known_projection(fr, uv, acc, 0.45, -1.0, fr.cos * fr.edge)
    assert not again.any()


def test_composite_keeps_known_pixels():
    base = np.zeros((32, 32, 3))
    gen = np.ones((32, 32, 3))
    regen = np.zeros((32, 32), bool)
    regen[:, 16:] = True
    out, soft = composite(base, gen, regen, 3)
    assert out[:, 20:].min() == 1 and out[:, :10].max() == 0 and 0 < out[5, 15, 0] < 1


def _views_blob_args(n: int = 6, size: int = 96):
    pos, nrm, uv = cube()
    return pos, nrm, uv, rm.cameras(pos, n, size)


async def _put_views(ctx: FakeCtx, tmp_path: Path, n: int = 6, size: int = 96, info: dict | None = None):
    pos, nrm, uv, cams = _views_blob_args(n, size)
    p = tmp_path / "views.npz"
    save_views(p, pos, nrm, uv, cams, info or {"stem": "crate", "uv_mode": "auto", "unwrapped": False,
                                               "overlap": 0.0})
    return await ctx.blobs.put_bytes(p.read_bytes(), name="retex_views.npz"), cams


async def test_project_bake_op(tmp_path: Path):
    ctx = FakeCtx(tmp_path)
    views, cams = await _put_views(ctx, tmp_path)
    imgs = [await put_img(ctx, Image.new("RGB", (96, 96), (200, 30 * i, 20)), f"p{i}.png") for i in range(len(cams))]
    res = await ProjectBake().run(ctx, {"texture": 512}, {"views": [views], "images": imgs})
    tex = Image.open(ctx.blobs.path(res.outputs["texture"][0].hash))
    assert tex.size == (512, 512) and res.outputs["texture"][0].name == "crate.png"
    rep = json.loads(ctx.blobs.path(res.outputs["report"][0].hash).read_bytes())
    assert rep["unpainted_pct"] == 0.0 and rep["texture"] == 512 and len(rep["views"]) == 6
    assert res.outputs["report"][0].name == REPORT_FILE
    with pytest.raises(StepError):
        await ProjectBake().run(ctx, {}, {"views": [views], "images": imgs[:2]})


class FakeGen:
    entry = {"outputs": [{"node": "16"}]}

    def __init__(self) -> None:
        self.calls: list[tuple[dict[str, Any], list]] = []

    def cache_material(self, params: dict[str, Any]) -> str:
        return "wf"

    async def run(self, ctx, params, inputs) -> StepResult:
        self.calls.append((params, inputs["image"]))
        k = len(self.calls)
        ctx.progress(0.5, 1.0)
        ref = await put_img(ctx, Image.new("RGB", (96, 96), (40 * k, 100, 200)), f"gen{k}.png")
        return StepResult(outputs={"images": [ref]}, meta={"seconds": 0.1})


async def test_paint_runs_views_in_order_and_masks_only_new_area(tmp_path: Path):
    ctx = FakeCtx(tmp_path)
    views, cams = await _put_views(ctx, tmp_path, 8)
    gen = FakeGen()
    ex = RetexPaintExecutor(lambda kind: gen)
    res = await ex.run(ctx, {"prompt": "old wooden crate", "seed": 7, "texture": 256, "_free_before": True},
                       {"views": [views]})
    assert len(res.outputs["images"]) == 8 and len(res.outputs["masks"]) == 8
    assert [c[0]["14.seed"] for c in gen.calls][:2] == [7, 8]
    assert "front view" in gen.calls[0][0]["10.text"] and "old wooden crate" in gen.calls[0][0]["10.text"]
    assert gen.calls[0][0].get("_free_before") and not gen.calls[1][0].get("_free_before")
    assert all(len(c[1]) == 3 for c in gen.calls)
    first_mask = np.asarray(Image.open(ctx.blobs.path(gen.calls[0][1][2].hash)).convert("L"))
    second_mask = np.asarray(Image.open(ctx.blobs.path(gen.calls[1][1][2].hash)).convert("L"))
    assert first_mask.min() == 255 and 0 < (second_mask > 127).mean() < 0.5
    rep = json.loads(ctx.blobs.path(res.outputs["report"][0].hash).read_bytes())
    assert rep["views"][0]["regenerated"] == 1.0 and rep["views"][1]["regenerated"] < 1.0
    assert rep["unpainted_after"] == [0.0] and len(load_views(ctx.blobs.path(res.outputs["views"][0].hash)).cams) == 8
    assert res.outputs["preview"][0].name == "painted_views.png"
    with pytest.raises(StepError):
        await ex.run(ctx, {"prompt": ""}, {"views": [views]})


async def test_paint_adds_a_view_for_what_the_fixed_ones_miss(tmp_path: Path):
    ctx = FakeCtx(tmp_path)
    views, _ = await _put_views(ctx, tmp_path, 4)
    gen = FakeGen()
    res = await RetexPaintExecutor(lambda kind: gen).run(ctx, {"prompt": "crate", "seed": 1, "texture": 256,
                                                               "extra_views": 2}, {"views": [views]})
    rep = json.loads(ctx.blobs.path(res.outputs["report"][0].hash).read_bytes())
    extra = rep["views"][4]
    assert extra["view"] == "extra1" and extra["elevation"] < -25 and "from below" in gen.calls[4][0]["10.text"]
    assert rep["unpainted_after"][0] > 2 and rep["unpainted_after"][-1] < 2
    cams = load_views(ctx.blobs.path(res.outputs["views"][0].hash)).cams
    assert len(cams) == len(res.outputs["images"]) == 5 and cams[4].name == "extra1"


class FakeBlender:
    version = "fake"

    def __init__(self, uv_mode: str = "keep") -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.uv_mode = uv_mode

    async def call(self, cmd: str, params: dict[str, Any], on_event=None, timeout: float = 0) -> dict[str, Any]:
        assert cmd == "script" and params["unrestricted"]
        self.calls.append((params["code"], params))
        sp = params["params"]
        Path(params["out"]).write_bytes(b"glTF-out")
        if "prepare" in params["code"]:
            pos, nrm, uv = cube()
            Path(sp["original"]).write_bytes(b"glTF-orig")
            np.savez_compressed(sp["npz"], pos=pos.astype(np.float32), nrm=nrm.astype(np.float32),
                                uv=uv.astype(np.float32))
            Path(sp["report"]).write_text(json.dumps({"meshes": 1, "tris": 12, "had_uv": True, "uv_mode": sp["uv"],
                                                      "unwrapped": False, "overlap_before": 0.0, "overlap": 0.0,
                                                      "warnings": []}))
        return {"out": params["out"], "tris": 12, "materials": ["crate"], "stdout": ""}


async def test_views_render_with_fake_blender(tmp_path: Path):
    ctx = FakeCtx(tmp_path)
    mesh = await ctx.blobs.put_bytes(b"fbx-bytes", name="Crate.fbx", mime="application/octet-stream")
    fake = FakeBlender()
    ex = BlenderViewsRenderExecutor(Settings(data_dir=tmp_path / "d"), fake)  # type: ignore[arg-type]
    res = await ex.run(ctx, {"views": 6, "res": 256, "uv": "keep"}, {"mesh": [mesh]})
    code, req = fake.calls[0]
    assert "retex_bpy.prepare" in code and req["params"]["src"].endswith("src.fbx") and req["params"]["uv"] == "keep"
    assert Path(req["params"]["src"]).read_bytes() == b"fbx-bytes"
    assert [r.name for r in res.outputs["depth"]][:2] == ["depth_front.png", "depth_right.png"]
    assert len(res.outputs["normals"]) == 6 and res.outputs["mesh"][0].name == "Crate_base.glb"
    assert res.outputs["original"][0].name == "Crate_original.glb"
    v = load_views(ctx.blobs.path(res.outputs["views"][0].hash))
    assert len(v.cams) == 6 and v.cams[0].size == 256 and v.info["stem"] == "Crate" and len(v.pos) == 12
    depth = np.asarray(Image.open(ctx.blobs.path(res.outputs["depth"][0].hash)))
    assert depth.shape == (256, 256) and depth.max() > 200
    with pytest.raises(StepError):
        await ex.run(ctx, {"views": 5}, {"mesh": [mesh]})
    assert mesh_ext("x.bin", "model/obj") == ".obj"
    with pytest.raises(StepError):
        mesh_ext("x.blend", "application/octet-stream")


async def test_retex_apply_wires_pbr_maps(tmp_path: Path):
    ctx = FakeCtx(tmp_path)
    base = await ctx.blobs.put_bytes(b"glTF-base", name="Crate_base.glb", mime="model/gltf-binary")
    maps = [await put_img(ctx, Image.new("RGB", (8, 8), (i * 40, 0, 0)), f"Crate_{r}.png")
            for i, r in enumerate(("albedo", "normal", "height", "roughness", "metallic", "ao"))]
    fake = FakeBlender()
    ex = BlenderRetexApplyExecutor(Settings(data_dir=tmp_path / "d"), fake)  # type: ignore[arg-type]
    res = await ex.run(ctx, {}, {"mesh": [base], "maps": maps})
    code, req = fake.calls[0]
    assert "retex_bpy.apply" in code and req["glb_in"].endswith("base.glb")
    assert set(req["params"]["maps"]) == {"albedo", "normal", "roughness", "metallic", "ao"}
    assert req["params"]["name"] == "Crate" and res.outputs["meshes"][0].name == "Crate_retextured.glb"
    assert map_roles([("a_albedo.png", Path("x"))]) == {"albedo": Path("x")}
    with pytest.raises(StepError):
        await ex.run(ctx, {}, {"mesh": [base], "maps": maps[1:3]})


def test_recipe_loads_and_lints():
    r = load_recipe_file(RECIPES / "mesh-retexture.yaml")
    ids = [s.id for s in r.steps]
    assert ids.index("views") < ids.index("paint") < ids.index("check") < ids.index("bake") < ids.index("apply")
    assert r.inputs["mesh"]["ext"] == ["glb", "fbx", "obj"]
    _, issues = validate((RECIPES / "mesh-retexture.yaml").read_text(encoding="utf-8"), None)
    assert issues == []
    for kind in ("blender.views_render", "retex.paint", "op.project_bake", "blender.retex_apply"):
        assert CATALOG[kind]["label"]


async def test_export_unity_and_godot(tmp_path: Path):
    from test_export_engines import _row

    from studio_core.export import godot, unity
    from studio_core.store.bundle import engine_ready, engine_source

    ctx = FakeCtx(tmp_path)
    views, cams = await _put_views(ctx, tmp_path)
    imgs = [await put_img(ctx, Image.new("RGB", (96, 96), (150, 90, 40)), f"p{i}.png") for i in range(len(cams))]
    bake = await ProjectBake().run(ctx, {"texture": 512}, {"views": [views], "images": imgs})
    pbr = await Pbr().run(ctx, {"seamless": False}, {"images": bake.outputs["texture"]})
    glb = await ctx.blobs.put_bytes(b"glTF-new", name="crate_retextured.glb", mime="model/gltf-binary")
    orig = await ctx.blobs.put_bytes(b"glTF-old", name="crate_original.glb", mime="model/gltf-binary")
    step = _row("approve", "gate.approve", {"meshes": [glb, orig], "material": pbr.outputs["material"],
                                            "maps": pbr.outputs["maps"], "report": bake.outputs["report"]})
    assert engine_ready(step, ctx.blobs)
    src = engine_source(step, [], ctx.blobs, "Crate")
    assert src.retexture and src.retexture["texture"] == 512 and len(src.pbr) == 1
    u = dict(unity.export(src))
    assert {"Models/crate_retextured.glb", "Models/crate_original.glb", "PBR/Crate.mat", "PBR/Crate_Standard.mat",
            "Crate_retexture.json"} <= set(u)
    std = u["PBR/Crate_Standard.mat"].decode()
    albedo_guid = u["PBR/Crate_albedo.png.meta"].decode().split("guid: ")[1].split("\n")[0]
    assert "0000000000000000f000000000000000" in std and f"guid: {albedo_guid}" in std and "_NORMALMAP" in std
    assert "_METALLICGLOSSMAP" in std and "_OcclusionMap" in std
    guids = [ln for k, b in u.items() if k.endswith(".meta") for ln in b.decode().splitlines()
             if ln.startswith("guid:")]
    assert len(guids) == len(set(guids))
    assert "Retexture" in u["README.txt"].decode() and "Crate_Standard.mat" in u["README.txt"].decode()
    g = dict(godot.export(src))
    assert {"PBR/Crate.tres", "Crate_retexture.json"} <= set(g) and any(k.endswith("crate_retextured.glb") for k in g)
    assert 'type="StandardMaterial3D"' in g["PBR/Crate.tres"].decode()
