"""`claude.card_list`: Claude designs a small trading card set from a theme: names, rules text, cost/attack/health,
rarity and type in the requested language, plus an English art prompt per card. op.card_plan reads the result like a
CSV / JSON card list."""
from __future__ import annotations

import asyncio
from typing import Any

from ..config import Settings
from ..contracts import Lane, Outputs, StepContext, StepError, StepResult, cache_enabled
from ..ops.card_set import LIST_TYPE, MAX_CARDS
from ..ops.rarity import DEFAULT_TIERS
from .claude import DEFAULT_MODEL, QueryFn, ask, load_images, put_json, sdk_query

# Rarity as printed on the card; the schema keeps the English tier so op.card_plan always maps it.
TIER_NAMES = {"vi": ("Thường", "Không phổ biến", "Hiếm", "Sử thi", "Huyền thoại")}
LANG_NAMES = {"vi": "Vietnamese", "en": "English", "ja": "Japanese", "zh": "Chinese", "ko": "Korean",
              "fr": "French", "es": "Spanish", "de": "German"}

SYSTEM = (
    "You are the lead designer of a collectible card game. You write tight, readable card rules in the game's "
    "language with consistent keywords, balanced costs and stats, and evocative short names. Card text is printed on "
    "a small card, so it stays short. Vietnamese text uses correct diacritics and natural game wording (Khiêu khích, "
    "Rút một thẻ, Khi ra sân...).")

PROMPT = """Design a set of exactly {n} cards for this card game.
Theme / setting: {theme}
Card text language: {lang}
Card types to use (write them exactly like this): {types}
Design notes: {notes}
{image}Return:
- `set_name`: a short name for the set (card language).
- `cards`: exactly {n} cards; each has
  - `name`: at most 24 characters, card language;
  - `description`: rules text, at most 110 characters, card language; keywords stay consistent across cards;
  - `cost`: 0-10; `attack` and `health`: 0-12 for units, -1 for cards that have none (spells, items, traps);
  - `rarity`: one of {tiers}; spread them: mostly common/uncommon, at most one legendary per 6 cards;
  - `type`: one of the card types above;
  - `art`: an English illustration prompt (15-35 words): the subject, its pose or action, the setting and the mood;
    no text, no card frame, no style words (the art style is added later);
  - `flavor`: an optional one-line flavour quote (card language), may be empty."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "set_name": {"type": "string"},
        "cards": {"type": "array", "items": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "description": {"type": "string"},
                           "cost": {"type": "integer"}, "attack": {"type": "integer"}, "health": {"type": "integer"},
                           "rarity": {"type": "string", "enum": list(DEFAULT_TIERS)}, "type": {"type": "string"},
                           "art": {"type": "string"}, "flavor": {"type": "string"}},
            "required": ["name", "description", "cost", "attack", "health", "rarity", "type", "art"]}},
    },
    "required": ["set_name", "cards"],
}


def build_prompt(theme: str, count: int, lang: str, types: str, notes: str, has_image: bool) -> str:
    return PROMPT.format(n=count, theme=theme or "(none — a classic fantasy card game)",
                         lang=f"{LANG_NAMES.get(lang, lang)} ({lang})", types=types or "creature, spell, item",
                         notes=notes or "(none)", tiers=", ".join(DEFAULT_TIERS),
                         image="The attached image shows the game's art style and world.\n" if has_image else "")


def to_card_list(raw: Any, count: int, lang: str) -> dict[str, Any]:
    cards = raw.get("cards") if isinstance(raw, dict) else None
    if not isinstance(cards, list) or not cards:
        raise StepError("claude_bad_output", "card list has no cards", retryable=True, detail={"reply": raw})
    out = []
    for c in cards[:count]:
        if not isinstance(c, dict) or not str(c.get("name") or "").strip():
            continue
        row = {k: c.get(k) for k in ("name", "description", "cost", "attack", "health", "rarity", "type", "art",
                                     "flavor")}
        names = TIER_NAMES.get(lang)
        if names and row["rarity"] in DEFAULT_TIERS:
            row["rarity"] = names[DEFAULT_TIERS.index(row["rarity"])]
        for k in ("attack", "health"):
            if not isinstance(row[k], int) or row[k] < 0:
                row[k] = None
        out.append(row)
    if not out:
        raise StepError("claude_bad_output", "no usable cards in the reply", retryable=True, detail={"reply": raw})
    return {"type": LIST_TYPE, "set_name": str(raw.get("set_name") or ""), "language": lang, "cards": out}


class ClaudeCardListExecutor:
    kind = "claude.card_list"
    lane = Lane.CLOUD
    version = "2"

    def __init__(self, settings: Settings | None = None, query: QueryFn = sdk_query):
        self.settings = settings
        self.query = query

    def cacheable(self, params: dict[str, Any]) -> bool:
        return cache_enabled(params)

    def cache_material(self, params: dict[str, Any]) -> str:
        return SYSTEM + PROMPT

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        try:
            count = int(params.get("count") or 6)
        except (TypeError, ValueError):
            count = 6
        if not 1 <= count <= MAX_CARDS:
            raise StepError("op_bad_params", f"count must be 1..{MAX_CARDS}")
        lang = str(params.get("language") or "vi").strip().lower() or "vi"
        theme = " ".join(str(params.get("theme") or "").split())
        images = await load_images(ctx, (inputs.get("images") or [])[:1])
        q = {"model": params.get("model") or DEFAULT_MODEL, "max_turns": 2, "system": SYSTEM, "schema": SCHEMA,
             "_timeout_s": params.get("_timeout_s")}
        await asyncio.to_thread(ctx.job_dir.mkdir, parents=True, exist_ok=True)
        ctx.progress(0, 1, "cards")
        prompt = build_prompt(theme, count, lang, " ".join(str(params.get("types") or "").split()),
                              " ".join(str(params.get("notes") or "").split()), bool(images))
        reply = await ask(self.query, q, prompt, str(ctx.job_dir), images, ctx=ctx)
        doc = to_card_list(reply.structured, count, lang)
        ctx.progress(1, 1, "cards")
        summary = f"{doc['set_name']}: " + ", ".join(f"{c['name']} ({c['rarity']})" for c in doc["cards"])
        return StepResult(outputs={"json": [await put_json(ctx, doc, "card_list.json")],
                                   "text": [await ctx.blobs.put_bytes(summary.encode("utf-8"), name="card_list.txt",
                                                                      mime="text/plain")]},
                          meta={**reply.meta, "cards": len(doc["cards"])})
