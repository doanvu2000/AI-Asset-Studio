"""Toon band brightness shared by the Blender icon render (icon3d_bpy, runs inside Blender) and the engine toon material
(export/toon3d). No bpy import: both sides load it."""

TOON_SHADOW = 0.5


def band_levels(bands: int) -> list[float]:
    return [TOON_SHADOW + (1 - TOON_SHADOW) * i / max(1, bands - 1) for i in range(bands)]
