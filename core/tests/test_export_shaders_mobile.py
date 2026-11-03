import re
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from studio_core.export import android, ios
from studio_core.export.flutter_shaders import SHADER_IDS
from studio_core.export.shaders import template_dir
from studio_core.export.shaders_agsl import AGSL_FILE, AGSL_IDS, agsl_source, kotlin_source
from studio_core.export.shaders_metal import METAL_FILE, METAL_IDS, SWIFT, SWIFT_FILE, metal_source
from studio_core.export.source import Source
from studio_core.store.pack_mobile import AndroidPack, IosPack

W = 32
FLUTTER_ONLY = {"uRect", "uSrc", "uImage"}
TYPE_MAP = {"float": "float", "vec2": "float2", "vec4": "float4"}


def _frag_uniforms(shader_id: str) -> dict[str, str]:
    text = (template_dir() / "flutter" / f"{shader_id}.frag").read_text(encoding="utf-8")
    return {n: t for t, n in re.findall(r"^uniform (\w+) (\w+);", text, re.M)}


def _agsl_uniforms(shader_id: str) -> dict[str, str]:
    return {n: t for t, n in re.findall(r"^uniform (\w+) (\w+);", agsl_source(shader_id), re.M)}


def _metal_functions() -> dict[str, list[tuple[str, str]]]:
    out = {}
    for m in re.finditer(r"\[\[ stitchable \]\] half4 (\w+)\((.*?)\) \{", metal_source(), re.S):
        params = [p.strip() for p in m[2].split(",")]
        out[m[1]] = [tuple(p.rsplit(" ", 1)) if " " in p else ("", p) for p in (q.replace("*", "") for q in params)]
    return out


def _camel(shader_id: str) -> str:
    return "assetStudio" + "".join(w.capitalize() for w in shader_id.split("_"))


def _top_level_args(text: str, start: int) -> int:
    depth, count, seen = 0, 0, False
    for ch in text[start:]:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
            if depth == 0:
                return count + (1 if seen else 0)
        elif ch == "," and depth == 1:
            count += 1
        elif not ch.isspace():
            seen = True
    raise AssertionError("unbalanced call")


def _sheet(tmp: Path) -> Source:
    img = np.zeros((W, 2 * W, 4), np.uint8)
    img[4:28, 4:28] = (180, 160, 140, 255)
    p = tmp / "orb.png"
    Image.fromarray(img, "RGBA").save(p)
    meta = {"frame_w": W, "frame_h": W, "cols": 2, "rows": 1, "count": 2, "padding": 0, "width": 2 * W, "height": W}
    return Source(asset="Orb", sheet=p, meta=meta)


def _write(root: Path, files: list) -> None:
    for name, data in files:
        dst = root / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data if isinstance(data, bytes) else data.read_bytes())


def test_ids_cover_flutter_effects() -> None:
    assert set(AGSL_IDS) == set(METAL_IDS) == set(SHADER_IDS) - {"lit_sprite"}
    for i in AGSL_IDS:
        assert (template_dir() / "agsl" / f"{i}.agsl").is_file()
        assert (template_dir() / "metal" / f"{i}.metal").is_file()


@pytest.mark.parametrize("shader_id", AGSL_IDS)
def test_agsl_uniforms_match_frag(shader_id: str) -> None:
    frag = {k: v for k, v in _frag_uniforms(shader_id).items() if k not in FLUTTER_ONLY}
    agsl = _agsl_uniforms(shader_id)
    assert agsl.pop("uSize") == "float2"
    for name, ftype in frag.items():
        assert name in agsl, f"{shader_id}: {name} missing in AGSL"
        assert agsl[name] == ("shader" if ftype == "sampler2D" else TYPE_MAP[ftype]), f"{shader_id}: {name} type"
    assert set(agsl) == set(frag)


@pytest.mark.parametrize("shader_id", AGSL_IDS)
def test_agsl_syntax_and_conventions(shader_id: str) -> None:
    src = agsl_source(shader_id)
    assert len(re.findall(r"half4 main\(float2 \w+\)", src)) == 1
    for a, b in ("{}", "()", "[]"):
        assert src.count(a) == src.count(b), f"{shader_id}: unbalanced {a}{b}"
    for banned in ("texture(", "gl_FragCoord", "FlutterFragCoord", "#version", "layout(", "sampler2D", "vec2", "vec4"):
        assert banned not in src, f"{shader_id}: GLSL leftover {banned}"
    assert "uTexture.eval(" in src
    if shader_id == "wind_sway":
        assert "uSize.y - fragCoord.y" in src


def test_palette_swap_ramp_uses_pixel_coordinates() -> None:
    src = agsl_source("palette_swap")
    assert "uniform shader uRamp;" in src and "uRamp.eval(float2(" in src and "/ 256.0" not in src


