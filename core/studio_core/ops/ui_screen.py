"""Game UI screens: a layout tree (Claude or a built-in template) laid over ui-kit 9-slice pieces and a bitmap font,
rendered on the CPU at 16:9 / 20:9 / 4:3 inside the safe area, with an overflow report. Engine export lives in
export/ui_screen.py and reads `ui_screen.json`."""
from __future__ import annotations

import asyncio
import json
import math
import re
import zlib
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from ..contracts import BlobRef, Outputs, StepContext, StepError, StepResult
from .executors import OpExecutor, _load, _put_png
from .ui import SLICES_FILE, SLICES_TYPE, make_states, nine_slice_borders, nine_slice_scale

LAYOUT_FILE = "ui_layout.json"
LAYOUT_TYPE = "asset_studio.ui_layout"
SCREEN_FILE = "ui_screen.json"
SCREEN_TYPE = "asset_studio.ui_screen"
SCREENS = ("home", "shop", "level_select", "pause", "result_win", "result_lose", "settings")
NODE_TYPES = ("panel", "button", "tab", "slot", "bar", "label", "icon", "list")
SKINNED = ("panel", "button", "tab", "slot", "bar")
INTERACTIVE = ("button", "tab")
ALIGNS = ("left", "center", "right")
REF = {"portrait": (1080, 1920), "landscape": (1920, 1080)}
MATCH = 0.5
# (label, physical px, safe-area insets l, t, r, b): a 20:9 phone loses a notch strip and the home-indicator bar.
ASPECTS = {
    "portrait": (("16x9", (1080, 1920), (0, 0, 0, 0)), ("20x9", (1080, 2400), (0, 96, 0, 64)),
                 ("4x3", (1536, 2048), (0, 0, 0, 0))),
    "landscape": (("16x9", (1920, 1080), (0, 0, 0, 0)), ("20x9", (2400, 1080), (96, 0, 96, 48)),
                  ("4x3", (2048, 1536), (0, 0, 0, 0))),
}
TEXT_FONT = "arial_bold"  # closest installed match to Unity's LegacyRuntime font (Arial) drawn bold
MIN_TOUCH = 64
MAX_NODES = 160
Rect = tuple[float, float, float, float]


def canvas_scale(size: tuple[int, int], ref: tuple[int, int], match: float = MATCH) -> float:
    """Unity CanvasScaler ScaleWithScreenSize / MatchWidthOrHeight, logarithmic blend."""
    lw, lh = math.log2(size[0] / ref[0]), math.log2(size[1] / ref[1])
    return 2 ** ((1 - match) * lw + match * lh)


def _ident(s: Any, fallback: str) -> str:
    out = re.sub(r"[^A-Za-z0-9_]+", "_", str(s or "")).strip("_")[:40] or fallback
    return f"n_{out}" if out[:1].isdigit() else out


def _hex(v: Any, default: str) -> str:
    s = str(v or "").strip().lstrip("#")
    if re.fullmatch(r"[0-9a-fA-F]{6}", s):
        return "#" + s.lower()
    if re.fullmatch(r"[0-9a-fA-F]{3}", s):
        return "#" + "".join(c * 2 for c in s.lower())
    return default


def _num(v: Any, default: float, lo: float, hi: float) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return default if x != x else min(hi, max(lo, x))


def _rect(raw: Any) -> list[float]:
    if isinstance(raw, dict):
        raw = [raw.get(k) for k in ("x", "y", "w", "h")]
    vals = list(raw) if isinstance(raw, (list, tuple)) else []
    x, y, w, h = [_num(v, d, 0, 1) for v, d in zip(vals + [None] * 4, (0, 0, 1, 1))][:4]
    w, h = max(0.01, min(w, 1 - x)), max(0.01, min(h, 1 - y))
    x, y = min(x, 1 - w), min(y, 1 - h)
    return [round(x, 4), round(y, 4), round(w, 4), round(h, 4)]


def _node(name: str, parent: str, type_: str, rect: list[float], **kw: Any) -> dict[str, Any]:
    return {"name": name, "parent": parent, "type": type_, "rect": rect, **kw}


def _top_bar(title: str, back: bool = True) -> list[dict[str, Any]]:
    nodes = [_node("TopBar", "", "panel", [0.02, 0.005, 0.96, 0.07])]
    if back:
        nodes.append(_node("Back", "TopBar", "button", [0.02, 0.12, 0.14, 0.76], icon="back"))
    nodes += [_node("Title", "TopBar", "label", [0.2, 0.1, 0.4, 0.8], text=title, font_size=56,
                    align="left" if back else "center"),
              _node("CoinIcon", "TopBar", "icon", [0.64, 0.18, 0.08, 0.64], icon="coin"),
              _node("Coins", "TopBar", "label", [0.73, 0.1, 0.24, 0.8], text="12,500", font_size=44, align="left")]
    return nodes


