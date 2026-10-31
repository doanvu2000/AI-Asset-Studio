import json
import math
import re
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from studio_core.contracts import StepError
from studio_core.engine.kinds import CATALOG
from studio_core.engine.library import validate
from studio_core.engine.recipe import load_recipe_file
from studio_core.export import weather as ex
from studio_core.export.source import Source
from studio_core.ops import weather as wx
from studio_core.ops.tile_anim import SEAM_RATIO_MAX
from test_ops_executors import FakeCtx

RECIPES = Path(__file__).resolve().parents[1] / "studio_core" / "recipes"
SMALL = {"weather": "rain_light, snow, mist, leaves, fireflies", "cell": 128, "frames": 8, "mist_size": 256,
         "particle_size": 32, "asset": "wx"}


def test_loop_speed_times_frames_is_a_whole_number_of_tiles():
    for kind, cfg in wx.KIND_CFG.items():
        for view in wx.VIEWS:
            for kx, ky in wx._cycles(cfg, view):
                assert isinstance(kx, int) and isinstance(ky, int), kind
                for cell, frames in ((128, 8), (192, 12), (256, 16)):
                    for k in (kx, ky):
                        assert wx.loop_velocity(cell, frames, k) * frames == pytest.approx(k * cell)


@pytest.mark.parametrize("view", wx.VIEWS)
def test_every_particle_layer_wraps_back_to_frame_zero(view):
    for kind in wx.KIND_CFG:
        frames, wrap, count = wx.particle_loop(kind, view, 256, 6, 1.0, 7, None)
        qa = wx.loop_qa(frames, wrap)
        assert count > 0 and qa["passed"], (kind, view, qa)
        assert qa["wrap_diff"] <= 1e-4
        assert frames[0][..., 3].max() > 0.02, kind


def test_frame_t_is_frame_zero_shifted_by_the_per_frame_step():
    cfg = {**wx.KIND_CFG["snow"], "cycles": [(0, 1)], "sway": (0, 0), "pulse": 0.0, "count": 40}
    frames, wrap, _ = wx.particle_loop("snow", "sideview", 128, 16, 1.0, 3, None, cfg)
    step = int(wx.loop_velocity(128, 16, 1))
    assert step == 8
    for t in (1, 5, 11):
        np.testing.assert_allclose(frames[t], np.roll(frames[0], step * t, axis=0), atol=1e-4)
    np.testing.assert_allclose(wrap, frames[0], atol=1e-4)


def test_seed_is_stable_and_changes_the_layout():
    a = wx.particle_loop("snow", "sideview", 128, 4, 1.0, 11, None)[0][0]
    b = wx.particle_loop("snow", "sideview", 128, 4, 1.0, 11, None)[0][0]
    c = wx.particle_loop("snow", "sideview", 128, 4, 1.0, 12, None)[0][0]
    assert np.array_equal(a, b) and not np.array_equal(a, c)


def test_zero_density_is_empty_and_max_density_is_capped():
    frames, wrap, count = wx.particle_loop("rain_heavy", "sideview", 128, 4, 0.0, 7, None)
    assert count == 0 and all(f[..., 3].max() == 0 for f in frames) and wx.loop_qa(frames, wrap)["passed"]
    assert wx.emitter_spec("rain_heavy", wx.KIND_CFG["rain_heavy"], "sideview", (1280, 720), 0.0, "#fff") is None
    huge = {**wx.KIND_CFG["snow"], "count": 10**6}
    p = wx.make_particles(huge, "sideview", 256, 1.0, 7, "snow", ["#ffffff"])
    assert p["n"] == wx.MAX_PARTICLES
    em = wx.emitter_spec("snow", huge, "sideview", (4096, 4096), 1.0, "#fff")
    assert em["amount"] == wx.MAX_AMOUNT and em["capped"]
    full = wx.emitter_spec("rain_light", wx.KIND_CFG["rain_light"], "sideview", (1280, 720), 1.0, "#fff")
    assert 1 <= full["amount"] <= wx.MAX_AMOUNT and not full["capped"]


def test_mist_tile_is_seamless_and_scrolls_exactly_one_period():
    terms = wx.mist_terms(5)
    lv = wx.mist_levels(terms, (1, 0))
    tile = wx.mist_frame(terms, 256, 0.0, lv, 0.55, "#dfe8f2")
    assert max(wx._seam_ratio(tile[..., 3:]).values()) <= SEAM_RATIO_MAX
    assert 0.05 < tile[..., 3].mean() < 0.5 and tile[..., 3].max() <= wx.ALPHA_MAX + 1e-6
    end = wx.mist_frame(terms, 256, 1.0, lv, 0.55, "#dfe8f2")
    np.testing.assert_allclose(end, tile, atol=1e-4)
    half = wx.mist_frame(terms, 256, 0.5, lv, 0.55, "#dfe8f2")
    assert np.abs(half - tile).mean() > 0.01


