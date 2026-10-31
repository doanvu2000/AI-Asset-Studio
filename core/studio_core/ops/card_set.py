"""Trading card sets: a card list (CSV / JSON / Claude) becomes art prompts (op.card_plan), then CPU-drawn frames by
rarity and type, the art in the window, Vietnamese-safe text shrunk to fit its box, a shared back, an atlas and
card_set.json, which export/card_set.py rebuilds as UGUI prefabs and Godot Control scenes (op.card_set)."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..contracts import BlobRef, Outputs, StepContext, StepError, StepResult
from .executors import OpExecutor, _load, _put_png
from .rarity import DEFAULT_COLORS, DEFAULT_TIERS

PLAN_FILE = "card_plan.json"
PLAN_TYPE = "asset-studio/card-plan"
LIST_TYPE = "asset-studio/card-list"
ART_TYPE = "asset-studio/card-art"
SET_FILE = "card_set.json"
SET_TYPE = "asset-studio/card-set"
ART_MODES = ("auto", "zimage", "klein", "none")
MAX_CARDS = 60
# Poker-size card, 63 x 88 mm; 756 px = 12 px/mm keeps the ratio exact.
CARD_MM = (63, 88)
DEFAULT_WIDTH = 756
ART_SIZE = (1024, 768)
BACK_SIZE = (768, 1024)
SS = 2
DEFAULT_STYLE = "painterly fantasy illustration, rich colours, dramatic lighting"
DEFAULT_BACK = "ornate symmetrical pattern, central glowing emblem, intricate filigree"

RARITY_ALIASES: tuple[tuple[str, ...], ...] = (
    ("common", "thường", "phổ thông", "basic", "c"),
    ("uncommon", "không phổ biến", "khá hiếm", "ít gặp", "u"),
    ("rare", "hiếm", "r"),
    ("epic", "sử thi", "cực hiếm", "e"),
    ("legendary", "huyền thoại", "mythic", "thần thoại", "l"),
)
# Type → (aliases, panel tint, art window shape).
TYPES: dict[str, tuple[tuple[str, ...], str, str]] = {
    "creature": (("creature", "unit", "minion", "monster", "beast", "sinh vật", "quái", "lính", "chiến binh",
                  "quân", "thú"), "#8a5a2b", "rect"),
    "spell": (("spell", "magic", "sorcery", "instant", "phép", "bùa", "thần chú", "ma thuật"), "#2f5fa8", "arch"),
    "item": (("item", "equipment", "weapon", "artifact", "relic", "gear", "vật phẩm", "trang bị", "vũ khí",
              "bảo vật", "cổ vật"), "#56656e", "chamfer"),
    "trap": (("trap", "secret", "bẫy", "bí mật"), "#7a2e6e", "chamfer"),
    "hero": (("hero", "leader", "champion", "anh hùng", "tướng", "thủ lĩnh"), "#a8842a", "arch"),
    "other": ((), "#5b4a6e", "rect"),
}
FIELDS: dict[str, tuple[str, ...]] = {
    "id": ("id", "key", "code", "mã"),
    "name": ("name", "title", "card", "tên", "tên thẻ"),
    "description": ("description", "desc", "text", "effect", "ability", "rules", "mô tả", "hiệu ứng", "kỹ năng"),
    "cost": ("cost", "mana", "energy", "chi phí", "năng lượng", "giá", "mana cost"),
    "attack": ("attack", "atk", "power", "công", "tấn công", "sát thương"),
    "health": ("health", "hp", "life", "defense", "def", "toughness", "máu", "sinh lực", "thủ"),
    "rarity": ("rarity", "tier", "độ hiếm", "bậc"),
    "type": ("type", "kind", "class", "loại", "hệ"),
    "art": ("art", "prompt", "art prompt", "image", "illustration", "hình", "ảnh", "tranh"),
    "flavor": ("flavor", "flavour", "lore", "quote", "trích dẫn"),
}

# Layout as fractions of the card (x of width, y of height), top-left origin.
CORNER = 0.05
BORDER = 0.035
BANNER = (0.19, 0.038, 0.93, 0.103)
COST = (0.11, 0.071, 0.083)
ART = (0.075, 0.122, 0.925)
STRIP = (0.11, 0.592, 0.89, 0.638)
GEM = (0.845, 0.615, 0.03)
DESC = (0.085, 0.652, 0.915)
DESC_BOTTOM = {True: 0.87, False: 0.94}
STAT_R = 0.072
ATK = (0.115, 0.93)
HP = (0.885, 0.93)
# Max / min font px at DEFAULT_WIDTH.
SIZES = {"name": (46, 18), "type": (30, 13), "desc": (34, 15), "cost": (66, 28), "attack": (60, 26),
         "health": (60, 26)}
INK = {"name": "#2b1a0b", "type": "#fff4d6", "desc": "#2a1f14", "cost": "#ffffff", "attack": "#ffffff",
       "health": "#ffffff"}
STROKE = {"type": "#1d130a", "cost": "#0b2350", "attack": "#4a1a02", "health": "#4a060c"}


def _norm(s: Any) -> str:
    return " ".join(unicodedata.normalize("NFC", str(s or "")).split())


def _key(s: str) -> str:
    return _norm(s).lower().strip(" :#*")


def slug(name: str) -> str:
    s = _norm(name).lower().replace("đ", "d")
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    out = "_".join(p for p in re.sub(r"[^a-z0-9]+", "_", s).split("_") if p)
    return f"c{out}" if out[:1].isdigit() else out


def _stat(v: Any) -> int | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        n = int(round(float(str(v).strip().replace(",", "."))))
    except (TypeError, ValueError):
        return None
    return n if 0 <= n <= 999 else None


def _match(value: str, aliases: tuple[str, ...]) -> bool:
    words = f" {re.sub(r'[^\w]+', ' ', value)} "
    return any(f" {a} " in words for a in aliases)


def rarity_index(raw: Any) -> int:
    v = _key(raw)
    if not v:
        return 0
    if v.isdigit():
        return max(0, min(4, int(v) - 1 if int(v) >= 1 else 0))
    for i, names in enumerate(RARITY_ALIASES):
        if v in names:
            return i
    # Longest alias first so "khá hiếm" does not read as "hiếm".
    hits = sorted(((len(a), i) for i, names in enumerate(RARITY_ALIASES) for a in names if len(a) > 1 and
                   _match(v, (a,))), reverse=True)
    return hits[0][1] if hits else 0


def type_key(raw: Any) -> str:
    v = _key(raw)
    for k, (aliases, _, _) in TYPES.items():
        if aliases and _match(v, aliases):
            return k
    return "other"


def _columns(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for col, val in row.items():
        k = _key(col)
        for field, aliases in FIELDS.items():
            if k == field or k in aliases:
                out.setdefault(field, val)
                break
    return out


def normalize_card(raw: Any, i: int, taken: set[str]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise StepError("op_bad_input", f"card {i + 1} is not an object")
    row = _columns(raw)
    name = _norm(row.get("name")) or f"Card {i + 1}"
    # Ids become file and node names; long card names would push engine paths past Windows' 260 characters.
    base = (slug(str(row.get("id") or "")) or slug(name))[:28].rstrip("_") or f"card_{i + 1}"
    cid, n = base, 2
    while cid in taken:
        cid, n = f"{base}_{n}", n + 1
    taken.add(cid)
    tier = rarity_index(row.get("rarity"))
    tkey = type_key(row.get("type"))
    attack, health = _stat(row.get("attack")), _stat(row.get("health"))
    if tkey in ("spell", "trap") and row.get("attack") in (None, "") and row.get("health") in (None, ""):
        attack = health = None
    return {"id": cid, "name": name, "description": _norm(row.get("description")),
            "cost": _stat(row.get("cost")), "attack": attack, "health": health,
            "rarity": DEFAULT_TIERS[tier], "rarity_index": tier, "rarity_label": _norm(row.get("rarity")),
            "type": tkey, "type_label": _norm(row.get("type")), "art": _norm(row.get("art")),
            "flavor": _norm(row.get("flavor"))}


def parse_cards(data: bytes, name: str = "") -> list[dict[str, Any]]:
    text = data.decode("utf-8-sig", errors="replace").strip()
    if not text:
        raise StepError("op_bad_input", "card list is empty")
    rows: list[Any]
    if name.lower().endswith(".json") or text[:1] in "[{":
        try:
            doc = json.loads(text)
        except ValueError as e:
            raise StepError("op_bad_input", f"card list is not valid JSON: {e}") from e
        rows = doc.get("cards") if isinstance(doc, dict) else doc
        if not isinstance(rows, list):
            raise StepError("op_bad_input", "JSON card list must be a list or {\"cards\": [...]}")
    else:
        try:
            dialect: Any = csv.Sniffer().sniff(text.split("\n", 1)[0], delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        rows = list(csv.DictReader(io.StringIO(text), dialect=dialect))
    if not rows:
        raise StepError("op_bad_input", "card list has no cards")
    if len(rows) > MAX_CARDS:
        raise StepError("op_bad_input", f"at most {MAX_CARDS} cards per set (got {len(rows)})")
    taken: set[str] = set()
    return [normalize_card(r, i, taken) for i, r in enumerate(rows)]


def _subject(card: dict[str, Any]) -> str:
    return card["art"] or f"{card['name']}, {card['type_label'] or card['type']}" + \
        (f": {card['description']}" if card["description"] else "")


def art_prompt(card: dict[str, Any], style: str) -> str:
    subject = _subject(card)
    # Never say "card" here: Z-Image then paints a framed card inside the art window.
    return (f"{subject}, {style}, full-bleed scene, one clear focal subject, detailed background reaching every "
            "edge of the image, no text, no letters, no border, no watermark")


def klein_prompt(subject: str, back: bool = False) -> str:
    tail = ("a symmetrical ornamental pattern filling the whole image" if back else
            "a full-bleed scene, the subject centered, detailed background reaching every edge of the image")
    # Klein copies the reference's own figures and scene unless told to drop them (live: 3 of 5 cards did).
    return (f"Paint a new illustration whose only subject is: {subject}. Keep just the art style of the reference "
            "image: the same line work, shading, colour palette and rendering technique. Replace the whole scene: keep "
            "no figure, animal, object, pose or composition of the reference image, and no frame. "
            f"{tail}. No text, no letters, no border, no watermark.")


def cover(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    w, h = size
    s = max(w / img.width, h / img.height)
    big = img.convert("RGBA").resize((max(w, math.ceil(img.width * s)), max(h, math.ceil(img.height * s))),
                                     Image.Resampling.LANCZOS)
    x, y = (big.width - w) // 2, (big.height - h) // 2
    return big.crop((x, y, x + w, y + h))


def resolve_mode(mode: str, has_ref: bool) -> str:
    if mode not in ART_MODES:
        raise StepError("op_bad_params", f"art must be one of {', '.join(ART_MODES)}")
    if mode == "auto":
        return "klein" if has_ref else "zimage"
    if mode == "klein" and not has_ref:
        raise StepError("op_bad_params", "art 'klein' needs a style reference image")
    return mode


async def _claude_art(ctx: StepContext, refs: list[BlobRef], cards: list[dict[str, Any]]) -> list[str]:
    """Fills empty art from claude.card_art; a card's own art column always wins."""
    if not refs:
        return []
    doc = json.loads(await asyncio.to_thread(ctx.blobs.path(refs[0].hash).read_bytes))
    art = doc.get("art") if isinstance(doc, dict) and doc.get("type") == ART_TYPE else None
    written = []
    for c in cards:
        a = _norm((art or {}).get(c["id"]))
        if a and not c["art"]:
            c["art"] = a
            written.append(c["id"])
    return written


