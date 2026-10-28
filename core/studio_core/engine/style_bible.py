"""Project style bible: the look measured from a project's approved assets (palette, outline, light, pixel vs
painterly, 1-3 reference images) plus a prompt suffix/negative, injected into new runs of that project."""
from __future__ import annotations

import math
import time
from collections.abc import Sequence
from typing import Any

import numpy as np
from PIL import Image
from pydantic import BaseModel, Field

from .project_qa import Measure, _dir, _hex, _nearest_dist, _rgb, _stride, measure
from ..ops.pixel import from_oklab, kmeans_palette, to_oklab
from .recipe import Recipe, RecipeError, coerce_value

PALETTE_K = 8
MAX_REFS = 3
MAX_SOURCES = 60
PIXEL_SIDE = 768
PIXEL_MAX_COLORS = 32
OPT_OUT_KEY = "use_style_bible"
RECORD_KEY = "style_bible"
PROMPT_INPUTS = ("prompt", "idea")
STYLE_REF_NAMES = ("style_ref", "style_image", "style_reference")


class BibleRef(BaseModel):
    hash: str
    version_id: str = ""
    asset_name: str = ""


class StyleBible(BaseModel):
    version: int = 0
    active: bool = True
    palette: list[str] = []
    outlined: bool | None = None
    outline_weight: float | None = None
    light_angle: float | None = None
    light_label: str | None = None
    pixel_art: bool = False
    refs: list[BibleRef] = []
    suffix: str = Field("", max_length=2000)
    negative: str = Field("", max_length=2000)
    summary: str = Field("", max_length=4000)
    source_count: int = 0
    built_at: float | None = None
    updated_at: float | None = None
    edited: bool = False
    cost_usd: float | None = None
    model: str | None = None
    vision_error: str | None = None


