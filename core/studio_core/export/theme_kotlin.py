"""Compose Theme.kt: lightColorScheme/darkColorScheme + Typography wrapped in AppTheme { }."""
from __future__ import annotations

import re

from studio_core.export.source import Files

FILE = "kotlin/theme/Theme.kt"
README = """theme/Theme.kt: MaterialTheme sáng/tối lấy từ palette style bible; mọi cặp chữ/nền đạt WCAG AA (>= 4.5).
Chép kotlin/theme/ vào app/src/main/java/{pkgpath}/theme/ (package {pkg}.theme) rồi bọc UI bằng AppTheme {{ ... }}.
"""
STYLES = ("displayLarge", "displayMedium", "displaySmall", "headlineLarge", "headlineMedium", "headlineSmall",
          "titleLarge", "titleMedium", "titleSmall", "bodyLarge", "bodyMedium", "bodySmall", "labelLarge",
          "labelMedium", "labelSmall")
SCHEME_FN = {"light": "lightColorScheme", "dark": "darkColorScheme"}


def font_res(font: str) -> str:
    name = re.sub(r"[^a-z0-9_]+", "_", font.lower()).strip("_")
    return f"f_{name}" if not name or name[0].isdigit() else name


def _scheme(mode: str, colors: dict[str, str]) -> str:
    rows = "\n".join(f"    {k} = Color(0xFF{v.lstrip('#').upper()})," for k, v in colors.items())
    return f"private val {mode.capitalize()}Colors = {SCHEME_FN[mode]}(\n{rows}\n)\n"


def _typography(font: str | None) -> str:
    if not font:
        return "private val AppTypography = Typography()\n"
    copies = "\n".join(f"        {s} = base.{s}.copy(fontFamily = AppFont)," for s in STYLES)
    return (f"private val AppFont = FontFamily(Font(R.font.{font_res(font)}))\n\n"
            f"private val AppTypography = Typography().let {{ base ->\n    base.copy(\n{copies}\n    )\n}}\n")


def theme_kt(schemes: dict[str, dict[str, str]], font: str | None, package: str) -> str:
    imports = ["androidx.compose.foundation.isSystemInDarkTheme", "androidx.compose.material3.MaterialTheme",
               "androidx.compose.material3.Typography", "androidx.compose.material3.darkColorScheme",
               "androidx.compose.material3.lightColorScheme", "androidx.compose.runtime.Composable",
               "androidx.compose.ui.graphics.Color"]
    if font:
        imports += ["androidx.compose.ui.text.font.Font", "androidx.compose.ui.text.font.FontFamily", f"{package}.R"]
    head = f"package {package}.theme\n\n" + "\n".join(f"import {i}" for i in sorted(imports)) + "\n\n"
    body = (f"{_scheme('light', schemes['light'])}\n{_scheme('dark', schemes['dark'])}\n{_typography(font)}\n"
            "@Composable\nfun AppTheme(\n    darkTheme: Boolean = isSystemInDarkTheme(),\n"
            "    content: @Composable () -> Unit,\n) {\n    MaterialTheme(\n"
            "        colorScheme = if (darkTheme) DarkColors else LightColors,\n"
            "        typography = AppTypography,\n        content = content,\n    )\n}\n")
    return head + body


def theme_files(schemes: dict[str, dict[str, str]], font: str | None, package: str) -> Files:
    note = README.format(pkg=package, pkgpath=package.replace(".", "/"))
    if font:
        note += f"Font: đặt file font vào res/font/{font_res(font)}.ttf (R.font.{font_res(font)}).\n"
    return [(FILE, theme_kt(schemes, font, package).encode()), ("README_THEME.md", note.encode())]