class CardPlan(OpExecutor):
    kind = "op.card_plan"
    version = "3"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        refs = inputs.get("cards") or []
        if not refs:
            raise StepError("op_no_input", "input 'cards' is empty (a CSV / JSON card list)")
        data = await asyncio.to_thread(ctx.blobs.path(refs[0].hash).read_bytes)
        cards = parse_cards(data, refs[0].name or "")
        written = await _claude_art(ctx, inputs.get("art") or [], cards)
        style = _norm(params.get("style")) or DEFAULT_STYLE
        back = _norm(params.get("back"))
        if back.lower() in ("none", "cpu", "-", "0", "off"):
            back = ""
        ref_img = inputs.get("ref") or []
        mode = resolve_mode(str(params.get("art") or "auto").strip().lower(), bool(ref_img))
        prompts = [art_prompt(c, style) for c in cards]
        plan: dict[str, Any] = {"type": PLAN_TYPE, "art_mode": mode, "style": style, "back": back, "cards": cards,
                                "prompts_z": [], "back_z": [], "prompts_k": [], "back_k": []}
        ref_cards: list[BlobRef] = []
        ref_back: list[BlobRef] = []
        if mode == "zimage":
            plan["prompts_z"] = prompts
            plan["back_z"] = [f"{back}, {style}, no text, no letters, no watermark"] if back else []
        elif mode == "klein":
            ref = await _load(ctx, ref_img[0])
            # Klein keeps its input's size, so the crop sets each output's aspect.
            card_ref = await _put_png(ctx, cover(ref, ART_SIZE).convert("RGB"), "card_ref_art.png")
            ref_cards = [card_ref] * len(cards)
            plan["prompts_k"] = [klein_prompt(_subject(c)) for c in cards]
            if back:
                ref_back = [await _put_png(ctx, cover(ref, BACK_SIZE).convert("RGB"), "card_ref_back.png")]
                plan["back_k"] = [klein_prompt(back, back=True)]
        doc = await ctx.blobs.put_bytes(json.dumps(plan, ensure_ascii=False, indent=1).encode("utf-8"),
                                        name=PLAN_FILE, mime="application/json")
        summary = f"{len(cards)} cards, art: {mode}" + (", painted back" if plan["back_z"] or plan["back_k"] else "")
        # Klein reads the name/rules (often Vietnamese) poorly and then repaints the reference's own subject.
        no_art = [c["id"] for c in cards if mode == "klein" and not c["art"]]
        if no_art:
            summary += (f"\nWarning: {len(no_art)} card(s) have no English art prompt ({', '.join(no_art)}); Klein may "
                        "repeat the style image's subject for them. Fill the art column, or use card-set-prompt.")
        return StepResult(outputs={"plan": [doc], "ref_cards": ref_cards, "ref_back": ref_back,
                                   "text": [await ctx.blobs.put_bytes(summary.encode(), name="card_plan.txt",
                                                                      mime="text/plain")]},
                          meta={"cards": len(cards), "art": mode, "no_art_prompt": no_art, "claude_art": written})