def pixel_score(img: Image.Image) -> float:
    """~1 for pixel art, low for painted art. Upscaled pixel art repeats each color in both directions (painted
    shading breaks exact equality along at least one axis); native-size pixel art is caught by its tiny color count."""
    im = img.convert("RGBA")
    if max(im.size) > PIXEL_SIDE:
        f = math.ceil(max(im.size) / PIXEL_SIDE)
        im = im.resize((im.width // f, im.height // f), Image.Resampling.NEAREST)
    a = np.asarray(im).astype(np.int32)
    opaque = a[..., 3] >= 128
    rgb = (a[..., 0] << 16) | (a[..., 1] << 8) | a[..., 2]
    h = (rgb[:, 1:] == rgb[:, :-1])[opaque[:, 1:] & opaque[:, :-1]]
    v = (rgb[1:] == rgb[:-1])[opaque[1:] & opaque[:-1]]
    if min(h.size, v.size) < 32:
        return 0.0
    if len(np.unique(rgb[opaque])) <= PIXEL_MAX_COLORS:
        return 1.0
    return float(min(h.mean(), v.mean()))


def aggregate(measures: Sequence[Measure], pixel_scores: Sequence[float]) -> dict[str, Any]:
    ok = [m for m in measures if m.ok]
    out: dict[str, Any] = {"palette": [], "outlined": None, "outline_weight": None, "light_angle": None,
                           "light_label": None, "pixel_art": False}
    pooled = [m.samples for m in ok if m.samples is not None and len(m.samples)]
    if pooled:
        lab = np.concatenate([p[_stride(len(p), 2000)] for p in pooled])
        out["palette"] = [_hex(c) for c in kmeans_palette(from_oklab(lab), PALETTE_K)]
    framed = [m for m in ok if m.outlined is not None]
    if framed:
        out["outlined"] = sum(bool(m.outlined) for m in framed) * 2 >= len(framed)
        thick = [m.outline for m in framed if m.outlined and m.outline]
        if out["outlined"] and thick:
            out["outline_weight"] = round(float(np.median(thick)), 2)
    lit = [m.light for m in ok if m.light is not None]
    if lit:
        sx = sum(s * math.cos(math.radians(a)) for a, s in lit)
        sy = sum(s * math.sin(math.radians(a)) for a, s in lit)
        if math.hypot(sx, sy) > 0.25 * sum(s for _, s in lit):
            ang = math.degrees(math.atan2(sy, sx)) % 360
            out["light_angle"], out["light_label"] = round(ang), _dir(ang, en=True)
    if pixel_scores:
        out["pixel_art"] = float(np.median(pixel_scores)) >= 0.55
    return out


def pick_refs(measures: Sequence[Measure], palette: Sequence[str], run_of: dict[str, str],
              k: int = MAX_REFS) -> list[str]:
    """Version ids closest to the set's palette, at most one per run so the refs show different assets."""
    ok = [m for m in measures if m.ok and m.samples is not None and len(m.samples)]
    if not ok:
        return []
    if palette:
        ref = to_oklab(np.array([_rgb(h) for h in palette]))
        ranked = sorted(ok, key=lambda m: float(_nearest_dist(m.samples[_stride(len(m.samples), 800)], ref).mean()))
    else:
        ranked = list(ok)
    out: list[str] = []
    runs: set[str] = set()
    for m in ranked:
        run = run_of.get(m.version_id, m.version_id)
        if run not in runs:
            runs.add(run)
            out.append(m.version_id)
        if len(out) >= k:
            break
    return out


def fallback_text(stats: dict[str, Any]) -> tuple[str, str]:
    bits: list[str] = []
    neg: list[str] = []
    if stats.get("pixel_art"):
        bits.append("pixel art, crisp hard pixel edges")
        neg += ["blurry", "smooth gradients", "anti-aliasing"]
    else:
        bits.append("painterly game art")
        neg.append("pixel art")
    if stats.get("outlined") is True:
        bits.append("clean dark outline" + (", thick line weight" if (stats.get("outline_weight") or 0) > 6 else ""))
    elif stats.get("outlined") is False:
        bits.append("no outlines")
        neg.append("black outline")
    if stats.get("light_label"):
        bits.append(f"lit from the {stats['light_label']}")
    if stats.get("palette"):
        bits.append("limited palette of " + " ".join(stats["palette"][:6]))
    neg += ["photorealistic", "3d render", "text", "watermark"]
    return ", ".join(bits), ", ".join(dict.fromkeys(neg))


def measure_all(images: Sequence[tuple[str, Image.Image | None]]) -> tuple[list[Measure], list[float]]:
    measures: list[Measure] = []
    pix: list[float] = []
    for vid, img in images:
        if img is None:
            measures.append(Measure(vid, ok=False, note="unreadable"))
            continue
        try:
            measures.append(measure(img, vid))
            pix.append(pixel_score(img))
        except Exception:
            measures.append(Measure(vid, ok=False, note="measure failed"))
    return measures, pix


def new_bible(prev: StyleBible | None, stats: dict[str, Any], refs: list[BibleRef], suffix: str, negative: str,
              summary: str, sources: int, **extra: Any) -> StyleBible:
    now = time.time()
    return StyleBible(**stats, refs=refs, suffix=suffix.strip()[:2000], negative=negative.strip()[:2000],
                      summary=summary.strip()[:4000], source_count=sources, built_at=now, updated_at=now,
                      version=(prev.version if prev else 0) + 1, active=prev.active if prev else True, **extra)


def _empty(v: Any) -> bool:
    return v is None or v == "" or v == []


def _append(text: str, add: str) -> str:
    add = add.strip().strip(",").strip()
    text = text.strip()
    if not add or add.lower() in text.lower():
        return text
    return f"{text.rstrip(',').rstrip()}, {add}" if text else add


def style_ref_input(recipe: Recipe) -> str | None:
    """The image input a recipe uses as a style sample: named so, or an `image` input labelled as a style ref."""
    for name, spec in recipe.inputs.items():
        if spec.get("type") != "image":
            continue
        if name in STYLE_REF_NAMES or "style" in str(spec.get("label") or "").lower():
            return name
    return None


def apply_bible(recipe: Recipe, inputs: dict[str, Any], user_inputs: dict[str, Any],
                bible: StyleBible) -> tuple[dict[str, Any], list[str]]:
    """Adds the bible to inputs that already carry the project style; `user_inputs` are what the user typed, so a
    style/negative/style-ref the user set is left alone. Returns (inputs, changed keys)."""
    out = dict(inputs)
    applied: list[str] = []
    specs = recipe.inputs
    user = user_inputs or {}

    def put(name: str, value: Any) -> None:
        try:
            out[name] = coerce_value(name, specs[name], value)
        except RecipeError:
            return
        applied.append(name)

    if bible.suffix.strip():
        if "style" in specs and specs["style"].get("type", "text") == "text":
            if _empty(user.get("style")):
                new = _append(str(out.get("style") or ""), bible.suffix)
                if new != (out.get("style") or ""):
                    put("style", new)
        else:
            for name in PROMPT_INPUTS:
                text = out.get(name)
                if name not in specs or not isinstance(text, str) or not text.strip():
                    continue
                # One image per line in multiline prompts: each line needs the suffix, not only the last.
                if specs[name].get("multiline"):
                    new = "\n".join(_append(ln, bible.suffix) if ln.strip() else ln for ln in text.split("\n"))
                else:
                    new = _append(text, bible.suffix)
                if new != text:
                    put(name, new)
    if bible.negative.strip() and "negative" in specs and _empty(user.get("negative")):
        new = _append(str(out.get("negative") or ""), bible.negative)
        if new != (out.get("negative") or ""):
            put("negative", new)
    ref_input = style_ref_input(recipe)
    if ref_input and bible.refs and _empty(out.get(ref_input)):
        put(ref_input, bible.refs[0].hash)
    return out, applied
