import io
import json
import time
from pathlib import Path

import numpy as np
import pytest
import yaml
from PIL import Image, ImageDraw

from studio_core.contracts import RunRow, StepError, StepRow, StepStatus, Lane
from studio_core.engine.library import validate
from studio_core.engine.recipe import load_recipe_file
from studio_core.export import godot, unity
from studio_core.export.normal2d import Normal2DOptions, apply, normal_image, sheet_normal
from studio_core.export.sheet import layout
from studio_core.export.source import Source
from studio_core.ops.normal2d import Normal2D

from test_api_basic import AUTH, app_ctx  # noqa: F401  (fixture reuse)
from test_ops_executors import FakeCtx, put_img, reg  # noqa: F401

RECIPES = Path(__file__).resolve().parents[1] / "studio_core" / "recipes"
F = 48


def _disc(size: int = 64, r: int = 24, color=(180, 120, 90)) -> np.ndarray:
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    c = size // 2
    ImageDraw.Draw(im).ellipse([c - r, c - r, c + r - 1, c + r - 1], fill=(*color, 255))
    return np.asarray(im)


def _vec(px) -> np.ndarray:
    return np.asarray(px, np.float32) / 127.5 - 1


def test_disc_edges_point_outwards_with_green_up() -> None:
    n = normal_image(_disc(), Normal2DOptions())
    c = 32
    right, left, top, bottom = _vec(n[c, c + 22]), _vec(n[c, c - 23]), _vec(n[c - 23, c]), _vec(n[c + 22, c])
    assert right[0] > 0.4 and left[0] < -0.4
    # Image row 0 is the top; OpenGL normal maps (Godot, Unity) want the top rim facing +G.
    assert top[1] > 0.4 and bottom[1] < -0.4
    centre = _vec(n[c, c])
    assert centre[2] > 0.97
    assert tuple(n[0, 0]) == (128, 128, 255) and tuple(n[63, 63]) == (128, 128, 255)
    unit = np.linalg.norm(_vec(n[8:56, 8:56]), axis=-1)
    assert np.allclose(unit, 1, atol=0.03)


def test_strength_and_bevel_change_the_slope() -> None:
    rgba = _disc()
    soft = normal_image(rgba, Normal2DOptions(strength=0.3))
    hard = normal_image(rgba, Normal2DOptions(strength=3))
    assert _vec(hard[32, 54])[0] > _vec(soft[32, 54])[0]
    narrow = normal_image(rgba, Normal2DOptions(bevel=2))
    wide = normal_image(rgba, Normal2DOptions(bevel=16))
    assert _vec(narrow[32, 44])[2] > 0.97 and _vec(wide[32, 44])[2] < 0.97


def test_luminance_detail_adds_relief_inside_flat_alpha() -> None:
    rgba = np.zeros((40, 40, 4), np.uint8)
    rgba[..., 3] = 255
    rgba[..., :3] = 90
    rgba[:, 20:, :3] = 220
    flat = normal_image(rgba, Normal2DOptions(detail=0, bevel=2))
    relief = normal_image(rgba, Normal2DOptions(detail=2, bevel=2))
    assert tuple(flat[20, 20]) == tuple(flat[20, 10])
    assert _vec(relief[20, 20])[0] < -0.1


def _sheet(tmp: Path) -> Path:
    """2×1 sheet whose frames fill their cells edge to edge: a sheet-wide filter would see one long bar."""
    im = Image.new("RGBA", (2 * F, F), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 10, 2 * F - 1, 37], fill=(200, 60, 60, 255))
    d.ellipse([F + 10, 4, F + 30, 24], fill=(60, 60, 220, 255))
    p = tmp / "sheet.png"
    im.save(p)
    return p


def _meta() -> dict:
    return {"frame_w": F, "frame_h": F, "cols": 2, "rows": 1, "count": 2, "padding": 0, "width": 2 * F, "height": F}


def test_sheet_frames_are_filtered_on_their_own(tmp_path: Path) -> None:
    sheet = _sheet(tmp_path)
    rgba = np.asarray(Image.open(sheet).convert("RGBA"))
    lay = layout(_meta())
    opts = Normal2DOptions()
    per = np.asarray(Image.open(io.BytesIO(sheet_normal(sheet, lay, opts))))
    for c in lay.cells:
        alone = normal_image(np.ascontiguousarray(rgba[c.y:c.y + c.h, c.x:c.x + c.w]), opts)
        assert np.array_equal(per[c.y:c.y + c.h, c.x:c.x + c.w], alone)
    # The bar is cut at the frame seam, so each frame bevels its own end there.
    assert _vec(per[24, F - 3])[0] > 0.25 and _vec(per[24, F + 2])[0] < -0.25
    whole = normal_image(rgba, opts)
    assert abs(_vec(whole[24, F - 3])[0]) < 0.05


def _src(tmp: Path, normal=None) -> Source:
    return Source(asset="Hero", uid="u1", sheet=_sheet(tmp), meta=_meta(), ppu=48, normal=normal)


