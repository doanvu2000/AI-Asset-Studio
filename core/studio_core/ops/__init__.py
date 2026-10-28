from __future__ import annotations

from typing import Any

from ..contracts import ExecutorRegistry, Resource
from .audio import AudioPost
from .executors import ALL_OPS, Sheet
from .fx import FX_OPS
from .pbr import PBR_OPS
from .pixel import PIXEL_OPS
from .presets import PresetStore
from .rarity import RARITY_OPS
from .rig import RIG_OPS
from .spine_ref import SPINE_REF_OPS, VideoFrames
from .spine_variant import VARIANT_OPS
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
    for tile_cls in (*TILE_OPS, *UI_OPS, *UPSCALE_OPS, *PBR_OPS, *RARITY_OPS):
        registry.register(tile_cls())
    return []
