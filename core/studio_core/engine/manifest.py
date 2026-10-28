"""Per-project asset list (manifest): rows of {name, recipe, inputs, notes}; status comes from the row's last run."""
from __future__ import annotations

import csv
import io
import json
import uuid
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from .recipe import BLOB_TYPES, RecipeError, coerce_value

MAX_ROWS = 500
RowStatus = Literal["todo", "running", "review", "approved", "failed"]
BASE_COLS = ["name", "recipe_id", "notes", "status", "run_id"]
INPUT_COL = "in:"


def new_row_id() -> str:
    return uuid.uuid4().hex[:12]


class ManifestRow(BaseModel):
    id: str = Field(default_factory=new_row_id, max_length=64)
    name: str = Field("", max_length=200)
    recipe_id: str = Field("", max_length=120)
    inputs: dict[str, Any] = {}
    notes: str = Field("", max_length=4000)
    run_id: str | None = None


def row_status(state: dict[str, Any] | None) -> RowStatus:
    """`state` = {status, approved} of the row's last run (repo.run_states); a cancelled run counts as not run."""
    if state is None:
        return "todo"
    match state["status"]:
        case "running":
            return "running"
        case "awaiting_human":
            return "review"
        case "failed" | "interrupted":
            return "failed"
        case "cancelled":
            return "todo"
    return "approved" if state.get("approved") else "review"


def _cell(v: Any) -> str:
    if v is None:
        return ""
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)


def _uncell(s: str) -> Any:
    s = s.strip()
    if s[:1] in "[{":
        try:
            return json.loads(s)
        except ValueError:
            pass
    return s


def to_csv(rows: list[ManifestRow], statuses: dict[str, str] | None = None) -> str:
    names = list(dict.fromkeys(k for r in rows for k in r.inputs))
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(BASE_COLS + [INPUT_COL + n for n in names])
    for r in rows:
        w.writerow([r.name, r.recipe_id, r.notes, (statuses or {}).get(r.id, ""), r.run_id or ""]
                   + [_cell(r.inputs.get(n)) for n in names])
    return buf.getvalue()


def from_csv(text: str) -> list[ManifestRow]:
    """Columns name, recipe_id, notes, run_id, `in:<input>` per input, or `inputs` as a JSON object; others ignored."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    out: list[ManifestRow] = []
    for rec in reader:
        rec = {(k or "").strip(): (v or "") for k, v in rec.items()}
        inputs: dict[str, Any] = {}
        if rec.get("inputs", "").strip():
            raw = _uncell(rec["inputs"])
            if not isinstance(raw, dict):
                raise ValueError(f"row {len(out) + 1}: 'inputs' must be a JSON object")
            inputs.update(raw)
        for k, v in rec.items():
            if k.startswith(INPUT_COL) and v.strip():
                inputs[k[len(INPUT_COL):].strip()] = _uncell(v)
        if not (rec.get("name", "").strip() or rec.get("recipe_id", "").strip() or inputs):
            continue
        out.append(ManifestRow(name=rec.get("name", "").strip(), recipe_id=rec.get("recipe_id", "").strip(),
                               notes=rec.get("notes", "").strip(), inputs=inputs,
                               run_id=rec.get("run_id", "").strip() or None))
    return out


def to_yaml(rows: list[ManifestRow]) -> str:
    docs = [r.model_dump(exclude={"id"}, exclude_none=True) for r in rows]
    return yaml.safe_dump({"rows": docs}, allow_unicode=True, sort_keys=False)


def from_yaml(text: str) -> list[ManifestRow]:
    doc = yaml.safe_load(text) or []
    items = doc.get("rows", []) if isinstance(doc, dict) else doc
    if not isinstance(items, list):
        raise ValueError("expected a list of rows or {rows: [...]}")
    out = []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            raise ValueError(f"row {i + 1}: expected a mapping")
        out.append(ManifestRow.model_validate({k: v for k, v in it.items() if k != "id"}))
    return out


def _coerce(name: str, spec: dict[str, Any], value: Any) -> Any:
    try:
        return coerce_value(name, spec, value)
    except RecipeError:
        # Claude sends every value as a string; numeric enum options need the parsed number.
        if not isinstance(value, str):
            raise
        return coerce_value(name, spec, json.loads(value))


def clean_inputs(specs: dict[str, dict[str, Any]], pairs: list[dict[str, Any]]) -> dict[str, Any]:
    """Model-proposed {name, value} pairs -> inputs the recipe accepts; unknown, file and invalid ones are dropped."""
    out: dict[str, Any] = {}
    for p in pairs:
        name, value = p.get("name"), p.get("value")
        spec = specs.get(name) if isinstance(name, str) else None
        if spec is None or spec.get("type") in BLOB_TYPES or value in (None, ""):
            continue
        try:
            out[name] = _coerce(name, spec, value)
        except (RecipeError, ValueError):
            continue
    return out


def rows_from_proposal(raw: Any, recipes: dict[str, dict[str, dict[str, Any]]],
                       limit: int) -> tuple[list[ManifestRow], list[str]]:
    """`recipes` = recipe id -> input specs. Returns (rows, notes about dropped rows)."""
    rows: list[ManifestRow] = []
    dropped: list[str] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()[:200]
        rid = str(item.get("recipe_id") or "").strip()
        if not name:
            continue
        if rid not in recipes:
            dropped.append(f"{name}: unknown recipe {rid!r}")
            continue
        if len(rows) >= limit:
            dropped.append(f"{name}: over the {limit}-row limit")
            continue
        pairs = item.get("inputs") if isinstance(item.get("inputs"), list) else []
        rows.append(ManifestRow(name=name, recipe_id=rid, notes=str(item.get("notes") or "").strip()[:4000],
                                inputs=clean_inputs(recipes[rid], [p for p in pairs if isinstance(p, dict)])))
    return rows, dropped
