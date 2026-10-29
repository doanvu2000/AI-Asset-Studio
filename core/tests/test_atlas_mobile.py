import io
import json
import re
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from studio_core.export import atlas as at
from studio_core.export import mobile as mob
from studio_core.export.unity import texture_meta
from test_api_basic import AUTH, app_ctx  # noqa: F401  (fixture reuse)
from test_pack import _image_run, _project, _sprite_run


def _blob(size: tuple[int, int], box: tuple[int, int, int, int], color: tuple[int, int, int]) -> Image.Image:
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    x0, y0, x1, y1 = box
    arr = np.zeros((y1 - y0, x1 - x0, 4), np.uint8)
    arr[..., :3] = color
    arr[..., 3] = 255
    arr[0, :, 0] = 7
    img.paste(Image.fromarray(arr, "RGBA"), (x0, y0))
    return img


def _sprites() -> list[at.Sprite]:
    return [at.Sprite("hero", _blob((64, 64), (20, 10, 44, 60), (200, 0, 0)), (32.0, 60.0)),
            at.Sprite("coin", _blob((32, 32), (8, 8, 24, 24), (250, 200, 0)), (16.0, 16.0)),
            at.Sprite("coin copy", _blob((32, 32), (8, 8, 24, 24), (250, 200, 0)), (16.0, 16.0)),
            at.Sprite("button", _blob((40, 20), (0, 0, 40, 20), (0, 90, 200)), (20.0, 10.0), border=(4, 4, 4, 4))]


def test_pack_trims_keeps_pivot_and_extrudes() -> None:
    sprites = _sprites()
    a = at.pack(sprites, "ui atlas", at.AtlasOptions(max_size=256, padding=2, extrude=1),
                {"x": (1000, 64, 64), "y": (500, 32, 32)})
    assert a.name == "ui_atlas" and len(a.pages) == 1
    page = np.asarray(a.pages[0])
    w, h = a.pages[0].size
    assert w & (w - 1) == 0 and h & (h - 1) == 0
    by = {f.sprite.name: f for f in a.frames}
    hero = by["hero"]
    assert (hero.tx, hero.ty, hero.w, hero.h) == (20, 10, 24, 50)
    src = np.asarray(sprites[0].image)
    assert np.array_equal(page[hero.y:hero.y + hero.h, hero.x:hero.x + hero.w], src[10:60, 20:44])
    # Extrude copies the edge row/column one pixel out.
    assert np.array_equal(page[hero.y - 1, hero.x:hero.x + hero.w], src[10, 20:44])
    assert np.array_equal(page[hero.y:hero.y + hero.h, hero.x - 1], src[10:60, 20])
    # Foot pivot (32, 60) sits at the bottom edge of the trimmed rect, horizontally centred.
    assert hero.pivot_norm() == (0.5, 0.0)
    assert by["coin_copy"].alias_of == "coin" and (by["coin_copy"].x, by["coin_copy"].y) == (by["coin"].x, by["coin"].y)
    btn = by["button"]
    assert (btn.tx, btn.ty, btn.w, btn.h) == (0, 0, 40, 20)
    rects = {(f.x, f.y, f.w, f.h) for f in a.frames}
    for r1 in rects:
        for r2 in rects:
            if r1 != r2:
                assert (r1[0] + r1[2] + 2 <= r2[0] or r2[0] + r2[2] + 2 <= r1[0]
                        or r1[1] + r1[3] + 2 <= r2[1] or r2[1] + r2[3] + 2 <= r1[1])
    assert a.stats["sprites"] == 4 and a.stats["unique"] == 3
    assert a.stats["before"] == {"textures": 2, "file_bytes": 1500, "gpu_bytes": (64 * 64 + 32 * 32) * 4}
    assert a.stats["after"]["gpu_bytes"] == w * h * 4

    doc = json.loads(at.atlas_json(a))
    assert doc["frames"]["hero"]["spriteSourceSize"] == {"x": 20, "y": 10, "w": 24, "h": 50}
    assert doc["frames"]["hero"]["pivot"] == {"x": 0.5, "y": 0.9375} and doc["frames"]["hero"]["trimmed"]
    assert doc["frames"]["button"]["border"]["left"] == 4 and doc["meta"]["pages"][0]["size"] == {"w": w, "h": h}


