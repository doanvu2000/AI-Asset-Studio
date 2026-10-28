"""`claude.npc_dialogue`: an NPC's barks and quest lines written in its own voice, one id per line with the text in
every requested language and the portrait expression to show. A lines CSV (id,lang,text,expression) replaces Claude."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import re
from typing import Any

from ..config import Settings
from ..contracts import Lane, Outputs, StepContext, StepError, StepResult
from .claude import DEFAULT_MODEL, QueryFn, ask, load_images, put_json, sdk_query

DIALOGUE_TYPE = "npc_dialogue"
CATEGORIES = ["greeting", "idle", "quest_offer", "quest_accept", "quest_decline", "farewell"]
DEFAULT_EXPRESSIONS = ["neutral", "happy", "sad", "angry", "surprised", "thinking", "scared", "smug"]
LANG_NAMES = {"vi": "Vietnamese", "en": "English", "ja": "Japanese", "zh": "Chinese", "ko": "Korean",
              "fr": "French", "es": "Spanish", "de": "German"}

SYSTEM = (
    "You are the narrative designer of a game studio. You write short spoken NPC lines that sound like one specific "
    "character: word choice, rhythm and attitude follow the persona. Lines are meant to be voiced by text-to-speech: "
    "plain spoken sentences, numbers written as words, no emoji, no stage directions, no markup, at most two short "
    "sentences each. Every language version is written natively for that language (not a literal translation) and "
    "keeps the same meaning and tone; Vietnamese uses natural pronouns for the character (ta/ngươi, tôi/bạn, "
    "lão/cháu...).")

PROMPT = """Write the dialogue lines of this NPC.
Name: {name}
Persona / role: {persona}
{image}Languages: {langs}
Return:
- `voice`: `gender` (male | female), `age` (young | adult | old), `speed` (0.85-1.15 speaking rate that fits the
  character), `style` (a few words describing how they sound).
- `lines`: exactly {n} lines covering every category at least once: {cats}. `idle` lines are ambient barks the NPC
  says on its own; quest lines offer, react to acceptance and react to refusal of the same quest. Each line has its
  `category`, the portrait `expression` that fits it (one of: {exprs}) and `texts`: one entry per language
  (`lang` code, `text`)."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "voice": {"type": "object", "properties": {"gender": {"type": "string"}, "age": {"type": "string"},
                                                   "speed": {"type": "number"}, "style": {"type": "string"}},
                  "required": ["gender", "age", "speed", "style"]},
        "lines": {"type": "array", "items": {
            "type": "object",
            "properties": {"category": {"type": "string", "enum": CATEGORIES},
                           "expression": {"type": "string"},
                           "texts": {"type": "array", "items": {
                               "type": "object", "properties": {"lang": {"type": "string"}, "text": {"type": "string"}},
                               "required": ["lang", "text"]}}},
            "required": ["category", "expression", "texts"]}},
    },
    "required": ["voice", "lines"],
}


def slug(text: Any, fallback: str = "line") -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")[:40] or fallback


def parse_languages(raw: Any) -> list[str]:
    out: list[str] = []
    for part in re.split(r"[,\s;]+", str(raw or "")):
        code = part.strip().lower()[:5]
        if code and code not in out:
            out.append(code)
    return out or ["vi", "en"]


def parse_expressions(raw: Any) -> list[str]:
    """`happy, a warm smile…` lines → names (text before the first comma); neutral always comes first."""
    names = [slug(ln.split(",")[0], "") for ln in str(raw or "").splitlines() if ln.strip()]
    names = [n for n in dict.fromkeys(names) if n]
    return ["neutral", *[n for n in names if n != "neutral"]]


def _expr(raw: Any, allowed: list[str]) -> str:
    e = slug(raw, "neutral")
    return e if e in allowed else "neutral"


def normalize_dialogue(raw: Any, *, name: str, persona: str, langs: list[str], expressions: list[str],
                       count: int) -> dict[str, Any]:
    if not isinstance(raw, dict) or not isinstance(raw.get("lines"), list):
        raise StepError("claude_bad_output", "dialogue reply has no lines", retryable=True)
    counters: dict[str, int] = {}
    lines = []
    for item in raw["lines"]:
        if not isinstance(item, dict):
            continue
        texts = {str(t.get("lang", "")).lower(): " ".join(str(t.get("text") or "").split())
                 for t in item.get("texts") or [] if isinstance(t, dict)}
        texts = {lang: texts[lang] for lang in langs if texts.get(lang)}
        if len(texts) != len(langs):
            continue
        cat = item.get("category") if item.get("category") in CATEGORIES else "idle"
        counters[cat] = counters.get(cat, 0) + 1
        lines.append({"id": f"{cat}_{counters[cat]:02d}", "category": cat,
                      "expression": _expr(item.get("expression"), expressions), "text": texts})
    lines = lines[:max(count, len(CATEGORIES))]
    if not lines:
        raise StepError("claude_bad_output", "dialogue has no line in every language", retryable=True,
                        detail={"reply": raw})
    voice = raw.get("voice") if isinstance(raw.get("voice"), dict) else {}
    try:
        speed = round(min(1.15, max(0.85, float(voice.get("speed") or 1.0))), 2)
    except (TypeError, ValueError):
        speed = 1.0
    return {"type": DIALOGUE_TYPE, "version": 1, "name": name, "persona": persona, "languages": langs,
            "expressions": expressions,
            "voice": {"gender": str(voice.get("gender") or "male").lower(), "age": str(voice.get("age") or "adult"),
                      "speed": speed, "style": str(voice.get("style") or "")},
            "lines": lines}


