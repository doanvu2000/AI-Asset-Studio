from __future__ import annotations

import math
from typing import Any

from test_r6_spine_physics import auto_rig

from studio_core.ops import spine_physics as ph


def run_doc(stem: str, n: int, length: float = 40.0) -> dict[str, Any]:
    bones: list[dict[str, Any]] = [{"name": "root"}, {"name": "torso", "parent": "root", "length": 50}]
    for k in range(1, n + 1):
        bones.append({"name": f"{stem}{k}", "parent": "torso" if k == 1 else f"{stem}{k - 1}", "length": length})
    return {"skeleton": {"spine": "4.2.43"}, "bones": bones}


def chain_constraints(stem: str, n: int) -> list[dict[str, Any]]:
    new, rep = ph.add_physics(run_doc(stem, n), mesh=False)
    assert [c["bones"] for c in rep["chains"]] == [[f"{stem}{k}" for k in range(1, n + 1)]]
    assert ph.validate_physics(new) == []
    return new["physics"]


def test_cloth_damping_strength_and_limit_tighten_along_the_chain() -> None:
    cs = chain_constraints("cape", 4)
    for a, b in zip(cs, cs[1:]):
        assert b["damping"] < a["damping"] and b["strength"] >= a["strength"]
        assert b["inertia"] <= a["inertia"] and b["limit"] <= a["limit"]
    for a, b in zip(cs[1:], cs[2:]):
        assert b["strength"] > a["strength"] and b["inertia"] < a["inertia"] and b["limit"] < a["limit"]
    assert cs[3]["damping"] < 0.9 and cs[3]["strength"] > 50 and cs[3]["limit"] < 0.75 * cs[0]["limit"]


def test_short_cloth_chains_keep_the_walk_tuned_values() -> None:
    cloth = ph.PRESETS["cloth"]
    one = chain_constraints("cape", 1)[0]
    assert (one["inertia"], one["strength"], one["damping"]) == (cloth["inertia"], cloth["strength"], cloth["damping"])
    a, b = chain_constraints("cape", 2)
    assert (a["strength"], b["strength"]) == (cloth["strength"], cloth["strength"])
    assert (b["inertia"], b["damping"]) == (cloth["inertia"], round(cloth["damping"] - ph.LINK_DAMPING, 4))


def test_hair_and_other_kinds_are_not_tapered() -> None:
    for stem, kind in (("hair", "hair"), ("tail", "tail"), ("rope", "rope")):
        p = ph.PRESETS[kind]
        cs = chain_constraints(stem, 5)
        assert [c["strength"] for c in cs] == [p["strength"]] * 5
        assert [c["inertia"] for c in cs] == [p["inertia"]] * 5
        assert [c["limit"] for c in cs] == [5000] * 5
        assert [c["damping"] for c in cs] == [round(max(0.5, p["damping"] - ph.LINK_DAMPING * j), 4)
                                              for j in range(5)]


def test_very_long_cloth_chain_stays_in_the_spine_domain() -> None:
    cs = chain_constraints("cape", 16)
    for c in cs:
        assert 0 <= c["inertia"] <= 1 and 0.5 <= c["damping"] < 1 and c["strength"] > 0 and c["limit"] > 0
        assert c["mass"] > 0 and math.isfinite(c["strength"])
    assert [c["damping"] for c in cs] == sorted((c["damping"] for c in cs), reverse=True)
    assert [c["strength"] for c in cs] == sorted(c["strength"] for c in cs)
    assert cs[-1]["inertia"] == ph.CLOTH_INERTIA_MIN and cs[-1]["damping"] == 0.5


def test_strength_param_scales_but_keeps_the_taper() -> None:
    new, _ = ph.add_physics(run_doc("cape", 4), mesh=False, strength=2.0)
    base = chain_constraints("cape", 4)
    for c, b in zip(new["physics"], base):
        assert c["strength"] == round(b["strength"] * 2, 3) and c["damping"] == b["damping"]


def test_existing_physics_constraints_are_left_alone() -> None:
    doc = run_doc("cape", 3)
    mine = {"name": "mine", "bone": "torso", "rotate": 1, "inertia": 0.3, "strength": 7, "damping": 0.99, "limit": 12}
    doc["physics"] = [dict(mine)]
    new, _ = ph.add_physics(doc, mesh=False)
    assert new["physics"][0] == mine and len(new["physics"]) == 4


def test_meshed_cape_chain_of_four_gets_the_taper() -> None:
    new, rep = ph.add_physics(auto_rig(), segments=4)
    cs = new["physics"]
    assert len(cs) == 4 == rep["constraints"] and ph.validate_physics(new) == []
    lengths = {b["name"]: float(b.get("length", 0) or 0) for b in new["bones"]}
    assert cs[0]["limit"] == round(ph.CLOTH_LIMIT_LENGTHS * lengths[cs[0]["bone"]], 3)
    assert cs[0]["damping"] > cs[1]["damping"] > cs[2]["damping"] > cs[3]["damping"]


def envelope_seconds(c: dict[str, Any], from_deg: float = 70.0, to_deg: float = 1.0) -> float:
    """Time for an isolated link's swing to fall from `from_deg` to `to_deg`. Spine multiplies the rotate velocity by
    damping ** (60 * step) every step (step 1/60 s), which shrinks an underdamped spring's amplitude by
    damping ** 30 per second."""
    return math.log(from_deg / to_deg) / (-30 * math.log(c["damping"]))


def test_isolated_link_settles_inside_three_seconds() -> None:
    # Chained links ring longer than one alone (WP-10 measured 5-7 s for the 4-bone cape at the old values), so the
    # chain tip must be well inside the bound, not just at it.
    cs = chain_constraints("cape", 4)
    assert all(envelope_seconds(c) < 3.0 for c in cs)
    assert envelope_seconds(cs[3]) < 0.7 * envelope_seconds(cs[0])
