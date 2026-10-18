from __future__ import annotations

from typing import Any

from ..contracts import BlobRef, ExecutorRegistry, GateResolution, Lane, Outputs, StepContext, StepError, StepResult

GATE_PREFIX = "gate."


def is_gate(kind: str) -> bool:
    return kind.startswith(GATE_PREFIX)


def gate_enabled(params: dict[str, Any]) -> bool:
    """`enabled: "${inputs.review}"` makes a gate optional; a disabled gate resolves itself without a human."""
    v = params.get("enabled", True)
    if isinstance(v, str):
        return v.strip().lower() not in ("false", "0", "no", "off", "")
    return v is None or bool(v)


class _Gate:
    lane = Lane.NONE
    version = "1"
    kind = ""

    def cacheable(self, params: dict[str, Any]) -> bool:
        return False

    def cache_material(self, params: dict[str, Any]) -> str:
        return ""

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        # gates are resolved by the engine (resolve_gate), so state survives a core restart
        raise StepError("gate_not_runnable", f"{self.kind} is resolved by the engine, not executed")

    def awaiting_data(self, params: dict[str, Any], inputs: Outputs) -> dict[str, Any]:
        raise NotImplementedError

    def resolve(self, params: dict[str, Any], inputs: Outputs, res: GateResolution) -> Outputs:
        raise NotImplementedError

    def auto_resolution(self, params: dict[str, Any], inputs: Outputs) -> GateResolution:
        raise NotImplementedError


class PickGate(_Gate):
    kind = "gate.pick"

    @staticmethod
    def candidates(inputs: Outputs) -> list[BlobRef]:
        out: list[BlobRef] = []
        seen: set[str] = set()
        for refs in inputs.values():
            for b in refs:
                if b.hash not in seen:
                    seen.add(b.hash)
                    out.append(b)
        return out

    def awaiting_data(self, params: dict[str, Any], inputs: Outputs) -> dict[str, Any]:
        return {"kind": "pick", "k": int(params.get("k", 1)),
                "candidates": [b.model_dump(mode="json") for b in self.candidates(inputs)]}

    def resolve(self, params: dict[str, Any], inputs: Outputs, res: GateResolution) -> Outputs:
        picked = res.picked or []
        k = int(params.get("k", 1))
        by_hash = {b.hash: b for b in self.candidates(inputs)}
        if not picked:
            raise ValueError("pick at least one image")
        if len(set(picked)) != len(picked):
            raise ValueError("duplicate picks")
        unknown = [h for h in picked if h not in by_hash]
        if unknown:
            raise ValueError(f"picked hashes are not candidates: {unknown}")
        if k and len(picked) != k:
            raise ValueError(f"expected exactly {k} picks, got {len(picked)}")
        return {"images": [by_hash[h] for h in picked]}

    def auto_resolution(self, params: dict[str, Any], inputs: Outputs) -> GateResolution:
        k = int(params.get("k", 1)) or 1
        return GateResolution(picked=[b.hash for b in self.candidates(inputs)[:k]])


class ApproveGate(_Gate):
    kind = "gate.approve"

    def awaiting_data(self, params: dict[str, Any], inputs: Outputs) -> dict[str, Any]:
        return {"kind": "approve", "k": 0, "candidates": [b.model_dump(mode="json") for b in PickGate.candidates(inputs)]}

    def resolve(self, params: dict[str, Any], inputs: Outputs, res: GateResolution) -> Outputs:
        if res.approved is None:
            raise ValueError("approved must be true or false")
        if not res.approved:
            raise StepError("rejected", "rejected by reviewer")
        return {k: list(v) for k, v in inputs.items()}

    def auto_resolution(self, params: dict[str, Any], inputs: Outputs) -> GateResolution:
        return GateResolution(approved=True)


GATES: dict[str, _Gate] = {g.kind: g for g in (PickGate(), ApproveGate())}


def register_gates(registry: ExecutorRegistry) -> None:
    for g in GATES.values():
        registry.register(g)  # type: ignore[arg-type]