def test_budget_and_param_validation():
    assert wx.sheet_grid(256, 16) == (4, 4) and wx.sheet_grid(128, 64) == (8, 8)
    with pytest.raises(StepError):
        wx.sheet_grid(256, 17)
    assert max(wx.MIST_SIZES) <= wx.MAX_TEX and max(wx.CELLS) * 1 <= wx.MAX_TEX
    for bad in ({"cell": 512}, {"mist_size": 2048}, {"frames": 3}, {"frames": 33, "cell": 256}, {"view": "iso"},
                {"weather": "rain_light, hail"}, {"intensity": 1.5}, {"area": "100x100"}, {"tint": "red"}):
        with pytest.raises(StepError):
            wx.parse_params({**SMALL, **bad})
    p = wx.parse_params({**SMALL, "weather": "", "intensity": "0.5", "area": "1920x1080", "tint": "#88aaff"})
    assert p["kinds"] == list(wx.KINDS) and p["intensity"] == 0.5 and p["area"] == (1920, 1080)


async def _run(tmp_path: Path, **over):
    ctx = FakeCtx(tmp_path)
    res = await wx.WeatherOverlay().run(ctx, {**SMALL, **over}, {})
    doc = json.loads(ctx.blobs.path(res.outputs["weather"][0].hash).read_bytes())
    return ctx, res, doc


async def test_op_outputs_doc_textures_and_qa(tmp_path: Path):
    ctx, res, doc = await _run(tmp_path)
    assert doc["type"] == wx.WEATHER_TYPE and doc["qa"]["passed"] and doc["budget"]["passed"]
    assert [la["name"] for la in doc["layers"]] == ["rain_light", "snow", "mist", "leaves", "fireflies"]
    assert len(res.outputs["sheets"]) == 5 and len(res.outputs["preview"]) == 1
    for key, t in doc["textures"].items():
        with Image.open(ctx.blobs.path(t["hash"])) as im:
            assert im.size == tuple(t["size"]) and max(im.size) <= wx.MAX_TEX, key
    sheet = doc["textures"]["rain_light_sheet"]["size"]
    assert sheet == [128 * 8, 128]
    assert {"spark", "snowflake", "leaf", "soft_dot", "mist"} <= set(doc["textures"])
    for la in doc["layers"]:
        assert la["qa"]["passed"], la["name"]
        if la["kind"] == "particles":
            assert la["emitter"]["amount"] <= wx.MAX_AMOUNT and la["emitter"]["lifetime"] > 0
            assert math.isclose(math.hypot(*la["emitter"]["direction"]), 1.0, abs_tol=1e-3)
    mist = next(la for la in doc["layers"] if la["kind"] == "mist")
    assert mist["ground_fade"] == 1.0 and mist["qa"]["edge_ratio"]["x"] <= SEAM_RATIO_MAX


async def test_op_zero_density_still_exports_a_valid_empty_pack(tmp_path: Path):
    _, _, doc = await _run(tmp_path, intensity=0.0, weather="rain_light, snow")
    assert doc["qa"]["passed"] and all(la["emitter"] is None for la in doc["layers"])
    src = _source(tmp_path, doc, {})
    assert ex.godot_files(src) == ([], [])


def _source(tmp_path: Path, doc, paths) -> Source:
    src = Source(asset="wx", uid="u1")
    src.weather, src.weather_files = doc, paths
    return src


async def _src(tmp_path: Path, **over) -> Source:
    ctx, _, doc = await _run(tmp_path, **over)
    return _source(tmp_path, doc, {k: ctx.blobs.path(t["hash"]) for k, t in doc["textures"].items()})