def _rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _mix(a: tuple[int, ...], b: tuple[int, ...], t: float) -> tuple[int, int, int]:
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))  # type: ignore[return-value]


def light(c: tuple[int, ...], t: float) -> tuple[int, int, int]:
    return _mix(c, (255, 255, 255), t)


def dark(c: tuple[int, ...], t: float) -> tuple[int, int, int]:
    return _mix(c, (0, 0, 0), t)


def vgrad(size: tuple[int, int], stops: list[tuple[float, tuple[int, ...]]]) -> Image.Image:
    w, h = size
    y = np.linspace(0, 1, max(1, h))
    pos = [p for p, _ in stops]
    rows = np.stack([np.interp(y, pos, [c[i] for _, c in stops]) for i in range(3)], -1)
    arr = np.broadcast_to(rows[:, None, :], (h, w, 3)).astype(np.uint8)
    return Image.fromarray(np.ascontiguousarray(arr), "RGB").convert("RGBA")


def radial(size: tuple[int, int], inner: tuple[int, ...], outer: tuple[int, ...], cx: float = 0.38,
           cy: float = 0.32) -> Image.Image:
    w, h = size
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    d = np.clip(np.hypot((xx / max(1, w - 1) - cx), (yy / max(1, h - 1) - cy)) / 0.85, 0, 1)[..., None]
    arr = np.array(inner, np.float32) * (1 - d) + np.array(outer, np.float32) * d
    return Image.fromarray(arr.astype(np.uint8), "RGB").convert("RGBA")


def _paint(canvas: Image.Image, mask: Image.Image, fill: Image.Image | tuple[int, ...], opacity: float = 1.0) -> None:
    box = mask.getbbox()
    if box is None:
        return
    m = mask.crop(box)
    layer = fill.crop(box) if isinstance(fill, Image.Image) else Image.new("RGBA", m.size, (*fill[:3], 255))
    a = np.asarray(m, np.float32) * opacity
    if isinstance(fill, Image.Image):
        a = a * (np.asarray(layer.getchannel("A"), np.float32) / 255)
    layer.putalpha(Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "L"))
    canvas.alpha_composite(layer, box[:2])


def _mask(size: tuple[int, int]) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    m = Image.new("L", size, 0)
    return m, ImageDraw.Draw(m)


def shape_mask(size: tuple[int, int], box: tuple[float, float, float, float], shape: str, r: float) -> Image.Image:
    m, d = _mask(size)
    x0, y0, x1, y1 = box
    if shape == "arch":
        a = (x1 - x0) * 0.16
        d.rectangle((x0, y0 + a, x1, y1), fill=255)
        d.ellipse((x0, y0, x1, y0 + 2 * a), fill=255)
    elif shape == "chamfer":
        c = (x1 - x0) * 0.055
        d.polygon([(x0 + c, y0), (x1 - c, y0), (x1, y0 + c), (x1, y1 - c), (x1 - c, y1), (x0 + c, y1), (x0, y1 - c),
                   (x0, y0 + c)], fill=255)
    else:
        d.rounded_rectangle(box, radius=r, fill=255)
    return m


def _grow(box: tuple[float, float, float, float], e: float) -> tuple[float, float, float, float]:
    return box[0] - e, box[1] - e, box[2] + e, box[3] + e


