from __future__ import annotations

import yaml

from studio_core.engine.library import merge_text

PREV = """id: r
# keep me
params: {flag: true, n: 1, f: 1.50, s: 'q'}
"""


def test_merge_text_keeps_comments_and_scalar_formatting() -> None:
    out = merge_text(PREV, {"id": "r", "params": {"flag": True, "n": 1, "f": 1.5, "s": "q"}})
    assert "# keep me" in out and "1.50" in out and "'q'" in out


def test_merge_text_bool_int_swaps_are_written() -> None:
    out = yaml.safe_load(merge_text(PREV, {"id": "r", "params": {"flag": 1, "n": True, "f": 1.5, "s": "q"}}))
    assert out["params"]["flag"] == 1 and type(out["params"]["flag"]) is int
    assert out["params"]["n"] is True
