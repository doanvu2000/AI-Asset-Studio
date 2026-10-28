"""Follow-up runs: `params.$meta.then` lists runs to start once this run succeeds.

Each entry is `{recipe_id, inputs?, map, title?, then?}`; `map` feeds a blob input of the next recipe from this
run's published outputs (`outputs.<name>`, `outputs.<name>[i]`) or its own inputs (`inputs.<name>`). The list lives
in the run's params, so a pending chain survives a core restart along with the run; `chain_runs` is written back
once the follow-ups start, which also keeps a re-finished run from starting them twice.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from typing import Any

from ..contracts import StepRow
from .recipe import BLOB_TYPES, Recipe, RecipeError

CHAIN_KEY = "then"
CHAIN_RUNS = "chain_runs"
CHAIN_ERROR = "chain_error"
CHAIN_PARENT = "chain_parent"
MAX_CHAIN = 8
_SRC = re.compile(r"^(outputs|inputs)\.([A-Za-z_][\w-]*)(?:\[(-?\d+)\])?$")


def parse_chain(raw: Any, get_recipe: Callable[[str], Recipe], depth: int = 0) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_CHAIN:
        raise RecipeError(f"$meta.{CHAIN_KEY} must be a list of at most {MAX_CHAIN} follow-up runs")
    if depth >= MAX_CHAIN:
        raise RecipeError(f"$meta.{CHAIN_KEY} nests deeper than {MAX_CHAIN}")
    out: list[dict[str, Any]] = []
    for i, e in enumerate(raw):
        where = f"$meta.{CHAIN_KEY}[{i}]"
        if not isinstance(e, dict) or not isinstance(e.get("recipe_id"), str):
            raise RecipeError(f"{where}: needs a recipe_id")
        try:
            recipe = get_recipe(e["recipe_id"])
        except LookupError:
            raise RecipeError(f"{where}: unknown recipe {e['recipe_id']}") from None
        inputs, mapping = e.get("inputs") or {}, e.get("map") or {}
        if not isinstance(inputs, dict) or not isinstance(mapping, dict) or not mapping:
            raise RecipeError(f"{where}: inputs must be a mapping and map a non-empty mapping")
        for target, src in mapping.items():
            if recipe.inputs.get(target, {}).get("type") not in BLOB_TYPES:
                raise RecipeError(f"{where}: map target '{target}' is not a file input of {recipe.id}")
            if not isinstance(src, str) or not _SRC.match(src):
                raise RecipeError(f"{where}: map.{target} must be 'outputs.<name>[i]' or 'inputs.<name>', got {src!r}")
        entry = {"recipe_id": recipe.id, "inputs": inputs, "map": dict(mapping), "title": str(e.get("title") or "")}
        nested = parse_chain(e.get(CHAIN_KEY), get_recipe, depth + 1)
        if nested:
            entry[CHAIN_KEY] = nested
        out.append(entry)
    return out


def published_outputs(steps: Iterable[StepRow]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for s in sorted(steps, key=lambda s: s.order):
        for out_name, step_out in ((s.meta.get("spec") or {}).get("publish") or {}).items():
            out.setdefault(out_name, []).extend(b.hash for b in s.outputs.get(step_out, []))
    return out


def map_inputs(mapping: dict[str, str], outputs: dict[str, list[str]], inputs: dict[str, Any]) -> dict[str, Any]:
    """Blob values for the next run; a source with one blob passes a single hash, several stay a list."""
    out: dict[str, Any] = {}
    for target, src in mapping.items():
        m = _SRC.match(src)
        assert m is not None
        kind, name, idx = m.groups()
        if kind == "outputs":
            hashes = list(outputs.get(name) or [])
        else:
            v = inputs.get(name)
            hashes = list(v) if isinstance(v, list) else ([v] if v else [])
        if idx is not None:
            i = int(idx)
            hashes = [hashes[i]] if -len(hashes) <= i < len(hashes) else []
        if not hashes:
            raise ValueError(f"{src} is empty")
        out[target] = hashes[0] if len(hashes) == 1 else hashes
    return out
