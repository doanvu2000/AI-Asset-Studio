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
    # Enemy variants (op.enemy_variants): (variant slug, sheet path), all cut on the same cells as `enemy_meta`.
    enemy_variants: list[tuple[str, Path]] = field(default_factory=list)
    enemy_meta: dict[str, Any] | None = None
    # Skybox (op.pano_cubemap): its skybox.json doc and paths by role (pano, irradiance, each cube face name).
    skybox: dict[str, Any] | None = None
    skybox_files: dict[str, Path] = field(default_factory=dict)
    # Destructible prop (blender.fracture): its destructible.json doc; the models themselves are in `glb` / `fbx`.
    destructible: dict[str, Any] | None = None
    # Retextured mesh (op.project_bake): its retexture.json report; GLBs are in `glb`, maps in `pbr`.
    retexture: dict[str, Any] | None = None
    # 3D terrain (op.terrain_pack): its terrain.json doc and paths by key (raw, heightmap, splat, <role>_<map>).
    terrain: dict[str, Any] | None = None
    terrain_files: dict[str, Path] = field(default_factory=dict)
    # UI screen (op.ui_screen): its ui_screen.json (normalized layout + sprite names); the sprites are in `ui`.
    ui_screen: dict[str, Any] | None = None
    # Particle VFX pack (op.vfx_particles): its vfx_particles.json doc and texture paths by key (soft_dot, smoke...).
    vfx: dict[str, Any] | None = None
    vfx_files: dict[str, Path] = field(default_factory=dict)
    # Minimap (op.minimap_finish): its minimap.json doc (POIs) and paths by key (map, mask, framed).
    minimap: dict[str, Any] | None = None
    minimap_files: dict[str, Path] = field(default_factory=dict)
    # Trim sheet (op.trim_sheet): its trim.json; the atlas and its maps are in `pbr`.
    trim: dict[str, Any] | None = None
    # Decal pack (op.decal_pack): its decal.json and (name, albedo RGBA, normal) per decal.
    decals_doc: dict[str, Any] | None = None
    decals: list[tuple[str, Path, Path]] = field(default_factory=list)
    # Modular kit (blender.modular_kit): its modular.json and the per-piece OBJ meshes (name, path); FBX/GLB are in
    # `fbx` / `glb`, the trim material in `pbr`.
    modular: dict[str, Any] | None = None
    modular_obj: list[tuple[str, Path]] = field(default_factory=list)
    # Animated tile (op.tile_animate): its tile_anim.json (frame layout, autotile table) and the frame sheet.
    tile_anim: dict[str, Any] | None = None
    tile_anim_sheet: Path | None = None
    # Card set (op.card_set): its card_set.json and image paths by key (front:<id>, art:<id>, frame:<key>, back).
    cards: dict[str, Any] | None = None
    cards_files: dict[str, Path] = field(default_factory=dict)
    # Isometric 2:1 tiles / prop (op.iso_tiles, op.iso_prop): its iso_tiles.json or iso_prop.json and image paths by
    # sprite name plus "atlas" (the strip every engine slices).
    iso: dict[str, Any] | None = None
    iso_files: dict[str, Path] = field(default_factory=dict)
    # Playable scene (op.playable_scene): its playable_scene.json and paths by key (atlas, sheet, prop:<n>, bg:<n>).
    playable: dict[str, Any] | None = None
    playable_files: dict[str, Path] = field(default_factory=dict)
    # Set when the export asked for traced 2D colliders + foot pivot on the sprite sheet (export/collider.py).
    collider: Any = None
    # Combat frame data (op.frame_data): its frame_data.json doc (sheet meta inlined as `sheet_meta`); the sheet is `sheet`.
    frame_data: dict[str, Any] | None = None

    def is_pixel(self) -> bool:
        if self.pixel is not None:
            return self.pixel
        m = self.meta or {}
        if m.get("filter") in ("point", "bilinear"):
            return m["filter"] == "point"
        if m.get("pixel") or m.get("pixel_art") or m.get("palette"):
            return True
        return max(int(m.get("frame_w") or 999), int(m.get("frame_h") or 999)) <= 64
