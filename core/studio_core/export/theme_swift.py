"""SwiftUI AppTheme.swift (Color/Font tokens) + one .colorset per role with light and dark values."""
from __future__ import annotations

import json

from studio_core.export.ios import CATALOG, INFO
from studio_core.export.source import Files

FILE = "Sources/AppTheme.swift"
PREFIX = "Theme"
README = """AppTheme.swift + AssetStudio.xcassets/Theme*.colorset: màu sáng/tối lấy từ palette style bible; mọi cặp chữ/nền
đạt WCAG AA (>= 4.5). Dùng: Text("x").foregroundStyle(AppTheme.Colors.onPrimary).background(AppTheme.Colors.primary).
"""
TEXT_STYLES = (("largeTitle", 34), ("title", 28), ("headline", 17), ("body", 17), ("callout", 16), ("caption", 12))


def _components(h: str) -> dict[str, str]:
    h = h.lstrip("#")
    return {"alpha": "1.000", "red": f"0x{h[0:2]}", "green": f"0x{h[2:4]}", "blue": f"0x{h[4:6]}"}


def _entry(h: str, appearance: str | None) -> dict:
    e: dict = {"color": {"color-space": "srgb", "components": _components(h.upper())}, "idiom": "universal"}
    if appearance:
        e["appearances"] = [{"appearance": "luminosity", "value": appearance}]
    return e


def role_name(role: str) -> str:
    return f"{PREFIX}{role[0].upper()}{role[1:]}"


def colorset_files(schemes: dict[str, dict[str, str]]) -> Files:
    files: Files = []
    for role, light in schemes["light"].items():
        doc = {"colors": [_entry(light, None), _entry(schemes["dark"][role], "dark")], "info": INFO}
        files.append((f"{CATALOG}/{role_name(role)}.colorset/Contents.json",
                      (json.dumps(doc, indent=2) + "\n").encode()))
    return files


def app_theme_swift(roles: list[str], font: str | None) -> str:
    colors = "\n".join(f'        static let {r} = Color("{role_name(r)}")' for r in roles)
    if font:
        fonts = "\n".join(
            f'        static let {n} = Font.custom("{font}", size: {s}, relativeTo: .{n})' for n, s in TEXT_STYLES)
    else:
        fonts = "\n".join(f"        static let {n} = Font.{n}" for n, _ in TEXT_STYLES)
    return (f"import SwiftUI\n\nenum AppTheme {{\n    enum Colors {{\n{colors}\n    }}\n\n"
            f"    enum Fonts {{\n{fonts}\n    }}\n}}\n")


def theme_files(schemes: dict[str, dict[str, str]], font: str | None) -> Files:
    note = README + (f"Font '{font}': thêm file vào target, khai báo UIAppFonts (Info_plist_fonts.xml) và đổi tên "
                     "family sang PostScript name nếu khác tên file.\n" if font else "")
    return [(FILE, app_theme_swift(list(schemes["light"]), font).encode()),
            (f"{CATALOG}/Contents.json", (json.dumps({"info": INFO}, indent=2) + "\n").encode()),
            *colorset_files(schemes), ("README_THEME.md", note.encode())]
