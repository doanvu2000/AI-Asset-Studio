from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image, ImageDraw

from studio_core.adapters.claude import ClaudeReply
from studio_core.adapters.trim import TrimLayoutExecutor, TrimStripsExecutor
from studio_core.contracts import StepError, StepResult
from studio_core.engine.kinds import CATALOG
from studio_core.engine.library import validate
from studio_core.engine.recipe import load_recipe_file
from studio_core.ops.decal import DecalPack, key_alpha
from studio_core.ops.pano import seam_report
from studio_core.ops.trim import (BASE, HEIGHTS, TrimSheet, fit_total, gen_size, make_strip, make_strip_repeats,
                                  normalize_layout, preset_layout, strip_ranges)
from test_ops_executors import FakeCtx

RECIPES = Path(__file__).resolve().parents[1] / "studio_core" / "recipes"


def _noise(w: int, h: int, seed: int = 0) -> Image.Image:
    rng = np.random.default_rng(seed)
    a = rng.random((h // 8, w // 8, 3))
    img = Image.fromarray((a * 255).astype(np.uint8), "RGB").resize((w, h), Image.Resampling.BICUBIC)
    # A left-to-right brightness ramp: a raw tile seams hard at the wrap until the quilt fixes it.
    ramp = np.linspace(0.6, 1.0, w)[None, :, None]
    return Image.fromarray(np.clip(np.asarray(img) * ramp, 0, 255).astype(np.uint8), "RGB")


def test_layout_heights_always_sum_to_sheet() -> None:
    assert sum(fit_total([256] * 3)) == BASE and sum(fit_total([512] * 7)) == BASE
    rng = np.random.default_rng(3)
    for _ in range(50):
        hs = fit_total([int(h) for h in rng.choice(HEIGHTS, rng.integers(2, 16))])
        assert sum(hs) == BASE and all(h in HEIGHTS for h in hs)
    lay = normalize_layout({"strips": [{"name": "Brick Wall", "px": 300, "prompt": "red brick"},
                                       {"name": "brick wall", "px": 40}, {"name": "", "px": "x"}]}, "castle")
    names = [s["name"] for s in lay["strips"]]
    assert names[:3] == ["brick_wall", "brick_wall_2", "strip_3"] and sum(s["px"] for s in lay["strips"]) == BASE
    assert lay["strips"][0]["gen_prompt"].startswith("red brick, red-brown fired clay colour, castle, seamless "
                                                     "horizontally tileable")
    assert lay["strips"][2]["gen_prompt"].startswith("strip_3, castle, seamless")
    with pytest.raises(ValueError):
        normalize_layout({"strips": [{"name": "a", "px": 64}]})
    mod = preset_layout("modular", "stone", 2, 0)
    by = {s["name"]: s for s in mod["strips"]}
    assert by["wall"]["aspect"] == 0.5 and by["floor"]["aspect"] == 1.0 and "aspect" not in by["cap"]
    assert preset_layout("modular", "x", 4, 0)["strips"][0]["aspect"] == 1.0
    r = strip_ranges(mod["strips"])
    assert r[0]["y0"] == 0 and r[-1]["y1"] == BASE and r[0]["v1"] == 1.0 and r[-1]["v0"] == 0.0
    assert r[0]["v0_safe"] > r[0]["v0"] and r[0]["v1_safe"] < r[0]["v1"]
    w, h = gen_size(0.5)
    assert w % 16 == h % 16 == 0 and abs(w / h * 0.875 - 0.5) < 0.05
    assert make_strip_repeats(512, 0.5) == 8 and make_strip_repeats(128, None) == 8


def test_strip_is_seamless_along_u_only() -> None:
    src = _noise(1536, 512)
    raw = seam_report(np.asarray(src.resize((2048, 128)), np.float32) / 255)["score"]
    band, info = make_strip(src, 2048, 128)
    assert band.shape == (128, 2048, 3) and info["repeats"] == 8 and info["tile_w"] == 256
    assert info["seam"]["score"] >= 90 > raw
    forced, finfo = make_strip(_noise(704, 1216, 1), 2048, 512, aspect=0.5)
    assert forced.shape == (512, 2048, 3) and finfo["repeats"] == 8 and finfo["seam"]["score"] >= 90


class FakeQuery:
    def __init__(self, reply: Any):
        self.reply = reply
        self.calls: list[tuple[Any, Any]] = []

    async def __call__(self, content, options) -> ClaudeReply:
        self.calls.append((content, options))
        return ClaudeReply(self.reply, "", {"cost_usd": 0.01, "model": options.model})


async def test_layout_executor_claude_and_preset(tmp_path: Path) -> None:
    ctx = FakeCtx(tmp_path)
    q = FakeQuery({"strips": [{"name": "wall", "px": 512, "prompt": "plaster"},
                              {"name": "molding", "px": 128, "prompt": "cornice"}]})
    res = await TrimLayoutExecutor(None, q).run(ctx, {"style": "baroque"}, {})
    lay = json.loads(ctx.blobs.path(res.outputs["json"][0].hash).read_bytes())
    assert sum(s["px"] for s in lay["strips"]) == BASE and lay["strips"][0]["name"] == "wall"
    assert "baroque" in q.calls[0][0][0]["text"] and res.meta["mode"] == "claude"
    q2 = FakeQuery({})
    res = await TrimLayoutExecutor(None, q2).run(ctx, {"style": "sci-fi", "mode": "preset", "preset": "modular",
                                                       "grid": 4}, {})
    assert not q2.calls and res.meta["strips"] == 10 and res.meta["names"][0] == "wall"
    with pytest.raises(StepError, match="claude_bad_output|at least 2"):
        await TrimLayoutExecutor(None, FakeQuery({"strips": []})).run(ctx, {"style": "x"}, {})
    with pytest.raises(StepError, match="style"):
        await TrimLayoutExecutor(None, q).run(ctx, {"style": " "}, {})


class FakeGen:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def cache_material(self, params: dict[str, Any]) -> str:
        return "wf"

    async def run(self, ctx, params, inputs) -> StepResult:
        self.calls.append(params)
        w, h = params["57:13.width"], params["57:13.height"]
        img = _noise(w, h, len(self.calls))
        path = ctx.job_dir / f"g{len(self.calls)}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        img.save(path)
        return StepResult(outputs={"images": [await ctx.blobs.put_file(path, name="z.png", mime="image/png")]})


async def _layout_ref(ctx: FakeCtx, lay: dict[str, Any]):
    return await ctx.blobs.put_bytes(json.dumps(lay).encode(), name="trim_layout.json", mime="application/json")


async def test_strips_then_sheet(tmp_path: Path) -> None:
    ctx = FakeCtx(tmp_path)
    lay = normalize_layout([{"name": "wall", "px": 1024, "prompt": "brick", "aspect": 1.0},
                            {"name": "trim", "px": 512, "prompt": "molding"},
                            {"name": "edge", "px": 512, "prompt": "edge"}], "stone")
    assert [s["px"] for s in lay["strips"]] == [512, 512, 512, 512]
    ref = await _layout_ref(ctx, lay)
    gen = FakeGen()
    res = await TrimStripsExecutor(lambda k: gen).run(ctx, {"seed": 7, "_free_before": True}, {"layout": [ref]})
    assert [r.name for r in res.outputs["images"]] == ["00_wall.png", "01_trim.png", "02_edge.png", "03_filler_4.png"]
    assert [c["57:3.seed"] for c in gen.calls] == [7, 8, 9, 10] and "_free_before" not in gen.calls[1]
    assert (gen.calls[0]["57:13.width"], gen.calls[0]["57:13.height"]) == gen_size(1.0)
    assert (gen.calls[1]["57:13.width"], gen.calls[1]["57:13.height"]) == (1536, 512)
    assert gen.calls[0]["57:27.text"].startswith("brick, red-brown fired clay colour, stone, seamless horizontally")
    assert gen.calls[1]["57:27.text"].startswith("molding, stone, seamless")

    out = await TrimSheet().run(ctx, {"size": 1024, "asset": "Castle Trim"},
                                {"images": res.outputs["images"], "layout": [ref]})
    trim = json.loads(ctx.blobs.path(out.outputs["trim"][0].hash).read_bytes())
    assert trim["type"] == "asset-studio/trim-sheet" and trim["size"] == 1024 and len(trim["strips"]) == 4
    assert trim["strips"][1]["v0"] == 0.5 and trim["strips"][1]["v1"] == 0.75 and trim["seam_min"] >= 85
    atlas = Image.open(ctx.blobs.path(out.outputs["images"][0].hash))
    assert atlas.size == (1024, 1024)
    mat = json.loads(ctx.blobs.path(out.outputs["material"][0].hash).read_bytes())
    assert mat["type"] == "asset-studio/pbr-material" and set(mat["materials"][0]["maps"]) == {
        "albedo", "normal", "height", "roughness", "metallic", "ao"}
    assert mat["materials"][0]["name"] == "castle_trim" and len(out.outputs["maps"]) == 6
    with pytest.raises(StepError, match="strip images"):
        await TrimSheet().run(ctx, {}, {"images": res.outputs["images"][:2], "layout": [ref]})


def _crack_image(size: int = 512) -> Image.Image:
    img = Image.new("RGB", (size, size), (250, 250, 248))
    d = ImageDraw.Draw(img)
    d.line([(100, 120), (250, 260), (400, 300)], fill=(40, 35, 30), width=6)
    d.line([(250, 260), (260, 420)], fill=(60, 55, 50), width=3)
    return img


async def test_decal_pack_keys_thin_marks_and_fades_edges(tmp_path: Path) -> None:
    ctx = FakeCtx(tmp_path)
    src = _crack_image()
    a, bg = key_alpha(np.asarray(src, np.float32) / 255)
    assert bg.min() > 0.95 and a[260, 250] > 0.9 and a[10, 10] == 0
    ref = await ctx.blobs.put_bytes(_png(src), name="c.png", mime="image/png")
    res = await DecalPack().run(ctx, {"names": "Wall cracks", "size": 256, "width_m": 2},
                                {"images": [ref], "cutout": []})
    doc = json.loads(ctx.blobs.path(res.outputs["decals"][0].hash).read_bytes())
    e = doc["decals"][0]
    assert e["name"] == "wall_cracks" and 0.01 < e["coverage"] < 0.5 and e["width_m"] == 2.0
    alb = np.asarray(Image.open(ctx.blobs.path(res.outputs["images"][0].hash)))
    assert alb.shape == (256, 256, 4) and alb[..., 3].max() == 255
    assert alb[0, :, 3].max() == 0 and alb[:, 0, 3].max() == 0
    nrm = np.asarray(Image.open(ctx.blobs.path(res.outputs["normals"][0].hash)))
    assert nrm.shape == (256, 256, 4) and np.array_equal(nrm[..., 3], alb[..., 3])
    blank = await ctx.blobs.put_bytes(_png(Image.new("RGB", (64, 64), "white")), name="b.png", mime="image/png")
    with pytest.raises(StepError, match="empty"):
        await DecalPack().run(ctx, {}, {"images": [blank]})
    with pytest.raises(StepError, match="cutout"):
        await DecalPack().run(ctx, {"alpha": "birefnet"}, {"images": [ref], "cutout": []})
    cut = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    ImageDraw.Draw(cut).ellipse((150, 150, 350, 350), fill=(200, 0, 0, 255))
    cref = await ctx.blobs.put_bytes(_png(cut), name="cut.png", mime="image/png")
    res = await DecalPack().run(ctx, {"alpha": "birefnet", "size": 256}, {"images": [ref], "cutout": [cref]})
    assert json.loads(ctx.blobs.path(res.outputs["decals"][0].hash).read_bytes())["decals"][0]["aspect"] == [1, 1]


async def test_decal_round_stain_gets_a_ragged_edge(tmp_path: Path) -> None:
    from studio_core.ops.decal import ROUND_LIMIT, roundness

    ctx = FakeCtx(tmp_path)
    img = Image.new("RGB", (512, 512), (250, 250, 248))
    ImageDraw.Draw(img).ellipse((140, 140, 370, 370), fill=(70, 55, 40))
    ref = await ctx.blobs.put_bytes(_png(img), name="s.png", mime="image/png")
    res = await DecalPack().run(ctx, {"names": "dirt stain\nround logo", "size": 256},
                                {"images": [ref, ref], "cutout": []})
    dirt, logo = json.loads(ctx.blobs.path(res.outputs["decals"][0].hash).read_bytes())["decals"]
    assert dirt["roundness"][0] > ROUND_LIMIT and dirt["irregular"] > 0 and dirt["roundness"][1] < 0.75
    # A keyed near-disc is broken up even without an organic name; a fixed 0 keeps it.
    assert logo["irregular"] > 0
    kept = await DecalPack().run(ctx, {"names": "dirt", "size": 256, "irregular": 0}, {"images": [ref], "cutout": []})
    k = json.loads(ctx.blobs.path(kept.outputs["decals"][0].hash).read_bytes())["decals"][0]
    assert k["irregular"] == 0 and k["roundness"][1] > ROUND_LIMIT
    alb = np.asarray(Image.open(ctx.blobs.path(res.outputs["images"][0].hash)))
    # Warped colour follows the warped alpha: no white key colour shows where the stain is opaque.
    solid = alb[..., 3] > 200
    assert solid.any() and alb[solid][:, :3].mean() < 120
    assert roundness(alb[..., 3] / 255) == pytest.approx(dirt["roundness"][1], abs=1e-3)


def test_trim_strip_material_colour() -> None:
    from studio_core.ops.trim import fit_material_color, strip_material

    assert strip_material("frame", "carved dark oak frame") == "wood"
    assert strip_material("planks", "grey stone slabs") == "wood"
    assert strip_material("metal", "riveted iron band") is None
    # Live layout (r7): accents and neutral materials must not tint a stone or iron strip.
    assert strip_material("stone_wall", "grey rubble stone wall with moss traces and soot stains") is None
    assert strip_material("iron_band", "black wrought iron strap with rust streaks") is None
    assert strip_material("trim", "strip with moss and rust") is None and strip_material("moss", "x") == "moss"
    assert strip_material("oak_beam", "heavy dark oak timber beam") == "wood"
    lay = normalize_layout([{"name": "beam", "px": 512, "prompt": "weathered beam"},
                            {"name": "wall", "px": 512, "prompt": "cut stone"}], "grey stone and dark oak")
    beam, wall = lay["strips"][:2]
    assert beam["material"] == "wood" and "material" not in wall
    assert beam["gen_prompt"].startswith("weathered beam, natural brown wood colour")
    grey = np.full((64, 256, 3), 0.4, np.float32) + np.random.default_rng(0).normal(0, 0.05, (64, 256, 3))
    out, s = fit_material_color(np.clip(grey, 0, 1).astype(np.float32), "wood")
    m = out.reshape(-1, 3).mean(0)
    assert s == 1.0 and m[0] > m[1] > m[2] and abs(float(m @ [0.2126, 0.7152, 0.0722]) - 0.4) < 0.02
    # Detail survives: the per-pixel variation is kept, only scaled per channel.
    assert out[..., 1].std() > 0.03
    brown = np.full((8, 8, 3), (0.46, 0.31, 0.19), np.float32)
    assert fit_material_color(brown, "wood")[1] == 0.0 and fit_material_color(grey, None)[1] == 0.0


def _png(img: Image.Image) -> bytes:
    import io

    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


async def test_export_trim_and_decals(tmp_path: Path) -> None:
    from test_export_engines import _row

    from studio_core.export import godot, unity
    from studio_core.store.bundle import engine_ready, engine_source

    ctx = FakeCtx(tmp_path)
    lay = normalize_layout([{"name": n, "px": 512} for n in ("wall", "trim", "edge", "base")], "x")
    ref = await _layout_ref(ctx, lay)
    imgs = [await ctx.blobs.put_bytes(_png(_noise(768, 256, i)), name=f"{i}.png", mime="image/png") for i in range(4)]
    sheet = await TrimSheet().run(ctx, {"size": 1024, "asset": "castle"}, {"images": imgs, "layout": [ref]})
    step = _row("approve", "gate.approve", {"maps": sheet.outputs["maps"], "material": sheet.outputs["material"],
                                            "trim": sheet.outputs["trim"], "preview": sheet.outputs["preview"]})
    src = engine_source(step, [], ctx.blobs, "castle")
    assert src.trim and src.pbr
    u = dict(unity.export(src))
    assert {"castle_trim.json", "PBR/castle.mat", "PBR/castle_albedo.png"} <= set(u)
    assert "wall (512px)" in u["README.txt"].decode()
    g = dict(godot.export(src))
    assert {"castle_trim.json", "PBR/castle.tres"} <= set(g)

    crack = await ctx.blobs.put_bytes(_png(_crack_image()), name="c.png", mime="image/png")
    dec = await DecalPack().run(ctx, {"names": "crack\nmoss", "size": 256, "irregular": 0},
                                {"images": [crack, crack]})
    step = _row("approve", "gate.approve", {"images": dec.outputs["images"], "normals": dec.outputs["normals"],
                                            "decals": dec.outputs["decals"], "preview": dec.outputs["preview"]})
    assert engine_ready(step, ctx.blobs)
    src = engine_source(step, [], ctx.blobs, "decals")
    assert [n for n, *_ in src.decals] == ["crack", "moss"]
    u = dict(unity.export(src))
    assert {"Decals/crack.png", "Decals/crack_normal.png", "Decals/crack.mat", "Decals/crack.prefab",
            "Decals/moss.prefab.meta"} <= set(u)
    mat = u["Decals/crack.mat"].decode()
    assert "guid: 9b4e681081e2b4c469111bb649e2f7ee" in mat and "- Base_Map:" in mat and "- Normal_Map:" in mat
    prefab = u["Decals/crack.prefab"].decode()
    assert "0777d029ed3dffa4692f417d4aba19ca" in prefab and "m_Size: {x: 1, y: 0.99" in prefab
    assert "wrapU: 1" in u["Decals/crack.png.meta"].decode() and "textureType: 1" in u[
        "Decals/crack_normal.png.meta"].decode()
    g = dict(godot.export(src))
    scene = g["Decals/moss.tscn"].decode()
    assert 'type="Decal"' in scene and 'path="moss_normal.png"' in scene and "size = Vector3(1, 0.5, 0.99" in scene


def test_recipes_load_and_lint() -> None:
    for rid, steps in (("trim-sheet", ["layout", "strips", "sheet", "approve"]),
                       ("decal-pack", ["gen", "rmbg", "pack", "approve"])):
        r = load_recipe_file(RECIPES / f"{rid}.yaml")
        assert [s.id for s in r.steps] == steps
        _, issues = validate((RECIPES / f"{rid}.yaml").read_text(encoding="utf-8"), None)
        assert issues == []
    assert CATALOG["op.trim_sheet"]["in"] == {"images": "image[]", "layout": "json"}
    assert CATALOG["trim.layout"]["params"]["mode"]["options"] == ["claude", "preset"]
