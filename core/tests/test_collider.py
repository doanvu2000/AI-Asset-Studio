import json
import math
import re
import time
from pathlib import Path

import numpy as np
import yaml
from PIL import Image, ImageDraw

from studio_core.contracts import Lane, RunRow, StepRow, StepStatus
from studio_core.export import godot, unity
from studio_core.export.collider import (ColliderOptions, _area, default_enabled, foot_pivot, sheet_collider, trace,
                                         unity_physics_shape)
from studio_core.export.sheet import layout
from studio_core.export.source import Source

from test_api_basic import AUTH, app_ctx  # noqa: F401  (fixture reuse)

F = 32


def _inside(poly, x, y) -> bool:
    hit = False
    for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]):
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            hit = not hit
    return hit


def _disc(size=64, r=20) -> np.ndarray:
    yy, xx = np.mgrid[:size, :size]
    return (xx + 0.5 - size / 2) ** 2 + (yy + 0.5 - size / 2) ** 2 <= r * r


def test_circle_is_one_simplified_ring_close_to_the_disc() -> None:
    mask = _disc()
    polys = trace(mask, tolerance=1.0)
    assert len(polys) == 1
    poly = polys[0]
    assert 8 <= len(poly) <= 40
    assert abs(abs(_area(poly)) - mask.sum()) / mask.sum() < 0.06
    for x, y in poly:
        assert abs(math.hypot(x - 32, y - 32) - 20) < 1.6
    assert len(trace(mask, tolerance=4.0)[0]) < len(poly)


def test_l_shape_keeps_its_concave_corner() -> None:
    mask = np.zeros((40, 40), dtype=bool)
    mask[5:35, 5:15] = True
    mask[25:35, 5:35] = True
    (poly,) = trace(mask, tolerance=0.6)
    assert 6 <= len(poly) <= 10
    assert _inside(poly, 10, 10) and _inside(poly, 30, 30)
    assert not _inside(poly, 30, 10)
    assert abs(abs(_area(poly)) - mask.sum()) <= 0.05 * mask.sum()


def test_two_islands_merge_into_a_hull_and_specks_drop() -> None:
    mask = np.zeros((40, 60), dtype=bool)
    mask[10:30, 5:20] = True
    mask[10:30, 40:55] = True
    mask[2, 30] = True
    kept = trace(mask, merge="keep")
    assert len(kept) == 2
    (hull,) = trace(mask)
    assert _inside(hull, 30, 20) and _inside(hull, 10, 20) and _inside(hull, 50, 20)
    assert not any(y < 9 for _, y in hull)


def test_foot_pivot_is_lowest_row_centre() -> None:
    mask = np.zeros((20, 20), dtype=bool)
    mask[3:10, 4:16] = True
    mask[10:15, 8:12] = True
    assert foot_pivot(mask) == (10.0, 15.0)
    assert foot_pivot(np.zeros((4, 4), dtype=bool)) is None


def test_recipe_default() -> None:
    assert default_enabled("sprite-8dir-walk") and default_enabled("sprite-8dir-2d")
    assert not default_enabled("vfx-flipbook") and not default_enabled(None)


