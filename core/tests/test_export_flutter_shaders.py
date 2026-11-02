import io
import re
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from studio_core.export import flutter
from studio_core.export.flutter_shaders import MAX_LIGHTS, SHADER_IDS
from studio_core.export.source import Source
from studio_core.store.pack_mobile import FlutterPack

W = 32


def _sheet(tmp: Path, normal: bool = False) -> Source:
    img = np.zeros((W, 2 * W, 4), np.uint8)
    yy, xx = np.mgrid[0:W, 0:W]
    for k in range(2):
        img[:, k * W:(k + 1) * W][(xx - 16) ** 2 + (yy - 16) ** 2 <= 144] = (180, 160, 140, 255)
    p = tmp / "orb.png"
    Image.fromarray(img, "RGBA").save(p)
    meta = {"frame_w": W, "frame_h": W, "cols": 2, "rows": 1, "count": 2, "padding": 0, "width": 2 * W, "height": W}
    src = Source(asset="Orb", sheet=p, meta=meta)
    if normal:
        n = tmp / "orb_n.png"
        Image.new("RGB", (2 * W, W), (128, 128, 255)).save(n)
        src.normal = n
    return src


def _files(files: list) -> dict[str, bytes]:
    return {n: d if isinstance(d, bytes) else d.read_bytes() for n, d in files}


def test_default_has_no_shader_pack_without_normal(tmp_path: Path) -> None:
    files = _files(flutter.export(_sheet(tmp_path)))
    assert not any(n.startswith("shaders/") for n in files)
    assert "shaders:" not in files["pubspec_assets.yaml"].decode()


def test_normal_map_brings_pack_lit_shader_and_pubspec(tmp_path: Path) -> None:
    files = _files(flutter.export(_sheet(tmp_path, normal=True)))
    for i in SHADER_IDS:
        assert f"shaders/asset_studio_{i}.frag" in files
    assert "assets/images/sprites/Orb_normal.png" in files
    spec = files["pubspec_assets.yaml"].decode()
    assert "  shaders:\n" in spec and "    - shaders/asset_studio_lit_sprite.frag" in spec
    assert "LitSpriteEffect" in files["lib/gen/asset_studio_shaders.dart"].decode()
    assert "Orb_normal.png" in files["README_FLUTTER.md"].decode()


def test_option_off_and_forced(tmp_path: Path) -> None:
    src = _sheet(tmp_path, normal=True)
    assert not any(n.startswith("shaders/") for n, _ in flutter.export(src, shaders=False))
    forced = _files(flutter.export(_sheet(tmp_path), shaders=True))
    assert "shaders/asset_studio_lit_sprite.frag" in forced
    n = Image.open(io.BytesIO(forced["assets/images/sprites/Orb_normal.png"]))
    assert n.size == (2 * W, W)


def test_shaders_without_normal_skip_lit(tmp_path: Path) -> None:
    src = Source(asset="x")
    from studio_core.export.flutter_shaders import shader_files

    files, _ = shader_files(src, None, "x")
    names = {n for n, _ in files}
    assert "shaders/asset_studio_lit_sprite.frag" not in names and "shaders/asset_studio_hit_flash.frag" in names


UNIFORM = re.compile(r"^uniform (float|vec2|vec4) (\w+);", re.M)
FLOATS = {"float": 1, "vec2": 2, "vec4": 4}
# (uniform names after the shared uRect/uSrc/uImage/uTime block, in the order Dart writes them)
ORDER = {
    "hit_flash": ["uColor", "uAmount", "uSpeed"],
    "dissolve": ["uEdgeColor", "uProgress", "uEdgeWidth", "uNoiseScale", "uSpeed"],
    "palette_swap": ["uRow", "uRows", "uStrength"],
    "outline_pulse": ["uColor", "uWidth", "uSpeed", "uMinAlpha"],
    "water_ripple": ["uTint", "uAmplitude", "uWavelength", "uSpeed"],
    "wind_sway": ["uStrength", "uSpeed", "uWave", "uWorldX"],
    "lit_sprite": ["uAmbient", "uHasNormal", "uLightPos0", "uLightCol0", "uLightPos1", "uLightCol1", "uLightPos2",
                   "uLightCol2", "uLightPos3", "uLightCol3"],
}


@pytest.mark.parametrize("sid", SHADER_IDS)
def test_frag_uniform_order_matches_dart_writer(sid: str) -> None:
    from studio_core.export.shaders import template_dir

    text = (template_dir() / "flutter" / f"{sid}.frag").read_text(encoding="utf-8")
    assert text.startswith("#version 460 core") and "#include <flutter/runtime_effect.glsl>" in text
    names = [n for _, n in UNIFORM.findall(text)]
    assert names == ["uRect", "uSrc", "uImage", "uTime", *ORDER[sid]]
    assert "FlutterFragCoord()" in text and "layout(location = 0) out vec4 fragColor;" in text
    samplers = re.findall(r"^uniform sampler2D (\w+);", text, re.M)
    assert samplers[0] == "uTexture" and len(samplers) == (2 if sid in ("palette_swap", "lit_sprite") else 1)


def test_dart_lit_writer_matches_frag_float_count() -> None:
    from studio_core.export.flutter_shaders import SHADERS_DART

    assert f"static const int maxLights = {MAX_LIGHTS};" in SHADERS_DART
    for cls in ("HitFlashEffect", "DissolveEffect", "PaletteSwapEffect", "OutlinePulseEffect", "WaterRippleEffect",
                "WindSwayEffect", "LitSpriteEffect", "LightSource", "ShaderSpriteComponent",
                "ShaderSpriteAnimationComponent"):
        assert f"class {cls}" in SHADERS_DART


def test_pack_merge_keeps_one_runtime_and_lists_shaders(tmp_path: Path) -> None:
    root = tmp_path / "flutter"
    root.mkdir()
    pack = FlutterPack(root, "Pack", [])
    for name in ("A", "B"):
        d = tmp_path / name
        for n, data in flutter.export(_sheet(tmp_path, normal=True)):
            p = d / n.replace("Orb", name)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data if isinstance(data, bytes) else data.read_bytes())
        pack.add(name, d)
    pack.finish()
    spec = (root / "pubspec_assets.yaml").read_text(encoding="utf-8")
    assert spec.count("shaders/asset_studio_lit_sprite.frag") == 1
    assert (root / "lib/gen/asset_studio_shaders.dart").is_file()
    assert (root / "assets/images/sprites/A_normal.png").is_file()

