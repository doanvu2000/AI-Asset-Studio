import io
import json
import re
from pathlib import Path

import pytest
from PIL import Image

from studio_core.export import godot, unity
from studio_core.export.shaders import (PRESETS, ShaderError, apply, catalog, godot_assign, parse_color, resolve,
                                        template_dir)
from studio_core.export.source import Source

from test_api_basic import AUTH, app_ctx  # noqa: F401  (fixture reuse)
from test_export_engines import COLS, FH, FW, ROWS, _bytes_png, _meta, _source, _step, _unity_docs


def _files(files) -> dict:
    return dict(files)


def _text(files: dict, rel: str) -> str:
    v = files[rel]
    return (v if isinstance(v, bytes) else Path(v).read_bytes()).decode()


def test_templates_declare_every_param() -> None:
    for p in PRESETS.values():
        gd = (template_dir() / f"{p.id}.gdshader").read_text(encoding="utf-8")
        hlsl = (template_dir() / f"AssetStudio_{p.unity}.shader").read_text(encoding="utf-8")
        assert gd.startswith("// AI Asset Studio") and "shader_type canvas_item;" in gd
        assert f'Shader "{p.shader_name}"' in hlsl and '"RenderPipeline" = "UniversalPipeline"' in hlsl
        cbuffer = hlsl.split("CBUFFER_START(UnityPerMaterial)")[1].split("CBUFFER_END")[0]
        for q in p.params:
            if q.godot:
                assert re.search(rf"uniform \w+ {q.godot}\b", gd), (p.id, q.godot)
            if q.unity:
                assert f"{q.unity} (" in hlsl and re.search(rf"\b{q.unity};", cbuffer), (p.id, q.unity)
    swap = (template_dir() / "palette_swap.gdshader").read_text(encoding="utf-8")
    assert "uniform sampler2D ramp" in swap and "uniform float rows" in swap


def test_resolve_validates_names_and_params() -> None:
    chosen = resolve(["dissolve", "hit_flash", "dissolve"], {"hit_flash": {"color": "#ff0000", "speed": 4},
                                                            "outline_pulse": {"width": 3}})
    assert [c.preset.id for c in chosen] == ["dissolve", "hit_flash"]
    assert chosen[1].values == {"color": "#ff0000", "speed": 4.0} and chosen[0].values == {}
    for names, params in ((["glow"], None), (["hit_flash"], {"hit_flash": {"glow": 1}}),
                          (["hit_flash"], {"hit_flash": {"color": "red"}}), (["hit_flash"], {"hit_flash": {"speed": 99}}),
                          (["hit_flash"], {"hit_flash": {"amount": True}}), (["hit_flash"], {"nope": {}})):
        with pytest.raises(ShaderError):
            resolve(names, params)
    assert parse_color("#80ff0040") == pytest.approx((128 / 255, 1.0, 0.0, 64 / 255))
    assert {c["id"] for c in catalog()} == set(PRESETS)
    assert next(c for c in catalog() if c["id"] == "outline_pulse")["speed"] == 1.5


def test_godot_shaders_ship_materials_and_assign_single(tmp_path: Path) -> None:
    src = _source(tmp_path, normal=False)
    base = godot.export(src)
    files = _files(apply(base, "godot", src, resolve(list(PRESETS), {"dissolve": {"progress": 0.25}})))
    for p in PRESETS.values():
        assert Path(files[f"Shaders/{p.id}.gdshader"]) == template_dir() / f"{p.id}.gdshader"
        mat = _text(files, f"Shaders/Knight_{p.id}.tres")
        assert mat.startswith('[gd_resource type="ShaderMaterial"') and f'path="{p.id}.gdshader"' in mat
        assert "resource_local_to_scene = true" in mat
    assert "shader_parameter/progress = 0.25" in _text(files, "Shaders/Knight_dissolve.tres")
    swap = _text(files, "Shaders/Knight_palette_swap.tres")
    assert 'path="Knight_ramp.png"' in swap and "shader_parameter/rows = 1.0" in swap and "row = 0.0" in swap
    with Image.open(io.BytesIO(files["Shaders/Knight_ramp.png"])) as im:
        assert im.size == (256, 1)
    assert "9_material" not in _text(files, "Knight.tscn")
    assert "Shaders/:" in _text(files, "README.txt") and "palette_swap:" in _text(files, "README.txt")

    one = _files(apply(base, "godot", src, resolve(["hit_flash"])))
    scene = _text(one, "Knight.tscn")
    assert '[ext_resource type="ShaderMaterial" path="Shaders/Knight_hit_flash.tres" id="9_material"]' in scene
    lines = scene.splitlines()
    assert lines[lines.index('[node name="Knight" type="AnimatedSprite2D"]') + 1] == 'material = ExtResource("9_material")'
    assert "Đã gán sẵn Shaders/Knight_hit_flash.tres" in _text(one, "README.txt")
    assert "load_steps=3 " in godot_assign('[gd_scene load_steps=2 format=3]\n\n[ext_resource x]\n\n[node name="a"]\n',
                                           "m.tres")