@dataclass
class Geometry:
    w: int
    h: int

    def px(self, fx: float, fy: float, s: float = 1.0) -> tuple[float, float]:
        return fx * self.w * s, fy * self.h * s

    def box(self, x0: float, y0: float, x1: float, y1: float, s: float = 1.0) -> tuple[float, float, float, float]:
        return x0 * self.w * s, y0 * self.h * s, x1 * self.w * s, y1 * self.h * s

    def art_box(self, s: float = 1.0) -> tuple[float, float, float, float]:
        x0, y0, x1 = ART
        hpx = (x1 - x0) * self.w * ART_SIZE[1] / ART_SIZE[0]
        return x0 * self.w * s, y0 * self.h * s, x1 * self.w * s, (y0 * self.h + hpx) * s


def _metal(size: tuple[int, int], c: tuple[int, ...]) -> Image.Image:
    return vgrad(size, [(0, light(c, 0.55)), (0.18, light(c, 0.15)), (0.5, c), (0.82, dark(c, 0.35)),
                        (1, dark(c, 0.15))])


def _disc(canvas: Image.Image, center: tuple[float, float], r: float, inner: tuple[int, ...], outer: tuple[int, ...],
          rim: tuple[int, ...]) -> None:
    size = canvas.size
    cx, cy = center
    m, d = _mask(size)
    d.ellipse((cx - r * 1.14, cy - r * 1.14, cx + r * 1.14, cy + r * 1.14), fill=255)
    _paint(canvas, m, _metal(size, rim))
    m, d = _mask(size)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=255)
    box = (int(cx - r), int(cy - r), int(cx + r) + 1, int(cy + r) + 1)
    fill = Image.new("RGBA", size, (0, 0, 0, 0))
    fill.paste(radial((box[2] - box[0], box[3] - box[1]), inner, outer), box[:2])
    _paint(canvas, m, fill)
    m, d = _mask(size)
    d.ellipse((cx - r * 0.62, cy - r * 0.86, cx + r * 0.3, cy - r * 0.2), fill=255)
    _paint(canvas, m.filter(ImageFilter.GaussianBlur(r * 0.12)), (255, 255, 255), 0.28)


def _gem(canvas: Image.Image, center: tuple[float, float], r: float, c: tuple[int, ...]) -> None:
    cx, cy = center
    m, d = _mask(canvas.size)
    d.polygon([(cx, cy - r * 1.25), (cx + r * 1.25, cy), (cx, cy + r * 1.25), (cx - r * 1.25, cy)], fill=255)
    _paint(canvas, m, (40, 30, 20))
    m, d = _mask(canvas.size)
    d.polygon([(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)], fill=255)
    _paint(canvas, m, light(c, 0.1))
    m, d = _mask(canvas.size)
    d.polygon([(cx, cy - r), (cx + r, cy), (cx, cy)], fill=255)
    _paint(canvas, m, light(c, 0.55))
    m, d = _mask(canvas.size)
    d.polygon([(cx, cy + r), (cx - r, cy), (cx, cy)], fill=255)
    _paint(canvas, m, dark(c, 0.35))


def _glow(canvas: Image.Image, mask: Image.Image, color: tuple[int, ...], width: float, strength: float) -> None:
    inner = mask.filter(ImageFilter.MinFilter(3))
    edge = Image.fromarray(np.clip(np.asarray(mask, np.int16) - np.asarray(inner, np.int16), 0, 255).astype(np.uint8))
    halo = edge.filter(ImageFilter.GaussianBlur(width))
    a = np.minimum(np.asarray(halo, np.float32) * 6, 255) * (np.asarray(mask, np.float32) / 255)
    _paint(canvas, Image.fromarray(a.astype(np.uint8)), color, strength)


def frame_key(card: dict[str, Any]) -> str:
    flags = "".join(f for f, on in (("c", card["cost"] is not None), ("a", card["attack"] is not None),
                                    ("h", card["health"] is not None)) if on)
    return f"frame_{card['rarity']}_{card['type']}" + (f"_{flags}" if flags else "")


