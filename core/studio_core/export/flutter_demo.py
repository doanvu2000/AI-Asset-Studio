"""Flame demo project: turns a Flutter export / pack tree (assets/, shaders/) into a Flutter project that runs
with `flutter run`: 8-direction player (keyboard + virtual joystick), follow camera, the .tmx map (or a repeated tile /
grid), parallax, props with footprint hitboxes, optional normal-map light. The scene is described by assets/demo/demo.json,
so the Dart sources are the same for every export."""
from __future__ import annotations

import io
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath
from typing import Any

from PIL import Image

from studio_core.export import flutter_dart as fd
from studio_core.export import flutter_demo_dart as dart
from studio_core.export.flutter_shaders import SHADERS_DART
from studio_core.export.source import Files

DEMO_DIR = "demo"
DIRS = ("SE", "SW", "NE", "NW", "E", "W", "N", "S")
STATE_ALIASES = {"run": "walk", "walking": "walk", "stand": "idle", "slash": "attack", "strike": "attack",
                 "swing": "attack", "shoot": "attack", "punch": "attack", "kick": "attack", "cast": "attack"}
CLIP = re.compile(r"(.+?)(?:_(" + "|".join(DIRS) + r"))?")
SPRITES = "assets/images/sprites/"
LIT_FRAG = "shaders/asset_studio_lit_sprite.frag"
DEFAULT_MAP = {"tile": 32, "cols": 40, "rows": 24}


def _bytes(data: Path | bytes) -> bytes:
    return data if isinstance(data, bytes) else Path(data).read_bytes()


def clip_parts(name: str) -> tuple[str, str]:
    """('walk', 'E') for walk_E; direction is '' for a clip without a facing suffix."""
    m = CLIP.fullmatch(name)
    state, d = (m[1], m[2] or "") if m else (name, "")
    state = state.lower()
    return STATE_ALIASES.get(state, state), d


def _visible_height(png: bytes, anim: dict[str, Any]) -> float:
    """Alpha bounding-box height of the first frame of the south (else first) clip, in sheet pixels."""
    fw, fh = anim["frameSize"]
    clips = anim["clips"]
    clip = next((c for c in clips if c["name"].endswith("_S") and c["frames"]), None) or next(
        (c for c in clips if c["frames"]), None)
    if clip is None:
        return float(fh)
    f = clip["frames"][0]
    with Image.open(io.BytesIO(png)) as im:
        box = im.convert("RGBA").crop((f["x"], f["y"], f["x"] + f["w"], f["y"] + f["h"])).getchannel("A").point(
            lambda v: 255 if v >= 32 else 0).getbbox()
    return float(box[3] - box[1]) if box else float(fh)


def _sheets(index: dict[str, Path | bytes]) -> list[dict[str, Any]]:
    out = []
    for rel in sorted(index):
        if not (rel.startswith(SPRITES) and rel.endswith("_anim.json") and "/" not in rel[len(SPRITES):]):
            continue
        name = rel[len(SPRITES):-len("_anim.json")]
        png = index.get(f"{SPRITES}{name}.png")
        if png is None:
            continue
        anim = json.loads(_bytes(index[rel]))
        clips = [{"name": c["name"], "state": s, "dir": d, "frames": len(c["frames"])} for c in anim["clips"]
                 for s, d in [clip_parts(c["name"])] if c["frames"]]
        if not clips:
            continue
        p = anim["pivot"]
        out.append({"name": name, "frame": anim["frameSize"], "anchor": [p["anchorX"], p["anchorY"]],
                    "height": _visible_height(_bytes(png), anim), "normal": f"{SPRITES}{name}_normal.png" in index,
                    "clips": clips})
    return out


def _props(el: ET.Element) -> dict[str, str]:
    return {p.get("name", ""): p.get("value", "") for p in el.findall("./properties/property")}


