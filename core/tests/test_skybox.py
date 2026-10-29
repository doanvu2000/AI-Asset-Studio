import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from studio_core.contracts import StepError
from studio_core.engine.kinds import CATALOG
from studio_core.engine.library import validate
from studio_core.engine.recipe import load_recipe_file
from studio_core.ops.pano import (FACES, PanoCubemap, PanoFix, cube_faces, dir_to_uv, dominant_light, face_dirs,
                                  face_size, irradiance, pano_fix, seam_report, uv_to_dir)
from studio_core.ops.tile import _rgb, soft_blur
from test_ops_executors import FakeCtx, load, put_img

RECIPES = Path(__file__).resolve().parents[1] / "studio_core" / "recipes"

# Every cube edge: (face, side, face, side); sides are L/R columns and T/B rows of the face as seen from inside.
EDGES = [("front", "R", "right", "L"), ("right", "R", "back", "L"), ("back", "R", "left", "L"),
         ("left", "R", "front", "L"), ("up", "B", "front", "T"), ("up", "R", "right", "T"), ("up", "T", "back", "T"),
         ("up", "L", "left", "T"), ("down", "T", "front", "B"), ("down", "R", "right", "B"),
         ("down", "B", "back", "B"), ("down", "L", "left", "B")]


def _edge(a: np.ndarray, side: str) -> np.ndarray:
    return {"L": a[:, 0], "R": a[:, -1], "T": a[0, :], "B": a[-1, :]}[side]


def grid_pano(w: int = 2048, step_deg: int = 10) -> Image.Image:
    """Latitude/longitude grid: dark 2px lines every `step_deg` on a light ground, tinted by direction so each face
    differs."""
    h = w // 2
    vv, uu = np.mgrid[0:h, 0:w]
    lon = (uu + 0.5) / w * 360
    lat = (vv + 0.5) / h * 180
    px = 360 / w
    line = (np.abs((lon + step_deg / 2) % step_deg - step_deg / 2) < px) | \
           (np.abs((lat + step_deg / 2) % step_deg - step_deg / 2) < px)
    rgb = np.stack([0.5 + 0.4 * np.sin(np.radians(lon)), 0.6 + 0.3 * np.cos(np.radians(lat)),
                    np.full(lon.shape, 0.8)], -1)
    rgb[line] = 0.05
    return Image.fromarray((rgb * 255).astype(np.uint8), "RGB")


def test_convention_forward_right_up():
    u, v = dir_to_uv(np.array([[0.0, 0, 1], [1, 0, 0], [0, 1, 0], [0, -1, 0]]))
    assert np.allclose(u[:2], [0.5, 0.75]) and np.allclose(v, [0.5, 0.5, 0, 1])
    d = uv_to_dir(np.array([0.5, 0.75]), np.array([0.5, 0.5]))
    assert np.allclose(d, [[0, 0, 1], [1, 0, 0]], atol=1e-9)
    assert np.allclose(face_dirs("front", 1)[0, 0], [0, 0, 1]) and np.allclose(face_dirs("right", 1)[0, 0], [1, 0, 0])
    assert np.allclose(face_dirs("up", 1)[0, 0], [0, 1, 0]) and np.allclose(face_dirs("down", 1)[0, 0], [0, -1, 0])


@pytest.mark.parametrize("a,sa,b,sb", EDGES)
def test_face_edges_meet_within_a_pixel(a, sa, b, sb):
    n = 64
    ea, eb = _edge(face_dirs(a, n), sa), _edge(face_dirs(b, n), sb)
    pixel = np.radians(90 / n)
    ang = [np.arccos(np.clip((ea * e).sum(-1), -1, 1)).max() for e in (eb, eb[::-1])]
    assert min(ang) < pixel


def test_grid_lines_continue_across_every_cube_edge():
    eq = _rgb(grid_pano())
    n = 256
    faces = cube_faces(eq, n)
    lum = {f: faces[f].mean(-1) for f in FACES}
    for a, sa, b, sb in EDGES:
        ea, eb = _edge(lum[a], sa), _edge(lum[b], sb)
        best = None
        for e in (eb, eb[::-1]):
            errs = {s: float(np.abs(ea[3:-3] - np.roll(e, s)[3:-3]).mean()) for s in range(-3, 4)}
            s = min(errs, key=lambda k: (round(errs[k], 4), abs(k)))
            if best is None or errs[s] < best[1]:
                best = (s, errs[s])
        assert abs(best[0]) <= 1, (a, sa, b, sb, best)
        assert best[1] < 0.08, (a, sa, b, sb, best)


