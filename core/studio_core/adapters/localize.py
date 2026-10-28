"""`claude.localize`: an image with text (button, logo, sign, banner) -> text regions (box, container, font style) and
their translations per target language, each with a shorter fallback for tight boxes."""
from __future__ import annotations

import asyncio
import io
import json
import re
from typing import Any

from PIL import Image

from ..config import Settings
from ..contracts import Lane, Outputs, StepContext, StepError, StepResult
from .claude import DEFAULT_MODEL, QueryFn, ask, load_images, put_json, sdk_query

PLAN_TYPE = "image_localize_plan"
MAX_LANGS = 12
MAX_REGIONS = 24
# The API downsizes anything larger and the model then answers in the downsized pixel space.
SEND_MAX_SIDE = 1568
SEND_MAX_PIXELS = 1_150_000
WEIGHTS = ["regular", "bold", "black"]
ALIGNS = ["left", "center", "right"]
FAMILIES = ["sans", "serif", "display", "rounded", "handwritten", "mono"]

SYSTEM = (
    "You are a game UI localization specialist and typographer. You find every piece of rendered text in an image, "
    "measure where it sits and how it is styled, and translate it into game-ready UI copy: short, natural, the way a "
    "shipped game in that language would phrase it, never a literal word-for-word rendering."
)

PROMPT = """The image is {w}x{h} pixels; give every box as [x0, y0, x1, y1] integer pixels in that space
(x right, y down, x1/y1 exclusive). Find each separate text block (one box per block; a multi-line block is one
region with `lines` > 1).
{context}For each region return:
- `text`: the text exactly as written; `translate`: false for brand names, numbers, units or text that must stay.
- `bbox`: tight box around the glyph ink including outline and drop shadow.
- `container`: the shape that holds the text (button face, sign board, banner ribbon); the image bounds if none.
  Replacement text must stay inside it.
- `lines`, and `style`: `font_px` (cap-to-baseline height scaled to a full font size, approx px), `weight`
  ({weights}), `italic`, `family` ({families}), `uppercase` (true if written in capitals), `align` ({aligns}),
  `color` (#rrggbb fill; the dominant colour if it is a gradient), `stroke_color` (#rrggbb or "" if no outline),
  `stroke_px` (outline width px, 0 if none), `shadow_color` (#rrggbb or ""), `shadow_dx`, `shadow_dy` (px).
- `translations`: one entry per target language ({langs}) with `lang` (the code as given), `text` (natural
  translation, same casing convention as the source), `short` (a shorter variant for when the text overflows its
  container: abbreviate or rephrase, at most about as many characters as the source, same as `text` if it already is).
Also return `source_lang` (BCP-47 code of the source text)."""

_HEX = {"type": "string"}
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "source_lang": {"type": "string"},
        "regions": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "translate": {"type": "boolean"},
                "bbox": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4},
                "container": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4},
                "lines": {"type": "integer"},
                "style": {"type": "object", "properties": {
                    "font_px": {"type": "number"}, "weight": {"type": "string", "enum": WEIGHTS},
                    "italic": {"type": "boolean"}, "family": {"type": "string", "enum": FAMILIES},
                    "uppercase": {"type": "boolean"}, "align": {"type": "string", "enum": ALIGNS},
                    "color": _HEX, "stroke_color": _HEX, "stroke_px": {"type": "number"},
                    "shadow_color": _HEX, "shadow_dx": {"type": "number"}, "shadow_dy": {"type": "number"}},
                    "required": ["font_px", "weight", "align", "color", "stroke_color", "stroke_px"]},
                "translations": {"type": "array", "items": {
                    "type": "object",
                    "properties": {"lang": {"type": "string"}, "text": {"type": "string"},
                                   "short": {"type": "string"}},
                    "required": ["lang", "text", "short"]}},
            },
            "required": ["text", "translate", "bbox", "container", "lines", "style", "translations"],
        }},
    },
    "required": ["source_lang", "regions"],
}