def test_unity_gets_normal_texture_and_secondary_slot(tmp_path: Path) -> None:
    src = _src(tmp_path)
    note = apply(src, Normal2DOptions(strength=1.5))
    assert isinstance(src.normal, bytes) and "Normal map 2D" in (note or "")
    files = dict(unity.export(src))
    png = Image.open(io.BytesIO(files["Hero_normal.png"]))
    assert png.size == (2 * F, F) and png.mode == "RGB"
    nmeta = yaml.safe_load(files["Hero_normal.png.meta"].decode())["TextureImporter"]
    assert nmeta["textureType"] == 1 and nmeta["mipmaps"]["sRGBTexture"] == 0 and nmeta["spriteMode"] == 0
    main = yaml.safe_load(files["Hero.png.meta"].decode())
    (sec,) = main["TextureImporter"]["spriteSheet"]["secondaryTextures"]
    assert sec["name"] == "_NormalMap"
    assert sec["texture"]["guid"] == yaml.safe_load(files["Hero_normal.png.meta"].decode())["guid"]
    prefab = files["Hero.prefab"].decode()
    assert unity.SPRITE_LIT_MAT in prefab and unity.SPRITE_DEFAULT_MAT not in prefab


def test_unity_prefab_without_normal_keeps_sprites_default(tmp_path: Path) -> None:
    prefab = dict(unity.export(_src(tmp_path)))["Hero.prefab"].decode()
    assert unity.SPRITE_DEFAULT_MAT in prefab and unity.SPRITE_LIT_MAT not in prefab


def test_godot_sprite_frames_use_canvas_texture(tmp_path: Path) -> None:
    src = _src(tmp_path)
    apply(src, Normal2DOptions())
    files = dict(godot.export(src))
    tres = files["Hero.tres"].decode()
    assert '[sub_resource type="CanvasTexture" id="CanvasTexture_sheet"]' in tres
    assert 'normal_texture = ExtResource("2_normal")' in tres and 'path="Hero_normal.png"' in tres
    assert tres.count('atlas = SubResource("CanvasTexture_sheet")') == 2
    assert "Hero_normal.png" in files


def test_rendered_normal_sheet_wins_and_off_is_noop(tmp_path: Path) -> None:
    rendered = tmp_path / "n.png"
    Image.new("RGB", (2 * F, F), (128, 128, 255)).save(rendered)
    src = _src(tmp_path, normal=rendered)
    assert "bỏ qua" in (apply(src, Normal2DOptions()) or "") and src.normal == rendered
    plain = _src(tmp_path)
    assert apply(plain, None) is None and plain.normal is None


async def test_op_with_grid_writes_normal_and_preview(tmp_path: Path) -> None:
    ctx = FakeCtx(tmp_path)
    ref = await put_img(ctx, Image.open(_sheet(tmp_path)).convert("RGBA"), "hero.png")
    res = await Normal2D().run(ctx, {"cols": 2, "rows": 1}, {"images": [ref]})
    (n,) = res.outputs["images"]
    assert n.name == "hero_normal.png"
    arr = np.asarray(Image.open(ctx.blobs.path(n.hash)))
    assert _vec(arr[24, F - 3])[0] > 0.25
    prev = Image.open(ctx.blobs.path(res.outputs["preview"][0].hash))
    assert prev.size == (6 * F, F)
    assert res.meta["green"] == "opengl"
    with pytest.raises(StepError):
        await Normal2D().run(ctx, {"strength": 0}, {"images": [ref]})
    off = await Normal2D().run(ctx, {"enabled": False}, {"images": [ref]})
    assert off.outputs == {"images": [], "preview": []}


def test_recipe_loads_lints_and_op_is_registered(reg) -> None:
    r = load_recipe_file(RECIPES / "sprite-normal-map.yaml")
    assert [s.id for s in r.steps] == ["normal", "approve"]
    _, issues = validate((RECIPES / "sprite-normal-map.yaml").read_text(encoding="utf-8"), None)
    assert issues == []
    assert reg.resolve("op.normal2d")


async def test_api_normal2d_export_flag(app_ctx, tmp_path: Path) -> None:  # noqa: F811
    app, client, _ = app_ctx
    repo, blobs = app.state.repo, app.state.blobs
    await repo.create_run(RunRow(id="r1", recipe_id="sprite-8dir-walk", recipe_hash="h", created_at=time.time()))
    meta = await blobs.put_bytes(json.dumps(_meta()).encode(), name="sheet.json", mime="application/json")
    sheet = await blobs.put_bytes(_sheet(tmp_path).read_bytes(), name="sheet.png", mime="image/png")
    await repo.create_steps([StepRow(id="walk-1", run_id="r1", key="walk", kind="op.sheet", lane=Lane.CPU,
                                     status=StepStatus.SUCCEEDED, params={"asset": "hero"},
                                     outputs={"sheet": [sheet], "meta": [meta]})])
    opts = (await client.get("/api/steps/walk-1/export", headers=AUTH)).json()
    assert opts["normal2d"] is True
    out = (await client.post("/api/steps/walk-1/export",
                             json={"engine": "unity", "normal2d": True, "normal2d_strength": 2}, headers=AUTH)).json()
    d = Path(out["dir"])
    assert (d / "hero_normal.png").is_file() and "_NormalMap" in (d / "hero.png.meta").read_text(encoding="utf-8")
    assert "Normal map 2D" in (d / "README.txt").read_text(encoding="utf-8")
    off = (await client.post("/api/steps/walk-1/export", json={"engine": "godot"}, headers=AUTH)).json()
    assert not (Path(off["dir"]) / "hero_normal.png").exists()
    bad = await client.post("/api/steps/walk-1/export", json={"engine": "godot", "normal2d": True,
                                                              "normal2d_strength": 0}, headers=AUTH)
    assert bad.status_code in (400, 422)
