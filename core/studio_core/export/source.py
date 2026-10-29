from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

Files = list[tuple[str, bytes | Path]]


@dataclass
class Source:
    """What one step hands an engine exporter; every field is optional except the asset name."""
    asset: str
    uid: str = ""
    sheet: Path | None = None
    meta: dict[str, Any] | None = None
    normal: Path | bytes | None = None
    clip_hint: str = "anim"
    directional_rows: bool = False
    glb: list[tuple[str, Path]] = field(default_factory=list)
    spine_json: tuple[str, Path] | None = None
    spine_atlas: tuple[str, Path] | None = None
    spine_pages: list[tuple[str, Path]] = field(default_factory=list)
    ppu: float = 100.0
    pixel: bool | None = None
    # Tileable textures (texture-seamless): (file name, path, role) with role color | normal | pixel | atlas; they import
    # with wrap = Repeat instead of as sprites.
    textures: list[tuple[str, Path, str]] = field(default_factory=list)
    # Sound clips (audio-sfx / audio-music): (file name, path); a "_loop" stem marks a seamless loop.
    audio: list[tuple[str, Path]] = field(default_factory=list)
    # UI kit / icon pack (op.nine_slice): (element name, path, (left, top, right, bottom) border, state or "").
    ui: list[tuple[str, Path, tuple[int, int, int, int], str]] = field(default_factory=list)
    # Rigged character (blender.rig_export): FBX files plus its rig3d.json report (humanoid flag, clip frame ranges).
    fbx: list[tuple[str, Path]] = field(default_factory=list)
    rig: dict[str, Any] | None = None
    # PBR materials (op.pbr): (material name, {role: path}) with roles albedo, normal, height, roughness, metallic, ao.
    pbr: list[tuple[str, dict[str, Path]]] = field(default_factory=list)
    # Item rarity set (op.rarity_set): (tier, path) low to high, tier colours, legendary shimmer frames.
    rarity: list[tuple[str, Path]] = field(default_factory=list)
    rarity_colors: dict[str, str] = field(default_factory=dict)
    rarity_shimmer: list[Path] = field(default_factory=list)
    # Damage states (op.damage_debris): (state, path, hit-flash path) intact → wrecked, debris pieces (name, path,
    # damage.json piece entry: rect/offset/pivot/dir in source-canvas px), the canvas size.
    damage_states: list[tuple[str, Path, Path | None]] = field(default_factory=list)
    damage_pieces: list[tuple[str, Path, dict[str, Any]]] = field(default_factory=list)
    damage_canvas: tuple[int, int] = (0, 0)
    # Scene soundscape (op.soundscape): its soundscape.json doc; the clips themselves are in `audio`.
    soundscape: dict[str, Any] | None = None
    # Time/season pack (op.fit_lut): (variant, image, LUT strip 1024x32 UE/Godot orientation, .cube); tileable flag.
    luts: list[tuple[str, Path, Path, Path]] = field(default_factory=list)
    lut_seamless: bool = False
    # Parallax background (op.parallax_layers): (layer, path, scroll factor) sky first → foreground, layer px size.
    parallax: list[tuple[str, Path, float]] = field(default_factory=list)
    parallax_size: tuple[int, int] = (0, 0)
    # Image layers (op.layers_export): (name, full-canvas RGBA path) back → front, canvas size.
    image_layers: list[tuple[str, Path]] = field(default_factory=list)
    image_layers_size: tuple[int, int] = (0, 0)
    # Localized image text (op.localize_render): (language code or "notext", path), source language.
    localized: list[tuple[str, Path]] = field(default_factory=list)
    localized_source_lang: str = ""
    # NPC pack (op.npc_pack): its npc.json doc and (expression, portrait path); voiced clips are in `audio`.
    npc: dict[str, Any] | None = None
    npc_portraits: list[tuple[str, Path]] = field(default_factory=list)
    # Blob-47 autotile terrain (op.autotile): its autotile.json doc and atlas.
    autotile: dict[str, Any] | None = None
    autotile_atlas: Path | None = None
    # Bitmap font (op.bitmap_font): its font.json doc (BMFont metrics) and atlas.
    font: dict[str, Any] | None = None
    font_atlas: Path | None = None
    # Paper-doll equipment (op.paperdoll): its paperdoll.json doc (sockets doc inlined as `sockets_doc`) and the layer
    # sheets (name, path), cut on the same cells as `sheet`.
    paperdoll: dict[str, Any] | None = None
    paperdoll_layers: list[tuple[str, Path]] = field(default_factory=list)
    # Set when the export asked for traced 2D colliders + foot pivot on the sprite sheet (export/collider.py).
    collider: Any = None

    def is_pixel(self) -> bool:
        if self.pixel is not None:
            return self.pixel
        m = self.meta or {}
        if m.get("filter") in ("point", "bilinear"):
            return m["filter"] == "point"
        if m.get("pixel") or m.get("pixel_art") or m.get("palette"):
            return True
        return max(int(m.get("frame_w") or 999), int(m.get("frame_h") or 999)) <= 64