def _sheet(tmp: Path) -> Path:
    im = Image.new("RGBA", (2 * F, 2 * F), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for r in range(2):
        for c in range(2):
            ox, oy = c * F, r * F
            d.ellipse([ox + 8 + c * 4, oy + 4, ox + 20 + c * 4, oy + 16], fill=(200, 50, 50, 255))
            d.rectangle([ox + 12, oy + 16, ox + 18, oy + 27], fill=(50, 50, 200, 255))
    p = tmp / "sheet.png"
    im.save(p)
    return p


def _meta():
    return {"frame_w": F, "frame_h": F, "cols": 2, "rows": 2, "count": 4, "padding": 0, "width": 2 * F,
            "height": 2 * F, "row_angles": [0, 180]}


def _src(tmp: Path, **opts) -> Source:
    return Source(asset="Hero", sheet=_sheet(tmp), meta=_meta(), ppu=32, collider=ColliderOptions(**opts))


def test_union_per_clip_and_per_frame(tmp_path: Path) -> None:
    lay = layout(_meta())
    union = sheet_collider(_sheet(tmp_path), lay, ColliderOptions())
    assert [k for k, _ in union.groups] == ["anim_S", "anim_N"]
    assert union.shapes["anim_S_0"] == union.shapes["anim_S_1"]
    assert union.pivot == (15.5, 28.0)
    frame = sheet_collider(_sheet(tmp_path), lay, ColliderOptions(per_frame=True))
    assert [k for k, _ in frame.groups] == ["anim_S_0", "anim_S_1", "anim_N_0", "anim_N_1"]
    assert frame.shapes["anim_S_0"] != frame.shapes["anim_S_1"]


def _unity_docs(text: str) -> list[dict]:
    plain = re.sub(r"^--- !u!(\d+) &(-?\d+)", r"---", text, flags=re.M)
    return [d for d in yaml.safe_load_all(plain) if d]


def test_unity_meta_has_physics_shape_and_foot_pivot(tmp_path: Path) -> None:
    files = dict(unity.export(_src(tmp_path)))
    meta = yaml.safe_load(files["Hero.png.meta"].decode())
    sprites = meta["TextureImporter"]["spriteSheet"]["sprites"]
    assert len(sprites) == 4
    for s in sprites:
        (shape,) = s["physicsShape"]
        assert len(shape) >= 4
        assert all(-F / 2 <= p["x"] <= F / 2 and -F / 2 <= p["y"] <= F / 2 for p in shape)
        assert s["pivot"] == {"x": round(15.5 / F, 4), "y": round(1 - 28 / F, 4)}
    assert "Custom Physics Shape" in files["README.txt"].decode()


def test_unity_physics_shape_is_centre_relative_y_up() -> None:
    doc = yaml.safe_load("s:\n" + unity_physics_shape([[(0, 0), (32, 0), (32, 16)]], 32, 32))
    assert doc["s"]["physicsShape"] == [[{"x": -16, "y": 16}, {"x": 16, "y": 16}, {"x": 16, "y": 0}]]


def test_unity_without_collider_is_unchanged(tmp_path: Path) -> None:
    src = Source(asset="Hero", sheet=_sheet(tmp_path), meta=_meta(), ppu=32)
    meta = yaml.safe_load(dict(unity.export(src))["Hero.png.meta"].decode())
    assert all(s["physicsShape"] == [] for s in meta["TextureImporter"]["spriteSheet"]["sprites"])


def test_godot_body_scene_has_collision_polygons(tmp_path: Path) -> None:
    files = dict(godot.export(_src(tmp_path, occluder=True)))
    body = files["Hero_body.tscn"].decode()
    assert '[node name="Hero_body" type="CharacterBody2D"]' in body
    assert '[ext_resource type="PackedScene" path="Hero.tscn" id="1_sprite"]' in body
    polys = re.findall(r'\[node name="Collider_(\w+)" type="CollisionPolygon2D" parent="\."\]\npolygon = '
                       r'PackedVector2Array\(([^)]*)\)', body)
    assert [k for k, _ in polys] == ["anim_S", "anim_N"]
    nums = [float(v) for v in polys[0][1].split(",")]
    assert len(nums) >= 8 and max(nums[1::2]) <= 0.01
    assert body.count("type=\"LightOccluder2D\"") == 2 and body.count('[sub_resource type="OccluderPolygon2D"') == 2
    assert body.count("disabled = true") == 1
    assert "asset_studio_collider.gd" in files
    scene = files["Hero.tscn"].decode()
    assert "offset = Vector2(0.5, -12.0)" in scene


def test_godot_without_collider_has_no_body(tmp_path: Path) -> None:
    files = dict(godot.export(Source(asset="Hero", sheet=_sheet(tmp_path), meta=_meta(), ppu=32)))
    assert "Hero_body.tscn" not in files and "asset_studio_collider.gd" not in files


async def test_api_collider_flag_and_recipe_default(app_ctx, tmp_path: Path) -> None:  # noqa: F811
    app, client, _ = app_ctx
    repo, blobs = app.state.repo, app.state.blobs
    await repo.create_run(RunRow(id="r1", recipe_id="sprite-8dir-walk", recipe_hash="h", created_at=time.time()))
    meta = await blobs.put_bytes(json.dumps(_meta()).encode(), name="sheet.json", mime="application/json")
    sheet = await blobs.put_bytes(_sheet(tmp_path).read_bytes(), name="sheet.png", mime="image/png")
    await repo.create_steps([StepRow(id="walk-1", run_id="r1", key="walk", kind="op.sheet", lane=Lane.CPU,
                                     status=StepStatus.SUCCEEDED, params={"asset": "hero"},
                                     outputs={"sheet": [sheet], "meta": [meta]})])
    opts = (await client.get("/api/steps/walk-1/export", headers=AUTH)).json()
    assert opts["collider_default"] is True, opts
    g = (await client.post("/api/steps/walk-1/export", json={"engine": "godot", "collider_tolerance": 2},
                           headers=AUTH)).json()
    assert (Path(g["dir"]) / "hero_body.tscn").is_file()
    off = (await client.post("/api/steps/walk-1/export", json={"engine": "godot", "collider": False},
                             headers=AUTH)).json()
    assert not (Path(off["dir"]) / "hero_body.tscn").exists()
    bad = await client.post("/api/steps/walk-1/export", json={"engine": "unity", "collider_mode": "x"}, headers=AUTH)
    assert bad.status_code in (400, 422)
