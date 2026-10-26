from __future__ import annotations

from typing import Any

from ..contracts import ProjectStyle
from .recipe import Recipe, RecipeError, coerce_value

PROMPT_INPUTS = ("prompt", "idea")


def _empty(v: Any) -> bool:
    return v is None or v == "" or v == []


def _join(*parts: str) -> str:
    return ", ".join(p.strip().strip(",").strip() for p in parts if p and p.strip().strip(","))


def _wrap(text: str, prefix: str, suffix: str) -> str:
    low = text.lower()
    head = prefix if prefix.strip() and prefix.strip().lower() not in low else ""
    tail = suffix if suffix.strip() and suffix.strip().lower() not in low else ""
    return _join(head, text, tail)


def apply_style(recipe: Recipe, inputs: dict[str, Any], style: ProjectStyle) -> tuple[dict[str, Any], list[str]]:
    """Fills a project's style into the recipe inputs the user left empty; returns (inputs, changed keys).

    Only inputs the recipe declares are touched, and a style value the recipe would reject (wrong enum option,
    out of range) is skipped so one project style can serve recipes with different input ranges."""
    out = dict(inputs or {})
    applied: list[str] = []
    specs = recipe.inputs

    def fill(name: str, value: Any) -> None:
        if name not in specs or _empty(value) or not _empty(out.get(name)) or name in applied:
            return
        try:
            out[name] = coerce_value(name, specs[name], value)
        except RecipeError:
            return
        applied.append(name)

    look = _join(style.prompt_prefix, style.prompt_suffix)
    # Brief recipes keep subject and look apart: the look goes to `style`, the idea stays untouched.
    if "style" in specs and specs["style"].get("type", "text") == "text":
        fill("style", look)
    elif look:
        for name in PROMPT_INPUTS:
            text = out.get(name)
            if name in specs and isinstance(text, str) and text.strip():
                wrapped = _wrap(text, style.prompt_prefix, style.prompt_suffix)
                if wrapped != text:
                    out[name] = wrapped
                    applied.append(name)
    fill("negative", style.negative)
    fill("preset", style.preset)
    if style.pixel is not None:
        fill("pixel", style.pixel.size)
        fill("palette", style.pixel.palette)
        fill("colors", style.pixel.colors)
        fill("outline", style.pixel.outline)
        fill("dither", style.pixel.dither)
    fill("palette_image", style.palette_image)
    if "image" in specs and specs["image"].get("type") == "image":
        fill("image", style.reference_image)
    for name, value in style.inputs.items():
        fill(name, value)
    return out, applied


def style_blobs(style: ProjectStyle) -> list[str]:
    return [h for h in (style.palette_image, style.reference_image) if h]
