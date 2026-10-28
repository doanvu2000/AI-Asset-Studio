"""Spine 4.x / libgdx texture atlas reader (text only; no image deps so the MCP process can use it)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AtlasRegion:
    name: str
    page: int
    x: int
    y: int
    width: int
    height: int
    degrees: int = 0
    offset_x: int = 0
    offset_y: int = 0
    orig_w: int = 0
    orig_h: int = 0
    index: int = -1


@dataclass
class AtlasPage:
    name: str
    props: dict[str, str] = field(default_factory=dict)

    @property
    def pma(self) -> bool:
        return self.props.get("pma", "").lower() == "true"


@dataclass
class Atlas:
    pages: list[AtlasPage]
    regions: dict[str, AtlasRegion]


def _ints(v: str) -> list[int]:
    return [int(float(x)) for x in v.split(",")]


def parse_atlas(text: str) -> Atlas:
    pages: list[AtlasPage] = []
    regions: dict[str, AtlasRegion] = {}
    region: AtlasRegion | None = None
    fresh = True
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            fresh, region = True, None
            continue
        key, sep, val = line.partition(":")
        if fresh:
            pages.append(AtlasPage(line))
            fresh = False
            continue
        if not sep:
            region = AtlasRegion(line, len(pages) - 1, 0, 0, 0, 0)
            regions.setdefault(line, region)
            continue
        key, val = key.strip(), val.strip()
        if region is None:
            pages[-1].props[key] = val
            continue
        match key:
            case "bounds":
                region.x, region.y, region.width, region.height = _ints(val)
            case "xy":
                region.x, region.y = _ints(val)
            case "size":
                region.width, region.height = _ints(val)
            case "offsets":
                region.offset_x, region.offset_y, region.orig_w, region.orig_h = _ints(val)
            case "offset":
                region.offset_x, region.offset_y = _ints(val)
            case "orig":
                region.orig_w, region.orig_h = _ints(val)
            case "rotate":
                region.degrees = 90 if val == "true" else (0 if val == "false" else int(val))
            case "index":
                region.index = int(val)
    for r in regions.values():
        r.orig_w = r.orig_w or r.width
        r.orig_h = r.orig_h or r.height
    return Atlas(pages, regions)


def atlas_page_files(atlas_path: Path) -> list[Path]:
    """Page images next to the atlas, in page order. Raises FileNotFoundError naming the first missing page."""
    atlas = parse_atlas(atlas_path.read_text(encoding="utf-8-sig", errors="replace"))
    out = []
    for p in atlas.pages:
        f = atlas_path.parent / p.name
        if not f.is_file():
            raise FileNotFoundError(f"atlas page not found next to {atlas_path.name}: {p.name}")
        out.append(f)
    return out
