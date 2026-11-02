"""AI usage disclosure of a project or pack (Steam "AI generated content disclosure", Itch, Google Play, App Store),
built from the provenance entries `pack.asset_provenance` already produces."""
from __future__ import annotations

import time
from typing import Any

from studio_core import __version__

# Step kinds that call a generative model; everything else (op.*, gates, exports) is deterministic processing.
_AI_PREFIXES = ("comfy.", "codex.", "claude.", "tts.", "views.qwen", "mesh.checked")
_COMMERCIAL_LABEL = {"yes": "commercial use allowed", "restricted": "commercial use under conditions",
                     "unknown": "license unverified", "no": "non-commercial only"}


def is_ai_step(kind: str) -> bool:
    return kind.startswith(_AI_PREFIXES)


def _models(prov: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for p in prov:
        for m in p["models"]:
            seen.setdefault(m["name"], m)
    return sorted(seen.values(), key=lambda m: m["name"])


def license_warnings(prov: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for p in prov:
        bad = [m for m in p["models"] if m["commercial"] != "yes"]
        if not bad:
            continue
        detail = ", ".join(f"{m['name']} ({m['license']}, {_COMMERCIAL_LABEL.get(m['commercial'], m['commercial'])})"
                           for m in bad)
        out.append(f"{p['folder']}: check model license before shipping a commercial game: {detail}")
    return out


def disclosure(prov: list[dict[str, Any]]) -> dict[str, Any]:
    """Machine-readable block merged into provenance.json (additive; `format` stays asset-studio-provenance/1)."""
    ai = [p for p in prov if any(is_ai_step(g["kind"]) for g in p["generators"])]
    return {"pre_generated": bool(ai), "live_generated": False, "ai_assets": len(ai), "total_assets": len(prov),
            "human_approved": True, "models": [m["name"] for m in _models(prov)]}


def _asset_line(p: dict[str, Any]) -> str:
    ai_steps = sorted({g["kind"] for g in p["generators"] if is_ai_step(g["kind"])})
    other = sorted({g["kind"] for g in p["generators"] if not is_ai_step(g["kind"])})
    models = ", ".join(m["name"] for m in p["models"]) or "-"
    stage = ", ".join(ai_steps) if ai_steps else "no AI step"
    post = f"; post-processing: {', '.join(other)}" if other else ""
    return f"| {p['name']} | {p.get('recipe_id') or '-'} | {stage}{post} | {models} | {p['commercial']} |"


def build_ai_credits(project_name: str, prov: list[dict[str, Any]], version: str | None = None,
                     now: float | None = None) -> tuple[str, list[str]]:
    warnings = license_warnings(prov)
    d = disclosure(prov)
    date = time.strftime("%Y-%m-%d", time.localtime(now if now is not None else time.time()))
    title = f"{project_name} {version}".strip() if version else project_name
    lines = [f"# AI Credits: {title}", "", f"Generated {date} by AI Asset Studio {__version__}.", "",
             "## AI generated content disclosure", ""]
    if d["pre_generated"]:
        lines += [f"- **Pre-generated content:** yes. {d['ai_assets']} of {d['total_assets']} assets were created "
                  "during development with generative AI models listed below.",
                  "- **Live-generated content:** no. The shipped product does not call generative AI at runtime.",
                  "- **Human review:** every asset was reviewed and approved by a person in AI Asset Studio before "
                  "packing. Manual edits outside the tool are not tracked here; add them if applicable."]
    else:
        lines += ["- **Pre-generated content:** none of the packed assets used a generative AI step.",
                  "- **Live-generated content:** no."]
    models = _models(prov)
    lines += ["", "## Models used", ""]
    if models:
        lines += ["| Model | License | Commercial use | Source |", "|---|---|---|---|"]
        lines += [f"| {m['name']} | {m['license']} | {_COMMERCIAL_LABEL.get(m['commercial'], m['commercial'])} | "
                  f"{m.get('source') or '-'} |" for m in models]
    else:
        lines.append("No generative models.")
    lines += ["", "## Assets", ""]
    if prov:
        lines += ["| Asset | Recipe | Pipeline | Models | Commercial |", "|---|---|---|---|---|"]
        lines += [_asset_line(p) for p in prov]
    else:
        lines.append("No approved assets yet.")
    if warnings:
        lines += ["", "## License warnings", ""] + [f"- {w}" for w in warnings]
    lines += ["", "provenance.json lists the recipe, prompts and seeds behind every asset.", ""]
    return "\n".join(lines), warnings
