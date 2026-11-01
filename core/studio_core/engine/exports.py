"""What a recipe can hand to a game engine, read off its step kinds (the export modules key on these steps' outputs)."""
from __future__ import annotations

from collections.abc import Iterable

_ENGINES = ("unity", "godot")

_SHEET = {"op.sheet", "op.sprite_fx", "op.item_spin", "op.spin_frames", "op.rarity_set", "op.enemy_variants",
          "op.frame_data", "op.paperdoll", "op.damage_states", "op.damage_debris", "op.icon_compose",
          "op.vfx_particles", "op.vfx_frames", "op.weather_overlay", "op.tile_animate", "op.aseprite_import"}
_TEXTURE = {"op.seamless", "op.pbr", "op.tile_normal", "op.tile_wang", "op.autotile", "op.terrain_pack",
            "op.decal_pack", "op.trim_sheet", "op.fit_lut", "op.lut_prep", "op.pano_cubemap", "op.parallax_layers",
            "op.image_layers", "op.layers_export", "op.minimap_finish", "op.iso_tiles", "op.iso_prop"}
_UI = {"op.nine_slice", "op.ui_screen", "op.card_set", "op.bitmap_font", "op.npc_pack", "op.store_kit",
       "op.localize_render", "op.playable_scene", "op.ui_split"}
_GLB = {"op.glb_validate", "blender.game_ready", "blender.lod", "blender.rig_export", "blender.modular_kit",
        "blender.retex_apply", "blender.fracture", "blender.icon_render", "comfy.img2mesh_trellis2_textured"}
_FBX = {"blender.rig_export", "blender.lod"}
_SPINE = {"op.spine_export"}
_AUDIO = {"op.audio_post", "op.soundscape"}

# Display order of the tags the app shows.
_ORDER = ("unity", "godot", "spine", "glb", "fbx", "audio")


def recipe_exports(kinds: Iterable[str]) -> list[str]:
    ks = set(kinds)
    tags: set[str] = set()
    if ks & (_SHEET | _TEXTURE | _UI | _GLB | _SPINE | _AUDIO):
        tags.update(_ENGINES)
    if ks & _SPINE:
        tags.add("spine")
    if ks & _GLB:
        tags.add("glb")
    if ks & _FBX:
        tags.add("fbx")
    if ks & _AUDIO:
        tags.add("audio")
    return [t for t in _ORDER if t in tags]