def parse_languages(raw: Any) -> list[str]:
    items = raw if isinstance(raw, list) else re.split(r"[,;\s]+", str(raw or ""))
    out: list[str] = []
    for it in items:
        code = re.sub(r"[^A-Za-z0-9_-]", "", str(it)).replace("_", "-").strip("-")
        if code and code.lower() not in (c.lower() for c in out):
            out.append(code)
    if not out:
        raise StepError("op_bad_params", "no target languages given (e.g. 'vi, ja, zh-Hans, ko, de')")
    if len(out) > MAX_LANGS:
        raise StepError("op_bad_params", f"at most {MAX_LANGS} target languages, got {len(out)}")
    return out


def build_prompt(size: tuple[int, int], langs: list[str], context: str = "") -> str:
    ctx = f"Context from the designer (what this is, terms to keep): {context.strip()}\n" if context.strip() else ""
    return PROMPT.format(w=size[0], h=size[1], context=ctx, langs=", ".join(langs), weights="|".join(WEIGHTS),
                         families="|".join(FAMILIES), aligns="|".join(ALIGNS))


def send_scale(size: tuple[int, int]) -> float:
    w, h = size
    return min(1.0, SEND_MAX_SIDE / max(w, h), (SEND_MAX_PIXELS / max(w * h, 1)) ** 0.5)


def _send_image(data: bytes) -> tuple[bytes, tuple[int, int], float]:
    with Image.open(io.BytesIO(data)) as im:
        im.load()
        size = im.size
        s = send_scale(size)
        img = im.convert("RGBA")
    if s < 1.0:
        img = img.resize((max(1, round(size[0] * s)), max(1, round(size[1] * s))), Image.Resampling.LANCZOS)
    # Transparent pixels hide text colour and outline from the model: show it on a neutral mid grey.
    flat = Image.new("RGBA", img.size, (128, 128, 128, 255))
    flat.alpha_composite(img)
    buf = io.BytesIO()
    flat.convert("RGB").save(buf, "PNG")
    return buf.getvalue(), size, s


def _hex(raw: Any, default: str = "") -> str:
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", str(raw or "").strip())
    if m:
        return "#" + m.group(1).lower()
    m = re.fullmatch(r"#?([0-9a-fA-F]{3})", str(raw or "").strip())
    return "#" + "".join(c * 2 for c in m.group(1).lower()) if m else default


def _num(raw: Any, default: float = 0.0) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _box(raw: Any, size: tuple[int, int], scale: float) -> list[int] | None:
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(v) / scale for v in raw)
    except (TypeError, ValueError):
        return None
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    b = [max(0, round(x0)), max(0, round(y0)), min(size[0], round(x1)), min(size[1], round(y1))]
    return b if b[2] - b[0] >= 2 and b[3] - b[1] >= 2 else None


def _style(raw: Any, scale: float, box_h: int) -> dict[str, Any]:
    s = raw if isinstance(raw, dict) else {}
    font_px = _num(s.get("font_px"), 0) / scale
    stroke = max(0.0, _num(s.get("stroke_px"), 0) / scale)
    stroke_color = _hex(s.get("stroke_color"))
    shadow = _hex(s.get("shadow_color"))
    return {
        "font_px": round(font_px if font_px > 0 else box_h, 1),
        "weight": s.get("weight") if s.get("weight") in WEIGHTS else "regular",
        "italic": bool(s.get("italic")), "uppercase": bool(s.get("uppercase")),
        "family": s.get("family") if s.get("family") in FAMILIES else "sans",
        "align": s.get("align") if s.get("align") in ALIGNS else "center",
        "color": _hex(s.get("color"), "#ffffff"),
        "stroke_color": stroke_color, "stroke_px": round(stroke, 1) if stroke_color else 0.0,
        "shadow_color": shadow,
        "shadow_dx": round(_num(s.get("shadow_dx")) / scale, 1) if shadow else 0.0,
        "shadow_dy": round(_num(s.get("shadow_dy")) / scale, 1) if shadow else 0.0,
    }


