import json
import re
from pathlib import Path

import pytest

from studio_core.export import theme as th

PALETTES = [
    ("#c0392b", "#2c3e50", "#f1c40f", "#27ae60"),
    ("#ffeb3b", "#fff59d"),
    ("#0d1b2a", "#1b263b", "#415a77"),
    ("#808080", "#909090"),
    ("#000000", "#ffffff"),
    ("#ff00ff", "#00ffff", "#ffff00"),
    (),
    ("not-a-color", "#12"),
]


@pytest.mark.parametrize("palette", PALETTES)
def test_every_pair_reaches_aa_in_light_and_dark(palette) -> None:
    schemes = th.build_schemes(th.ThemeSpec(palette=palette))
    assert set(schemes) == {"light", "dark"}
    for mode, scheme in schemes.items():
        assert list(scheme) == list(th.ROLES)
        for on, base in th.PAIRS:
            assert th.contrast(scheme[on], scheme[base]) >= th.MIN_CONTRAST, (mode, on, base)
    assert min(th.contrast_report(schemes).values()) >= th.MIN_CONTRAST


def test_palette_is_deterministic_and_order_independent() -> None:
    a = th.build_schemes(th.ThemeSpec(palette=PALETTES[0]))
    assert a == th.build_schemes(th.ThemeSpec(palette=PALETTES[0]))
    assert a == th.build_schemes(th.ThemeSpec(palette=tuple(reversed(PALETTES[0]))))


def test_primary_follows_the_most_colorful_palette_entry() -> None:
    scheme = th.build_scheme(("#808080", "#d4351c", "#2c3e50"), dark=False)
    r, g, b = (int(scheme["primary"][i:i + 2], 16) for i in (1, 3, 5))
    assert r > g and r > b


def test_missing_or_invalid_palette_falls_back_to_neutral() -> None:
    for pal in ((), ("zzz", "#12", "")):
        spec = th.ThemeSpec(palette=pal)
        assert not th.has_palette(spec)
        assert th.build_schemes(spec) == th.build_schemes(th.ThemeSpec(palette=th.NEUTRAL_PALETTE))
    assert th.has_palette(th.ThemeSpec(palette=("#336699",)))


def test_dark_is_darker_than_light() -> None:
    s = th.build_schemes(th.ThemeSpec(palette=PALETTES[0]))
    assert th.luminance(s["dark"]["surface"]) < 0.1 < th.luminance(s["light"]["surface"])


def test_flutter_file() -> None:
    files = dict(th.theme_files(th.ThemeSpec(palette=PALETTES[0]), "flutter"))
    dart = files["lib/gen/app_theme.dart"].decode()
    assert "static final ThemeData light" in dart and "static final ThemeData dark" in dart
    assert dart.count("ColorScheme(") == 2 and "Brightness.dark" in dart and "surfaceContainerHighest" in dart
    assert "fontFamily" not in dart
    with_font = dict(th.theme_files(th.ThemeSpec(palette=PALETTES[0], font="Pixel"), "flutter"))
    assert "fontFamily: 'Pixel'" in with_font["lib/gen/app_theme.dart"].decode()
    assert "family: Pixel" in with_font["README_THEME.md"].decode()


def test_android_file() -> None:
    files = dict(th.theme_files(th.ThemeSpec(palette=PALETTES[0]), "android", "com.acme.game"))
    kt = files["kotlin/theme/Theme.kt"].decode()
    assert kt.startswith("package com.acme.game.theme\n")
    assert "lightColorScheme(" in kt and "darkColorScheme(" in kt and "fun AppTheme(" in kt
    assert "R.font" not in kt and "com.acme.game.R" not in kt
    font = dict(th.theme_files(th.ThemeSpec(font="Pixel Font 2"), "android", "com.acme.game"))
    fkt = font["kotlin/theme/Theme.kt"].decode()
    assert "R.font.pixel_font_2" in fkt and "import com.acme.game.R" in fkt and fkt.count("fontFamily = AppFont") == 15


def test_ios_structure() -> None:
    files = dict(th.theme_files(th.ThemeSpec(palette=PALETTES[0]), "ios"))
    swift = files["Sources/AppTheme.swift"].decode()
    assert "enum AppTheme" in swift and 'static let primary = Color("ThemePrimary")' in swift
    assert "static let body = Font.body" in swift
    roles = {m[1] for m in re.finditer(r'static let (\w+) = Color\("Theme\w+"\)', swift)}
    assert roles == set(th.ROLES)
    schemes = th.build_schemes(th.ThemeSpec(palette=PALETTES[0]))
    sets = {p for p in files if p.endswith(".colorset/Contents.json")}
    assert len(sets) == len(th.ROLES)
    doc = json.loads(files["AssetStudio.xcassets/ThemeOnPrimary.colorset/Contents.json"])
    light, dark = doc["colors"]
    assert "appearances" not in light and dark["appearances"][0]["value"] == "dark"
    red = light["color"]["components"]["red"]
    assert red == "0x" + schemes["light"]["onPrimary"][1:3] and doc["info"]["version"] == 1
    assert json.loads(files["AssetStudio.xcassets/Contents.json"])["info"]["author"] == "xcode"
    assert 'Font.custom("Pixel"' in dict(th.theme_files(th.ThemeSpec(font="Pixel"), "ios"))[
        "Sources/AppTheme.swift"].decode()


def test_unknown_target_and_font_detection(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        th.theme_files(th.ThemeSpec(), "unity")
    assert th.detect_font(tmp_path, "ios") is None
    (tmp_path / "Fonts").mkdir()
    (tmp_path / "Fonts" / "b.fnt").write_text("x")
    assert th.detect_font(tmp_path, "ios") is None
    (tmp_path / "Fonts" / "Zeta.otf").write_bytes(b"")
    (tmp_path / "Fonts" / "Alpha.ttf").write_bytes(b"")
    assert th.detect_font(tmp_path, "ios") == "Alpha"


@pytest.mark.parametrize("palette", [("#c0392b", "#2c3e50"), ("#e53935",), ("#ff0000",), ("#800000",), ("#ff4081",)])
def test_error_stays_distinct_from_a_red_primary(palette) -> None:
    for dark in (False, True):
        s = th.build_scheme(palette, dark)
        assert th._distance(s["primary"], s["error"]) >= th.MIN_ERROR_DISTANCE, (palette, dark)
        assert th.contrast(s["onError"], s["error"]) >= th.MIN_CONTRAST
        r, g, b = (int(s["error"][i:i + 2], 16) for i in (1, 3, 5))
        assert r > g and r > b