def test_pack_multi_page_rotation_and_limits() -> None:
    many = [at.Sprite(f"s{i}", _blob((100, 60), (0, 0, 100, 60), (i * 9 % 255, 50, 90)), (50.0, 30.0))
            for i in range(12)]
    a = at.pack(many, "big", at.AtlasOptions(max_size=256, padding=0, extrude=0))
    assert len(a.pages) == 2 and all(max(p.size) <= 256 for p in a.pages)
    assert at.page_file(a, 1) == "big_1.png"
    r = at.pack([at.Sprite("block", _blob((200, 150), (0, 0, 200, 150), (1, 2, 3)), (10.0, 100.0)),
                 at.Sprite("tall", _blob((50, 160), (0, 0, 50, 160), (4, 5, 6)), (25.0, 80.0))],
                "rot", at.AtlasOptions(max_size=210, padding=0, extrude=0, rotate=True, pot=False))
    tall = next(f for f in r.frames if f.sprite.name == "tall")
    assert tall.rotated and len(r.pages) == 1
    assert np.array_equal(np.asarray(r.pages[0])[tall.y:tall.y + 50, tall.x:tall.x + 160],
                          np.rot90(np.asarray(r.frames[1].sprite.image), k=-1))
    assert json.loads(at.atlas_json(r))["frames"]["tall"]["frame"] == {"x": tall.x, "y": tall.y, "w": 50, "h": 160}
    with pytest.raises(at.AtlasError):
        at.engine_files(r, "unity")
    assert "rot.json" in dict(at.engine_files(r, None))
    with pytest.raises(at.AtlasError):
        at.pack([at.Sprite("huge", _blob((300, 10), (0, 0, 300, 10), (1, 1, 1)), (0.0, 0.0))], "x",
                at.AtlasOptions(max_size=256))


def test_unity_and_godot_atlas_files() -> None:
    sprites = _sprites()
    sprites[0].shapes = [[(20.0, 10.0), (44.0, 10.0), (44.0, 60.0)]]
    a = at.pack(sprites, "ui", at.AtlasOptions(max_size=256))
    files = dict(at.engine_files(a, "unity", 100.0, "u1"))
    meta = files["ui.png.meta"].decode()
    assert "  spriteMode: 2\n" in meta and "  spriteMeshType: 0\n" in meta
    assert meta.count("    - serializedVersion: 2\n      name: ") == 4
    hero = next(f for f in a.frames if f.sprite.name == "hero")
    ph = a.pages[0].height
    assert f"name: hero\n      rect:\n        serializedVersion: 2\n        x: {hero.x}\n        y: {ph - hero.y - 50}" in meta
    assert "pivot: {x: 0.5, y: 0}" in meta and "border: {x: 4, y: 4, z: 4, w: 4}" in meta
    assert "      physicsShape:\n      - - {x: -12, y: 25}" in meta
    assert re.search(r"nameFileIdTable:\n      hero: -?\d+", meta)
    assert "README.txt" in files and b"MB" in files["README.txt"]

    g = dict(at.engine_files(a, "godot"))
    tres = g["ui/hero.tres"].decode()
    assert 'path="../ui.png"' in tres and f"region = Rect2({hero.x}, {hero.y}, 24, 50)" in tres
    assert "margin = Rect2(20, 10, 40, 14)" in tres and "metadata/pivot = Vector2(32.0, 60.0)" in tres


def _png_bytes(size: tuple[int, int], alpha: bool = True) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA" if alpha else "RGB", size).save(buf, "PNG")
    return buf.getvalue()


def test_mobile_unity_overrides_and_report() -> None:
    sprite_meta = texture_meta("hero", "a" * 32, None, (4096, 1024), 100.0, False)
    pixel_meta = texture_meta("pix", "b" * 32, None, (64, 64), 100.0, True)
    tex_meta = texture_meta("rock", "c" * 32, None, (2048, 2048), 100.0, False, repeat=True)
    files = [("hero.png", _png_bytes((4096, 1024))), ("hero.png.meta", sprite_meta.encode()),
             ("pix.png", _png_bytes((64, 64))), ("pix.png.meta", pixel_meta.encode()),
             ("Textures/rock.png", _png_bytes((2048, 2048), False)), ("Textures/rock.png.meta", tex_meta.encode()),
             ("UI/panel.png", _png_bytes((300, 100))),
             ("Seasons/LUT/x_dusk_lut.png", _png_bytes((1024, 32))),
             ("README.txt", b"hello\n")]
    out, rep = mob.apply(files, "unity", uid="u", asset="a")
    d = dict(out)
    hero = d["hero.png.meta"].decode()
    assert hero.count("buildTarget: Android") == 1 and hero.count("buildTarget: iPhone") == 1
    android = hero[hero.index("buildTarget: Android"):]
    assert "maxTextureSize: 2048" in android and "textureFormat: 50" in android and "overridden: 1" in android
    assert "crunchedCompression: 0" in android and "buildTarget: Standalone" in hero
    assert "textureFormat: 4\n" in d["pix.png.meta"].decode()
    rock = d["Textures/rock.png.meta"].decode()
    assert "maxTextureSize: 1024" in rock[rock.index("buildTarget: Android"):] and "    enableMipMap: 1\n" in rock
    assert "UI/panel.png.meta" in d and "textureFormat: 48" in d["UI/panel.png.meta"].decode()
    assert "Seasons/LUT/x_dusk_lut.png.meta" not in d
    rows = {r["file"]: r for r in rep["rows"]}
    assert set(rows) == {"hero.png", "pix.png", "Textures/rock.png", "UI/panel.png"}
    assert rows["hero.png"]["import_size"] == [2048, 512] and rows["hero.png"]["gpu_before"] == 4096 * 1024 * 4
    assert rows["hero.png"]["gpu_after"] == mob.astc_bytes(2048, 512, 6)
    assert rows["pix.png"]["gpu_after"] == rows["pix.png"]["gpu_before"]
    assert rep["totals"]["gpu_after"] < rep["totals"]["gpu_before"] / 8
    assert "Ngân sách mobile" in d["README.txt"].decode() and json.loads(d["mobile_budget.json"])["engine"] == "unity"
    again, _ = mob.apply(out, "unity")
    assert dict(again)["hero.png.meta"].decode().count("buildTarget: Android") == 1


