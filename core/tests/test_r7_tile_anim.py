from __future__ import annotations

import asyncio
import io
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image
from test_engine_runner import Env, FakeExec

from studio_core.contracts import Lane, RunStatus, StepError, StepResult
from studio_core.engine.kinds import CATALOG
from studio_core.engine.library import validate
from studio_core.engine.recipe import RecipeError, apply_rules, load_recipe_file
from studio_core.export import godot, unity
from studio_core.export.tile_anim import frame_cells
from studio_core.ops import tile_anim as ta
from studio_core.ops.tile import _rgb, _seam_ratio, soft_blur, wrap_resize
from studio_core.store.bundle import engine_ready, engine_source

RECIPES = Path(__file__).resolve().parents[1] / "studio_core" / "recipes"


def texture(size: int = 64, seed: int = 1, tint: tuple[float, float, float] = (0.2, 0.45, 0.8)) -> np.ndarray:
    """Tileable noise (wrap-aware blur) in one hue."""
    rng = np.random.default_rng(seed)
    lum = soft_blur(rng.random((size, size)).astype(np.float32), 2)
    lum = (lum - lum.min()) / np.ptp(lum)
    return np.clip(lum[..., None] * 0.7 + np.asarray(tint, np.float32) * 0.5, 0, 1).astype(np.float32)


def _png(a: np.ndarray) -> bytes:
    buf = io.BytesIO()
    ta._to_img(a).save(buf, "PNG")
    return buf.getvalue()


def test_cpu_kinds_wrap_and_loop_exactly() -> None:
    base = texture()
    src = max(_seam_ratio(base).values())
    for kind in ta.CPU_KINDS:
        frames = ta.cpu_frames(base, kind, 8)
        check = ta.frame_check(frames)
        assert check["passed"] and check["loop_ratio"] <= 1.2, (kind, check)
        assert max(check["seam_ratio"].values()) <= max(src, 1.0) + 0.5
        moved = float(np.abs(frames[3] - frames[0]).mean())
        assert moved > 0.005, kind
        # Twice the frames over the same loop: every other frame is the same picture, so the loop is exact.
        double = ta.cpu_frames(base, kind, 16)
        assert float(np.abs(double[2] - frames[1]).max()) < 1e-5
    scrolled = ta.cpu_frames(base, "water", 8, strength=0.0, scroll="down")
    assert np.allclose(scrolled[0], base, atol=1e-5)
    assert np.allclose(scrolled[2], np.roll(base, 16, 0), atol=1e-4)
    with pytest.raises(StepError):
        ta.cpu_frames(base, "torch", 8)
    with pytest.raises(StepError):
        ta.cpu_frames(base, "water", 8, scroll="sideways")


def test_seam_blend_rebuilds_edges_from_neighbours() -> None:
    tile = texture(224, seed=3)
    canvas = np.tile(tile, (3, 3, 1))
    # A slow drift across the whole canvas, as Wan adds: the centre cell alone no longer wraps.
    canvas = np.clip(canvas + np.linspace(0, 0.3, canvas.shape[1], dtype=np.float32)[None, :, None], 0, 1)
    centre = canvas[224:448, 224:448]
    assert _seam_ratio(centre)["x"] > 3
    out = ta.seam_blend(canvas)
    assert out.shape == (224, 224, 3)
    assert max(_seam_ratio(out).values()) <= ta.SEAM_RATIO_MAX
    assert float(np.abs(out[60:160, 60:160] - centre[60:160, 60:160]).max()) < 1e-6


