"""Flutter app_theme.dart: ColorScheme light/dark + ThemeData for MaterialApp(theme:, darkTheme:)."""
from __future__ import annotations

from studio_core.export.source import Files

FILE = "lib/gen/app_theme.dart"
README = """app_theme.dart: ThemeData + ColorScheme sáng/tối lấy từ palette style bible; mọi cặp chữ/nền đạt WCAG AA (>= 4.5).
Dùng: MaterialApp(theme: AppTheme.light, darkTheme: AppTheme.dark).
"""
README_FONT = """Font '{font}': khai báo trong pubspec.yaml rồi AppTheme tự dùng làm fontFamily:
  flutter:
    fonts:
      - family: {font}
        fonts:
          - asset: assets/fonts/{font}.ttf
"""
FLUTTER_ROLES = {"surfaceVariant": "surfaceContainerHighest"}
SKIP = {"background", "onBackground"}


def _color(h: str) -> str:
    return f"Color(0xFF{h.lstrip('#').upper()})"


def _scheme(name: str, mode: str, colors: dict[str, str]) -> str:
    rows = "\n".join(f"    {FLUTTER_ROLES.get(k, k)}: {_color(v)}," for k, v in colors.items() if k not in SKIP)
    return f"  static const ColorScheme {name} = ColorScheme(\n    brightness: Brightness.{mode},\n{rows}\n  );\n"


def app_theme_dart(schemes: dict[str, dict[str, str]], font: str | None) -> str:
    family = f"        fontFamily: '{font}',\n" if font else ""
    return (
        "import 'package:flutter/material.dart';\n\n"
        "class AppTheme {\n  const AppTheme._();\n\n"
        f"{_scheme('lightScheme', 'light', schemes['light'])}\n{_scheme('darkScheme', 'dark', schemes['dark'])}\n"
        "  static final ThemeData light = _build(lightScheme);\n"
        "  static final ThemeData dark = _build(darkScheme);\n\n"
        "  static ThemeData _build(ColorScheme scheme) => ThemeData(\n"
        "        useMaterial3: true,\n        colorScheme: scheme,\n"
        f"{family}        scaffoldBackgroundColor: scheme.surface,\n      );\n}}\n")


def theme_files(schemes: dict[str, dict[str, str]], font: str | None) -> Files:
    note = README + (README_FONT.format(font=font) if font else "")
    return [(FILE, app_theme_dart(schemes, font).encode()), ("README_THEME.md", note.encode())]
