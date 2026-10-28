from __future__ import annotations

from typing import Any

from ..contracts import ExecutorRegistry, Resource
from .audio import AudioPost
from .damage import DAMAGE_OPS
from .executors import ALL_OPS, Sheet
from .fx import FX_OPS
from .keyart import KEYART_OPS, KeyartVideo
from .pbr import PBR_OPS
from .pixel import PIXEL_OPS
from .presets import PresetStore
from .localize import LOCALIZE_OPS
from .lut import LUT_OPS
from .normal2d import NORMAL2D_OPS
from .npc import NPC_OPS
from .parallax import PARALLAX_OPS
from .rarity import RARITY_OPS
from .rig import RIG_OPS
from .soundscape import SOUNDSCAPE_OPS
from .spine_ref import SPINE_REF_OPS, VideoFrames
from .spine_variant import VARIANT_OPS
from .store_kit import STORE_OPS
from .tile import TILE_OPS
from .ui import UI_OPS
from .upscale import UPSCALE_OPS
from .vfx import VfxFrames


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
    for ka_cls in KEYART_OPS:
        registry.register(ka_cls())
    for dmg_cls in DAMAGE_OPS:
        registry.register(dmg_cls())
    for sc_cls in SOUNDSCAPE_OPS:
        registry.register(sc_cls())
    for npc_cls in NPC_OPS:
        registry.register(npc_cls())
    for loc_cls in LOCALIZE_OPS:
        registry.register(loc_cls())
    for tile_cls in (*TILE_OPS, *UI_OPS, *UPSCALE_OPS, *PBR_OPS, *RARITY_OPS, *LUT_OPS, *PARALLAX_OPS):
        registry.register(tile_cls())
    for store_cls in STORE_OPS:
        registry.register(store_cls())
    for n2d_cls in NORMAL2D_OPS:
        registry.register(n2d_cls())
    return []