def test_close_wrap_fixes_edge_tone_and_keeps_the_centre() -> None:
    tile = _rgb(wrap_resize(ta._to_img(texture(64, seed=6)), 64, 48))
    # A non-square tile (48×64) lit from the left, as Wan relights a neighbour cell: the x wrap breaks.
    lit = np.clip(tile + np.linspace(0.25, 0, tile.shape[1], dtype=np.float32)[None, :, None], 0, 1)
    assert _seam_ratio(lit)["x"] > ta.SEAM_RATIO_MAX
    out = ta.close_wrap(lit)
    assert out.shape == lit.shape and out.dtype == np.float32
    assert max(_seam_ratio(out).values()) < 1.0
    assert float(np.abs(out[12:36, 16:48] - lit[12:36, 16:48]).max()) < 1e-6
    rgba = np.concatenate([lit, np.full((*lit.shape[:2], 1), 0.5, np.float32)], -1)
    fixed = ta.close_wrap(rgba)
    assert np.allclose(fixed[..., :3], out, atol=1e-6) and np.all(fixed[..., 3] == 0.5)
    tiny = ta.close_wrap(texture(64)[:1, :5])
    assert tiny.shape == (1, 5, 3)
    # Already as smooth as the interior (a real mortar line may sit on the edge): untouched.
    assert np.array_equal(ta.close_wrap(tile), tile)


def test_match_edge_tone_steadies_the_border_only() -> None:
    base = texture(64, seed=8)
    # Odd frame count, each frame's border ring a different brightness.
    frames = [np.clip(base + 0.15 * ta._edge_ring(64, 64, 0.2)[..., None] * math.sin(i), 0, 1).astype(np.float32)
              for i in range(7)]
    out = ta.match_edge_tone(frames)
    ring = ta._edge_ring(64, 64, 0.2) > 0.5
    spread = lambda fs: float(np.std([f[ring].mean() for f in fs]))
    assert spread(out) < spread(frames) * 0.3
    assert all(float(np.abs(a[24:40, 24:40] - b[24:40, 24:40]).max()) < 1e-6 for a, b in zip(out, frames))
    one = ta.match_edge_tone(frames[:1])
    assert np.array_equal(one[0], frames[0]) and one[0] is not frames[0]
    rgba = [np.concatenate([f, np.full((64, 64, 1), 0.25, np.float32)], -1) for f in frames]
    assert all(np.all(f[..., 3] == 0.25) for f in ta.match_edge_tone(rgba))


def test_video_loop_finds_period_and_closes_it() -> None:
    base = texture(48, seed=5)
    rng = np.random.default_rng(0)
    clip = [np.clip(np.roll(base, round(48 * (i % 12) / 12), 1) + rng.normal(0, 0.01, base.shape), 0, 1)
            .astype(np.float32) for i in range(40)]
    frames, rep = ta.video_loop(clip, 8)
    assert rep["period"] % 12 == 0 and len(frames) == 8
    assert ta.loop_ratio(frames) <= ta.SEAM_RATIO_MAX
    with pytest.raises(StepError):
        ta.video_loop(clip[:4], 8)


def test_prompts_and_layers() -> None:
    assert "flames" in ta.video_prompt("torch", "") and "static camera" in ta.video_prompt("custom", "glow").lower()
    with pytest.raises(StepError):
        ta.video_prompt("custom", " ")
    assert ta.auto_layer({"terrains": ["grass", "water"]}, "water") == "b"
    assert ta.auto_layer({"terrains": ["lava_rock", "stone"]}, "lava") == "a"
    assert ta.auto_layer({"terrains": ["a", "b"]}, "grass_wind") == "a"
    assert ta.SEAM_RATIO_MAX == pytest.approx(2.119, abs=1e-3)


def test_recipe_lint_and_catalog() -> None:
    text = (RECIPES / "tile-animated.yaml").read_text(encoding="utf-8")
    assert validate(text, None)[1] == []
    recipe = load_recipe_file(RECIPES / "tile-animated.yaml")
    with pytest.raises(RecipeError):
        apply_rules(recipe.rules, {"kind": "custom", "prompt": ""})
    apply_rules(recipe.rules, {"kind": "water", "prompt": ""})
    for kind in ("op.tile_anim_prep", "op.tile_anim_candidates", "op.tile_animate"):
        assert CATALOG[kind]["label"]


