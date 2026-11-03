from pathlib import Path

import pytest
from PIL import Image

from studio_core.export import audit as au
from studio_core.export import audit_flutter as af
from studio_core.export.restyle import file_hash
from studio_core.scan_jobs import ScanCancelled
from test_audit_flutter import _photo

SHOTS = Path(__file__).resolve().parents[2] / "docs" / "screenshots"


def _mosaic(cols: int, rows: int) -> Image.Image:
    files = sorted(SHOTS.glob("*.png"))
    if len(files) < 6:
        pytest.skip("docs/screenshots missing")
    tiles = [Image.open(f).convert("RGB") for f in files]
    w, h = tiles[0].size
    out = Image.new("RGB", (w * cols, h * rows))
    for i in range(cols * rows):
        out.paste(tiles[(i * 3) % len(tiles)].resize((w, h)), (i % cols * w, i // cols * h))
    return out


@pytest.mark.parametrize("cols,rows", [(2, 2), (3, 2), (3, 3), (4, 3)])
def test_estimate_within_25_percent_on_real_screenshots(tmp_path: Path, cols: int, rows: int):
    p = tmp_path / "m.png"
    _mosaic(cols, rows).save(p)
    est, estimated = af.estimate_webp(p)
    real = len(af.encode_webp(p))
    assert estimated and abs(est / real - 1) <= 0.25


@pytest.mark.parametrize("size", [(1800, 1200), (2400, 1600), (3200, 2000)])
def test_estimate_within_25_percent_on_photo_like(tmp_path: Path, size: tuple[int, int]):
    p = tmp_path / "p.png"
    _photo(*size).save(p)
    est, estimated = af.estimate_webp(p)
    assert estimated and abs(est / len(af.encode_webp(p)) - 1) <= 0.25


def test_small_images_are_exact(tmp_path: Path):
    p = tmp_path / "s.png"
    _photo(300, 200).save(p)
    assert af.estimate_webp(p) == (len(af.encode_webp(p)), False)


def _project(tmp: Path, n: int = 5) -> tuple[Path, dict, list[dict]]:
    proj = tmp / "demo"
    (proj / "lib").mkdir(parents=True)
    (proj / "assets").mkdir()
    names = [f"p{i}.png" for i in range(n)]
    (proj / "pubspec.yaml").write_text("name: d\nflutter:\n  assets:\n    - assets/\n", encoding="utf-8")
    (proj / "lib" / "m.dart").write_text("\n".join(f"var a{i} = 'assets/{x}';" for i, x in enumerate(names)),
                                         encoding="utf-8")
    for i, x in enumerate(names):
        _photo(1700 + i * 40, 1300).save(proj / "assets" / x)
    rep = au.scan(str(proj))
    acts = [f["fix"] for f in rep["findings"] if (f.get("fix") or {}).get("type") == "flutter_webp"]
    assert len(acts) == n
    return proj, rep, acts


def test_dry_run_is_estimated_and_write_reports_real_bytes(tmp_path: Path):
    proj, rep, acts = _project(tmp_path)
    plan = au.make_plan(rep, acts)
    dry = au.apply_fixes(rep, acts, Path(), True, None, None, plan)
    assert dry["estimated"] is True and all(f["estimated"] for f in dry["files"])
    done = au.apply_fixes(rep, acts, tmp_path / "bk", False, None, None, plan)
    real = sum((proj / "assets" / f"p{i}.webp").stat().st_size for i in range(5))
    assert done["written"] == 5 and done["bytes_after"] == real and "estimated" not in done
    assert all("estimated" not in f for f in done["files"])
    assert abs(dry["bytes_after"] / real - 1) <= 0.25


def test_parallel_write_matches_sequential(tmp_path: Path, monkeypatch):
    proj, rep, acts = _project(tmp_path / "a")
    au.apply_fixes(rep, acts, tmp_path / "bk1", False)
    par = {i: file_hash(proj / "assets" / f"p{i}.webp") for i in range(5)}
    monkeypatch.setattr(af, "_workers", lambda: 1)
    proj2, rep2, acts2 = _project(tmp_path / "b")
    au.apply_fixes(rep2, acts2, tmp_path / "bk2", False)
    assert par == {i: file_hash(proj2 / "assets" / f"p{i}.webp") for i in range(5)}


def test_cancel_while_planning_changes_nothing(tmp_path: Path):
    proj, rep, acts = _project(tmp_path)
    before = {p.name: file_hash(p) for p in (proj / "assets").iterdir()}

    def stop(i: int, n: int, phase: str) -> None:
        if i >= 2:
            raise ScanCancelled()

    doc = au.apply_fixes(rep, acts, tmp_path / "bk", False, None, stop)
    assert doc["cancelled"] and doc["written"] == 0
    assert before == {p.name: file_hash(p) for p in (proj / "assets").iterdir()}


def test_cancel_between_units_while_writing_keeps_done_units_undoable(tmp_path: Path):
    proj, rep, acts = _project(tmp_path)
    plan = au.make_plan(rep, acts)
    seen: list[str] = []

    def stop(i: int, n: int, phase: str) -> None:
        seen.append(phase)
        if phase == "files" and i == 2:
            raise ScanCancelled()

    doc = au.apply_fixes(rep, acts, tmp_path / "bk", False, None, stop, plan)
    assert doc["cancelled"] and doc["written"] == 2 and doc["skipped"] == 3
    assert len(list((proj / "assets").glob("*.webp"))) == 2 and len(list((proj / "assets").glob("*.png"))) == 3
    assert au.undo(tmp_path / "bk", doc["backup_id"], False)["restored"]
    assert not list((proj / "assets").glob("*.webp")) and len(list((proj / "assets").glob("*.png"))) == 5


def test_borderline_estimate_is_rechecked_exactly(tmp_path: Path, monkeypatch):
    proj, rep, acts = _project(tmp_path, 1)
    monkeypatch.setattr(af, "estimate_webp", lambda p: (10**9, True))
    plan = af.plan_fixes(rep, acts)
    assert plan[0]["action"] == "write" and plan[0]["estimated"] is False
