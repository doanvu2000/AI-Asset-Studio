from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from studio_core.adapters.claude import ClaudeReply
from studio_core.adapters.terrain import ClaudeTerrainPlanExecutor, TEXTURE_SUFFIX, normalize_plan
from studio_core.contracts import RunStatus, StepError, StepResult
from studio_core.engine.kinds import CATALOG
from studio_core.engine.library import validate
from studio_core.engine.recipe import load_recipe_file
from studio_core.ops.terrain import (HEIGHT_FILE, ROLES, TERRAIN_FILE, HeightParams, TerrainHeight, TerrainPack,
                                     TerrainSky, build_height, histogram_report, quantize16, river_field, splat8)
from test_engine_runner import Env, FakeExec
from test_ops_executors import FakeCtx, put_img

RECIPES = Path(__file__).resolve().parents[1] / "studio_core" / "recipes"

REPLY: dict[str, Any] = {
    "summary": "A volcanic island with a crater lake and a river to the southern sea.",
    "base": 0.15, "water_level": 0.1, "snowline": 0.75,
    "regions": [{"type": "mountains", "x": 0.35, "y": 0.3, "rx": 0.25, "ry": 0.18, "angle": 15, "height": 0.9},
                {"type": "hills", "x": 0.7, "y": 0.5, "rx": 0.2, "ry": 0.2, "height": 0.3},
                {"type": "plateau", "x": 0.75, "y": 0.2, "rx": 0.1, "ry": 0.08, "height": 0.4},
                {"type": "lake", "x": 0.25, "y": 0.65, "rx": 0.1, "ry": 0.07, "height": 0.3},
                {"type": "sea", "x": 0.5, "y": 1.1, "rx": 0.9, "ry": 0.2, "height": 0.6},
                {"type": "volcano?", "x": 0.1, "y": 0.1, "rx": 0.1, "ry": 0.1, "height": 1}],
    "rivers": [{"points": [[0.4, 0.4], [0.45, 0.6], [0.5, 0.75], [0.55, 0.97]], "width": 0.01, "depth": 0.03}],
    "layers": {"ground": {"name": "Meadow", "prompt": "short green grass with clover", "tile_m": 9},
               "shore": {"name": "Wet sand", "prompt": "dark wet volcanic sand with pebbles", "tile_m": 7},
               "cliff": {"name": "Basalt", "prompt": "cracked black basalt rock face", "tile_m": 18},
               "peak": {"name": "Ash", "prompt": "pale grey volcanic ash with small stones", "tile_m": 12}},
    "sky": "360 degree panorama with equirectangular projection, tropical afternoon sky, scattered cumulus",
}


def test_heightmap_is_continuous_and_splat_sums_to_one() -> None:
    res = build_height(REPLY, HeightParams(size=257, world_m=512, height_m=120, erosion=0.6, seed=7))
    h16, s8 = res["h16"], res["splat"]
    assert h16.shape == (257, 257) and h16.dtype == np.uint16 and s8.shape == (256, 256, 4)
    assert h16.min() == 0 and h16.max() == 65535
    hist = res["histogram"]
    assert hist["continuous"] and hist["coarse_empty_bins"] == 0 and hist["max_level_share"] < 0.01
    assert (s8.astype(int).sum(-1) == 255).all()
    assert res["water"] is not None and 0 < res["water"] < 0.5
    assert all(res["coverage"][r] > 0 for r in ("ground", "shore", "cliff"))
    # Unknown region types never reach the generator.
    assert all(r["type"] != "volcano?" for r in res["plan"]["regions"])
    again = build_height(REPLY, HeightParams(size=257, world_m=512, height_m=120, erosion=0.6, seed=7))
    assert np.array_equal(again["h16"], h16)


def test_histogram_flags_terraces() -> None:
    ramp = np.linspace(0, 1, 512 * 512, dtype=np.float32).reshape(512, 512)
    smooth = histogram_report(quantize16(ramp, np.random.default_rng(0)))
    assert smooth["continuous"] and smooth["levels_used"] > 60000
    stairs = histogram_report(quantize16(np.floor(ramp * 12) / 11, np.random.default_rng(0)))
    assert not stairs["continuous"] and stairs["max_level_share"] > 0.05


def test_splat8_sums_exactly_255() -> None:
    w = np.random.default_rng(1).random((64, 64, 4)).astype(np.float32) ** 3
    s = splat8(w)
    assert (s.astype(int).sum(-1) == 255).all()
    assert np.abs(s / 255 - w / w.sum(-1, keepdims=True)).max() < 2 / 255


