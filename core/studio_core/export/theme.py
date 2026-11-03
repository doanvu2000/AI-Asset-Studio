"""UI theme from a project's style-bible palette: Material-style light/dark color roles whose text/background pairs
all clear WCAG AA, rendered as Flutter ThemeData, Compose MaterialTheme or SwiftUI Color/Font tokens."""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from studio_core.export.source import Files
from studio_core.ops.pixel import from_oklab, parse_hex, to_oklab

MIN_CONTRAST = 4.5
TARGETS = ("flutter", "android", "ios")
NEUTRAL_PALETTE = ("#4B6584", "#7A6F64", "#5F7F73")
FONT_EXT = (".ttf", ".otf")
FONT_DIRS = {"flutter": "assets/fonts", "android": "res/font", "ios": "Fonts"}
MAX_CHROMA = 0.16
MIN_HUE_GAP = 30.0
ERROR_HUE = 29.0
GAMUT_TOL = 0.004
MIN_ERROR_DISTANCE = 0.08
ERROR_VARIANTS = ((0.0, 0.0), (0.0, 0.1), (0.0, -0.1), (-14.0, 0.1), (-14.0, -0.1), (-29.0, 0.1), (-29.0, -0.1))
ERROR_DARK_TONE_SIGN = -1.0

ACCENTS = ("primary", "secondary", "tertiary", "error")
ROLES = ("primary", "onPrimary", "primaryContainer", "onPrimaryContainer",
         "secondary", "onSecondary", "secondaryContainer", "onSecondaryContainer",
         "tertiary", "onTertiary", "tertiaryContainer", "onTertiaryContainer",
         "error", "onError", "errorContainer", "onErrorContainer",
         "surface", "onSurface", "surfaceVariant", "onSurfaceVariant", "outline", "background", "onBackground")
PAIRS = tuple((f"on{a[0].upper()}{a[1:]}{s}", f"{a}{s}") for a in ACCENTS for s in ("", "Container")) + (
    ("onSurface", "surface"), ("onSurfaceVariant", "surfaceVariant"), ("onBackground", "background"))

LCH = tuple[float, float, float]


@dataclass(frozen=True)
class ThemeSpec:
    name: str = ""
    palette: tuple[str, ...] = ()
    font: str | None = None


def _hex(rgb: np.ndarray | tuple[int, ...]) -> str:
    r, g, b = (int(v) for v in rgb)
    return f"#{r:02X}{g:02X}{b:02X}"