def test_mobile_godot_imports() -> None:
    files = [("hero.png", _png_bytes((512, 512))), ("hero_normal.png", _png_bytes((512, 512))),
             ("Textures/rock.png", _png_bytes((2048, 2048), False)), ("pix/p.png", _png_bytes((32, 32)))]
    out, rep = mob.apply(files, "godot")
    d = {k: v for k, v in out}
    hero = d["hero.png.import"].decode()
    assert 'importer="texture"' in hero and "compress/mode=2" in hero and "compress/high_quality=true" in hero
    assert "mipmaps/generate=false" in hero and "process/size_limit=2048" in hero and "uid=" not in hero
    assert "compress/normal_map=1" in d["hero_normal.png.import"].decode()
    rock = d["Textures/rock.png.import"].decode()
    assert "mipmaps/generate=true" in rock and "process/size_limit=1024" in rock
    rows = {r["file"]: r for r in rep["rows"]}
    assert rows["Textures/rock.png"]["format"] == "VRAM: ETC2 RGB"
    assert rows["Textures/rock.png"]["gpu_after"] == int(1024 * 1024 * 0.5 * 4 / 3)
    _, prep = mob.apply(files, "godot", pixel=True)
    assert {r["file"]: r["kind"] for r in prep["rows"]}["pix/p.png"] == "pixel"


async def test_pack_with_atlas_and_mobile(app_ctx, tmp_path: Path) -> None:  # noqa: F811
    app, client, _ = app_ctx
    await _project(app)
    await _sprite_run(app, "r1", "Hiệp sĩ", (200, 0, 0))
    await _sprite_run(app, "r2", "Cung thủ", (0, 0, 200))
    await _image_run(app, "r3", "Icon")
    r = await client.post("/api/projects/p1/pack", headers=AUTH, json={
        "engines": ["unity", "godot"], "out_dir": str(tmp_path), "atlas": True, "mobile": True,
        "atlas_max_size": 512})
    assert r.status_code == 201, r.text
    res = r.json()
    assert res["atlas"]["folder"] == "Atlas" and res["atlas"]["sprites"] == 17
    assert res["atlas"]["before"]["textures"] == 3 and res["atlas"]["pages"] == 1
    root = Path(res["dir"])
    pkg = root / "unity" / "com.assetstudio.rung-dem" / "Runtime" / "Atlas"
    meta = (pkg / "rung_dem_atlas.png.meta").read_text(encoding="utf-8")
    assert "spriteMode: 2" in meta and "buildTarget: Android" in meta and "name: Hiep_si_" in meta
    assert (pkg / "rung_dem_atlas.json").is_file()
    sheet_meta = (root / "unity" / "com.assetstudio.rung-dem" / "Runtime" / "Hiep_si" / "Hiep_si.png.meta")
    assert "buildTarget: iPhone" in sheet_meta.read_text(encoding="utf-8")
    gd = root / "godot" / "addons" / "rung_dem" / "Atlas"
    assert (gd / "rung_dem_atlas.png.import").is_file() and any((gd / "rung_dem_atlas").glob("Icon_*.tres"))
    assert res["mobile"]["unity"]["totals"]["gpu_after"] < res["mobile"]["unity"]["totals"]["gpu_before"]
    assert any(row["file"].startswith("Atlas/") for row in res["mobile"]["godot"]["rows"])


async def test_atlas_endpoint(app_ctx) -> None:  # noqa: F811
    app, client, _ = app_ctx
    await _project(app)
    await _sprite_run(app, "r1", "Knight", (200, 0, 0))
    await _image_run(app, "r3", "Icon")
    r = await client.post("/api/atlas", headers=AUTH, json={
        "steps": [{"step_id": "r1-sheet", "name": "Knight"}, {"step_id": "r3-gen", "name": "Icon"}],
        "name": "level1", "engine": "godot", "mobile": True})
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["stats"]["sprites"] == 9 and out["mobile"]["rows"][0]["file"] == "level1.png"
    d = Path(out["dir"])
    assert (d / "level1.json").is_file() and len(list((d / "level1").glob("Knight_*.tres"))) == 8
    bad = await client.post("/api/atlas", headers=AUTH, json={"steps": [{"step_id": "nope"}]})
    assert bad.status_code == 404
