from __future__ import annotations

from pathlib import Path

import pytest

from studio_core.ops import executors as ex


def _pkg(root: Path, files: dict[str, str]) -> str:
    for name, src in files.items():
        (root / f"{name}.py").write_text(src, encoding="utf-8")
    return str(root)


@pytest.fixture(autouse=True)
def _clear() -> None:
    ex._scoped_hash.cache_clear()


def _h(d: str, name: str) -> str:
    ex._scoped_hash.cache_clear()
    return ex._scoped_hash(d, name)


def test_editing_one_op_only_changes_itself_and_dependents(tmp_path: Path) -> None:
    files = {
        "__init__": "from .a import A\n",
        "util": "X = 1\n",
        "a": "from .util import X\n",
        "b": "from . import util\n",
        "c": "def f():\n    import studio_core.ops.util\n",
        "d": "Y = 2\n",
    }
    d = _pkg(tmp_path, files)
    before = {n: _h(d, n) for n in "abcd"}
    (tmp_path / "d.py").write_text("Y = 3\n", encoding="utf-8")
    after = {n: _h(d, n) for n in "abcd"}
    assert after["d"] != before["d"]
    assert all(after[n] == before[n] for n in "abc")

    (tmp_path / "util.py").write_text("X = 2\n", encoding="utf-8")
    shared = {n: _h(d, n) for n in "abcd"}
    assert all(shared[n] != after[n] for n in "abc") and shared["d"] == after["d"]


def test_lazy_import_and_cycle(tmp_path: Path) -> None:
    d = _pkg(tmp_path, {"a": "def f():\n    from .b import y\n", "b": "from .a import f\ny = 1\n"})
    h1 = _h(d, "a")
    (tmp_path / "b.py").write_text("from .a import f\ny = 2\n", encoding="utf-8")
    assert _h(d, "a") != h1


def test_parse_error_falls_back_to_whole_package(tmp_path: Path) -> None:
    d = _pkg(tmp_path, {"a": "def (:\n", "b": "z = 1\n"})
    h1 = _h(d, "a")
    (tmp_path / "b.py").write_text("z = 2\n", encoding="utf-8")
    assert _h(d, "a") != h1


def test_real_ops_are_scoped() -> None:
    assert ex.op_source_hash("studio_core.ops.sheet_layout") != ex.ops_source_hash()
    assert ex.op_source_hash("tests.fake") == ex.ops_source_hash()