async def test_godot_export_files_and_scene_format(tmp_path: Path):
    files = dict((rel, data) for rel, data in ex.godot_files(await _src(tmp_path))[0])
    for rel in ("Weather/wx_rain_light.tscn", "Weather/wx_snow.tscn", "Weather/wx_leaves.tscn",
                "Weather/wx_fireflies.tscn", "Weather/wx_mist.tscn", "Weather/wx_mist.gdshader",
                "Weather/wx_weather_demo.tscn", "Weather/wx_weather.json", "Weather/Textures/wx_mist.png",
                "Weather/Textures/wx_rain_light_sheet.png", "Weather/Textures/wx_spark.png"):
        assert rel in files, rel
    rain = files["Weather/wx_rain_light.tscn"].decode()
    assert rain.startswith("[gd_scene format=3]")
    for needle in ("type=\"GPUParticles2D\"", "ParticleProcessMaterial", "particle_flag_align_y = true",
                   "emission_shape = 3", "gravity = Vector3(0, 0, 0)", "type=\"CanvasLayer\""):
        assert needle in rain, needle
    assert "blend_mode = 1" in files["Weather/wx_fireflies.tscn"].decode()
    assert "turbulence_enabled = true" in files["Weather/wx_snow.tscn"].decode()
    for scene in (k for k in files if k.endswith(".tscn")):
        text = files[scene].decode()
        ids = set(re.findall(r'\[sub_resource [^\]]*id="([^"]+)"', text)) | set(
            re.findall(r'\[ext_resource [^\]]*id="([^"]+)"', text))
        for used in re.findall(r'(?:SubResource|ExtResource)\("([^"]+)"\)', text):
            assert used in ids, (scene, used)
    shader = files["Weather/wx_mist.gdshader"].decode()
    assert shader.startswith("shader_type canvas_item;") and "repeat_enable" in shader and "TIME" in shader
    assert shader.count("{") == shader.count("}")
    mist = files["Weather/wx_mist.tscn"].decode()
    assert 'path="wx_mist.gdshader"' in mist and "ColorRect" in mist and "mouse_filter = 2" in mist


async def test_unity_export_files_guids_and_references(tmp_path: Path):
    files, notes = ex.unity_files(await _src(tmp_path))
    paths = dict(files)
    assert len(paths) == len(files) and notes
    for rel in ("Weather/Prefabs/wx_rain_light.prefab", "Weather/Prefabs/wx_mist.prefab", "Weather/wx_weather.prefab",
                "Weather/Shaders/wx_mist.shader", "Weather/Mist/wx_mist.mat", "Weather/Materials/wx_spark_alpha.mat",
                "Weather/Materials/wx_soft_dot_add.mat", "Editor/AssetStudioWeather.cs", "Weather/wx_weather.json"):
        assert rel in paths and f"{rel}.meta" in paths, rel
    guids = {}
    for rel, data in files:
        if rel.endswith(".meta"):
            g = re.search(r"^guid: ([0-9a-f]{32})$", data.decode(), re.M)
            assert g, rel
            guids[rel[:-5]] = g.group(1)
    assert len(set(guids.values())) == len(guids)
    builtin = {"0000000000000000f000000000000000", "0000000000000000e000000000000000"}
    for rel, data in files:
        if rel.endswith((".mat", ".prefab")):
            for g in re.findall(r"guid: ([0-9a-f]{32})", data.decode()):
                assert g in guids.values() or g in builtin, (rel, g)
    prefab = paths["Weather/Prefabs/wx_rain_light.prefab"].decode()
    assert prefab.startswith("%YAML 1.1") and "ParticleSystem:" in prefab and "VelocityModule:" in prefab
    assert "type: 5" in prefab and "m_RenderMode: 1" in prefab and "m_SortingOrder: 110" in prefab
    ids = re.findall(r"^--- !u!\d+ &(-?\d+)$", prefab, re.M)
    assert len(ids) == len(set(ids))
    assert "MeshRenderer:" in paths["Weather/Prefabs/wx_mist.prefab"].decode()
    shader = paths["Weather/Shaders/wx_mist.shader"].decode()
    assert 'Shader "AssetStudio/Weather/wx_Mist"' in shader and shader.count("{") == shader.count("}")
    tex_meta = paths["Weather/Textures/wx_mist.png.meta"].decode()
    assert "wrapU: 0" in tex_meta
    assert "namespace AssetStudio.Weather" in paths["Editor/AssetStudioWeather.cs"].decode()
    combined = paths["Weather/wx_weather.prefab"].decode()
    assert combined.count("--- !u!1 &") == 1 + 5


def test_recipe_loads_and_lints():
    r = load_recipe_file(RECIPES / "weather-overlay.yaml")
    assert [s.id for s in r.steps] == ["weather", "approve"]
    _, issues = validate((RECIPES / "weather-overlay.yaml").read_text(encoding="utf-8"), None)
    assert issues == []
    assert r.steps[0].kind == "op.weather_overlay"
    assert set(r.inputs["cell"]["options"]) == set(wx.CELLS) and set(r.inputs["mist_size"]["options"]) == set(wx.MIST_SIZES)


@pytest.mark.skipif("op.weather_overlay" not in CATALOG, reason="catalog entry is added by the integrator")
def test_catalog_declares_the_ports_the_recipe_uses():
    spec = CATALOG["op.weather_overlay"]
    assert {"images", "sheets", "preview", "weather"} <= set(spec["out"]) and "styled" in spec["in"]