def normalize_plan(raw: Any, langs: list[str], size: tuple[int, int], scale: float = 1.0,
                   context: str = "") -> dict[str, Any]:
    """Model reply (boxes in the sent image's pixels) -> plan in source-image pixels, one translation per language;
    a missing translation keeps the source text and is listed under `missing`."""
    if not isinstance(raw, dict) or not isinstance(raw.get("regions"), list):
        raise StepError("claude_bad_output", "localize reply has no regions", retryable=True)
    regions, missing = [], []
    for r in raw["regions"][:MAX_REGIONS]:
        if not isinstance(r, dict) or not str(r.get("text") or "").strip():
            continue
        bbox = _box(r.get("bbox"), size, scale)
        if bbox is None:
            continue
        cont = _box(r.get("container"), size, scale) or [0, 0, size[0], size[1]]
        # The container must hold the text it was measured around.
        cont = [min(cont[0], bbox[0]), min(cont[1], bbox[1]), max(cont[2], bbox[2]), max(cont[3], bbox[3])]
        text = str(r["text"]).strip()
        got = {str(t.get("lang") or "").lower(): t for t in r.get("translations") or [] if isinstance(t, dict)}
        trans = {}
        for lang in langs:
            t = got.get(lang.lower())
            full = str((t or {}).get("text") or "").strip()
            if not full and r.get("translate", True):
                missing.append(f"{lang}: {text}")
            full = full or text
            short = str((t or {}).get("short") or "").strip() or full
            trans[lang] = {"text": full, "short": short if len(short) <= len(full) else full}
        idx = len(regions)
        regions.append({"id": f"r{idx}", "text": text, "translate": r.get("translate") is not False,
                        "bbox": bbox, "container": cont, "lines": max(1, min(6, int(_num(r.get("lines"), 1)))),
                        "style": _style(r.get("style"), scale, bbox[3] - bbox[1]), "translations": trans})
    if not regions:
        raise StepError("claude_bad_output", "no text regions found in the image", retryable=True,
                        detail={"reply": raw})
    return {"type": PLAN_TYPE, "source_lang": str(raw.get("source_lang") or "").strip() or "und",
            "canvas": list(size), "languages": langs, "context": context.strip(), "regions": regions,
            "missing": missing}


class ClaudeLocalizeExecutor:
    kind = "claude.localize"
    lane = Lane.CLOUD
    version = "1"

    def __init__(self, settings: Settings | None = None, query: QueryFn = sdk_query):
        self.settings = settings
        self.query = query

    def cacheable(self, params: dict[str, Any]) -> bool:
        return bool(params.get("cache"))

    def cache_material(self, params: dict[str, Any]) -> str:
        return SYSTEM + PROMPT + json.dumps(SCHEMA, sort_keys=True)

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        images = await load_images(ctx, inputs.get("images", []))
        if not images:
            raise StepError("op_no_input", "input 'images' (image with text) is empty")
        langs = parse_languages(params.get("languages"))
        context = str(params.get("context") or "")
        try:
            data, size, scale = await asyncio.to_thread(_send_image, images[0][0])
        except OSError as e:
            raise StepError("op_bad_image", str(e)) from e
        sent = (max(1, round(size[0] * scale)), max(1, round(size[1] * scale)))
        q = {"model": params.get("model") or DEFAULT_MODEL, "max_turns": 2, "system": SYSTEM, "schema": SCHEMA,
             "_timeout_s": params.get("_timeout_s")}
        await asyncio.to_thread(ctx.job_dir.mkdir, parents=True, exist_ok=True)
        ctx.progress(0, 1, "localize")
        reply = await ask(self.query, q, build_prompt(sent, langs, context), str(ctx.job_dir),
                          [(data, "image/png")], ctx=ctx)
        plan = normalize_plan(reply.structured, langs, size, scale, context)
        if plan["missing"]:
            ctx.log(f"{len(plan['missing'])} translations missing, source text kept: "
                    + "; ".join(plan["missing"][:6]), "warning")
        summary = "\n".join(f"[{r['id']}] {r['text']} -> " + " | ".join(f"{k}: {v['text']}"
                                                                         for k, v in r["translations"].items())
                            for r in plan["regions"])
        outputs = {"json": [await put_json(ctx, plan, "localize_plan.json")],
                   "text": [await ctx.blobs.put_bytes(summary.encode("utf-8"), name="localize_plan.txt",
                                                      mime="text/plain")]}
        ctx.progress(1, 1, "localize")
        return StepResult(outputs=outputs, meta={**reply.meta, "regions": len(plan["regions"]), "languages": langs,
                                                 "source_lang": plan["source_lang"]})