def test_river_bed_only_goes_down_and_cuts_the_bank() -> None:
    n = 129
    vv, uu = np.mgrid[0:n, 0:n] / (n - 1)
    h = (1 - vv).astype(np.float32) * 0.5 + 0.2 * np.sin(uu * 9).astype(np.float32)
    river = {"points": [[0.5, 0.05], [0.4, 0.5], [0.6, 0.95]], "width": 0.01, "depth": 0.03}
    dist, bed = river_field(river, h, np.random.default_rng(0))
    assert dist.min() < 0.01
    on = dist < 0.004
    rows = np.nonzero(on)[0]
    order = np.argsort(rows)
    beds = bed[on][order]
    assert beds[-1] <= beds[0] and (bed[on] <= h[on] + 1e-6).all()


def test_normalize_plan_maps_layers_and_skybox() -> None:
    plan = normalize_plan(REPLY, idea="volcanic island", style="stylized", skybox=False)
    assert plan["sky_prompts"] == [] and len(plan["layer_prompts"]) == 4
    assert plan["layer_prompts"][0].startswith("short green grass with clover, stylized")
    assert plan["layer_prompts"][2].endswith(TEXTURE_SUFFIX)
    assert plan["layer_names"].split("\n") == ["Meadow", "Wet sand", "Basalt", "Ash"]
    on = normalize_plan({**REPLY, "sky": "sunset clouds"}, idea="x", skybox=True)
    assert len(on["sky_prompts"]) == 1 and on["sky_prompts"][0].startswith("360 degree panorama")
    with pytest.raises(StepError):
        normalize_plan({**REPLY, "layers": {"ground": {"prompt": "grass"}}}, idea="x")
    with pytest.raises(StepError):
        normalize_plan({**REPLY, "regions": []}, idea="x")


def _texture(seed: int, color: tuple[int, int, int]) -> Image.Image:
    rng = np.random.default_rng(seed)
    a = np.clip(np.array(color, np.float32)[None, None] + rng.normal(0, 25, (64, 64, 3)), 0, 255)
    return Image.fromarray(a.astype(np.uint8), "RGB")


async def _pack(ctx: FakeCtx, sky: bool = False) -> tuple[StepResult, StepResult, StepResult, StepResult]:
    from studio_core.ops.pbr import Pbr

    plan = normalize_plan(REPLY, idea="island")
    plan_ref = await ctx.blobs.put_bytes(json.dumps(plan).encode(), name="terrain_plan.json", mime="application/json")
    hmap = await TerrainHeight().run(ctx, {"size": 129, "world": 256, "height": 60, "asset": "Đảo"},
                                     {"plan": [plan_ref]})
    texs = [await put_img(ctx, _texture(i, c), f"layer_{i + 1:02d}_seamless.png")
            for i, c in enumerate([(90, 130, 60), (190, 170, 120), (110, 110, 110), (230, 230, 235)])]
    mat = await Pbr().run(ctx, {}, {"images": texs})
    skybox = StepResult(outputs={"skybox": []})
    if sky:
        from test_skybox import _seamed

        pano = await put_img(ctx, _seamed(), "pano.png")
        skybox = await TerrainSky().run(ctx, {"asset": "dao"}, {"images": [pano]})
    pack = await TerrainPack().run(ctx, {}, {"terrain": hmap.outputs["terrain"], "material": mat.outputs["material"],
                                             "plan": [plan_ref], "skybox": skybox.outputs["skybox"]})
    return hmap, mat, pack, skybox


async def test_executors_pack_layers_and_report(tmp_path: Path) -> None:
    ctx = FakeCtx(tmp_path)
    hmap, mat, pack, _ = await _pack(ctx)
    assert [r.name for r in hmap.outputs["terrain"]] == [HEIGHT_FILE]
    assert hmap.outputs["raw"][0].name == "dao_height.raw"
    raw = ctx.blobs.path(hmap.outputs["raw"][0].hash).read_bytes()
    assert len(raw) == 129 * 129 * 2
    with Image.open(ctx.blobs.path(hmap.outputs["heightmap"][0].hash)) as im:
        assert im.mode.startswith("I") and im.size == (129, 129)
        assert np.array_equal(np.asarray(im, np.uint16), np.frombuffer(raw, "<u2").reshape(129, 129))
    doc = json.loads(ctx.blobs.path(pack.outputs["terrain"][0].hash).read_bytes())
    assert pack.outputs["terrain"][0].name == TERRAIN_FILE and doc["type"] == "asset-studio/terrain"
    assert [l["role"] for l in doc["layers"]] == list(ROLES)
    assert [l["name"] for l in doc["layers"]] == ["Meadow", "Wet sand", "Basalt", "Ash"]
    assert doc["layers"][2]["tile_m"] == 18 and doc["skybox"] is None
    assert doc["histogram"]["continuous"] and doc["splat_size"] == 128
    assert len(pack.outputs["preview"]) == 2 and len(pack.outputs["masks"]) == 4
    with Image.open(ctx.blobs.path(pack.outputs["preview"][0].hash)) as im:
        assert im.size == (960, 540)
    with pytest.raises(StepError):
        await TerrainPack().run(ctx, {}, {"terrain": hmap.outputs["terrain"], "material": []})