class FakeWan(FakeExec):
    """Wan on the 3×3 start canvas: the texture scrolls right by one cell per 12 frames, with a slow brightness drift."""

    def __init__(self) -> None:
        super().__init__("comfy.keyart_wan22_ti2v", Lane.GPU)
        self.params: list[dict[str, Any]] = []

    async def run(self, ctx: Any, params: dict[str, Any], inputs: Any) -> StepResult:
        self.params.append(params)
        start = np.asarray(Image.open(ctx.blobs.path(inputs["image"][0].hash)).convert("RGB"), np.float32) / 255
        c = start.shape[1] // 3
        n = int(params["55.length"])
        refs = []
        for i in range(n):
            f = np.roll(start, round(c * (i % 12) / 12), 1) + 0.02 * np.sin(i / 5)
            refs.append(await ctx.blobs.put_bytes(_png(np.clip(f, 0, 1)), name=f"wan_{i:03d}.png", mime="image/png"))
        return StepResult(outputs={"images": refs})


def _env(tmp_path: Path, execs: list[FakeExec]) -> Env:
    env = Env(tmp_path, execs, {"tile-animated": (RECIPES / "tile-animated.yaml").read_text(encoding="utf-8")})
    for cls in ta.TILE_ANIM_OPS:
        env.registry.register(cls())
    if not execs:
        env.registry.register(FakeWan())
    return env


async def _wait(env: Env, inputs: dict[str, Any]) -> dict[str, Any]:
    run_id = await env.engine.start_run("tile-animated", inputs)
    await asyncio.wait_for(env.engine.wait_idle(run_id), 120)
    steps = env.steps(run_id)
    assert env.status(run_id) == RunStatus.SUCCEEDED, {k: s.error for k, s in steps.items() if s.error}
    return steps


def _doc(env: Env, step: Any) -> dict[str, Any]:
    return json.loads(env.blobs.path(step.outputs["tile_anim"][0].hash).read_bytes())


async def test_water_recipe_runs_cpu_and_exports(tmp_path: Path) -> None:
    wan = FakeWan()
    env = _env(tmp_path, [wan])
    ref = await env.blobs.put_bytes(_png(texture()), name="water.png", mime="image/png")
    steps = await _wait(env, {"image": ref.hash, "kind": "water", "frames": 8, "fps": 6, "review": False})
    assert wan.params == []
    doc = _doc(env, steps["approve"])
    assert doc["mode"] == "single" and doc["frames"] == 8 and doc["check"]["passed"]
    sheet = Image.open(env.blobs.path(doc["sheet"]["hash"]))
    assert sheet.size == (8 * 64, 64)
    assert Image.open(env.blobs.path(steps["approve"].outputs["preview"][0].hash)).n_frames == 8
    assert engine_ready(steps["approve"], env.blobs)
    src = engine_source(steps["approve"], list(steps.values()), env.blobs, "Water")
    g = dict(godot.export(src))
    tres = g["Water_tileset.tres"].decode()
    assert "0:0/animation_frames_count = 8" in tres and "0:0/animation_columns = 8" in tres
    assert "0:0/animation_speed = 6" in tres and "TileAnim/Water_tiles.png" in tres
    demo = g["Water_demo.tscn"].decode()
    assert 'path="Water_tileset.tres"' in demo and 'type="TileMapLayer"' in demo and 'type="Camera2D"' in demo
    assert _map_cells(demo) == {(x, y, 0, 0) for x in range(8) for y in range(5)}
    u = dict(unity.export(src))
    tile = u["TileAnim/Water_tile.asset"].decode()
    assert tile.count("guid: ") >= 9 and ta_guid() in tile and "m_MinSpeed: 6" in tile
    meta = u["TileAnim/Water_tiles.png.meta"].decode()
    assert meta.count("name: tile_f") == 8 and "spritePixelsToUnits: 64" in meta
    assert u["TileAnim/Water_tile.anim"].decode().count("    - time:") == 8
    assert "TileAnim/Water_tile.asset.meta" in u and b"AnimatedTile" in u["README.txt"]