def test_face_size_auto():
    assert face_size(2048) == 512 and face_size(8192) == 2048 and face_size(512) == 256 and face_size(2048, 300) == 300


def _seamed(w: int = 1024) -> Image.Image:
    rng = np.random.default_rng(3)
    h = w // 2
    base = soft_blur(rng.random((h, w, 3)).astype(np.float32), 5, wrap=False)
    base = (base - base.min()) / (base.max() - base.min())
    ramp = np.linspace(0, 1, w, dtype=np.float32)[None, :, None]
    return Image.fromarray((np.clip(base * 0.6 + ramp * 0.4, 0, 1) * 255).astype(np.uint8), "RGB")


def test_pano_fix_closes_the_wrap_and_pinches_poles():
    src = _seamed()
    assert seam_report(_rgb(src))["score"] < 30
    out, info = pano_fix(src)
    assert out.size == src.size
    assert info["before"]["seam"]["score"] < 30 and info["after"]["seam"]["score"] >= 90
    assert info["after"]["pole"]["top"] < 0.002 and info["after"]["pole"]["bottom"] < 0.002
    assert info["overlap_px"] == round(1024 * 0.03)
    rolled, _ = pano_fix(out, overlap=0, pole=0, yaw=90)
    assert np.array_equal(np.asarray(rolled)[:, :256], np.asarray(out)[:, 256:512])


def test_pano_fix_makes_2to1_and_rejects_tiny():
    out, info = pano_fix(_seamed().resize((1000, 600)))
    assert out.size == (1000, 500) and info["resized"]
    with pytest.raises(ValueError):
        pano_fix(Image.new("RGB", (128, 64)))


def test_irradiance_and_sun_follow_the_bright_spot():
    w, h = 512, 256
    vv, uu = np.mgrid[0:h, 0:w]
    d = uv_to_dir((uu + 0.5) / w, (vv + 0.5) / h)
    sun = np.array([0.6, 0.5, -0.62])
    sun /= np.linalg.norm(sun)
    eq = np.clip(0.15 + (np.clip(d @ sun, 0, 1) ** 40)[..., None] * 3, 0, 1).astype(np.float32) * np.ones(3)
    light = dominant_light(eq)
    assert np.degrees(np.arccos(np.clip(np.dot(light["dir"], sun), -1, 1))) < 6
    assert light["distinct"] and 20 < light["elevation"] < 40
    irr = irradiance(eq)
    lit = irr[..., 0]
    at = lambda v: lit[int(dir_to_uv(v)[1] * irr.shape[0]), int(dir_to_uv(v)[0] * irr.shape[1]) % irr.shape[1]]  # noqa: E731
    assert at(sun) > lit.mean() + 0.05 > at(-sun) + 0.05


async def _cube(ctx, upscale: int = 0):
    src = await put_img(ctx, _seamed(), "cand.png")
    fix = await PanoFix().run(ctx, {"asset": "Hoàng hôn", "upscale": upscale}, {"images": [src]})
    up = []
    if fix.outputs["upscale_in"]:
        strip = load(ctx, fix.outputs["upscale_in"][0])
        up = [await put_img(ctx, strip.resize((strip.width * 4, strip.height * 4), Image.Resampling.NEAREST),
                            "up.png")]
    cube = await PanoCubemap().run(ctx, {"asset": "Hoàng hôn"}, {"images": fix.outputs["images"], "upscaled": up,
                                                                 "info": fix.outputs["info"]})
    return fix, cube


async def test_executors_write_faces_and_skybox_doc(tmp_path: Path):
    ctx = FakeCtx(tmp_path)
    fix, cube = await _cube(ctx)
    assert fix.meta["seam_after"] >= 90 and fix.outputs["upscale_in"] == []
    assert load(ctx, fix.outputs["preview"][0]).height > 0
    faces = cube.outputs["faces"]
    assert [r.name for r in faces] == [f"hoang_hon_{f}.png" for f in FACES]
    assert load(ctx, faces[0]).size == (256, 256)
    doc = json.loads(ctx.blobs.path(cube.outputs["skybox"][0].hash).read_bytes())
    assert doc["type"] == "asset-studio/skybox" and doc["face_size"] == 256 and doc["pano"]["width"] == 1024
    assert set(doc["faces"]) == set(FACES) and doc["seam"]["score"] >= 90 and len(doc["light"]["dir"]) == 3
    assert load(ctx, cube.outputs["preview"][0]).size == (1024, 768)


