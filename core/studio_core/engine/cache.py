from __future__ import annotations

from typing import Any

import orjson
from blake3 import blake3

from ..contracts import Executor, Lane, Outputs
from ..store.stats import LIVE_COST

# Step params that steer how a step runs (timeouts, retries, VRAM housekeeping, the cache switch itself) but never
# what it outputs; keeping them in the key would split identical results. Anything not listed stays in the key.
NON_OUTPUT_PARAMS = frozenset({"cache", "_timeout_s", "_auto_retry", "_restart_before", "_free_before"})

# Spend lives in step meta; a cache hit must not report the original run's cost again.
_COST_KEYS = frozenset({"cost_usd", "claude_cost_usd", "prior_cost_usd", LIVE_COST, "auto_retries"})


def canonical_json(value: Any) -> bytes:
    return orjson.dumps(value, option=orjson.OPT_SORT_KEYS | orjson.OPT_NON_STR_KEYS)


def normalize_params(params: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in params.items() if k not in NON_OUTPUT_PARAMS}


def cache_key(kind: str, version: str, params: dict[str, Any], inputs: Outputs, material: str) -> str:
    # input order within a name is semantic (e.g. image index), so only names are sorted
    hashes = {name: [b.hash for b in refs] for name, refs in sorted(inputs.items())}
    h = blake3()
    for part in (kind.encode(), str(version).encode(), canonical_json(normalize_params(params)),
                 canonical_json(hashes), (material or "").encode()):
        h.update(part)
        h.update(b"|")
    return h.hexdigest()


def _cloud_material() -> str:
    # Steps that omit `model` follow DEFAULT_MODEL, and aliases map to pinned ids: re-pinning either must miss.
    from ..adapters.claude import DEFAULT_MODEL, MODEL_IDS

    return "claude:" + canonical_json({"default": DEFAULT_MODEL, "ids": MODEL_IDS}).decode()


def executor_cache_key(ex: Executor, kind: str, params: dict[str, Any], inputs: Outputs) -> str | None:
    if not ex.cacheable(params):
        return None
    material = ex.cache_material(params) or ""
    if ex.lane == Lane.CLOUD:
        material += "|" + _cloud_material()
    return cache_key(kind, ex.version, params, inputs, material)


def cacheable_meta(meta: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in meta.items() if k not in _COST_KEYS}
    items = out.get("items")
    if isinstance(items, list):
        out["items"] = [{k: v for k, v in i.items() if k not in _COST_KEYS} if isinstance(i, dict) else i
                        for i in items]
    return out