def _tmx(rel: str, data: bytes, index: dict[str, Path | bytes]) -> dict[str, Any] | None:
    try:
        root = ET.fromstring(data)
        tile, cols, rows = int(root.get("tilewidth", "0")), int(root.get("width", "0")), int(root.get("height", "0"))
    except (ET.ParseError, ValueError):
        return None
    if not (tile and cols and rows):
        return None
    solid_gids: set[int] = set()
    for ts in root.findall("tileset"):
        first = int(ts.get("firstgid", "1"))
        for t in ts.findall("tile"):
            if _props(t).get("solid") == "true":
                solid_gids.add(first + int(t.get("id", "0")))
    cells: set[tuple[int, int]] = set()
    for layer in root.findall("layer"):
        everything = _props(layer).get("collision") == "true"
        csv = layer.findtext("data") or ""
        for i, v in enumerate(int(x) for x in re.findall(r"\d+", csv)):
            if v and (everything or v in solid_gids):
                cells.add((i % cols, i // cols))
    player: tuple[float, float] | None = None
    props = []
    base = PurePosixPath(rel).parent
    for o in root.findall("./objectgroup/object"):
        kind, x, y = o.get("type", ""), float(o.get("x", "0")), float(o.get("y", "0"))
        if kind == "player":
            player = (x, y)
        elif kind == "prop":
            sprite = _props(o).get("sprite", "")
            path = (base / sprite).as_posix()
            if path in index:
                w, h = float(o.get("width", "0")), float(o.get("height", "0"))
                props.append({"sprite": path.removeprefix("assets/"), "x": x + w / 2, "y": y, "w": w, "h": h})
    return {"tmx": PurePosixPath(rel).name, "tile": tile, "cols": cols, "rows": rows,
            "style": _props(root).get("style", "topdown"), "solid": sorted(cells), "player": player, "props": props}


def _find_tmx(index: dict[str, Path | bytes]) -> dict[str, Any] | None:
    maps = [info for rel in sorted(index) if rel.startswith("assets/tiles/") and rel.endswith(".tmx")
            and "/" not in rel[len("assets/tiles/"):] and (info := _tmx(rel, _bytes(index[rel]), index))]
    return next((m for m in maps if m["player"]), maps[0] if maps else None)


def demo_config(index: dict[str, Path | bytes], title: str) -> dict[str, Any] | None:
    from studio_core.ops.playable_scene import CHAR_HEIGHT_TILES, SIDE_GRAVITY, SIDE_JUMP_TILES, SIDE_SPEED, TOPDOWN_SPEED

    sheets = _sheets(index)
    if not sheets:
        return None
    tm = _find_tmx(index)
    m = tm or {**DEFAULT_MAP, "style": "topdown", "tmx": None, "solid": [], "player": None, "props": []}
    tile, cols, rows, style = m["tile"], m["cols"], m["rows"], m["style"]
    side = style == "sideview"
    ground = None if tm else next((rel.removeprefix("assets/") for rel in sorted(index) if rel.startswith("assets/tiles/")
                                   and rel.endswith(".png") and "/" not in rel[len("assets/tiles/"):]), None)
    parallax = next((PurePosixPath(rel).parent.name for rel in sorted(index) if rel.startswith("assets/images/parallax/")
                     and rel.endswith("/parallax.json")), None)
    spawn = m["player"] or ((cols / 2) * tile, (rows / 2) * tile)
    gravity = SIDE_GRAVITY * tile if side else 0.0
    return {"title": title, "style": style, "tile": tile, "cols": cols, "rows": rows, "tmx": m["tmx"][:-4] if m["tmx"] else None,
            "ground": ground, "solid": [list(c) for c in m["solid"]], "spawn": list(spawn),
            "speed": (SIDE_SPEED if side else TOPDOWN_SPEED) * tile, "gravity": gravity,
            "jump": round((2 * SIDE_GRAVITY * SIDE_JUMP_TILES) ** 0.5 * tile, 2) if side else 0.0,
            "charHeight": round(CHAR_HEIGHT_TILES[style] * tile, 2), "viewRows": min(rows, 11 if side else 12),
            "parallax": parallax, "lit": LIT_FRAG in index and any(s["normal"] for s in sheets),
            "sheets": sheets, "props": m["props"]}


def _wanted(rel: str, cfg: dict[str, Any]) -> bool:
    if rel.startswith(SPRITES):
        return "/" not in rel[len(SPRITES):]
    if rel.startswith(("assets/tiles/", "assets/images/parallax/")):
        return True
    return rel == LIT_FRAG and cfg["lit"]


def _pubspec(paths: list[str], shaders: list[str]) -> str:
    dirs = list(dict.fromkeys(str(PurePosixPath(p).parent) + "/" for p in paths))
    lines = ["flutter:", "  uses-material-design: true", "  assets:"] + [f"    - {d}" for d in dirs]
    if shaders:
        lines += ["  shaders:"] + [f"    - {s}" for s in shaders]
    return dart.PUBSPEC + "\n".join(lines) + "\n"


def demo_project(tree: Files, title: str) -> Files:
    """The complete Flutter project (pubspec, lib/, test/, the assets it uses); [] when the tree has no character sheet."""
    index: dict[str, Path | bytes] = dict(tree)
    cfg = demo_config(index, title)
    if cfg is None:
        return []
    files: Files = [(rel, index[rel]) for rel in sorted(index) if _wanted(rel, cfg)]
    files.append(("assets/demo/demo.json", json.dumps(cfg, indent=1).encode()))
    paths = [rel for rel, _ in files if rel.startswith("assets/")]
    shaders = [LIT_FRAG] if cfg["lit"] else []
    parallax = cfg["parallax"] is not None
    files += [("pubspec.yaml", _pubspec(paths, shaders).encode()), ("analysis_options.yaml", dart.ANALYSIS.encode()),
              ("lib/main.dart", dart.MAIN.encode()), ("lib/demo_config.dart", dart.CONFIG.encode()),
              ("lib/game.dart", dart.GAME.encode()), ("lib/player.dart", dart.PLAYER.encode()),
              ("lib/world.dart", dart.WORLD.encode()),
              ("lib/gen/asset_studio_flame.dart", fd.flame_dart(True, False, parallax, False, cfg["tmx"] is not None).encode()),
              ("lib/gen/asset_studio_shaders.dart", SHADERS_DART.encode()),
              ("test/demo_test.dart", dart.TEST.encode()),
              ("README_DEMO.md", dart.README.format(title=title, notes=_notes(cfg)).encode())]
    return files


def _notes(cfg: dict[str, Any]) -> str:
    sheets = ", ".join(f"{s['name']} ({round(s['frame'][0])}px)" for s in cfg["sheets"])
    lines = [f"\nScene: {cfg['cols']}x{cfg['rows']} tiles of {cfg['tile']}px ({cfg['style']}), "
             f"{'map ' + cfg['tmx'] + '.tmx' if cfg['tmx'] else 'no .tmx: ' + ('tileset floor' if cfg['ground'] else 'grid')}, "
             f"{len(cfg['props'])} props, {len(cfg['solid'])} solid tiles, parallax: {cfg['parallax'] or 'none'}, "
             f"light: {'on' if cfg['lit'] else 'off (no normal map)'}.", f"Sheets: {sheets}."]
    return "\n".join(lines)


def tree_of(root: Path) -> Files:
    return [(p.relative_to(root).as_posix(), p) for p in sorted(root.rglob("*")) if p.is_file()
            and not p.relative_to(root).as_posix().startswith(f"{DEMO_DIR}/")]


def write_demo(root: Path, title: str) -> list[str]:
    """Writes `<root>/demo/` from the Flutter tree under `root`; returns the written paths (relative to root)."""
    import shutil

    files = demo_project(tree_of(root), title)
    out = root / DEMO_DIR
    if out.exists():
        shutil.rmtree(out)
    for rel, data in files:
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, Path):
            shutil.copyfile(data, dst)
        else:
            dst.write_bytes(data)
    return [f"{DEMO_DIR}/{rel}" for rel, _ in files]
