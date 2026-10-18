from __future__ import annotations

from typing import Any

import orjson
from blake3 import blake3

from ..contracts import Executor, Outputs


def canonical_json(value: Any) -> bytes:
    return orjson.dumps(value, option=orjson.OPT_SORT_KEYS | orjson.OPT_NON_STR_KEYS)


def cache_key(kind: str, version: str, params: dict[str, Any], inputs: Outputs, material: str) -> str:
    # input order within a name is semantic (e.g. image index), so only names are sorted
    hashes = {name: [b.hash for b in refs] for name, refs in sorted(inputs.items())}
    h = blake3()
    for part in (kind.encode(), str(version).encode(), canonical_json(params), canonical_json(hashes),
                 (material or "").encode()):
        h.update(part)
        h.update(b"|")
    return h.hexdigest()


def executor_cache_key(ex: Executor, kind: str, params: dict[str, Any], inputs: Outputs) -> str | None:
    if not ex.cacheable(params):
        return None
    return cache_key(kind, ex.version, params, inputs, ex.cache_material(params))