def test_unity_shaders_materials_meta_and_prefab(tmp_path: Path) -> None:
    src = _source(tmp_path, normal=False)
    src.uid = "step-1"
    base = unity.export(src)
    files = _files(apply(base, "unity", src, resolve(["wind_sway", "palette_swap"], {"wind_sway": {"strength": 0.2}})))
    shader_guid = re.search(r"guid: (\w+)", _text(files, "Shaders/AssetStudio_WindSway.shader.meta")).group(1)
    assert "ShaderImporter" in _text(files, "Shaders/AssetStudio_WindSway.shader.meta")
    mat = _unity_docs(_text(files, "Shaders/Knight_wind_sway.mat"))[0]["Material"]
    assert mat["m_Shader"] == {"fileID": 4800000, "guid": shader_guid, "type": 3}
    floats = {k: v for d in mat["m_SavedProperties"]["m_Floats"] for k, v in d.items()}
    assert floats == {"_Strength": 0.2, "_Speed": 0.6} and mat["m_SavedProperties"]["m_Colors"] == []
    assert "mainObjectFileID: 2100000" in _text(files, "Shaders/Knight_wind_sway.mat.meta")
    swap = _unity_docs(_text(files, "Shaders/Knight_palette_swap.mat"))[0]["Material"]["m_SavedProperties"]
    ramp_guid = re.search(r"guid: (\w+)", _text(files, "Shaders/Knight_ramp.png.meta")).group(1)
    assert swap["m_TexEnvs"][1]["_Ramp"]["m_Texture"]["guid"] == ramp_guid
    assert "filterMode: 0" in _text(files, "Shaders/Knight_ramp.png.meta")
    assert "10754" in _text(files, "Knight.prefab")

    one = _files(apply(base, "unity", src, resolve(["outline_pulse"])))
    mat_guid = re.search(r"guid: (\w+)", _text(one, "Shaders/Knight_outline_pulse.mat.meta")).group(1)
    renderer = next(d["SpriteRenderer"] for d in _unity_docs(_text(one, "Knight.prefab")) if "SpriteRenderer" in d)
    assert renderer["m_Materials"] == [{"fileID": 2100000, "guid": mat_guid, "type": 2}]
    other = Source(asset="Knight", uid="step-2", sheet=src.sheet, meta=src.meta)
    again = _files(apply(unity.export(other), "unity", other, resolve(["outline_pulse"])))
    assert _text(again, "Shaders/AssetStudio_OutlinePulse.shader.meta") != _text(one, "Shaders/AssetStudio_OutlinePulse.shader.meta")


def test_palette_ramp_rows_follow_rarity_tiers(tmp_path: Path) -> None:
    png = tmp_path / "t.png"
    Image.new("RGBA", (16, 16), (90, 90, 90, 255)).save(png)
    src = Source(asset="Sword", rarity=[("common", png), ("epic", png), ("legendary", png)],
                 rarity_colors={"common": "#9aa0a6", "epic": "#a335ee", "legendary": "#ff8000"})
    files = _files(apply(godot.export(src), "godot", src, resolve(["palette_swap"], {"palette_swap": {"row": 1}})))
    with Image.open(io.BytesIO(files["Shaders/Sword_ramp.png"])) as im:
        assert im.size == (256, 3)
        mid = im.getpixel((128, 2))
    assert mid[0] > mid[2]
    mat = _text(files, "Shaders/Sword_palette_swap.tres")
    assert "shader_parameter/row = 1.0" in mat and "rows = 3.0" in mat
    assert "1 = epic" in _text(files, "README.txt")
    audio = Source(asset="Boom", audio=[("boom.ogg", png)])
    with pytest.raises(ShaderError):
        apply(godot.export(audio), "godot", audio, resolve(["hit_flash"]))


async def test_api_export_with_shaders(app_ctx) -> None:  # noqa: F811
    app, client, _ = app_ctx
    blobs = app.state.blobs
    meta = await blobs.put_bytes(json.dumps(_meta()).encode(), name="sheet.json", mime="application/json")
    sheet = await blobs.put_bytes(_bytes_png((COLS * FW, ROWS * FH)), name="sheet.png", mime="image/png")
    await _step(app, "walk-1", "walk", "op.sheet", {"sheet": [sheet], "meta": [meta]})

    opts = (await client.get("/api/steps/walk-1/export", headers=AUTH)).json()
    assert [s["id"] for s in opts["shaders"]] == list(PRESETS)
    body = {"engine": "godot", "shaders": ["hit_flash"], "shader_params": {"hit_flash": {"color": "#ff3030"}}}
    r = await client.post("/api/steps/walk-1/export", json=body, headers=AUTH)
    assert r.status_code == 200, r.text
    root = Path(r.json()["dir"])
    assert "Color(1.0, 0.188235, 0.188235, 1.0)" in (root / "Shaders/knight_hit_flash.tres").read_text(encoding="utf-8")
    assert "9_material" in (root / "knight.tscn").read_text(encoding="utf-8")
    u = await client.post("/api/steps/walk-1/export", json={"engine": "unity", "shaders": list(PRESETS)}, headers=AUTH)
    assert u.status_code == 200 and (Path(u.json()["dir"]) / "Shaders/AssetStudio_Dissolve.shader").is_file()

    bad = await client.post("/api/steps/walk-1/export", json={"engine": "unity", "shaders": ["glow"]}, headers=AUTH)
    assert bad.status_code == 422
    bad = await client.post("/api/steps/walk-1/export",
                            json={"engine": "unity", "shaders": ["hit_flash"], "shader_params": {"hit_flash": {"speed": 50}}},
                            headers=AUTH)
    assert bad.status_code == 422
    assert (await client.post("/api/steps/walk-1/export", json={"shaders": ["hit_flash"]}, headers=AUTH)).status_code == 400