def luminance(color: str) -> float:
    def lin(v: int) -> float:
        c = v / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(v) for v in parse_hex(color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _lch(color: str) -> LCH:
    lab = to_oklab(np.array([parse_hex(color)]))[0]
    return float(lab[0]), float(math.hypot(lab[1], lab[2])), math.degrees(math.atan2(lab[2], lab[1])) % 360


def _render(lch: LCH) -> str:
    """Hex of an OKLCH color; chroma is lowered until the color is inside sRGB so lightness stays what was asked."""
    lightness, chroma, hue = lch
    lightness = min(max(lightness, 0.0), 1.0)
    rad = math.radians(hue)
    for _ in range(40):
        lab = np.array([[lightness, chroma * math.cos(rad), chroma * math.sin(rad)]])
        rgb = from_oklab(lab)
        if chroma <= 0 or float(np.abs(to_oklab(rgb) - lab).max()) <= GAMUT_TOL:
            return _hex(rgb[0])
        chroma = max(chroma - 0.01, 0.0)
    return _hex(from_oklab(np.array([[lightness, 0.0, 0.0]]))[0])


def _fit(base: LCH, on: LCH, move_base: bool) -> tuple[str, str]:
    """Pair that reaches MIN_CONTRAST; the moving side is pushed away from the other one in lightness."""
    sign = 1.0 if base[0] >= on[0] else -1.0
    if not move_base:
        sign = -sign
    moving = base if move_base else on
    for step in range(101):
        shifted = (moving[0] + sign * 0.01 * step, moving[1], moving[2])
        b, o = (_render(shifted), _render(on)) if move_base else (_render(base), _render(shifted))
        if contrast(b, o) >= MIN_CONTRAST:
            return b, o
    return _render((1.0, 0.0, 0.0)), _render((0.0, 0.0, 0.0))


def _seeds(palette: tuple[str, ...]) -> list[LCH]:
    seeds: dict[str, LCH] = {}
    for raw in palette:
        try:
            seeds[_hex(parse_hex(raw))] = _lch(raw)
        except (ValueError, TypeError):
            continue
    return [seeds[h] for h in sorted(seeds, key=lambda h: (-seeds[h][1], h))]


def _hue_gap(a: float, b: float) -> float:
    d = abs(a - b) % 360
    return min(d, 360 - d)


def accent_seeds(palette: tuple[str, ...]) -> tuple[LCH, LCH, LCH]:
    """primary = most colorful palette entry, secondary/tertiary = next entries with a clearly different hue, or the
    primary hue rotated when the palette has no such entry."""
    seeds = _seeds(palette) or _seeds(NEUTRAL_PALETTE)
    primary = seeds[0]
    picked = [primary]
    for rotate in (40.0, 160.0):
        near = next((s for s in seeds if all(_hue_gap(s[2], p[2]) >= MIN_HUE_GAP for p in picked)), None)
        picked.append(near if near and near[1] >= 0.03 else (primary[0], primary[1] * 0.6, (primary[2] + rotate) % 360))
    return picked[0], picked[1], picked[2]


def _tone(seed: LCH, lightness: float, chroma_scale: float = 1.0, cap: float = MAX_CHROMA) -> LCH:
    return lightness, min(seed[1] * chroma_scale, cap), seed[2]


def _distance(a: str, b: str) -> float:
    return float(np.linalg.norm(to_oklab(np.array([parse_hex(a)]))[0] - to_oklab(np.array([parse_hex(b)]))[0]))


def _error_seed(primary: LCH, dark: bool) -> tuple[LCH, float]:
    """Error red and its tone offset: shifted in lightness (then slightly toward pink) when a red brand would make
    errors look like the main button; the hue stays red so it still reads as an error."""
    tone = 0.80 if dark else 0.48
    on: LCH = (0.2 if dark else 1.0, 0.01, primary[2])
    base = _fit(_tone(primary, tone), on, True)[0]
    best: tuple[float, LCH, float] = (-1.0, (0.5, 0.19, ERROR_HUE), 0.0)
    for shift, step in ERROR_VARIANTS:
        dt = step * (ERROR_DARK_TONE_SIGN if dark else 1.0)
        seed: LCH = (0.5, 0.19, (ERROR_HUE + shift) % 360)
        gap = _distance(base, _fit((tone + dt, seed[1], seed[2]), (on[0], on[1], seed[2]), True)[0])
        if gap >= MIN_ERROR_DISTANCE:
            return seed, dt
        best = max(best, (gap, seed, dt), key=lambda t: t[0])
    return best[1], best[2]


def build_scheme(palette: tuple[str, ...], dark: bool) -> dict[str, str]:
    prim, sec, ter = accent_seeds(palette)
    ink = 0.92 if dark else 0.22
    out: dict[str, str] = {}
    err, err_shift = _error_seed(prim, dark)
    for name, seed in (("primary", prim), ("secondary", sec), ("tertiary", ter), ("error", err)):
        tone = (0.80 if dark else 0.48) + (err_shift if name == "error" else 0.0)
        out[name], out[f"on{name.capitalize()}"] = _fit(_tone(seed, tone), (0.2 if dark else 1.0, 0.01, seed[2]), True)
        cont = _tone(seed, 0.33 if dark else 0.91, 0.6, 0.08)
        out[f"{name}Container"], out[f"on{name.capitalize()}Container"] = _fit(
            cont, (0.93 if dark else 0.25, min(seed[1], 0.04), seed[2]), False)
    hue = prim[2]
    out["surface"], out["onSurface"] = _fit((0.17 if dark else 0.985, 0.008, hue), (ink, 0.012, hue), False)
    out["surfaceVariant"], out["onSurfaceVariant"] = _fit((0.27 if dark else 0.93, 0.02, hue),
                                                          (0.84 if dark else 0.36, 0.02, hue), False)
    out["outline"] = _render((0.62 if dark else 0.55, 0.02, hue))
    out["background"], out["onBackground"] = out["surface"], out["onSurface"]
    return {r: out[r] for r in ROLES}


def build_schemes(spec: ThemeSpec) -> dict[str, dict[str, str]]:
    return {"light": build_scheme(spec.palette, False), "dark": build_scheme(spec.palette, True)}


def contrast_report(schemes: dict[str, dict[str, str]]) -> dict[str, float]:
    return {f"{mode}.{on}/{base}": round(contrast(s[on], s[base]), 2)
            for mode, s in schemes.items() for on, base in PAIRS}


def has_palette(spec: ThemeSpec) -> bool:
    return bool(_seeds(spec.palette))


def detect_font(root: Path, target: str) -> str | None:
    d = root / FONT_DIRS[target]
    fonts = sorted(p for p in d.glob("*") if p.suffix.lower() in FONT_EXT) if d.is_dir() else []
    return fonts[0].stem if fonts else None


def theme_files(spec: ThemeSpec, target: str, package: str = "com.example.app") -> Files:
    if target not in TARGETS:
        raise ValueError(f"target must be one of {', '.join(TARGETS)}")
    schemes = build_schemes(spec)
    if target == "flutter":
        from studio_core.export.theme_dart import theme_files as build
        return build(schemes, spec.font)
    if target == "android":
        from studio_core.export.theme_kotlin import theme_files as build
        return build(schemes, spec.font, package)
    from studio_core.export.theme_swift import theme_files as build
    return build(schemes, spec.font)