def _map_cells(scene: str) -> set[tuple[int, int, int, int]]:
    import re
    import struct

    raw = bytes(int(b) for b in re.search(r"tile_map_data = PackedByteArray\(([^)]*)\)", scene).group(1).split(","))
    assert struct.unpack_from("<H", raw)[0] == 0 and (len(raw) - 2) % 12 == 0
    rows = [struct.unpack_from("<hhHHHH", raw, 2 + 12 * i) for i in range((len(raw) - 2) // 12)]
    return {(x, y, ax, ay) for x, y, _, ax, ay, _ in rows}


def ta_guid() -> str:
    from studio_core.export.tile_anim import ANIMATED_TILE_GUID

    return ANIMATED_TILE_GUID


async def test_torch_recipe_runs_through_wan(tmp_path: Path) -> None:
    wan = FakeWan()
    env = _env(tmp_path, [wan])
    ref = await env.blobs.put_bytes(_png(texture(48, seed=9, tint=(0.6, 0.3, 0.1))), name="torch.png",
                                    mime="image/png")
    steps = await _wait(env, {"image": ref.hash, "kind": "torch", "frames": 6, "candidates": 2, "review": False})
    assert len(wan.params) == 2 and "flames" in wan.params[0]["6.text"]
    assert wan.params[0]["55.width"] == 672 and wan.params[0]["55.length"] == 49
    assert len(steps["candidates"].outputs["previews"]) == 2
    rep = json.loads(env.blobs.path(steps["anim"].outputs["json"][0].hash).read_bytes())
    assert rep["video"]["period"] % 12 == 0 and set(rep["video"]["seam_before_edge_fix"]) == {"x", "y"}
    doc = _doc(env, steps["approve"])
    assert doc["frames"] == 6 and doc["tile_size"] == 48 and doc["check"]["passed"], doc["check"]


async def test_autotile_input_animates_the_whole_set(tmp_path: Path) -> None:
    from studio_core.ops.autotile import Autotile

    env = _env(tmp_path, [])
    grass = await env.blobs.put_bytes(_png(texture(64, 2, (0.2, 0.6, 0.15))), name="grass.png", mime="image/png")
    water = await env.blobs.put_bytes(_png(texture(64, 4)), name="water.png", mime="image/png")
    from test_ops_executors import FakeCtx

    ctx = FakeCtx(tmp_path / "at")
    ctx.blobs = env.blobs
    ctx.job_dir.mkdir(parents=True)
    res = await Autotile().run(ctx, {"terrains": "grass, water", "size": 32, "seamless": "off"},
                               {"tile_a": [grass], "tile_b": [water]})
    atlas, js = res.outputs["images"][0], res.outputs["autotile"][0]
    steps = await _wait(env, {"image": atlas.hash, "autotile": js.hash, "kind": "water", "frames": 4,
                              "review": False})
    doc = _doc(env, steps["approve"])
    assert doc["mode"] == "blob47" and doc["autotile"]["layer"] == "b" and doc["check"]["passed"]
    assert doc["layout"] == {"cols": 8, "rows": 24, "frame_offset": [0, 6]}
    sheet = np.asarray(Image.open(env.blobs.path(doc["sheet"]["hash"])).convert("RGB"), np.float32)
    assert sheet.shape == (4 * 6 * 32, 8 * 32, 3)
    # Grass (A) stays put, the water fill (B, atlas cell 47 = col 7, row 5) moves.
    full_a = next(e for e in doc["autotile"]["tiles"] if e["mask"] == 255)
    a0 = sheet[full_a["row"] * 32:(full_a["row"] + 1) * 32, full_a["col"] * 32:(full_a["col"] + 1) * 32]
    a2 = sheet[(full_a["row"] + 12) * 32:(full_a["row"] + 13) * 32, full_a["col"] * 32:(full_a["col"] + 1) * 32]
    assert np.abs(a0 - a2).max() <= 1
    b0, b2 = sheet[160:192, 224:256], sheet[160 + 12 * 32:192 + 12 * 32, 224:256]
    assert np.abs(b0 - b2).mean() > 1
    assert len(frame_cells(doc)) == 48 * 4
    src = engine_source(steps["approve"], list(steps.values()), env.blobs, "Shore")
    tres = dict(godot.export(src))["Shore_tileset.tres"].decode()
    assert tres.count("/animation_separation = Vector2i(0, 5)") == 48 and 'terrain_0/name = "grass"' in tres
    painted = _map_cells(dict(godot.export(src))["Shore_demo.tscn"].decode())
    frame0 = {(e["col"], e["row"]) for e in doc["autotile"]["tiles"]}
    assert len(painted) == 12 * 8 and {(ax, ay) for _, _, ax, ay in painted} <= frame0
    assert len({(ax, ay) for _, _, ax, ay in painted}) > 10
    u = dict(unity.export(src))
    rule = u["TileAnim/Shore_grass_rule.asset"].decode()
    assert rule.count("m_Output: 2") == 47 and rule.count("    - {fileID:") == 47 * 4
    assert ta_guid() in u["TileAnim/Shore_water.asset"].decode()


async def test_prep_rejects_bad_inputs(tmp_path: Path) -> None:
    from test_ops_executors import FakeCtx, put_img

    ctx = FakeCtx(tmp_path)
    ctx.job_dir.mkdir(parents=True)
    ref = await put_img(ctx, ta._to_img(texture()), "t.png")
    with pytest.raises(StepError):
        await ta.TileAnimPrep().run(ctx, {"kind": "smoke"}, {"images": [ref]})
    with pytest.raises(StepError):
        await ta.TileAnimPrep().run(ctx, {"kind": "water", "resolution": "4k"}, {"images": [ref]})
    bad = await ctx.blobs.put_bytes(b'{"type": "x"}', name="autotile.json", mime="application/json")
    with pytest.raises(StepError):
        await ta.TileAnimPrep().run(ctx, {"kind": "water"}, {"images": [ref], "autotile": [bad]})
    res = await ta.TileAnimPrep().run(ctx, {"kind": "water"}, {"images": [ref]})
    info = json.loads(ctx.blobs.path(res.outputs["json"][0].hash).read_bytes())
    assert res.outputs["starts"] == [] and info["pick"] is False and info["width"] == 672


async def test_pick_gate_only_waits_for_a_real_choice(tmp_path: Path) -> None:
    from studio_core.contracts import StepStatus

    env = _env(tmp_path, [])
    ref = await env.blobs.put_bytes(_png(texture()), name="water.png", mime="image/png")
    for kind, waiting in (("water", "approve"), ("waterfall", "pick")):
        run_id = await env.engine.start_run("tile-animated", {"image": ref.hash, "kind": kind, "candidates": 2})
        await asyncio.wait_for(env.engine.wait_idle(run_id), 120)
        steps = env.steps(run_id)
        assert env.status(run_id) == RunStatus.AWAITING_HUMAN
        assert [k for k, s in steps.items() if s.status == StepStatus.AWAITING_HUMAN] == [waiting]


def test_follow_up_offers_tile_animated_only_for_tiles() -> None:
    from studio_core.mcp.followup import looks_like_tile

    assert looks_like_tile({"mime": "image/png", "name": "x.png"}, "autotile-terrain")
    assert looks_like_tile({"mime": "image/png", "name": "stone_tile_01.png"})
    assert not looks_like_tile({"mime": "image/png", "name": "hero.png"}, "concept-2d")
    assert not looks_like_tile({"mime": "application/json", "name": "tile.json"})


def test_recipe_forms_expose_edge_fix_and_toon_bands() -> None:
    tile = load_recipe_file(RECIPES / "tile-animated.yaml")
    assert tile.inputs["edge_fix"]["default"] is True
    icon = load_recipe_file(RECIPES / "icon-3d.yaml")
    assert icon.inputs["toon_bands"]["default"] == 3
    assert validate((RECIPES / "icon-3d.yaml").read_text(encoding="utf-8"), None)[1] == []