async def test_terrain_sky_skips_without_image(tmp_path: Path) -> None:
    res = await TerrainSky().run(FakeCtx(tmp_path), {}, {"images": []})
    assert res.meta["skipped"] and res.outputs["skybox"] == []


def _approve_row(ctx: FakeCtx, hmap: StepResult, mat: StepResult, pack: StepResult, sky: StepResult | None = None):
    from test_export_engines import _row

    out = {"images": pack.outputs["preview"], "terrain": pack.outputs["terrain"], "raw": hmap.outputs["raw"],
           "maps": mat.outputs["maps"], "masks": pack.outputs["masks"]}
    if sky is not None:
        out.update(skybox=sky.outputs["skybox"], faces=sky.outputs["faces"])
    return _row("approve", "gate.approve", out)


async def test_export_unity_and_godot(tmp_path: Path) -> None:
    from studio_core.export import godot, unity
    from studio_core.store.bundle import engine_ready, engine_source

    ctx = FakeCtx(tmp_path)
    hmap, mat, pack, _ = await _pack(ctx)
    step = _approve_row(ctx, hmap, mat, pack)
    assert engine_ready(step, ctx.blobs)
    src = engine_source(step, [], ctx.blobs, "Isle")
    assert src.terrain and not src.pbr and not src.skybox
    u = dict(unity.export(src))
    info = json.loads(u["Terrain/Isle_terrain.json"])
    assert info["resolution"] == 129 and info["splat_size"] == 128 and info["water"]
    assert [l["asset"] for l in info["layers"]] == [f"Layers/Isle_{r}.terrainlayer" for r in ROLES]
    for lay in info["layers"]:
        for k in ("albedo", "normal", "mask"):
            assert f"Terrain/{lay[k]}" in u and f"Terrain/{lay[k]}.meta" in u
    assert "textureType: 1" in u["Terrain/Layers/Isle_cliff_normal.png.meta"].decode()
    assert "sRGBTexture: 0" in u["Terrain/Layers/Isle_cliff_mask.png.meta"].decode()
    assert "Terrain/Isle_height.raw" in u and "Terrain/Isle_height16.png" in u
    cs = u["Editor/AssetStudioTerrain.cs"].decode()
    assert "namespace AssetStudio" in cs and "TerrainLayer" in cs and "SetAlphamaps" in cs
    assert "Build Terrain" in u["README.txt"].decode()
    g = dict(godot.export(src))
    r32 = np.frombuffer(g["Terrain/Isle_height.r32"], "<f4")
    raw = np.frombuffer(ctx.blobs.path(hmap.outputs["raw"][0].hash).read_bytes(), "<u2")
    assert r32.size == 129 * 129 and np.allclose(r32 * 65535, raw, atol=0.01)
    assert "process/fix_alpha_border=false" in g["Terrain/Isle_splat.png.import"].decode()
    assert "compress/normal_map=1" in g["Terrain/Layers/Isle_peak_normal.png.import"].decode()
    mat_tres = g["Terrain/Isle_terrain_material.tres"].decode()
    for line in mat_tres.splitlines():
        if line.startswith("[ext_resource"):
            rel = line.split('path="')[1].split('"')[0]
            assert f"Terrain/{rel}" in g, rel
    scene = g["Isle_terrain.tscn"].decode()
    assert 'path="Terrain/asset_studio_terrain.gd"' in scene and 'heightmap_file = "Isle_height.r32"' in scene
    assert "resolution = 129" in scene and 'name="Water"' in scene and "ProceduralSkyMaterial" in scene
    assert "HeightMapShape3D" in g["Terrain/asset_studio_terrain.gd"].decode()


