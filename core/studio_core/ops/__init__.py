from __future__ import annotations

from typing import Any

from ..contracts import ExecutorRegistry, Resource
from .audio import AudioPost
from .animatic import Animatic
from .anim_video import ANIM_VIDEO_OPS
from .aseprite import ASEPRITE_OPS
from .autotile import AUTOTILE_OPS
from .bitmap_font import BITMAP_FONT_OPS
from .damage import DAMAGE_OPS
from .enemy_variants import ENEMY_OPS
from .engine_pack import ENGINE_PACK_OPS
from .executors import ALL_OPS, Sheet
from .fx import FX_OPS
from .frame_data import FRAME_DATA_OPS
from .image_layers import IMAGE_LAYERS_OPS
from .item_fx import ITEM_FX_OPS
from .keyart import KEYART_OPS, KeyartVideo
from .pbr import PBR_OPS
from .retexture import RETEX_OPS
from .pixel import PIXEL_OPS
from .pixel_cleanup import PIXEL_CLEANUP_OPS
from .shadow_outline import SHADOW_OUTLINE_OPS
from .anim_stabilize import ANIM_STABILIZE_OPS
from .presets import PresetStore
from .localize import LOCALIZE_OPS
from .lut import LUT_OPS
from .minimap import MINIMAP_OPS
from .normal2d import NORMAL2D_OPS
from .npc import NPC_OPS
from .pano import PANO_OPS
from .paperdoll import PAPERDOLL_OPS
from .parallax import PARALLAX_OPS
from .rarity import RARITY_OPS
from .restyle import register_restyle
from .rig import RIG_OPS
from .room_kit import ROOM_KIT_OPS
from .soundscape import SOUNDSCAPE_OPS
from .spine_convert import SPINE_CONVERT_OPS
from .spine_physics import SPINE_PHYSICS_OPS
from .spine_ref import SPINE_REF_OPS, VideoFrames
from .spine_variant import VARIANT_OPS
from .store_kit import STORE_OPS
from .tile import TILE_OPS
from .ui import UI_OPS
from .ui_screen import UI_SCREEN_OPS
from .trim import TRIM_OPS
from .decal import DECAL_OPS
from .terrain import TERRAIN_OPS
from .upscale import UpscalePost, UpscalePrep
from .vfx import VfxFrames
from .vfx_particles import VFX_PARTICLE_OPS


def register(registry: ExecutorRegistry, settings: Any = None, bus: Any = None, blobs: Any = None) -> list[Resource]:
    presets = PresetStore(getattr(settings, "presets_path", None))
    for cls in ALL_OPS:
        registry.register(Sheet(presets) if cls is Sheet else cls())
    for rig_cls in (*RIG_OPS, *PIXEL_OPS):
        registry.register(rig_cls())
    for fx_cls in FX_OPS:
        registry.register(fx_cls())
    for spine_cls in (*SPINE_REF_OPS, *VARIANT_OPS):
        registry.register(spine_cls())
    registry.register(VideoFrames(settings))
    registry.register(AudioPost(settings))
    registry.register(VfxFrames())
    registry.register(KeyartVideo(settings))
    registry.register(Animatic(settings))
    for ka_cls in KEYART_OPS:
        registry.register(ka_cls())
    for dmg_cls in DAMAGE_OPS:
        registry.register(dmg_cls())
    for sc_cls in SOUNDSCAPE_OPS:
        registry.register(sc_cls())
    for npc_cls in NPC_OPS:
        registry.register(npc_cls())
    for fd_cls in FRAME_DATA_OPS:
        registry.register(fd_cls())
    for rk_cls in ROOM_KIT_OPS:
        registry.register(rk_cls())
    for sc_cls in SPINE_CONVERT_OPS:
        registry.register(sc_cls())
    for loc_cls in LOCALIZE_OPS:
        registry.register(loc_cls())
    for tile_cls in (*TILE_OPS, *PIXEL_CLEANUP_OPS, *SHADOW_OUTLINE_OPS, *ANIM_STABILIZE_OPS, *UI_OPS, *PBR_OPS, *RARITY_OPS, *LUT_OPS, *PARALLAX_OPS):
        registry.register(tile_cls())
    registry.register(UpscalePrep())
    registry.register(UpscalePost(settings))
    for store_cls in STORE_OPS:
        registry.register(store_cls())
    for n2d_cls in NORMAL2D_OPS:
        registry.register(n2d_cls())
    for ase_cls in ASEPRITE_OPS:
        registry.register(ase_cls())
    for at_cls in AUTOTILE_OPS:
        registry.register(at_cls())
    for bf_cls in BITMAP_FONT_OPS:
        registry.register(bf_cls())
    for pd_cls in PAPERDOLL_OPS:
        registry.register(pd_cls())
    for il_cls in IMAGE_LAYERS_OPS:
        registry.register(il_cls())
    for ev_cls in ENEMY_OPS:
        registry.register(ev_cls())
    for ep_cls in ENGINE_PACK_OPS:
        registry.register(ep_cls())
    for pano_cls in PANO_OPS:
        registry.register(pano_cls())
    register_restyle(registry, settings)
    for av_cls in ANIM_VIDEO_OPS:
        registry.register(av_cls())
    for rt_cls in RETEX_OPS:
        registry.register(rt_cls())
    for trim_cls in (*TRIM_OPS, *DECAL_OPS):
        registry.register(trim_cls())
    for us_cls in UI_SCREEN_OPS:
        registry.register(us_cls())
    for tr_cls in TERRAIN_OPS:
        registry.register(tr_cls())
    for fx_cls in (*ITEM_FX_OPS, *SPINE_PHYSICS_OPS):
        registry.register(fx_cls())
    for vp_cls in VFX_PARTICLE_OPS:
        registry.register(vp_cls())
    from .weather import WEATHER_OPS

    for wx_cls in WEATHER_OPS:
        registry.register(wx_cls())
    for mm_cls in MINIMAP_OPS:
        registry.register(mm_cls())
    from .tile_anim import TILE_ANIM_OPS

    for ta_cls in TILE_ANIM_OPS:
        registry.register(ta_cls())
    from .icon3d import ICON3D_OPS
    from .iso import ISO_OPS

    for iso_cls in (*ICON3D_OPS, *ISO_OPS):
        registry.register(iso_cls())
    from .card_set import CARD_OPS

    for cs_cls in CARD_OPS:
        registry.register(cs_cls())
    from .spine_face import FACE_OPS

    for sf_cls in FACE_OPS:
        registry.register(sf_cls())
    from .playable_scene import PLAYABLE_OPS, SETTINGS_OPS

    for pl_cls in PLAYABLE_OPS:
        registry.register(pl_cls())
    for pl_cls in SETTINGS_OPS:
        registry.register(pl_cls(settings))
    from .style_audit import STYLE_AUDIT_OPS, STYLE_AUDIT_SETTINGS_OPS

    for sa_cls in STYLE_AUDIT_OPS:
        registry.register(sa_cls())
    for sa_cls in STYLE_AUDIT_SETTINGS_OPS:
        registry.register(sa_cls(settings))
    return []