def template(screen: str, title: str = "") -> dict[str, Any]:
    """A sensible default layout per screen type: used with planner=template and as Claude's reference shape."""
    t = title.strip()
    if screen == "home":
        nodes = [_node("TopBar", "", "panel", [0.02, 0.005, 0.96, 0.07]),
                 _node("CoinIcon", "TopBar", "icon", [0.03, 0.18, 0.08, 0.64], icon="coin"),
                 _node("Coins", "TopBar", "label", [0.12, 0.1, 0.3, 0.8], text="12,500", font_size=44, align="left"),
                 _node("GemIcon", "TopBar", "icon", [0.45, 0.18, 0.08, 0.64], icon="gem"),
                 _node("Gems", "TopBar", "label", [0.54, 0.1, 0.22, 0.8], text="350", font_size=44, align="left"),
                 _node("Settings", "TopBar", "button", [0.84, 0.1, 0.14, 0.8], icon="settings"),
                 _node("GameTitle", "", "label", [0.05, 0.14, 0.9, 0.14], text=t or "MY GAME", font_size=120),
                 _node("Play", "", "button", [0.2, 0.52, 0.6, 0.09], text="PLAY", font_size=72),
                 _node("Daily", "", "button", [0.2, 0.63, 0.6, 0.07], text="Daily Reward", font_size=48),
                 _node("NavBar", "", "panel", [0.0, 0.89, 1.0, 0.11]),
                 _node("Nav", "NavBar", "list", [0.03, 0.1, 0.94, 0.8], count=4, columns=4, item="tab",
                       item_icons=["home", "shop", "trophy", "settings"],
                       item_texts=["Home", "Shop", "Ranks", "Settings"], font_size=32)]
    elif screen == "shop":
        nodes = _top_bar(t or "SHOP") + [
            _node("Tabs", "", "list", [0.04, 0.09, 0.92, 0.06], count=3, columns=3, item="tab",
                  item_texts=["Coins", "Gems", "Items"], font_size=40),
            _node("Body", "", "panel", [0.03, 0.16, 0.94, 0.8]),
            _node("Items", "Body", "list", [0.05, 0.04, 0.9, 0.92], count=6, columns=2, item="slot",
                  item_icons=["coin", "coin", "gem", "gem", "chest", "heart"],
                  item_texts=["$0.99", "$4.99", "$1.99", "$9.99", "$2.99", "$0.99"], font_size=44)]
    elif screen == "level_select":
        nodes = _top_bar(t or "LEVELS") + [
            _node("Body", "", "panel", [0.03, 0.09, 0.94, 0.76]),
            _node("Levels", "Body", "list", [0.05, 0.04, 0.9, 0.92], count=20, columns=4, item="slot",
                  item_texts=[str(i) for i in range(1, 21)], font_size=56),
            _node("Play", "", "button", [0.2, 0.88, 0.6, 0.08], text="PLAY", font_size=64)]
    elif screen == "pause":
        nodes = [_node("Window", "", "panel", [0.1, 0.27, 0.8, 0.46]),
                 _node("Title", "Window", "label", [0.1, 0.05, 0.8, 0.14], text=t or "PAUSED", font_size=80),
                 _node("Buttons", "Window", "list", [0.12, 0.24, 0.76, 0.66], count=3, columns=1, item="button",
                       item_texts=["Resume", "Restart", "Home"], font_size=56)]
    elif screen in ("result_win", "result_lose"):
        win = screen == "result_win"
        nodes = [_node("Window", "", "panel", [0.07, 0.18, 0.86, 0.62]),
                 _node("Title", "Window", "label", [0.08, 0.04, 0.84, 0.12],
                       text=t or ("VICTORY!" if win else "DEFEAT"),
                       font_size=96)]
        if win:
            nodes += [_node("Stars", "Window", "list", [0.18, 0.18, 0.64, 0.16], count=3, columns=3, item="icon",
                            item_icons=["star", "star", "star"]),
                      _node("Score", "Window", "label", [0.1, 0.37, 0.8, 0.08], text="Score: 12,345", font_size=56),
                      _node("RewardIcon", "Window", "icon", [0.3, 0.48, 0.1, 0.08], icon="coin"),
                      _node("Reward", "Window", "label", [0.42, 0.48, 0.3, 0.08], text="+250", font_size=56,
                            align="left"),
                      _node("Next", "Window", "button", [0.15, 0.62, 0.7, 0.13], text="NEXT", font_size=64),
                      _node("Actions", "Window", "list", [0.1, 0.8, 0.8, 0.12], count=2, columns=2, item="button",
                            item_texts=["Retry", "Home"], font_size=44)]
        else:
            nodes += [_node("Broken", "Window", "icon", [0.35, 0.2, 0.3, 0.2], icon="heart"),
                      _node("Message", "Window", "label", [0.1, 0.43, 0.8, 0.08], text="Out of moves", font_size=52),
                      _node("Continue", "Window", "button", [0.15, 0.56, 0.7, 0.14], text="Continue  50",
                            icon="gem", font_size=56),
                      _node("Actions", "Window", "list", [0.1, 0.76, 0.8, 0.14], count=2, columns=2, item="button",
                            item_texts=["Retry", "Home"], font_size=44)]
    elif screen == "settings":
        nodes = [_node("Window", "", "panel", [0.06, 0.12, 0.88, 0.74]),
                 _node("Title", "Window", "label", [0.1, 0.03, 0.66, 0.1], text=t or "SETTINGS", font_size=72),
                 _node("Close", "Window", "button", [0.82, 0.03, 0.13, 0.08], icon="close"),
                 _node("MusicLabel", "Window", "label", [0.08, 0.18, 0.4, 0.07], text="Music", font_size=48,
                       align="left"),
                 _node("Music", "Window", "bar", [0.48, 0.19, 0.44, 0.05], value=0.8),
                 _node("SoundLabel", "Window", "label", [0.08, 0.29, 0.4, 0.07], text="Sound", font_size=48,
                       align="left"),
                 _node("Sound", "Window", "bar", [0.48, 0.3, 0.44, 0.05], value=0.6),
                 _node("VibrationLabel", "Window", "label", [0.08, 0.41, 0.4, 0.07], text="Vibration",
                       font_size=48, align="left"),
                 _node("Vibration", "Window", "button", [0.56, 0.405, 0.3, 0.08], text="ON", font_size=44),
                 _node("LanguageLabel", "Window", "label", [0.08, 0.53, 0.4, 0.07], text="Language", font_size=48,
                       align="left"),
                 _node("Language", "Window", "button", [0.5, 0.525, 0.42, 0.08], text="English", font_size=44),
                 _node("Support", "Window", "button", [0.15, 0.7, 0.7, 0.1], text="Support", font_size=52),
                 _node("Version", "Window", "label", [0.1, 0.88, 0.8, 0.06], text="v1.0.0", font_size=32)]
    else:
        raise StepError("op_bad_params", f"unknown screen '{screen}' (use {', '.join(SCREENS)})")
    return {"type": LAYOUT_TYPE, "screen": screen, "nodes": nodes}


def _expand_list(n: dict[str, Any], taken: set[str]) -> list[dict[str, Any]]:
    count = int(_num(n.get("count"), 3, 1, 40))
    cols = int(_num(n.get("columns"), 1, 1, 8))
    rows = math.ceil(count / cols)
    item = n.get("item") if n.get("item") in ("slot", "button", "tab", "icon", "panel") else "slot"
    icons = [str(v) for v in n.get("item_icons") or []]
    texts = [str(v) for v in n.get("item_texts") or []]
    gap = _num(n.get("gap"), 0.06, 0, 0.3)
    cw, ch = 1 / cols, 1 / rows
    out = []
    for i in range(count):
        r, c = divmod(i, cols)
        name = _unique(f"{n['name']}_{i + 1:02d}", taken)
        rect = [c * cw + cw * gap / 2, r * ch + ch * gap / 2, cw * (1 - gap), ch * (1 - gap)]
        node: dict[str, Any] = {"name": name, "parent": n["name"], "type": item, "rect": [round(v, 4) for v in rect]}
        icon = icons[i] if i < len(icons) else ""
        text = texts[i] if i < len(texts) else ""
        if item == "icon":
            node["icon"] = icon or "star"
        elif item in INTERACTIVE:
            node.update(text=text, icon=icon, font_size=n.get("font_size") or 44)
        else:
            out.append(node)
            if icon:
                out.append({"name": _unique(f"{name}_icon", taken), "parent": name, "type": "icon", "icon": icon,
                            "rect": [0.18, 0.08, 0.64, 0.56] if text else [0.15, 0.15, 0.7, 0.7]})
            if text:
                out.append({"name": _unique(f"{name}_text", taken), "parent": name, "type": "label", "text": text,
                            "font_size": n.get("font_size") or 44,
                            "rect": [0.06, 0.66, 0.88, 0.28] if icon else [0.06, 0.15, 0.88, 0.7]})
            continue
        out.append(node)
    return out