def parse_lines_csv(data: bytes, *, name: str = "npc", persona: str = "") -> dict[str, Any]:
    """id,lang,text,expression[,category] rows (header required, any column order) → a dialogue doc."""
    text = data.decode("utf-8-sig", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:2048], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.DictReader(io.StringIO(text), dialect=dialect))
    if not rows or not {"id", "lang", "text"} <= {(k or "").strip().lower() for k in rows[0]}:
        raise StepError("op_bad_input", "lines CSV needs a header with id, lang, text (and optionally expression, "
                                        "category)")
    by_id: dict[str, dict[str, Any]] = {}
    langs: list[str] = []
    exprs = ["neutral"]
    for r in rows:
        r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
        lid, lang, body = slug(r.get("id"), ""), r.get("lang", "").lower(), " ".join(r.get("text", "").split())
        if not lid or not lang or not body:
            continue
        if lang not in langs:
            langs.append(lang)
        cat = r.get("category") or next((c for c in CATEGORIES if lid.startswith(c)), "line")
        line = by_id.setdefault(lid, {"id": lid, "category": slug(cat, "line"), "expression": "neutral", "text": {}})
        if r.get("expression"):
            line["expression"] = slug(r["expression"], "neutral")
            if line["expression"] not in exprs:
                exprs.append(line["expression"])
        line["text"][lang] = body
    if not by_id:
        raise StepError("op_bad_input", "lines CSV has no usable row")
    return {"type": DIALOGUE_TYPE, "version": 1, "name": name, "persona": persona, "languages": langs,
            "expressions": exprs, "voice": {"gender": "male", "age": "adult", "speed": 1.0, "style": ""},
            "lines": list(by_id.values()), "source": "csv"}


def lines_csv(doc: dict[str, Any]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["id", "lang", "text", "expression", "category"])
    for line in doc.get("lines") or []:
        for lang in doc.get("languages") or []:
            if (line.get("text") or {}).get(lang):
                w.writerow([line["id"], lang, line["text"][lang], line.get("expression", ""), line.get("category", "")])
    return buf.getvalue().encode("utf-8-sig")


def summary(doc: dict[str, Any]) -> str:
    v = doc.get("voice") or {}
    head = f"{doc.get('name')} ({v.get('gender')}, {v.get('age')}, x{v.get('speed')}) {v.get('style', '')}".strip()
    rows = [f"[{ln['id']} · {ln['expression']}] " + " | ".join(f"{k}: {t}" for k, t in ln["text"].items())
            for ln in doc.get("lines") or []]
    return "\n".join([head, *rows])


async def put_dialogue(ctx: StepContext, doc: dict[str, Any]) -> dict[str, list[Any]]:
    return {"json": [await put_json(ctx, doc, "npc_dialogue.json")],
            "text": [await ctx.blobs.put_bytes(summary(doc).encode("utf-8"), name="npc_dialogue.txt",
                                               mime="text/plain")],
            "csv": [await ctx.blobs.put_bytes(lines_csv(doc), name="npc_lines.csv", mime="text/csv")]}


class ClaudeNpcDialogueExecutor:
    kind = "claude.npc_dialogue"
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
        name = str(params.get("name") or "").strip() or "NPC"
        persona = str(params.get("persona") or "").strip()
        expressions = parse_expressions(params.get("expressions"))
        csv_refs = inputs.get("csv") or []
        if csv_refs:
            data = await asyncio.to_thread(ctx.blobs.path(csv_refs[0].hash).read_bytes)
            doc = parse_lines_csv(data, name=name, persona=persona)
            return StepResult(outputs=await put_dialogue(ctx, doc), meta={"lines": len(doc["lines"]), "source": "csv"})
        langs = parse_languages(params.get("languages"))
        count = max(len(CATEGORIES), min(60, int(params.get("line_count", 12))))
        images = await load_images(ctx, inputs.get("images", []))
        image = "The attached image shows the NPC; let its look inform the voice.\n" if images else ""
        prompt = PROMPT.format(name=name, persona=persona or "(invent one that fits the image)", image=image,
                               langs=", ".join(f"{c} ({LANG_NAMES.get(c, c)})" for c in langs), n=count,
                               cats=", ".join(CATEGORIES), exprs=", ".join(expressions))
        q = {"model": params.get("model") or DEFAULT_MODEL, "max_turns": 2, "system": SYSTEM, "schema": SCHEMA,
             "_timeout_s": params.get("_timeout_s")}
        await asyncio.to_thread(ctx.job_dir.mkdir, parents=True, exist_ok=True)
        ctx.progress(0, 1, "dialogue")
        reply = await ask(self.query, q, prompt, str(ctx.job_dir), images[:1], ctx=ctx)
        doc = normalize_dialogue(reply.structured, name=name, persona=persona, langs=langs, expressions=expressions,
                                 count=count)
        ctx.progress(1, 1, "dialogue")
        return StepResult(outputs=await put_dialogue(ctx, doc),
                          meta={**reply.meta, "lines": len(doc["lines"]), "voice": doc["voice"]})