def draw_frame(g: Geometry, card: dict[str, Any], colors: list[tuple[int, int, int]]) -> Image.Image:
    """The card without art or text: the art window is a transparent hole, so engines layer art under it."""
    W, H = g.w * SS, g.h * SS
    size = (W, H)
    tier = int(card["rarity_index"])
    rc = colors[tier]
    _, tint_hex, shape = TYPES[card["type"]]
    tint = _rgb(tint_hex)
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    radius = CORNER * W
    outer, d = _mask(size)
    d.rounded_rectangle((0, 0, W - 1, H - 1), radius=radius, fill=255)
    _paint(canvas, outer, _metal(size, rc))
    b = BORDER * W
    panel_box = (b, b, W - b, H - b)
    panel = shape_mask(size, panel_box, "rect", radius * 0.6)
    _paint(canvas, panel, (*dark(rc, 0.65),))
    _paint(canvas, shape_mask(size, _grow(panel_box, -SS * 2), "rect", radius * 0.55),
           vgrad(size, [(0, dark(tint, 0.45)), (0.5, dark(tint, 0.62)), (1, dark(tint, 0.78))]))
    if tier >= 3:
        _glow(canvas, panel, light(rc, 0.25), W * 0.03, 0.75 if tier == 4 else 0.5)
    corners = [(b * 2.1, b * 2.1), (W - b * 2.1, b * 2.1), (b * 2.1, H - b * 2.1), (W - b * 2.1, H - b * 2.1)]
    if tier >= 1:
        for c in corners:
            m, dd = _mask(size)
            r = W * (0.008 + 0.003 * tier)
            dd.ellipse((c[0] - r, c[1] - r, c[0] + r, c[1] + r), fill=255)
            _paint(canvas, m, light(rc, 0.45))
    art = g.art_box(SS)
    _paint(canvas, shape_mask(size, _grow(art, W * 0.014), shape, W * 0.02), _metal(size, light(rc, 0.1)))
    _paint(canvas, shape_mask(size, _grow(art, W * 0.004), shape, W * 0.014), (20, 14, 10))
    strip = g.box(*STRIP, SS)
    _paint(canvas, shape_mask(size, _grow(strip, W * 0.006), "rect", (strip[3] - strip[1]) * 0.5),
           _metal(size, light(rc, 0.2)))
    _paint(canvas, shape_mask(size, strip, "rect", (strip[3] - strip[1]) * 0.5),
           vgrad(size, [(0, dark(rc, 0.35)), (1, dark(rc, 0.6))]))
    stats = card["attack"] is not None or card["health"] is not None
    desc = g.box(DESC[0], DESC[1], DESC[2], DESC_BOTTOM[stats], SS)
    parchment = vgrad(size, [(0, (246, 238, 214)), (1, (226, 209, 168))])
    _paint(canvas, shape_mask(size, _grow(desc, W * 0.006), "rect", W * 0.024), (*dark(tint, 0.3),))
    _paint(canvas, shape_mask(size, desc, "rect", W * 0.02), parchment)
    banner = g.box(*BANNER, SS)
    if tier == 4:
        cx = (banner[0] + banner[2]) / 2
        m, dd = _mask(size)
        dd.polygon([(cx - W * 0.16, banner[1] + 4), (cx - W * 0.05, banner[1] - W * 0.02), (cx, H * 0.004),
                    (cx + W * 0.05, banner[1] - W * 0.02), (cx + W * 0.16, banner[1] + 4)], fill=255)
        _paint(canvas, m, _metal(size, light(rc, 0.2)))
    _paint(canvas, shape_mask(size, _grow(banner, W * 0.008), "rect", (banner[3] - banner[1]) * 0.42),
           _metal(size, rc))
    _paint(canvas, shape_mask(size, banner, "rect", (banner[3] - banner[1]) * 0.38), parchment)
    gx, gy, gr = GEM
    _gem(canvas, (gx * W, gy * H), gr * W, light(rc, 0.05))
    if card["cost"] is not None:
        cx, cy, r = COST
        _disc(canvas, (cx * W, cy * H), r * W, (120, 196, 255), (22, 70, 170), light(rc, 0.15))
    if card["attack"] is not None:
        _disc(canvas, (ATK[0] * W, ATK[1] * H), STAT_R * W, (255, 196, 92), (190, 70, 10), light(rc, 0.15))
    if card["health"] is not None:
        _disc(canvas, (HP[0] * W, HP[1] * H), STAT_R * W, (255, 120, 120), (150, 16, 28), light(rc, 0.15))
    hole = shape_mask(size, art, shape, W * 0.012)
    a = np.asarray(canvas.getchannel("A"), np.float32) * (1 - np.asarray(hole, np.float32) / 255)
    canvas.putalpha(Image.fromarray(a.astype(np.uint8)))
    return canvas.resize((g.w, g.h), Image.Resampling.LANCZOS)


def placeholder_art(card: dict[str, Any], size: tuple[int, int] = ART_SIZE) -> Image.Image:
    tint = _rgb(TYPES[card["type"]][1])
    img = radial(size, light(tint, 0.45), dark(tint, 0.55), 0.5, 0.45)
    d = ImageDraw.Draw(img)
    w, h = size
    for i in range(6):
        r = (0.12 + i * 0.09) * w
        d.ellipse((w / 2 - r, h * 0.48 - r, w / 2 + r, h * 0.48 + r), outline=(*light(tint, 0.6), 90), width=3)
    return img


def placeholder_back(size: tuple[int, int], color: tuple[int, int, int]) -> Image.Image:
    w, h = size
    img = radial(size, light(color, 0.25), dark(color, 0.7), 0.5, 0.5)
    d = ImageDraw.Draw(img)
    step = w / 9
    for i in range(-10, 20):
        d.line([(i * step, 0), (i * step + h, h)], fill=(*light(color, 0.3), 40), width=2)
        d.line([(i * step, 0), (i * step - h, h)], fill=(*light(color, 0.3), 40), width=2)
    cx, cy = w / 2, h / 2
    for k, r in enumerate((0.36, 0.3, 0.2)):
        rr = r * w
        d.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), outline=(*light(color, 0.5 + k * 0.15), 255), width=4)
    pts = [(cx + math.cos(a) * w * (0.17 if j % 2 == 0 else 0.07), cy + math.sin(a) * w * (0.17 if j % 2 == 0 else 0.07))
           for j, a in enumerate(np.linspace(-math.pi / 2, 1.5 * math.pi, 17)[:-1])]
    d.polygon(pts, fill=(*light(color, 0.65), 255))
    return img


def draw_back(g: Geometry, art: Image.Image, color: tuple[int, int, int]) -> Image.Image:
    W, H = g.w * SS, g.h * SS
    size = (W, H)
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    radius = CORNER * W
    outer, d = _mask(size)
    d.rounded_rectangle((0, 0, W - 1, H - 1), radius=radius, fill=255)
    _paint(canvas, outer, _metal(size, color))
    b = BORDER * W
    inner = (b, b, W - b, H - b)
    _paint(canvas, shape_mask(size, _grow(inner, SS * 3), "rect", radius * 0.6), (24, 16, 10))
    fill = Image.new("RGBA", size, (0, 0, 0, 0))
    fill.paste(cover(art, (int(W - 2 * b), int(H - 2 * b))), (int(b), int(b)))
    _paint(canvas, shape_mask(size, inner, "rect", radius * 0.55), fill)
    _glow(canvas, shape_mask(size, inner, "rect", radius * 0.55), light(color, 0.3), W * 0.02, 0.6)
    return canvas.resize((g.w, g.h), Image.Resampling.LANCZOS)


@dataclass
class Fonts:
    title: str
    body: str
    number: str

    def doc(self) -> dict[str, Any]:
        out = {}
        for role in ("title", "body", "number"):
            path = getattr(self, role)
            fam, sty = ("", "")
            if path:
                try:
                    fam, sty = ImageFont.truetype(path, 16).getname()
                except OSError:
                    pass
            out[role] = {"file": path.replace("\\", "/").rsplit("/", 1)[-1] if path else "", "family": fam or "",
                         "style": sty or "", "bold": "bold" in (sty or "").lower() or "black" in (sty or "").lower()}
        return out