def _unique(name: str, taken: set[str]) -> str:
    base, k = name, 2
    while name.lower() in taken:
        name, k = f"{base}_{k}", k + 1
    taken.add(name.lower())
    return name


def normalize_layout(raw: Any, screen: str, orientation: str = "portrait") -> dict[str, Any]:
    """Validated, flat, parents-first node list; lists expanded into their items. Rects are fractions of the parent
    (x, y, w, h), clamped inside it, so nothing can leave the safe area by construction."""
    if orientation not in REF:
        raise StepError("op_bad_params", f"orientation must be {' or '.join(REF)}")
    src = raw.get("nodes") if isinstance(raw, dict) else raw
    if not isinstance(src, list) or not src:
        raise StepError("ui_bad_layout", "layout has no nodes")
    taken: set[str] = {"root", "safearea", "background", "icon", "text", "fill"}
    renamed: dict[str, str] = {}
    items: list[dict[str, Any]] = []
    for i, n in enumerate(src[:MAX_NODES]):
        if not isinstance(n, dict):
            continue
        orig = str(n.get("name") or f"node_{i + 1}")
        name = _unique(_ident(orig, f"node_{i + 1}"), taken)
        renamed[orig] = name
        items.append({**n, "name": name, "_parent": str(n.get("parent") or "")})
    by_name = {n["name"]: n for n in items}
    out: list[dict[str, Any]] = []
    placed: set[str] = set()

    def clean(n: dict[str, Any]) -> dict[str, Any]:
        t = n.get("type") if n.get("type") in NODE_TYPES else ("label" if n.get("text") else "panel")
        c: dict[str, Any] = {"name": n["name"], "parent": n["parent"], "type": t, "rect": _rect(n.get("rect"))}
        text = " ".join(str(n.get("text") or "").split())[:60]
        if t in ("label", *INTERACTIVE) and text:
            c["text"] = text
        if t in ("label", *INTERACTIVE):
            c["font_size"] = int(_num(n.get("font_size"), 44, 14, 180))
            c["align"] = n.get("align") if n.get("align") in ALIGNS else "center"
            c["color"] = _hex(n.get("color"), "#ffffff")
        icon = _ident(n.get("icon"), "") if n.get("icon") else ""
        if icon and t in ("icon", *INTERACTIVE, "slot"):
            c["icon"] = icon.lower()
        if t == "icon" and not icon:
            c["icon"] = "star"
        if t == "bar":
            c["value"] = round(_num(n.get("value"), 0.7, 0, 1), 3)
        if t in SKINNED and n.get("variant") is not None:
            c["variant"] = int(_num(n.get("variant"), 0, 0, 9))
        return c

    def place(n: dict[str, Any], depth: int = 0) -> None:
        if n["name"] in placed:
            return
        parent = renamed.get(n["_parent"], _ident(n["_parent"], "")) if n["_parent"] else ""
        if parent and parent in by_name and parent != n["name"] and depth < 16:
            place(by_name[parent], depth + 1)
        n["parent"] = parent if parent in placed else ""
        placed.add(n["name"])
        node = clean(n)
        if node["type"] == "list" and n.get("expanded"):
            out.append({**node, "expanded": True})
        elif node["type"] == "list":
            node.update({k: n.get(k) for k in ("count", "columns", "item", "item_icons", "item_texts", "gap",
                                               "font_size") if n.get(k) is not None})
            out.append({**{k: v for k, v in node.items() if k in ("name", "parent", "type", "rect")}, "expanded": True})
            for child in _expand_list(node, taken):
                placed.add(child["name"])
                out.append(clean({**child, "_parent": child["parent"]}))
        else:
            out.append(node)

    for n in items:
        place(n)
    if not out:
        raise StepError("ui_bad_layout", "layout has no usable nodes")
    return {"type": LAYOUT_TYPE, "screen": screen, "orientation": orientation, "ref": list(REF[orientation]),
            "match": MATCH, "nodes": out[:MAX_NODES * 2]}


def compile_rects(nodes: list[dict[str, Any]], safe: Rect) -> dict[str, Rect]:
    rects: dict[str, Rect] = {"": safe}
    for n in nodes:
        px0, py0, px1, py1 = rects.get(n["parent"], safe)
        x, y, w, h = n["rect"]
        pw, ph = px1 - px0, py1 - py0
        rects[n["name"]] = (px0 + x * pw, py0 + y * ph, px0 + (x + w) * pw, py0 + (y + h) * ph)
    return rects


@dataclass
class Aspect:
    label: str
    size: tuple[int, int]
    scale: float
    logical: tuple[float, float]
    safe: Rect
    rects: dict[str, Rect] = field(default_factory=dict)


def aspects(layout: dict[str, Any]) -> list[Aspect]:
    ref = tuple(layout["ref"])
    out = []
    for label, size, (il, it, ir, ib) in ASPECTS[layout["orientation"]]:
        s = canvas_scale(size, ref, float(layout.get("match", MATCH)))  # type: ignore[arg-type]
        lw, lh = size[0] / s, size[1] / s
        safe = (il / s, it / s, lw - ir / s, lh - ib / s)
        out.append(Aspect(label, size, s, (lw, lh), safe, compile_rects(layout["nodes"], safe)))
    return out


@dataclass
class Sprite:
    name: str
    img: Image.Image
    border: tuple[int, int, int, int]
    states: dict[str, Image.Image] = field(default_factory=dict)
    kind: str = "element"