async def test_upscaled_strip_is_unwrapped_to_target(tmp_path: Path):
    ctx = FakeCtx(tmp_path)
    fix, cube = await _cube(ctx, upscale=4096)
    strip = load(ctx, fix.outputs["upscale_in"][0])
    assert strip.size == (1024 + 64, 512)
    pano = load(ctx, cube.outputs["images"][0])
    assert pano.size == (4096, 2048) and cube.meta["face_size"] == 1024
    small = np.asarray(pano.resize((1024, 512), Image.Resampling.BOX), np.float32)
    ref = np.asarray(load(ctx, fix.outputs["images"][0]), np.float32)[..., :3]
    assert np.abs(small[..., :3] - ref).mean() < 4


async def test_bad_params(tmp_path: Path):
    ctx = FakeCtx(tmp_path)
    src = await put_img(ctx, _seamed(), "c.png")
    with pytest.raises(StepError):
        await PanoFix().run(ctx, {"upscale": 3000}, {"images": [src]})
    with pytest.raises(StepError):
        await PanoCubemap().run(ctx, {"face_size": 10}, {"images": [src]})


async def test_export_unity_and_godot(tmp_path: Path):
    from test_export_engines import _row

    from studio_core.export import godot, unity
    from studio_core.export.skybox import PANORAMIC_SHADER_ID, SIX_SIDED_SHADER_ID
    from studio_core.store.bundle import engine_ready, engine_source

    ctx = FakeCtx(tmp_path)
    fix, cube = await _cube(ctx)
    step = _row("approve", "gate.approve", {"images": cube.outputs["images"], "faces": cube.outputs["faces"],
                                            "skybox": cube.outputs["skybox"], "seam": fix.outputs["preview"]})
    assert engine_ready(step, ctx.blobs)
    src = engine_source(step, [], ctx.blobs, "Desert")
    assert src.skybox and set(src.skybox_files) == {"pano", "irradiance", *FACES}
    u = dict(unity.export(src))
    assert {"Skybox/Desert_pano.png", "Skybox/Desert_front.png", "Skybox/Desert_panoramic.mat",
            "Skybox/Desert_6sided.mat", "Skybox/Desert_skybox.json", "Editor/AssetStudioSkybox.cs"} <= set(u)
    pano_meta = u["Skybox/Desert_pano.png.meta"].decode()
    assert "enableMipMap: 0" in pano_meta and "wrapU: 0" in pano_meta and "wrapV: 1" in pano_meta
    assert "wrapU: 1" in u["Skybox/Desert_up.png.meta"].decode()
    pm, sm = u["Skybox/Desert_panoramic.mat"].decode(), u["Skybox/Desert_6sided.mat"].decode()
    assert f"fileID: {PANORAMIC_SHADER_ID}," in pm and "_MAPPING_LATITUDE_LONGITUDE_LAYOUT" in pm
    assert f"fileID: {SIX_SIDED_SHADER_ID}," in sm and sm.count("guid: ") == 7
    guids = [ln for k, b in u.items() if k.endswith(".meta") for ln in b.decode().splitlines() if ln.startswith("guid:")]
    assert len(guids) == len(set(guids))
    assert "namespace AssetStudio" in u["Editor/AssetStudioSkybox.cs"].decode()
    assert "Use As Skybox" in u["README.txt"].decode()
    g = dict(godot.export(src))
    assert {"Skybox/Desert_sky.tres", "Skybox/Desert_environment.tres", "Skybox/Desert_world.tscn",
            "Skybox/Faces/Desert_down.png"} <= set(g)
    assert 'PanoramaSkyMaterial' in g["Skybox/Desert_sky.tres"].decode()
    assert 'path="Desert_pano.png"' in g["Skybox/Desert_sky.tres"].decode()
    scene = g["Skybox/Desert_world.tscn"].decode()
    assert "WorldEnvironment" in scene and "DirectionalLight3D" in scene


def test_recipe_loads_and_lints():
    r = load_recipe_file(RECIPES / "skybox-360.yaml")
    assert [s.id for s in r.steps] == ["brief", "gen", "pick", "fix", "up", "cube", "approve"]
    _, issues = validate((RECIPES / "skybox-360.yaml").read_text(encoding="utf-8"), None)
    assert issues == []
    for kind in ("op.pano_fix", "op.pano_cubemap"):
        assert CATALOG[kind]["label"]