def pick_fonts(text: str, title: str = "auto", body: str = "auto") -> Fonts:
    from .localize import covers, pick_font
    from .store_capsules import available_fonts

    fonts = available_fonts()

    def chosen(choice: str, weight: str, family: str) -> str:
        choice = (choice or "auto").strip()
        if choice != "auto":
            path = fonts.get(choice) or choice
            try:
                ImageFont.truetype(path, 12)
            except OSError as e:
                raise StepError("op_bad_params", f"font '{choice}' is not installed (have: {', '.join(fonts)})") from e
            if not covers(path, text):
                raise StepError("op_bad_params", f"font '{choice}' lacks glyphs for this card text")
            return path
        return pick_font("vi", text, weight, family)[0]

    t, b = chosen(title, "bold", "serif"), chosen(body, "regular", "sans")
    digits = "0123456789"
    num = next((fonts[f] for f in ("arial_black", "segoe_black", "impact", "arial_bold") if f in fonts and
                covers(fonts[f], digits)), t)
    return Fonts(t, b, num)


def _font(path: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    from .localize import load_font

    return load_font(path, max(6, int(size)))


def _ink(draw: ImageDraw.ImageDraw, xy: tuple[float, float], text: str, font: Any, stroke: int,
         anchor: str = "mm") -> tuple[float, float, float, float]:
    return draw.textbbox(xy, text, font=font, anchor=anchor, stroke_width=stroke)


def _wrap(text: str, font: Any, width: float) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        cur = ""
        for word in para.split():
            trial = f"{cur} {word}".strip()
            if not cur or font.getlength(trial) <= width:
                cur = trial
            else:
                lines.append(cur)
                cur = word
        lines.append(cur)
    return [ln for ln in lines if ln] or [""]


@dataclass
class Placed:
    role: str
    text: str
    box: tuple[float, float, float, float]
    size: int
    lines: list[str]
    line_h: float
    font: str
    color: str
    stroke: int
    stroke_color: str
    align: str
    wrap: bool
    ok: bool
    ink: tuple[float, float, float, float]


def _layout_lines(draw: ImageDraw.ImageDraw, lines: list[str], font: Any, box: tuple[float, float, float, float],
                  line_h: float, stroke: int) -> tuple[list[tuple[float, float]], tuple[float, float, float, float]]:
    x0, y0, x1, y1 = box
    cx = (x0 + x1) / 2
    top = (y0 + y1) / 2 - line_h * len(lines) / 2
    pos = [(cx, top + line_h * (i + 0.5)) for i in range(len(lines))]
    inks = [_ink(draw, p, ln, font, stroke) for p, ln in zip(pos, lines) if ln]
    if not inks:
        return pos, (cx, (y0 + y1) / 2, cx, (y0 + y1) / 2)
    return pos, (min(i[0] for i in inks), min(i[1] for i in inks), max(i[2] for i in inks), max(i[3] for i in inks))


def fit_text(role: str, text: str, box: tuple[float, float, float, float], font_path: str, scale: float,
             wrap: bool, stroke_frac: float = 0.0) -> Placed:
    """Largest size whose rendered ink stays inside the box (multi-line for wrapped text); below the minimum the
    text is cut with an ellipsis and reported."""
    from .localize import wrap as balanced

    draw = ImageDraw.Draw(Image.new("L", (4, 4)))
    hi, lo = (max(6, round(v * scale)) for v in SIZES[role])
    x0, y0, x1, y1 = box
    bw = x1 - x0

    def attempt(size: int, body: str, multi: bool, max_lines: int
                ) -> tuple[list[str], float, int, tuple[float, float, float, float], bool]:
        font = _font(font_path, size)
        stroke = max(1, round(size * stroke_frac)) if stroke_frac else 0
        lines = _wrap(body, font, bw - 2 * stroke) if multi else [body]
        if 1 < len(lines) <= 8:
            # Same line count, but no lone last word.
            even = balanced(body, len(lines))
            if len(even) == len(lines) and all(font.getlength(ln) <= bw - 2 * stroke for ln in even):
                lines = even
        asc, desc = font.getmetrics() if hasattr(font, "getmetrics") else (size, size // 4)
        line_h = (asc + desc) * (1.02 if multi else 1.0)
        _, ink = _layout_lines(draw, lines, font, box, line_h, stroke)
        fits = (len(lines) <= max_lines and ink[0] >= x0 - 0.5 and ink[2] <= x1 + 0.5 and ink[1] >= y0 - 0.5
                and ink[3] <= y1 + 0.5)
        return lines, line_h, stroke, ink, fits

    # A long card name goes to two lines before it gets tiny.
    passes = [(True, 99, range(hi, lo - 1, -1))] if wrap else (
        [(False, 1, range(hi, round(hi * 0.7) - 1, -1)), (True, 2, range(round(hi * 0.75), lo - 1, -1))]
        if role == "name" else [(False, 1, range(hi, lo - 1, -1))])
    for multi, max_lines, sizes in passes:
        for size in sizes:
            lines, line_h, stroke, ink, fits = attempt(size, text, multi, max_lines)
            if fits:
                return Placed(role, text, box, size, lines, line_h, font_path, INK[role], stroke,
                              STROKE.get(role, ""), "center", multi, True, ink)
    multi, max_lines = passes[-1][0], passes[-1][1]
    words, body = text.split(), text
    lines, line_h, stroke, ink, _ = attempt(lo, body, multi, max_lines)
    while len(words) > 1:
        words = words[:-1]
        body = " ".join(words).rstrip(",.;:") + "…"
        lines, line_h, stroke, ink, fits = attempt(lo, body, multi, max_lines)
        if fits:
            break
    return Placed(role, body, box, lo, lines, line_h, font_path, INK[role], stroke, STROKE.get(role, ""), "center",
                  multi, False, ink)


def draw_text(img: Image.Image, p: Placed) -> None:
    draw = ImageDraw.Draw(img)
    font = _font(p.font, p.size)
    pos, _ = _layout_lines(draw, p.lines, font, p.box, p.line_h, p.stroke)
    for xy, ln in zip(pos, p.lines):
        draw.text(xy, ln, font=font, fill=p.color, anchor="mm", stroke_width=p.stroke,
                  stroke_fill=p.stroke_color or None)


def _type_line(card: dict[str, Any]) -> str:
    t = card["type_label"] or card["type"].capitalize()
    r = card["rarity_label"]
    return f"{t} • {r}" if r and _key(r) != _key(t) else t


def text_boxes(g: Geometry, card: dict[str, Any]) -> dict[str, tuple[float, float, float, float]]:
    W, H = g.w, g.h
    out: dict[str, tuple[float, float, float, float]] = {}
    bx0, by0, bx1, by1 = g.box(*BANNER)
    pad = W * 0.03
    left = bx0 + pad + (W * 0.035 if card["cost"] is not None else 0)
    out["name"] = (left, by0 + (by1 - by0) * 0.08, bx1 - pad, by1 - (by1 - by0) * 0.08)
    sx0, sy0, sx1, sy1 = g.box(*STRIP)
    out["type"] = (sx0 + W * 0.03, sy0 + 1, GEM[0] * W - GEM[2] * W * 1.6, sy1 - 1)
    stats = card["attack"] is not None or card["health"] is not None
    dx0, dy0, dx1, dy1 = g.box(DESC[0], DESC[1], DESC[2], DESC_BOTTOM[stats])
    if card["description"]:
        out["desc"] = (dx0 + W * 0.04, dy0 + H * 0.012, dx1 - W * 0.04, dy1 - H * 0.012)
    discs = {"cost": (COST[0], COST[1], COST[2]), "attack": (*ATK, STAT_R), "health": (*HP, STAT_R)}
    for role, (fx, fy, fr) in discs.items():
        if card[role] is not None:
            cx, cy, r = fx * W, fy * H, fr * W * 0.78
            out[role] = (cx - r, cy - r, cx + r, cy + r)
    return out


def card_texts(g: Geometry, card: dict[str, Any], fonts: Fonts) -> list[Placed]:
    scale = g.w / DEFAULT_WIDTH
    out = []
    for role, box in text_boxes(g, card).items():
        if role == "name":
            out.append(fit_text(role, card["name"], box, fonts.title, scale, False))
        elif role == "type":
            out.append(fit_text(role, _type_line(card), box, fonts.body, scale, False, 0.08))
        elif role == "desc":
            out.append(fit_text(role, card["description"], box, fonts.body, scale, True))
        else:
            out.append(fit_text(role, str(card[role]), box, fonts.number, scale, False, 0.09))
    return out


def contact_sheet(images: list[Image.Image], width: int = 300, cols: int = 4, gap: int = 18) -> Image.Image:
    h = round(width * images[0].height / images[0].width)
    cols = min(cols, len(images))
    rows = math.ceil(len(images) / cols)
    sheet = Image.new("RGBA", (cols * width + (cols + 1) * gap, rows * h + (rows + 1) * gap), (30, 30, 33, 255))
    for i, im in enumerate(images):
        x, y = gap + (i % cols) * (width + gap), gap + (i // cols) * (h + gap)
        sheet.alpha_composite(im.resize((width, h), Image.Resampling.LANCZOS), (x, y))
    return sheet


def _frac(box: tuple[float, float, float, float], g: Geometry) -> list[float]:
    x0, y0, x1, y1 = box
    return [round(x0 / g.w, 5), round(y0 / g.h, 5), round((x1 - x0) / g.w, 5), round((y1 - y0) / g.h, 5)]


def _parse_colors(raw: Any) -> list[tuple[int, int, int]]:
    from .rarity import parse_colors

    return parse_colors(raw)


def _covers_all(path: str, text: str) -> list[str]:
    from .localize import _has_glyph

    try:
        return sorted({c for c in text if not c.isspace() and not _has_glyph(path, c)})
    except OSError:
        return sorted({c for c in text if not c.isspace()})


def build_set(plan: dict[str, Any], arts: list[Image.Image], back_art: Image.Image | None, width: int,
              colors: list[tuple[int, int, int]], back_color: tuple[int, int, int], title_font: str = "auto",
              body_font: str = "auto") -> dict[str, Any]:
    cards = plan["cards"]
    g = Geometry(width, round(width * CARD_MM[1] / CARD_MM[0]))
    every = " ".join(f"{c['name']} {c['description']} {_type_line(c)}" for c in cards)
    fonts = pick_fonts(every, title_font, body_font)
    issues: list[dict[str, Any]] = []
    for role, path, txt in (("title", fonts.title, " ".join(c["name"] for c in cards)), ("body", fonts.body, every)):
        miss = _covers_all(path, txt) if path else sorted(set(txt) - {" "})
        if miss:
            issues.append({"card": "", "role": role, "kind": "missing_glyphs", "detail": "".join(miss[:40])})
    frames: dict[str, Image.Image] = {}
    fronts, crops, entries = [], [], []
    for i, card in enumerate(cards):
        key = frame_key(card)
        if key not in frames:
            frames[key] = draw_frame(g, card, colors)
        ab = g.art_box()
        aw, ah = round(ab[2] - ab[0]), round(ab[3] - ab[1])
        art = cover(arts[i] if i < len(arts) else placeholder_art(card), (aw, ah))
        front = Image.new("RGBA", (g.w, g.h), (0, 0, 0, 0))
        front.alpha_composite(art, (round(ab[0]), round(ab[1])))
        front.alpha_composite(frames[key])
        placed = card_texts(g, card, fonts)
        for p in placed:
            draw_text(front, p)
            if not p.ok:
                issues.append({"card": card["id"], "role": p.role, "kind": "text_overflow",
                               "detail": f"'{p.text}' cut at {p.size}px"})
        fronts.append(front)
        crops.append(art)
        entries.append({**card, "frame": key, "art_rect": _frac((round(ab[0]), round(ab[1]), round(ab[0]) + aw,
                                                                 round(ab[1]) + ah), g),
                        "texts": {p.role: {"text": p.text, "rect": _frac(p.box, g), "size": p.size,
                                           "lines": p.lines, "color": p.color, "stroke": p.stroke,
                                           "stroke_color": p.stroke_color, "align": p.align, "wrap": p.wrap,
                                           "font": {"name": "title", "desc": "body", "type": "body"}.get(p.role,
                                                                                                        "number"),
                                           "fits": p.ok, "ink": _frac(p.ink, g)} for p in placed}})
    back = draw_back(g, back_art if back_art is not None else placeholder_back(BACK_SIZE, back_color), back_color)
    return {"geometry": g, "fronts": fronts, "back": back, "frames": frames, "arts": crops, "entries": entries,
            "fonts": fonts, "issues": issues, "preview": contact_sheet([*fronts, back])}


async def _json_input(ctx: StepContext, ref: BlobRef) -> Any:
    try:
        return json.loads(await asyncio.to_thread(ctx.blobs.path(ref.hash).read_bytes))
    except (OSError, ValueError) as e:
        raise StepError("op_bad_input", f"'{ref.name}' is not JSON: {e}") from e


class CardSet(OpExecutor):
    kind = "op.card_set"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        if not inputs.get("plan"):
            raise StepError("op_no_input", "input 'plan' (card_plan.json) is empty")
        plan = await _json_input(ctx, inputs["plan"][0])
        if not isinstance(plan, dict) or plan.get("type") != PLAN_TYPE or not plan.get("cards"):
            raise StepError("op_bad_input", "plan must be a card_plan.json from op.card_plan")
        n = len(plan["cards"])
        art_refs = inputs.get("art") or []
        if art_refs and len(art_refs) != n:
            raise StepError("op_bad_input", f"{len(art_refs)} art images for {n} cards")
        width = int(params.get("width") or DEFAULT_WIDTH)
        if not 252 <= width <= 2016:
            raise StepError("op_bad_params", "width must be 252..2016 px")
        colors = _parse_colors(params.get("colors") or "auto")
        back_color = _rgb(str(params.get("back_color") or "#c9a24a")) if re.fullmatch(
            r"#?[0-9a-fA-F]{6}", str(params.get("back_color") or "#c9a24a").strip()) else _rgb("#c9a24a")
        arts = [await _load(ctx, r) for r in art_refs]
        back_refs = inputs.get("back") or []
        back_art = await _load(ctx, back_refs[0]) if back_refs else None
        ctx.progress(0, 3, label="render")
        res = await asyncio.to_thread(build_set, plan, arts, back_art, width, colors, back_color,
                                      str(params.get("title_font") or "auto"), str(params.get("body_font") or "auto"))
        ctx.progress(1, 3, label="atlas")
        g: Geometry = res["geometry"]
        fronts = [await _put_png(ctx, im, f"{e['id']}.png") for e, im in zip(res["entries"], res["fronts"])]
        back = await _put_png(ctx, res["back"], "card_back.png")
        frames = {k: await _put_png(ctx, im, f"{k}.png") for k, im in res["frames"].items()}
        arts_out = [await _put_png(ctx, im, f"art_{e['id']}.png") for e, im in zip(res["entries"], res["arts"])]
        atlas_refs, atlas_json = await self._atlas(ctx, res)
        ctx.progress(2, 3, label="json")
        cards = []
        for e, f, a in zip(res["entries"], fronts, arts_out):
            cards.append({**e, "front": {"file": f.name, "hash": f.hash},
                          "art_image": {"file": a.name, "hash": a.hash},
                          "frame_image": {"file": frames[e["frame"]].name, "hash": frames[e["frame"]].hash}})
        doc = {"type": SET_TYPE, "version": 1, "size": [g.w, g.h], "mm": list(CARD_MM),
               "ratio": round(g.h / g.w, 4), "art_mode": plan.get("art_mode"), "style": plan.get("style"),
               "rarities": [{"name": t, "color": "#%02x%02x%02x" % c} for t, c in zip(DEFAULT_TIERS, colors)],
               "fonts": res["fonts"].doc(), "back": {"file": back.name, "hash": back.hash},
               "frames": {k: {"file": r.name, "hash": r.hash} for k, r in frames.items()},
               "atlas": atlas_json, "cards": cards, "text_issues": res["issues"], "ok": not res["issues"]}
        set_ref = await ctx.blobs.put_bytes(json.dumps(doc, ensure_ascii=False, indent=1).encode("utf-8"),
                                            name=SET_FILE, mime="application/json")
        preview = await _put_png(ctx, res["preview"], "cards_preview.png")
        ctx.progress(3, 3)
        return StepResult(outputs={"images": [*fronts, back], "preview": [preview], "cards": [set_ref],
                                   "frames": list(frames.values()), "art": arts_out, "atlas": atlas_refs},
                          meta={"cards": n, "size": [g.w, g.h], "frames": len(frames),
                                "text_issues": len(res["issues"]), "art": plan.get("art_mode"),
                                "fonts": {k: v["file"] for k, v in doc["fonts"].items()}})

    async def _atlas(self, ctx: StepContext, res: dict[str, Any]) -> tuple[list[BlobRef], dict[str, Any]]:
        from ..export.atlas import AtlasOptions, Sprite, atlas_json, pack, page_file

        sprites = [Sprite(e["id"], im, (im.width / 2, im.height / 2)) for e, im in zip(res["entries"], res["fronts"])]
        sprites.append(Sprite("card_back", res["back"], (res["back"].width / 2, res["back"].height / 2)))
        atlas = await asyncio.to_thread(pack, sprites, "cards_atlas", AtlasOptions(max_size=4096, trim=False))
        pages = [await ctx.blobs.put_bytes(atlas.png[i], name=page_file(atlas, i), mime="image/png")
                 for i in range(len(atlas.pages))]
        data = atlas_json(atlas)
        ref = await ctx.blobs.put_bytes(data, name="cards_atlas.json", mime="application/json")
        return [*pages, ref], {"pages": [{"file": p.name, "hash": p.hash} for p in pages],
                               "json": {"file": ref.name, "hash": ref.hash}}


CARD_OPS: tuple[type[OpExecutor], ...] = (CardPlan, CardSet)