async def test_export_with_skybox_uses_its_environment(tmp_path: Path) -> None:
    from studio_core.export import godot, unity
    from studio_core.store.bundle import engine_source

    ctx = FakeCtx(tmp_path)
    hmap, mat, pack, sky = await _pack(ctx, sky=True)
    doc = json.loads(ctx.blobs.path(pack.outputs["terrain"][0].hash).read_bytes())
    assert doc["skybox"] and doc["skybox"]["file"] == "skybox.json"
    step = _approve_row(ctx, hmap, mat, pack, sky)
    src = engine_source(step, [], ctx.blobs, "Isle")
    assert src.terrain and src.skybox
    g = dict(godot.export(src))
    scene = g["Isle_terrain.tscn"].decode()
    assert 'path="Skybox/Isle_environment.tres"' in scene and "Skybox/Isle_environment.tres" in g
    assert json.loads(dict(unity.export(src))["Terrain/Isle_terrain.json"])["skybox"] is True


class FakeQuery:
    def __init__(self, reply: dict[str, Any]):
        self.reply = reply
        self.calls: list[Any] = []

    async def __call__(self, content: list[dict[str, Any]], options: Any) -> ClaudeReply:
        self.calls.append((content, options))
        return ClaudeReply(self.reply, "", {"cost_usd": 0.03, "model": options.model})


class FakeImageGen(FakeExec):
    def __init__(self, kind: str, size: tuple[int, int]):
        super().__init__(kind)
        self.size = size
        self.params: list[dict[str, Any]] = []

    async def run(self, ctx: Any, params: dict[str, Any], inputs: Any) -> StepResult:
        self.params.append(params)
        if self.size[0] == self.size[1]:
            img = _texture(len(self.params), (60 * len(self.params), 120, 90)).resize(self.size)
        else:
            from test_skybox import _seamed

            img = _seamed(self.size[0])
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return StepResult(outputs={"images": [await ctx.blobs.put_bytes(buf.getvalue(), name="gen.png",
                                                                        mime="image/png")]})


def _env(tmp_path: Path) -> tuple[Env, FakeQuery, FakeImageGen, FakeImageGen]:
    from studio_core.ops import register as register_ops

    q = FakeQuery(REPLY)
    tex, sky = FakeImageGen("comfy.t2i_zimage_turbo", (256, 256)), FakeImageGen("comfy.t2i_qwen_image_360", (1024, 512))
    env = Env(tmp_path, [tex, sky], {"terrain-3d": (RECIPES / "terrain-3d.yaml").read_text(encoding="utf-8")})
    register_ops(env.registry)
    env.registry.register(ClaudeTerrainPlanExecutor(env.settings, q))
    return env, q, tex, sky


@pytest.mark.parametrize("skybox", [False, True])
async def test_recipe_runs_with_fakes(tmp_path: Path, skybox: bool) -> None:
    env, q, tex, sky = _env(tmp_path)
    run_id = await env.engine.start_run("terrain-3d", {"idea": "volcanic island", "size": 1025, "texture": 512,
                                                       "skybox": skybox, "asset": "isle"},
                                        params={"approve": {"enabled": False}, "hmap": {"size": 129}})
    await asyncio.wait_for(env.engine.wait_idle(run_id), 120)
    steps = env.steps(run_id)
    assert env.status(run_id) == RunStatus.SUCCEEDED, {k: s.error for k, s in steps.items() if s.error}
    assert q.calls and q.calls[0][1].model == "claude-opus-5-5"
    assert [p["57:27.text"].split(",")[0] for p in tex.params] == [
        "short green grass with clover", "dark wet volcanic sand with pebbles", "cracked black basalt rock face",
        "pale grey volcanic ash with small stones"]
    assert len(sky.params) == (1 if skybox else 0)
    out = steps["approve"].outputs
    doc = json.loads(env.blobs.path(out["terrain"][0].hash).read_bytes())
    assert doc["resolution"] == 129 and len(doc["layers"]) == 4
    assert bool(doc["skybox"]) == skybox and bool(out.get("skybox")) == skybox


def test_recipe_loads_and_lints() -> None:
    r = load_recipe_file(RECIPES / "terrain-3d.yaml")
    assert {s.id for s in r.steps} == {"plan", "hmap", "gen", "seam", "fit", "mat", "sky", "skybox", "pack", "approve"}
    assert r.step("gen").map_param == "57:27.text" and r.step("sky").map_param == "6.text"
    _, issues = validate((RECIPES / "terrain-3d.yaml").read_text(encoding="utf-8"), None)
    assert issues == []
    for kind in ("op.terrain_height", "op.terrain_pack", "op.terrain_sky", "claude.terrain_plan"):
        assert CATALOG[kind]["label"]
