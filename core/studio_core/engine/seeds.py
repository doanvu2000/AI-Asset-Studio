from __future__ import annotations

from typing import Any

SEED_USED = "seed_used"
# `$meta` key set per batch item: every step with a `seed` param runs with this value unless the run overrides it.
SEED_OVERRIDE_KEY = "seed_override"
# Step param holding one pin dict per item of a mapped step; the runner applies it per call and executors never see it.
ITEM_SEEDS = "_item_seeds"


def _num(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def raw_seeds(meta: dict[str, Any]) -> dict[str, int]:
    seeds = meta.get("seeds")
    if not isinstance(seeds, dict):
        return {}
    return {str(k): v for k, v in seeds.items() if _num(v) is not None}


def seed_used(params: dict[str, Any], meta: dict[str, Any]) -> int | None:
    """The seed that actually produced a step's output: whatever the executor drew (-1 / unset), else the fixed one.
    Mapped steps (`meta.items`) repeat the same keys per item, so they report nothing here."""
    for v in (meta.get(SEED_USED), meta.get("seed")):
        if _num(v) is not None:
            return v
    drawn = raw_seeds(meta)
    if drawn:
        return next(iter(drawn.values()))
    fixed = _num(params.get("seed"))
    return fixed if fixed is not None and fixed >= 0 else None


def _item_pins(params: dict[str, Any], items: Any) -> tuple[list[dict[str, Any]], bool]:
    pins: list[dict[str, Any]] = []
    missing = False
    for item in items if isinstance(items, list) else []:
        meta = item if isinstance(item, dict) else {}
        used = seed_used(params, meta)
        pin = {"seed": used} if "seed" in params and used is not None else {} if "seed" in params else raw_seeds(meta)
        missing = missing or ("seed" in params and used is None)
        pins.append(pin)
    return (pins if any(pins) else []), missing


def pin_overrides(params: dict[str, Any], meta: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Params that replay a finished step with the same seed -> (overrides, seed_unknown). Unknown = the step has a
    random seed param but nothing recorded which value it drew. Mapped steps draw per item, so they pin a list."""
    if "seed" in params:
        used = seed_used(params, meta)
        if used is not None:
            return {"seed": used}, False
        if meta.get("items"):
            pins, missing = _item_pins(params, meta["items"])
            return ({ITEM_SEEDS: pins} if pins else {}), missing or not pins
        return {}, _num(params["seed"]) is not None and params["seed"] < 0
    if raw := raw_seeds(meta):
        return dict(raw), False
    pins, _ = _item_pins(params, meta.get("items"))
    return ({ITEM_SEEDS: pins} if pins else {}), False


def override_seed_keys(kind: str, executor: Any, resolved: dict[str, Any]) -> list[str]:
    """Params through which a step takes its seed: ones the recipe already sets, the ComfyUI workflow's seed nodes,
    or a plain `seed` the kind catalog declares."""
    from .kinds import CATALOG

    keys = {k for k in resolved if k == "seed" or k.endswith(".seed")}
    entry = getattr(executor, "entry", None)
    if isinstance(entry, dict):
        keys.update(str(k) for k in entry.get("seeds") or [])
    if "seed" in ((CATALOG.get(kind) or {}).get("params") or {}):
        keys.add("seed")
    return sorted(keys)