def _rounded(size: tuple[int, int], radius: int, fill: tuple[int, ...], edge: tuple[int, ...], width: int,
             shine: bool = True) -> Image.Image:
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius, fill=edge)
    d.rounded_rectangle((width, width, size[0] - 1 - width, size[1] - 1 - width), max(1, radius - width), fill=fill)
    if shine:
        top = tuple(min(255, c + 40) for c in fill[:3]) + (255,)
        d.rounded_rectangle((width + 2, width + 2, size[0] - 3 - width, size[1] // 2 - 1), max(1, radius - width - 2),
                            fill=top)
    return img


def default_kit(accent: str = "#f0a020") -> dict[str, list[Sprite]]:
    """Flat CPU kit for when no ui-kit run is given: rounded frames in a dark theme plus one accent colour."""
    acc = tuple(int(accent[i:i + 2], 16) for i in (1, 3, 5))
    dark = (36, 40, 56, 235)
    edge = (14, 16, 24, 255)
    kit: dict[str, list[Sprite]] = {}

    def add(kind: str, name: str, img: Image.Image, states: bool = False) -> None:
        border, _ = nine_slice_borders(img)
        kit.setdefault(kind, []).append(Sprite(name, img, border, make_states(img) if states else {}, kind))

    add("panel", "panel_01", _rounded((160, 160), 28, dark, edge, 6, shine=False))
    add("button", "button_01", _rounded((240, 96), 26, (*acc, 255), edge, 6), states=True)
    add("button", "button_02", _rounded((240, 96), 26, (70, 140, 220, 255), edge, 6), states=True)
    add("slot", "slot_01", _rounded((128, 128), 20, (58, 64, 88, 255), edge, 5, shine=False), states=True)
    add("tab", "tab_01", _rounded((200, 80), 22, (72, 78, 104, 255), edge, 5), states=True)
    add("bar", "bar_01", _rounded((240, 40), 18, (20, 22, 30, 255), edge, 4, shine=False))
    add("bar", "bar_02", _rounded((240, 40), 18, (*acc, 255), (0, 0, 0, 0), 0))
    return kit


def kit_from_slices(doc: dict[str, Any], load: Any) -> dict[str, list[Sprite]]:
    """ui_slices.json (op.nine_slice) → sprites by kind; the kind is the first word of the element name."""
    if not isinstance(doc, dict) or doc.get("type") != SLICES_TYPE:
        raise StepError("op_bad_input", "kit must be a ui_slices.json from ui-kit / ui-kit-style")
    base: dict[str, Sprite] = {}
    states: list[tuple[str, str, Any]] = []
    for e in doc.get("elements") or []:
        img = load(str(e.get("hash") or ""))
        if img is None:
            continue
        if e.get("state"):
            states.append((str(e.get("state_of") or ""), str(e["state"]), img))
            continue
        b = [int(v) for v in (e.get("border") or [0, 0, 0, 0])][:4]
        name = _ident(e.get("name"), "element")
        base[name] = Sprite(name, img, (b[0], b[1], b[2], b[3]), {}, name.split("_")[0])
    for of, state, img in states:
        if _ident(of, "") in base:
            base[_ident(of, "")].states[state] = img
    kit: dict[str, list[Sprite]] = {}
    for sp in base.values():
        kit.setdefault(sp.kind, []).append(sp)
    if not kit:
        raise StepError("op_bad_input", "kit has no readable elements (were they made in this studio?)")
    return kit


def pick_skin(kit: dict[str, list[Sprite]], kind: str, variant: int = 0) -> Sprite | None:
    # ui-kit never names a tab (classify has no such kind); a second button reads as one.
    # Generated sheets do not always yield every kind; any framed piece still 9-slices into a usable button.
    chain = {"tab": ("tab", "button", "slot", "element", "panel"), "slot": ("slot", "panel", "element", "button"),
             "panel": ("panel", "element", "slot"), "button": ("button", "tab", "element", "slot", "panel"),
             "bar": ("bar",)}.get(kind, (kind,))
    for k in chain:
        opts = kit.get(k) or []
        if k == "bar":
            opts = opts[:1]
        if opts:
            if kind == "tab" and k == "button" and len(opts) > 1 and variant == 0:
                return opts[1]
            return opts[min(variant, len(opts) - 1)]
    return None


def bar_fill(kit: dict[str, list[Sprite]]) -> Sprite | None:
    bars = kit.get("bar") or []
    return bars[1] if len(bars) > 1 else None


ICON_COLORS = {"coin": "#f5c542", "gem": "#45c8f0", "star": "#ffd23f", "heart": "#ef4b5f", "close": "#ef4b5f",
               "back": "#2a2438", "play": "#7ddc4a", "settings": "#d8dce8", "home": "#ffffff", "shop": "#ffd23f",
               "trophy": "#f5c542", "chest": "#c98a3c", "lock": "#c0c4d0"}


def placeholder_icon(name: str, size: int = 128) -> Image.Image:
    """Simple CPU glyph so a screen reads before an icon-pack run replaces it (same name, same slot)."""
    s = size
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    key = next((k for k in ICON_COLORS if k in name), "")
    col = ICON_COLORS.get(key) or "#{:06x}".format(zlib.crc32(name.encode()) & 0x7F7F7F | 0x606060)
    rgb = tuple(int(col[i:i + 2], 16) for i in (1, 3, 5))
    ink, w = (24, 22, 30, 255), max(3, s // 20)
    c = s / 2
    if key in ("star", "trophy"):
        pts = [(c + (0.46 if k % 2 == 0 else 0.2) * s * math.sin(k * math.pi / 5),
                c - (0.46 if k % 2 == 0 else 0.2) * s * math.cos(k * math.pi / 5) + s * 0.04) for k in range(10)]
        d.polygon(pts, fill=(*rgb, 255), outline=ink, width=w)
    elif key == "gem":
        d.polygon([(c, s * 0.06), (s * 0.9, s * 0.4), (c, s * 0.94), (s * 0.1, s * 0.4)], fill=(*rgb, 255),
                  outline=ink, width=w)
    elif key == "heart":
        d.ellipse((s * 0.08, s * 0.14, s * 0.54, s * 0.6), fill=(*rgb, 255))
        d.ellipse((s * 0.46, s * 0.14, s * 0.92, s * 0.6), fill=(*rgb, 255))
        d.polygon([(s * 0.1, s * 0.44), (s * 0.9, s * 0.44), (c, s * 0.92)], fill=(*rgb, 255))
    elif key == "close":
        d.line([(s * 0.2, s * 0.2), (s * 0.8, s * 0.8)], fill=(*rgb, 255), width=s // 6)
        d.line([(s * 0.8, s * 0.2), (s * 0.2, s * 0.8)], fill=(*rgb, 255), width=s // 6)
    elif key == "back":
        d.line([(s * 0.62, s * 0.18), (s * 0.3, c), (s * 0.62, s * 0.82)], fill=(*rgb, 255), width=s // 7,
               joint="curve")
    elif key == "play":
        d.polygon([(s * 0.28, s * 0.14), (s * 0.86, c), (s * 0.28, s * 0.86)], fill=(*rgb, 255), outline=ink, width=w)
    elif key == "settings":
        for k in range(8):
            a = k * math.pi / 4
            d.line([(c, c), (c + 0.44 * s * math.cos(a), c + 0.44 * s * math.sin(a))], fill=(*rgb, 255), width=s // 7)
        d.ellipse((s * 0.2, s * 0.2, s * 0.8, s * 0.8), fill=(*rgb, 255), outline=ink, width=w)
        d.ellipse((s * 0.38, s * 0.38, s * 0.62, s * 0.62), fill=ink)
    elif key == "home":
        d.polygon([(c, s * 0.1), (s * 0.92, s * 0.5), (s * 0.08, s * 0.5)], fill=(*rgb, 255))
        d.rectangle((s * 0.2, s * 0.48, s * 0.8, s * 0.9), fill=(*rgb, 255))
    else:
        d.ellipse((s * 0.06, s * 0.06, s * 0.94, s * 0.94), fill=(*rgb, 255), outline=ink, width=w)
        if key == "coin":
            d.ellipse((s * 0.26, s * 0.26, s * 0.74, s * 0.74), outline=ink, width=w)
        elif name[:1].isalnum():
            from .store_capsules import load_font

            d.text((c, c), name[0].upper(), font=load_font("arial_black", int(s * 0.5)), fill=ink, anchor="mm")
    return img


ICON_SUBJECTS = {"back": "a bold left-pointing arrow", "close": "a bold X cross mark", "settings": "a gear cog",
                 "play": "a play triangle", "home": "a small house", "pause": "two thick vertical pause bars",
                 "music": "a music note", "sound": "a speaker", "lock": "a padlock", "info": "a letter i in a circle",
                 "plus": "a bold plus sign", "retry": "a circular arrow", "restart": "a circular arrow",
                 "next": "a bold right-pointing arrow", "menu": "three horizontal bars"}


def icon_names(layout: dict[str, Any]) -> list[str]:
    seen: list[str] = []
    for n in layout.get("nodes") or []:
        name = str(n.get("icon") or "").strip()
        if name and name not in seen:
            seen.append(name)
    return seen


def icon_prompt(name: str, style: str) -> str:
    words = " ".join(re.split(r"[^A-Za-z0-9]+", name)).strip().lower()
    subject = next((v for k, v in ICON_SUBJECTS.items() if k in words.split()), f"a {words}")
    style = " ".join(style.split())[:300]
    return (f"A single game UI icon of {subject}, {style}. One icon centered, filling most of the image, on a plain "
            "pure white background, no frame, no text, no letters.")


def icons_to_draw(layout: dict[str, Any], style: str, have: list[str]) -> tuple[list[str], list[str]]:
    """Icon names the layout uses that `have` (an icon-pack's names) cannot cover, and a Z-Image prompt for each."""
    pack = dict.fromkeys(have)
    names = [n for n in icon_names(layout) if match_icon(n, pack) is None]  # type: ignore[arg-type]
    return names, [icon_prompt(n, style) for n in names]


def _tokens(s: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", s.lower()) if t and t not in ("icon", "a", "the", "of")}


def match_icon(name: str, icons: dict[str, Any]) -> str | None:
    want = _tokens(name)
    best, score = None, 0.0
    for k in icons:
        have = _tokens(k)
        hit = len(want & have) / max(1, len(want))
        if hit > score:
            best, score = k, hit
    return best if score >= 0.5 else None


def _font(size: int) -> Any:
    from .store_capsules import load_font

    return load_font(TEXT_FONT, max(6, int(size)))


@dataclass
class BitmapFont:
    doc: dict[str, Any]
    fnt: dict[str, Any]
    atlas: Image.Image

    @property
    def size(self) -> int:
        return int(self.doc.get("size") or 32)

    def render(self, text: str) -> Image.Image:
        from .bitmap_font import render_text

        return render_text(self.fnt, self.atlas, text)


def text_width(text: str, size: int, bmf: BitmapFont | None) -> float:
    if bmf is not None:
        return float(bmf.render(text).width)
    return float(_font(size).getlength(text))


def text_height(size: int, bmf: BitmapFont | None) -> float:
    if bmf is not None:
        return float(bmf.doc.get("line_height") or bmf.size)
    asc, desc = _font(size).getmetrics()
    return float(asc + desc)


def sub_rect(r: Rect, f: list[float] | None) -> Rect:
    if not f:
        return r
    w, h = r[2] - r[0], r[3] - r[1]
    return r[0] + f[0] * w, r[1] + f[1] * h, r[0] + (f[0] + f[2]) * w, r[1] + (f[1] + f[3]) * h


def place_content(layout: dict[str, Any], ref: Aspect) -> None:
    """Icon + text inside a button/tab: stacked on narrow buttons, side by side on wide ones. Stored as fractions of
    the node so the mockup and both engines put them in the same place."""
    for n in layout["nodes"]:
        if n["type"] not in INTERACTIVE:
            continue
        x0, y0, x1, y1 = ref.rects[n["name"]]
        aspect = (x1 - x0) / max(1.0, y1 - y0)
        if n.get("icon") and n.get("text"):
            if aspect < 1.8:
                n["icon_rect"], n["text_rect"] = [0.2, 0.08, 0.6, 0.52], [0.06, 0.6, 0.88, 0.34]
            else:
                iw = min(0.3, 0.7 / aspect)
                n["icon_rect"] = [0.05, 0.15, iw, 0.7]
                n["text_rect"] = [round(0.07 + iw, 4), 0.11, round(0.86 - iw, 4), 0.78]
        elif n.get("icon"):
            n["icon_rect"] = [0.18, 0.18, 0.64, 0.64]
        elif n.get("text"):
            n["text_rect"] = [0.08, 0.11, 0.84, 0.78]
        for k in ("icon_rect", "text_rect"):
            if k in n:
                n[k] = [round(v, 4) for v in n[k]]


def _text_box(n: dict[str, Any], r: Rect) -> tuple[float, float]:
    x0, y0, x1, y1 = sub_rect(r, n.get("text_rect"))
    return max(1.0, (x1 - x0) * 0.96), max(1.0, (y1 - y0) * 0.96)


def fit_text(layout: dict[str, Any], views: list[Aspect], bmf: BitmapFont | None) -> list[dict[str, Any]]:
    """Shrinks each text's font size until it fits its box at every aspect (TTF), or flags it (bitmap fonts render at
    their native size in engines, so they cannot shrink)."""
    issues = []
    for n in layout["nodes"]:
        if not n.get("text"):
            continue
        size = int(n["font_size"]) if bmf is None else bmf.size
        n["font_size_max"] = size
        for v in views:
            bw, bh = _text_box(n, v.rects[n["name"]])
            if bmf is None:
                while size > 12 and (text_width(n["text"], size, None) > bw or text_height(size, None) > bh * 1.2):
                    size = max(12, int(size * min(0.95, bw / max(1.0, text_width(n["text"], size, None)))))
            elif text_width(n["text"], size, bmf) > bw * 1.02 or text_height(size, bmf) > bh * 1.4:
                need = text_width(n["text"], size, bmf)
                issues.append({"aspect": v.label, "node": n["name"], "kind": "text_overflow",
                               "detail": f"'{n['text']}' needs {need:.0f}px, box {bw:.0f}px"})
        n["font_size"] = size
    return issues


def check(layout: dict[str, Any], views: list[Aspect]) -> dict[str, Any]:
    """Every node inside the safe area and its parent at every aspect; interactive targets not too small."""
    eps = 0.5
    by = {n["name"]: n for n in layout["nodes"]}
    per: dict[str, Any] = {}
    for v in views:
        over, small = [], []
        s0, s1, s2, s3 = v.safe
        for name, (x0, y0, x1, y1) in v.rects.items():
            if not name:
                continue
            p0, p1, p2, p3 = v.rects.get(by[name]["parent"], v.safe)
            if x0 < max(s0, p0) - eps or y0 < max(s1, p1) - eps or x1 > min(s2, p2) + eps or y1 > min(s3, p3) + eps:
                over.append(name)
            if by[name]["type"] in INTERACTIVE and min(x1 - x0, y1 - y0) < MIN_TOUCH:
                small.append(name)
        per[v.label] = {"size": list(v.size), "canvas": [round(v.logical[0], 1), round(v.logical[1], 1)],
                        "scale": round(v.scale, 4), "safe": [round(c, 1) for c in v.safe], "overflow": over,
                        "small_touch": small}
    return per


def fit_sprites(layout: dict[str, Any], views: list[Aspect], skins: dict[str, Sprite]) -> dict[str, float]:
    """Per sprite, the scale (≤ 1) that keeps its 9-slice corners within 80% of the smallest node using it, so the
    engines can draw it 1:1 (Godot StyleBoxTexture has no corner scale)."""
    k: dict[str, float] = {}
    for n in layout["nodes"]:
        sp = skins.get(n["name"])
        if sp is None or not any(sp.border):
            continue
        l, t, r, b = sp.border
        for v in views:
            x0, y0, x1, y1 = v.rects[n["name"]]
            need = min(0.8 * (x1 - x0) / max(1, l + r), 0.8 * (y1 - y0) / max(1, t + b), 1.0)
            k[sp.name] = min(k.get(sp.name, 1.0), max(0.2, need))
    return k


def scaled(sp: Sprite, k: float) -> Sprite:
    if k >= 0.999:
        return sp
    size = (max(3, round(sp.img.width * k)), max(3, round(sp.img.height * k)))

    def rs(im: Image.Image) -> Image.Image:
        return im.resize(size, Image.Resampling.LANCZOS)

    border = tuple(max(0, min(round(v * k), (size[i % 2] - 1) // 2)) for i, v in enumerate(sp.border))
    states = {s: rs(im) for s, im in sp.states.items()}
    return Sprite(sp.name, rs(sp.img), border, states, sp.kind)  # type: ignore[arg-type]


def fill_rect(n: dict[str, Any]) -> list[float]:
    """Bar fill inside its frame, fractions of the bar; shared by the mockup and both engine exports."""
    return [0.03, 0.15, round(0.94 * float(n.get("value", 0.7)), 4), 0.7]


def _draw_sliced(canvas: Image.Image, sp: Sprite, box: tuple[int, int, int, int], scale: float) -> None:
    w, h = box[2] - box[0], box[3] - box[1]
    if w < 2 or h < 2:
        return
    img = sp.img
    border = sp.border
    if abs(scale - 1) > 1e-3:
        img = img.resize((max(2, round(img.width * scale)), max(2, round(img.height * scale))),
                         Image.Resampling.LANCZOS)
        border = tuple(round(v * scale) for v in border)  # type: ignore[assignment]
    if any(border):
        l, t, r, b = border
        f = min(1.0, w / max(1, l + r + 1), h / max(1, t + b + 1))
        if f < 1:
            img = img.resize((max(2, round(img.width * f)), max(2, round(img.height * f))), Image.Resampling.LANCZOS)
            border = tuple(int(v * f) for v in border)  # type: ignore[assignment]
        part = nine_slice_scale(img, border, (w, h))
    else:
        part = img.resize((w, h), Image.Resampling.LANCZOS)
    canvas.alpha_composite(part.crop((0, 0, w, h)), (box[0], box[1]))


def _draw_icon(canvas: Image.Image, icon: Image.Image, box: tuple[int, int, int, int]) -> None:
    w, h = box[2] - box[0], box[3] - box[1]
    if w < 2 or h < 2:
        return
    f = min(w / icon.width, h / icon.height)
    im = icon.resize((max(1, round(icon.width * f)), max(1, round(icon.height * f))), Image.Resampling.LANCZOS)
    canvas.alpha_composite(im, (box[0] + (w - im.width) // 2, box[1] + (h - im.height) // 2))


def _text_image(text: str, size: int, color: str, bmf: BitmapFont | None, scale: float) -> Image.Image:
    if bmf is not None:
        im = bmf.render(text)
        return im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))),
                         Image.Resampling.NEAREST if bmf.doc.get("pixel") else Image.Resampling.LANCZOS)
    px = max(6, round(size * scale))
    font = _font(px)
    stroke = max(1, round(px * 0.07))
    asc, desc = font.getmetrics()
    w = int(font.getlength(text)) + 2 * stroke + 2
    im = Image.new("RGBA", (max(1, w), asc + desc + 2 * stroke), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((stroke, stroke), text, font=font, fill=color, stroke_width=stroke,
                            stroke_fill=(16, 14, 22, 230))
    return im


def _draw_text(canvas: Image.Image, n: dict[str, Any], box: tuple[int, int, int, int], scale: float,
               bmf: BitmapFont | None) -> None:
    im = _text_image(n["text"], int(n["font_size"]), n.get("color", "#ffffff"), bmf, scale)
    x0, y0, x1, y1 = box
    align = n.get("align", "center")
    x = x0 if align == "left" else (x1 - im.width if align == "right" else (x0 + x1 - im.width) // 2)
    y = (y0 + y1 - im.height) // 2
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    layer.alpha_composite(im, (max(-im.width, min(canvas.width, x)), max(-im.height, min(canvas.height, y))))
    clip = Image.new("L", canvas.size, 0)
    ImageDraw.Draw(clip).rectangle((x0, y0, x1, y1), fill=255)
    layer.putalpha(Image.fromarray(np.minimum(np.asarray(layer.getchannel("A")), np.asarray(clip))))
    canvas.alpha_composite(layer)


def background_image(size: tuple[int, int], bg: Image.Image | None, accent: str) -> Image.Image:
    if bg is not None:
        f = max(size[0] / bg.width, size[1] / bg.height)
        im = bg.convert("RGBA").resize((max(size[0], round(bg.width * f)), max(size[1], round(bg.height * f))),
                                       Image.Resampling.LANCZOS)
        x, y = (im.width - size[0]) // 2, (im.height - size[1]) // 2
        return im.crop((x, y, x + size[0], y + size[1]))
    acc = np.array([int(accent[i:i + 2], 16) for i in (1, 3, 5)], np.float32)
    t = np.linspace(0, 1, size[1], dtype=np.float32)[:, None]
    top, bot = acc * 0.35 + 20, np.array([12, 12, 22], np.float32)
    col = top * (1 - t) + bot * t
    arr = np.repeat(col[:, None, :], size[0], 1)
    return Image.fromarray(np.dstack([arr, np.full(arr.shape[:2], 255, np.float32)]).astype(np.uint8), "RGBA")


def render(layout: dict[str, Any], v: Aspect, skins: dict[str, Sprite], fill: Sprite | None,
           icons: dict[str, Image.Image], bmf: BitmapFont | None, bg: Image.Image | None, accent: str,
           outline_safe: bool = False) -> Image.Image:
    canvas = background_image(v.size, bg, accent)
    s = v.scale

    def px(r: Rect) -> tuple[int, int, int, int]:
        return round(r[0] * s), round(r[1] * s), round(r[2] * s), round(r[3] * s)

    for n in layout["nodes"]:
        box = px(v.rects[n["name"]])
        sp = skins.get(n["name"])
        if sp is not None:
            _draw_sliced(canvas, sp, box, s)
        if n["type"] == "bar" and fill is not None:
            _draw_sliced(canvas, fill, px(sub_rect(v.rects[n["name"]], fill_rect(n))), s)
        r = v.rects[n["name"]]
        if n.get("icon") and n["name"] in icons:
            _draw_icon(canvas, icons[n["name"]], px(sub_rect(r, n.get("icon_rect"))))
        if n.get("text"):
            _draw_text(canvas, n, px(sub_rect(r, n.get("text_rect"))), s, bmf)
    if outline_safe:
        d = ImageDraw.Draw(canvas)
        x0, y0, x1, y1 = px(v.safe)
        for i in range(0, max(1, x1 - x0), 24):
            d.line([(x0 + i, y0), (min(x1, x0 + i + 12), y0)], fill=(0, 255, 200, 255), width=3)
            d.line([(x0 + i, y1 - 2), (min(x1, x0 + i + 12), y1 - 2)], fill=(0, 255, 200, 255), width=3)
        for i in range(0, max(1, y1 - y0), 24):
            d.line([(x0, y0 + i), (x0, min(y1, y0 + i + 12))], fill=(0, 255, 200, 255), width=3)
            d.line([(x1 - 2, y0 + i), (x1 - 2, min(y1, y0 + i + 12))], fill=(0, 255, 200, 255), width=3)
    return canvas


def contact_sheet(shots: list[tuple[str, Image.Image]], report: dict[str, Any], height: int = 960) -> Image.Image:
    gap, label_h = 24, 40
    scaled_ = [(lab, im.resize((max(1, round(im.width * height / im.height)), height), Image.Resampling.LANCZOS))
               for lab, im in shots]
    w = sum(im.width for _, im in scaled_) + gap * (len(scaled_) + 1)
    out = Image.new("RGBA", (w, height + label_h + 2 * gap), (28, 30, 38, 255))
    d = ImageDraw.Draw(out)
    font = _font(26)
    x = gap
    for lab, im in scaled_:
        r = report.get(lab, {})
        bad = r.get("overflow")
        text = f"{lab}  {r.get('size', ['', ''])[0]}x{r.get('size', ['', ''])[1]}  " + ("TRÀN: " + ", ".join(bad[:3])
                                                                                         if bad else "OK")
        d.text((x, gap // 2), text, font=font, fill=(255, 110, 110, 255) if bad else (150, 230, 170, 255))
        out.alpha_composite(im, (x, gap + label_h))
        x += im.width + gap
    return out


def build(layout: dict[str, Any], kit: dict[str, list[Sprite]], icon_pack: dict[str, Image.Image],
          bmf: BitmapFont | None, bg: Image.Image | None, accent: str) -> dict[str, Any]:
    views = aspects(layout)
    place_content(layout, views[0])
    text_issues = fit_text(layout, views, bmf)
    skins: dict[str, Sprite] = {}
    for n in layout["nodes"]:
        if n["type"] in SKINNED:
            sp = pick_skin(kit, n["type"], int(n.get("variant", 0)))
            if sp is not None:
                skins[n["name"]] = sp
    ks = fit_sprites(layout, views, skins)
    fitted = {name: scaled(sp, ks.get(sp.name, 1.0)) for name, sp in skins.items()}
    fill = None
    if any(n["type"] == "bar" for n in layout["nodes"]):
        fill = bar_fill(kit)
        if fill is None:
            # A kit with only a bar frame still needs a fill the engines can stretch; the mockup draws the same one.
            plain = default_kit(accent)["bar"][1]
            fill = Sprite("bar_fill", plain.img, plain.border, {}, "bar")
        fill = scaled(fill, ks.get(next((sp.name for sp in kit.get("bar", [])[:1]), ""), 1.0))
    icons: dict[str, Image.Image] = {}
    icon_files: dict[str, tuple[str, Image.Image, bool]] = {}
    for n in layout["nodes"]:
        if not n.get("icon"):
            continue
        hit = match_icon(n["icon"], icon_pack)
        key = f"icon_{_ident(hit or n['icon'], 'icon').lower()}"
        if key not in icon_files:
            icon_files[key] = (key, icon_pack[hit] if hit else placeholder_icon(n["icon"]), hit is None)
        icons[n["name"]] = icon_files[key][1]
        n["icon_sprite"] = key
    for n in layout["nodes"]:
        if n["name"] in fitted:
            n["sprite"] = fitted[n["name"]].name
        if n["type"] == "bar" and fill is not None:
            n["fill_sprite"] = fill.name
    report = check(layout, views)
    shots = [(v.label, render(layout, v, fitted, fill, icons, bmf, bg, accent)) for v in views]
    sheet = contact_sheet([(v.label, render(layout, v, fitted, fill, icons, bmf, bg, accent, True)) for v in views],
                          report)
    sprites: dict[str, Sprite] = {sp.name: sp for sp in fitted.values()}
    if fill is not None:
        sprites[fill.name] = fill
    return {"views": views, "shots": shots, "preview": sheet, "report": report, "text_issues": text_issues,
            "sprites": sprites, "icons": icon_files}


def _load_bitmap_font(doc: Any, load: Any) -> BitmapFont | None:
    from .bitmap_font import FONT_TYPE, fnt_text, parse_fnt

    if not isinstance(doc, dict) or doc.get("type") != FONT_TYPE or not doc.get("chars"):
        raise StepError("op_bad_input", "font must be a font.json from bitmap-font")
    atlas = load(str((doc.get("atlas") or {}).get("hash") or ""))
    if atlas is None:
        raise StepError("op_bad_input", "bitmap font atlas not found in this studio's library")
    return BitmapFont(doc, parse_fnt(fnt_text(doc, "atlas.png")), atlas)


async def _json_in(ctx: StepContext, inputs: Outputs, name: str) -> Any:
    refs = inputs.get(name) or []
    if not refs:
        return None
    try:
        return json.loads(await asyncio.to_thread(ctx.blobs.path(refs[0].hash).read_bytes))
    except (OSError, ValueError) as e:
        raise StepError("op_bad_input", f"input '{name}' is not JSON: {e}") from e


class UiScreen(OpExecutor):
    kind = "op.ui_screen"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        screen = str(params.get("screen") or "home")
        orientation = str(params.get("orientation") or "portrait")
        accent = _hex(params.get("accent"), "#f0a020")
        raw = await _json_in(ctx, inputs, "layout")
        if raw is None:
            raw = template(screen, str(params.get("title") or ""))
        drawn = [str(v) for v in raw.get("icon_names") or []] if isinstance(raw, dict) else []
        layout = normalize_layout(raw, screen if screen in SCREENS else str(raw.get("screen") or screen), orientation)

        def load(h: str) -> Image.Image | None:
            p = ctx.blobs.path(h) if h else None
            if p is None or not p.is_file():
                return None
            with Image.open(p) as im:
                return im.convert("RGBA")

        kit_doc = await _json_in(ctx, inputs, "kit")
        kit = await asyncio.to_thread(kit_from_slices, kit_doc, load) if kit_doc is not None else \
            await asyncio.to_thread(default_kit, accent)
        icon_doc = await _json_in(ctx, inputs, "icons")
        icon_pack: dict[str, Image.Image] = {}
        if icon_doc is not None:
            for sp in [s for ss in (await asyncio.to_thread(kit_from_slices, icon_doc, load)).values() for s in ss]:
                icon_pack[sp.name] = sp.img
        # Z-Image icons arrive in the order of the layout's icon_names (the recipe maps one prompt per name).
        gen = inputs.get("gen_icons") or []
        if gen and len(gen) != len(drawn):
            ctx.log(f"{len(gen)} icon Z-Image cho {len(drawn)} tên icon: lệch thứ tự nên dùng icon tạm", "warning")
        elif gen:
            for name, ref in zip(drawn, gen):
                img = load(ref.hash)
                # Background removal can eat a pale icon whole; an invisible icon is worse than the placeholder.
                if img is not None and name not in icon_pack and img.getchannel("A").getbbox() is not None:
                    icon_pack[name] = img
        font_doc = await _json_in(ctx, inputs, "font")
        bmf = await asyncio.to_thread(_load_bitmap_font, font_doc, load) if font_doc is not None else None
        bg = await _load(ctx, inputs["background"][0]) if inputs.get("background") else None
        ctx.progress(0, 3, label="layout")
        res = await asyncio.to_thread(build, layout, kit, icon_pack, bmf, bg, accent)
        ctx.progress(1, 3, label="render")
        shots = [await _put_png(ctx, im, f"{screen}_{lab}.png") for lab, im in res["shots"]]
        prev = await _put_png(ctx, res["preview"], f"{screen}_preview.png")
        elements: list[dict[str, Any]] = []
        out_imgs: list[BlobRef] = []

        async def put(name: str, img: Image.Image, border: tuple[int, int, int, int], **kw: Any) -> None:
            ref = await _put_png(ctx, img, f"{name}.png")
            out_imgs.append(ref)
            elements.append({"name": name, "file": f"{name}.png", "w": img.width, "h": img.height,
                             "border": list(border), "states": [], "hash": ref.hash, **kw})

        for sp in res["sprites"].values():
            await put(sp.name, sp.img, sp.border, stretch_x=any(sp.border), stretch_y=any(sp.border),
                      off_center=False)
            base = elements[-1]
            for state, simg in sp.states.items():
                base["states"].append(f"{sp.name}_{state}")
                await put(f"{sp.name}_{state}", simg, sp.border, state=state, state_of=sp.name,
                          stretch_x=any(sp.border), stretch_y=any(sp.border), off_center=False)
        for key, img, _ in res["icons"].values():
            await put(key, img, (0, 0, 0, 0), stretch_x=False, stretch_y=False, off_center=False)
        if bg is not None:
            await put("background", bg.convert("RGBA"), (0, 0, 0, 0), stretch_x=False, stretch_y=False,
                      off_center=False)
            layout["background"] = "background"
        slices = await ctx.blobs.put_bytes(json.dumps({"type": SLICES_TYPE, "sliced": True, "elements": elements},
                                                      indent=1).encode(), name=SLICES_FILE, mime="application/json")
        ctx.progress(2, 3, label="report")
        sprite_states = {sp.name: sorted(sp.states) for sp in res["sprites"].values()}
        overflow = {k: v["overflow"] for k, v in res["report"].items() if v["overflow"]}
        doc = {**layout, "type": SCREEN_TYPE, "title": str(params.get("title") or ""), "accent": accent,
               "sprites": sprite_states, "placeholder_icons": sorted(k for k, _, ph in res["icons"].values() if ph),
               "bitmap_font": ({"name": bmf.doc.get("name"), "size": bmf.size, "pixel": bool(bmf.doc.get("pixel"))}
                               if bmf else None),
               "text_font": None if bmf else TEXT_FONT, "aspects": res["report"], "text_issues": res["text_issues"],
               "ok": not overflow and not res["text_issues"]}
        screen_ref = await ctx.blobs.put_bytes(json.dumps(doc, ensure_ascii=False, indent=1).encode(),
                                               name=SCREEN_FILE, mime="application/json")
        outputs: dict[str, list[BlobRef]] = {"images": shots, "preview": [prev], "slices": [slices],
                                             "screen": [screen_ref], "sprites": out_imgs}
        if font_doc is not None:
            from .bitmap_font import FONT_FILE

            # export/bitmap_font.font_set finds the font by this blob name, whatever the input file was called.
            outputs["font"] = [await ctx.blobs.put_bytes(json.dumps(font_doc, ensure_ascii=False, indent=1).encode(),
                                                         name=FONT_FILE, mime="application/json")]
        ctx.progress(3, 3)
        return StepResult(outputs=outputs, meta={
            "nodes": len(layout["nodes"]), "overflow": sum(len(v) for v in overflow.values()),
            "text_issues": len(res["text_issues"]), "placeholder_icons": len(doc["placeholder_icons"]),
            "small_touch": sorted({n for v in res["report"].values() for n in v["small_touch"]})[:12],
            "kit": "ui-kit" if kit_doc is not None else "default"})


UI_SCREEN_OPS: tuple[type[OpExecutor], ...] = (UiScreen,)