def test_metal_functions_signatures() -> None:
    fns = _metal_functions()
    assert set(fns) == {_camel(i) for i in METAL_IDS}
    layer = {"outline_pulse", "water_ripple", "wind_sway"}
    for i in METAL_IDS:
        params = fns[_camel(i)]
        assert params[0] == ("float2", "position")
        second = params[1][0]
        assert second == ("SwiftUI::Layer" if i in layer else "half4"), i
        if i in layer:
            assert ("float4", "uBounds") in params
        names = {n for _, n in params}
        frag = {k for k, t in _frag_uniforms(i).items() if k not in FLUTTER_ONLY and t != "sampler2D"}
        assert frag <= names, f"{i}: missing {frag - names}"
        assert ("float", "uTime") in params
    src = metal_source()
    assert src.count("#include <metal_stdlib>") == 1 and src.count("#include <SwiftUI/SwiftUI_Metal.h>") == 1
    for a, b in ("{}", "()", "[]"):
        assert src.count(a) == src.count(b)
    assert "gl_FragCoord" not in src and "1.0 - position" not in src


def test_swift_calls_match_metal_signatures() -> None:
    fns = _metal_functions()
    assert "@available(iOS 17, *)" in SWIFT
    for i in METAL_IDS:
        name = _camel(i)
        at = SWIFT.index(f"ShaderLibrary.{name}(")
        passed = _top_level_args(SWIFT, at + len(f"ShaderLibrary.{name}"))
        extra = len(fns[name]) - 2
        assert passed == extra - (1 if i == "palette_swap" else 0), f"{i}: {passed} args vs {extra} metal params"
        effect = "layerEffect" if i in ("outline_pulse", "water_ripple", "wind_sway") else "colorEffect"
        assert f".{effect}(" in SWIFT[SWIFT.rfind("func ", 0, at):at + 1]


def test_kotlin_embeds_every_shader_and_gates_api_33() -> None:
    kt = kotlin_source("com.example.app")
    assert kt.startswith("package com.example.app\n") and "@PKG@" not in kt and "@SOURCES@" not in kt
    assert "Build.VERSION_CODES.TIRAMISU" in kt and "@RequiresApi(33)" in kt
    for i in AGSL_IDS:
        assert f"private const val {i.upper()} = " in kt
        assert f'"{i}"' in kt
    for fn in ("hitFlash", "dissolve", "paletteSwap", "outlinePulse", "waterRipple", "windSway"):
        assert f"fun Modifier.{fn}(" in kt
    assert "ColorFilter.tint" in kt
    assert kt.count("{") == kt.count("}") and kt.count("(") == kt.count(")")
    for i in AGSL_IDS:
        for name in re.findall(r"^uniform (?!shader)\w+ (\w+);", agsl_source(i), re.M):
            assert f'"{name}"' in kt or name in ("uTime", "uSize", "uTexture"), f"{i}: {name} never set"


def test_export_android_ios_shader_option(tmp_path: Path) -> None:
    src = _sheet(tmp_path)
    assert AGSL_FILE not in {n for n, _ in android.export(src)}
    assert not {METAL_FILE, SWIFT_FILE} & {n for n, _ in ios.export(src)}
    a = dict(android.export(src, "com.example.game", shaders=True))
    assert a[AGSL_FILE].startswith(b"package com.example.game")
    assert "AssetStudioShaders.kt" in a["README_ANDROID.md"].decode()
    i = dict(ios.export(src, shaders=True))
    assert METAL_FILE in i and SWIFT_FILE in i
    assert "AssetStudioShaders.metal" in i["README_IOS.md"].decode()


def test_pack_merge_keeps_one_shader_copy(tmp_path: Path) -> None:
    warns: list[str] = []
    ap, ip = AndroidPack(tmp_path / "an", "Pack", "com.example.game", warns), IosPack(tmp_path / "ios", "Pack", warns)
    (tmp_path / "an").mkdir()
    (tmp_path / "ios").mkdir()
    for n in ("a", "b"):
        sub = tmp_path / n
        sub.mkdir()
        src = _sheet(sub)
        src.asset = n
        _write(sub / "an", android.export(src, "com.example.game", shaders=True))
        _write(sub / "ios", ios.export(src, shaders=True))
        ap.add(n, sub / "an")
        ip.add(n, sub / "ios")
    ap.finish()
    ip.finish()
    assert (tmp_path / "an" / AGSL_FILE).is_file() and (tmp_path / "ios" / METAL_FILE).is_file()
    assert (tmp_path / "ios" / SWIFT_FILE).is_file()
    assert not [w for w in warns if "Shaders" in w or "ShaderEffects" in w]
