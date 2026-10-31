import io
import json
import re
import struct
from pathlib import Path

import pytest
from PIL import Image

from studio_core.export import toon3d
from studio_core.export.shaders import ShaderError, template_dir
from studio_core.export.source import Source


def _glb(path: Path, color=None, lo=(-1, 0, -1), hi=(1, 4, 1)) -> Path:
    blob = b""
    doc = {"asset": {"version": "2.0"},
           "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
           "accessors": [{"min": list(lo), "max": list(hi), "count": 3, "componentType": 5126, "type": "VEC3"}],
           "materials": [{"pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 1]}}]}
    if color is not None:
        buf = io.BytesIO()
        Image.new("RGB", (8, 8), color).save(buf, "PNG")
        blob = buf.getvalue()
        blob += b"\0" * (-len(blob) % 4)
        doc["bufferViews"] = [{"buffer": 0, "byteOffset": 0, "byteLength": len(blob)}]
        doc["images"] = [{"bufferView": 0, "mimeType": "image/png"}]
        doc["textures"] = [{"source": 0}]
        doc["materials"][0]["pbrMetallicRoughness"]["baseColorTexture"] = {"index": 0}
    js = json.dumps(doc).encode()
    js += b" " * (-len(js) % 4)
    body = struct.pack("<I4s", len(js), b"JSON") + js
    if blob:
        body += struct.pack("<I4s", len(blob), b"BIN\x00") + blob
    path.write_bytes(b"glTF" + struct.pack("<II", 2, 12 + len(body)) + body)
    return path


def _src(tmp_path, color=(200, 60, 40)) -> Source:
    return Source(asset="rock", uid="step1", glb=[("rock.glb", _glb(tmp_path / "rock.glb", color))])


def _text(files: dict, rel: str) -> str:
    v = files[rel]
    return (v if isinstance(v, bytes) else Path(v).read_bytes()).decode()


def test_inspect_glb_size_center_and_dominant(tmp_path):
    info = toon3d.inspect_glb(_src(tmp_path).glb[0][1])
    assert info.size == pytest.approx(4.0)
    assert info.center == pytest.approx((0, 2, 0))
    assert info.color is not None and info.color[0] > info.color[2]


def test_no_texture_gives_neutral_ramp(tmp_path):
    info = toon3d.inspect_glb(_glb(tmp_path / "flat.glb"))
    assert info.color is None
    cols = toon3d.ramp_colors(3, None)
    assert all(r == g == b for r, g, b in cols)


@pytest.mark.parametrize("bands", [2, 3, 4])
def test_ramp_png_has_one_pixel_per_band_dark_to_light(bands):
    img = Image.open(io.BytesIO(toon3d.ramp_png(bands, (200, 60, 40))))
    assert img.size == (bands, 1)
    lum = [sum(img.getpixel((i, 0))) for i in range(bands)]
    assert lum == sorted(lum) and lum[0] < lum[-1]


@pytest.mark.parametrize("kw", [{"bands": 1}, {"bands": 5}, {"outline_color": "red"}, {"outline_width": 2},
                                {"rim": -0.1}])
def test_preset_validation(kw):
    with pytest.raises(ShaderError):
        toon3d.ToonPreset(**kw)
    with pytest.raises(ShaderError):
        toon3d.preset_from({"nope": 1})


def test_godot_files_and_next_pass(tmp_path):
    out = dict(toon3d.apply([("README.txt", b"x\n")], "godot", _src(tmp_path)))
    for rel in ("Shaders/toon3d.gdshader", "Shaders/toon3d_outline.gdshader", "Shaders/toon3d_apply.gd",
                "Shaders/rock_toon3d.tres", "Shaders/rock_toon3d_ramp.png", "rock_toon.tscn"):
        assert rel in out
    tres = _text(out, "Shaders/rock_toon3d.tres")
    assert 'next_pass = SubResource("ShaderMaterial_outline")' in tres
    assert "shader_parameter/width = 0.048" in tres
    assert 'path="toon3d_outline.gdshader"' in tres
    assert "cull_front" in _text(out, "Shaders/toon3d_outline.gdshader")
    assert "light()" in _text(out, "Shaders/toon3d.gdshader")
    assert 'path="Models/rock.glb"' in _text(out, "rock_toon.tscn")
    assert b"Toon 3D" in out["README.txt"]


def test_unity_guids_fixed_and_material_points_at_shader(tmp_path):
    out = dict(toon3d.apply([], "unity", _src(tmp_path)))
    meta = _text(out, "Shaders/AssetStudio_Toon3D.shader.meta")
    assert f"guid: {toon3d.UNITY_SHADER_GUID}" in meta
    assert f"guid: {toon3d.UNITY_EDITOR_GUID}" in _text(out, "Editor/AssetStudioToon3D.cs.meta")
    mat = _text(out, "Shaders/rock_toon3d.mat")
    assert f"guid: {toon3d.UNITY_SHADER_GUID}, type: 3" in mat
    ramp_guid = re.search(r"guid: ([0-9a-f]{32})", _text(out, "Shaders/rock_toon3d_ramp.png.meta"))[1]
    assert f"guid: {ramp_guid}, type: 3" in mat
    assert "_Center" in mat and "_OutlineWidth: 0.048" in mat
    again = dict(toon3d.apply([], "unity", _src(tmp_path)))
    assert _text(again, "Shaders/rock_toon3d.mat.meta") == _text(out, "Shaders/rock_toon3d.mat.meta")


def test_unity_shader_template_structure():
    s = (template_dir() / "AssetStudio_Toon3D.shader").read_text(encoding="utf-8")
    for needle in ("ShaderLibrary/Core.hlsl", "ShaderLibrary/Lighting.hlsl", "CBUFFER_START(UnityPerMaterial)",
                   '"LightMode" = "UniversalForward"', '"LightMode" = "SRPDefaultUnlit"', "Cull Front",
                   '"LightMode" = "ShadowCaster"'):
        assert needle in s
    assert s.count("{") == s.count("}")


def test_fixed_guids_do_not_clash_with_existing_exports():
    root = Path(__file__).parents[1] / "studio_core"
    for p in root.rglob("*.py"):
        if p.name == "toon3d.py":
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        assert toon3d.UNITY_SHADER_GUID not in text and toon3d.UNITY_EDITOR_GUID not in text


def test_requires_glb():
    with pytest.raises(ShaderError):
        toon3d.apply([], "godot", Source(asset="x"))
