from pathlib import Path

import httpx
import numpy as np
import pytest
from PIL import Image

from studio_core.export import audit as au
from studio_core.export import audit_flutter as af
from studio_core.export.restyle import file_hash
from studio_core.scan_jobs import ScanCancelled
from test_api_basic import AUTH, StubEngine, TOKEN, _settings
from test_audit import _noise

PUBSPEC = """name: demo
description: A demo
environment:
  sdk: ">=3.0.0 <4.0.0"
dependencies:
  flutter:
    sdk: flutter

# keep this comment
flutter:
  uses-material-design: true
  assets:
    - assets/images/logo.png   # logo
    - assets/images/ic.png
    - assets/images/gone.png
    - assets/missing_dir/
    - assets/icons/
    - assets/big/hero.png
    - assets/big/photo.jpg
    - assets/unused.png
  # trailing comment kept
"""

MAIN_DART = """const logo = 'assets/images/logo.png';
const ic = "assets/images/ic.png";
const a = 'assets/icons/a.png';
const n1 = 'assets/extra/new1.png';
const n2 = 'assets/images/new2.png';
const hero = 'assets/big/hero.png';
final photo = 'assets/big/photo.jpg';
"""


def _photo(w: int, h: int) -> Image.Image:
    x = np.linspace(0, 255, w, dtype=np.float32)[None, :, None]
    y = np.linspace(0, 255, h, dtype=np.float32)[:, None, None]
    rng = np.random.default_rng(7)
    arr = np.concatenate([x + 0 * y, y + 0 * x, (x + y) / 2], axis=2) + rng.normal(0, 3, (h, w, 3))
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


def _flutter(tmp: Path) -> Path:
    proj = tmp / "demo"
    (proj / "lib").mkdir(parents=True)
    (proj / "pubspec.yaml").write_text(PUBSPEC, encoding="utf-8", newline="")
    (proj / "lib" / "main.dart").write_text(MAIN_DART, encoding="utf-8")
    img = proj / "assets" / "images"
    (img / "2.0x").mkdir(parents=True)
    (img / "3.0x").mkdir()
    _noise(64, 64, 1).save(img / "logo.png")
    _noise(128, 128, 1).save(img / "2.0x" / "logo.png")
    _noise(64, 64, 2).save(img / "ic.png")
    _noise(192, 192, 2).save(img / "3.0x" / "ic.png")
    _noise(64, 64, 3).save(img / "new2.png")
    (proj / "assets" / "icons").mkdir()
    _noise(32, 32, 4).save(proj / "assets" / "icons" / "a.png")
    (proj / "assets" / "extra").mkdir()
    _noise(32, 32, 5).save(proj / "assets" / "extra" / "new1.png")
    _noise(32, 32, 6).save(proj / "assets" / "unused.png")
    big = proj / "assets" / "big"
    (big / "2.0x").mkdir(parents=True)
    (big / "3.0x").mkdir()
    _noise(704, 704, 7).save(big / "hero.png")
    _noise(1408, 1408, 7).save(big / "2.0x" / "hero.png")
    _noise(2112, 2112, 7).save(big / "3.0x" / "hero.png")
    _photo(2400, 1600).save(big / "photo.jpg", quality=95)
    return proj


def _snapshot(proj: Path) -> dict[str, str]:
    return {p.relative_to(proj).as_posix(): file_hash(p) for p in proj.rglob("*") if p.is_file()}


def _by(rep: dict, code: str) -> list[dict]:
    return [f for f in rep["findings"] if f["code"] == code]


def test_scan_finds_every_planted_problem(tmp_path: Path):
    proj = _flutter(tmp_path)
    rep = au.scan(str(proj / "assets"))
    assert rep["engine"] == "flutter" and Path(rep["project"]) == proj.resolve()
    assert {f["rel"] for f in _by(rep, "asset_missing")} == {"assets/images/gone.png", "assets/missing_dir/"}
    assert {f["rel"] for f in _by(rep, "asset_undeclared")} == {"assets/extra/new1.png", "assets/images/new2.png"}
    miss = {f["rel"]: f for f in _by(rep, "variant_missing")}
    assert miss["assets/images/ic.png"]["fix"]["kind"] == "2.0x" and "fix" not in miss["assets/images/logo.png"]
    assert "3.0x" in miss["assets/images/logo.png"]["msg"]
    assert {f["rel"] for f in _by(rep, "image_big")} == {"assets/big/hero.png", "assets/big/photo.jpg"}
    assert [u["rel"] for u in rep["unreferenced"]] == ["assets/unused.png"]
    assert rep["summary"]["fixable"] == 5 and rep["summary"]["flutter"]["errors"] == 2


def test_scan_scoped_to_a_subfolder_and_pure_dart_is_not_flutter(tmp_path: Path):
    proj = _flutter(tmp_path)
    rep = au.scan(str(proj / "assets" / "big"))
    assert {f["rel"] for f in rep["findings"]} == {"assets/big/hero.png", "assets/big/photo.jpg"}
    pure = tmp_path / "pure"
    pure.mkdir()
    (pure / "pubspec.yaml").write_text("name: x\ndependencies:\n  http: ^1.0.0\n", encoding="utf-8")
    with pytest.raises(au.AuditError):
        au.scan(str(pure))


def test_fix_dry_run_apply_rescan_and_undo(tmp_path: Path):
    proj = _flutter(tmp_path)
    before = _snapshot(proj)
    rep = au.scan(str(proj))
    acts = [f["fix"] for f in rep["findings"] if f.get("fix")]
    backups = tmp_path / "bk"
    plan = au.apply_fixes(rep, acts, backups, dry_run=True)
    assert plan["written"] == 0 and not backups.exists() and _snapshot(proj) == before
    assert plan["bytes_after"] < plan["bytes_before"]

    doc = au.apply_fixes(rep, acts, backups, dry_run=False)
    assert doc["written"] == 5 and doc["skipped"] == 0 and doc["bytes_after"] < doc["bytes_before"]
    pub = (proj / "pubspec.yaml").read_text(encoding="utf-8")
    assert "- assets/big/hero.webp" in pub and "- assets/big/photo.webp" in pub and "# logo" in pub
    assert "# keep this comment" in pub and "# trailing comment kept" in pub
    assert "    - assets/extra/new1.png\n" in pub and "    - assets/images/new2.png\n" in pub
    dart = (proj / "lib" / "main.dart").read_text(encoding="utf-8")
    assert "'assets/big/hero.webp'" in dart and "'assets/big/photo.webp'" in dart and "hero.png" not in dart
    for rel in ("big/hero.webp", "big/2.0x/hero.webp", "big/3.0x/hero.webp", "big/photo.webp", "images/2.0x/ic.png"):
        assert (proj / "assets" / rel).is_file()
    assert not (proj / "assets/big/hero.png").exists() and not (proj / "assets/big/2.0x/hero.png").exists()
    with Image.open(proj / "assets/images/2.0x/ic.png") as im:
        assert im.size == (128, 128)

    again = au.scan(str(proj))
    assert [(f["code"], f["rel"]) for f in again["findings"]] == [
        ("asset_missing", "assets/images/gone.png"), ("asset_missing", "assets/missing_dir/"),
        ("variant_missing", "assets/images/logo.png")]
    assert [u["rel"] for u in again["unreferenced"]] == ["assets/unused.png"]
    assert again["summary"]["textures"] == 13 and again["summary"]["texture_bytes"] < rep["summary"]["texture_bytes"]

    res = au.undo(backups, doc["backup_id"])
    assert res["conflicts"] == [] and res["restored"]
    assert _snapshot(proj) == before
    with pytest.raises(au.AuditError):
        au.undo(backups, doc["backup_id"])


def test_undo_keeps_everything_when_a_file_was_edited_after(tmp_path: Path):
    proj = _flutter(tmp_path)
    rep = au.scan(str(proj))
    doc = au.apply_fixes(rep, [f["fix"] for f in rep["findings"] if f.get("fix")], tmp_path / "bk", dry_run=False)
    (proj / "lib" / "main.dart").write_text("// edited", encoding="utf-8")
    snap = _snapshot(proj)
    res = au.undo(tmp_path / "bk", doc["backup_id"])
    assert res["restored"] == [] and res["conflicts"][0]["rel"] == "lib/main.dart" and _snapshot(proj) == snap
    forced = au.undo(tmp_path / "bk", doc["backup_id"], force=True)
    assert (proj / "assets/big/hero.png").is_file() and forced["restored"]


def test_cancel_between_fixes_leaves_whole_units_only(tmp_path: Path):
    proj = _flutter(tmp_path)
    before = _snapshot(proj)
    rep = au.scan(str(proj))
    acts = [f["fix"] for f in rep["findings"] if f.get("fix")]
    calls = {"n": 0}

    def stop_on_third_unit(done: int, total: int, phase: str = "files") -> None:
        if phase == "files":
            calls["n"] += 1
            if calls["n"] == 4:
                raise ScanCancelled()

    doc = au.apply_fixes(rep, acts, tmp_path / "bk", dry_run=False, progress=stop_on_third_unit)
    assert doc["cancelled"] and doc["written"] == 3 and doc["skipped"] == 2
    assert not list(proj.rglob("*.audit~"))
    pub = (proj / "pubspec.yaml").read_text(encoding="utf-8")
    assert "new1.png" in pub and "new2.png" in pub and "hero.png" in pub and "hero.webp" not in pub
    assert (proj / "assets/images/2.0x/ic.png").is_file() and (proj / "assets/big/hero.png").is_file()
    au.undo(tmp_path / "bk", doc["backup_id"])
    assert _snapshot(proj) == before

    def stop_in_scan(done: int, total: int, phase: str = "files") -> None:
        raise ScanCancelled()

    with pytest.raises(ScanCancelled):
        au.scan(str(proj), progress=stop_in_scan)
    assert _snapshot(proj) == before


def test_cancel_before_first_write_keeps_no_backup(tmp_path: Path):
    proj = _flutter(tmp_path)
    rep = au.scan(str(proj))
    acts = [f["fix"] for f in rep["findings"] if f.get("fix")]

    def never(done: int, total: int, phase: str = "files") -> None:
        raise ScanCancelled()

    doc = au.apply_fixes(rep, acts, tmp_path / "bk", dry_run=False, progress=never)
    assert doc["cancelled"] and doc["written"] == 0 and not list((tmp_path / "bk").glob("*"))


def test_changed_files_are_skipped_not_overwritten(tmp_path: Path):
    proj = _flutter(tmp_path)
    rep = au.scan(str(proj))
    acts = [f["fix"] for f in rep["findings"] if f.get("fix")]
    _noise(64, 64, 99).save(proj / "assets/extra/new1.png")
    Image.open(proj / "assets/big/photo.jpg").convert("RGB").resize((2300, 1500)).save(proj / "assets/big/photo.jpg")
    doc = au.apply_fixes(rep, acts, tmp_path / "bk", dry_run=False)
    skipped = {f["rel"] for f in doc["files"] if f["action"] == "skip"}
    assert "assets/big/photo.jpg" in skipped and doc["written"] >= 3
    assert au.apply_fixes(rep, [{"type": "flutter_declare", "rel": "assets/unused.png"}], tmp_path / "bk",
                          dry_run=False)["files"][0]["why"]
    assert au.apply_fixes(rep, [{"type": "flutter_webp", "rel": "../x.png"}], tmp_path / "bk", dry_run=False)["written"] == 0


def test_pubspec_without_assets_crlf_and_quoting():
    text = "name: x\r\nflutter:\r\n  uses-material-design: true\r\n"
    out = af.declare_entry(text, "assets/a b.png")
    assert out == 'name: x\r\nflutter:\r\n  assets:\r\n    - "assets/a b.png"\r\n  uses-material-design: true\r\n'
    again = af.declare_entry(out, "assets/c.png")
    assert again.endswith('    - "assets/a b.png"\r\n    - assets/c.png\r\n  uses-material-design: true\r\n')
    with pytest.raises(au.AuditError):
        af.declare_entry("name: x\nflutter:\n  assets: [a.png]\n", "assets/b.png")
    with pytest.raises(au.AuditError):
        af.declare_entry("name: x\n", "assets/b.png")
    renamed = af.rename_entry("flutter:\n  assets:\n    - path: assets/a.png\n      flavors: [x]\n    - 'assets/a.png'\n",
                              "assets/a.png", "assets/a.webp")
    assert "path: assets/a.webp" in renamed and "- 'assets/a.webp'" in renamed
    assert af.rewrite_refs("x('assets/a.png'); y('$base/a.png'); z('assets/b/a.png')", "assets/a.png",
                           "assets/a.webp", bare=True) == \
        "x('assets/a.webp'); y('$base/a.webp'); z('assets/b/a.webp')"
    assert af.rewrite_refs("'myassets/a.png'", "assets/a.png", "assets/a.webp", bare=False) == "'myassets/a.png'"


def test_pubspec_without_assets_section_scans_and_declares(tmp_path: Path):
    proj = tmp_path / "bare"
    (proj / "lib").mkdir(parents=True)
    (proj / "assets").mkdir()
    (proj / "pubspec.yaml").write_text("name: bare\nflutter:\n  uses-material-design: true\n", encoding="utf-8")
    (proj / "lib" / "m.dart").write_text("var a = 'assets/x.png';", encoding="utf-8")
    _noise(32, 32, 1).save(proj / "assets" / "x.png")
    rep = au.scan(str(proj))
    assert [f["code"] for f in rep["findings"]] == ["asset_undeclared"]
    doc = au.apply_fixes(rep, [rep["findings"][0]["fix"]], tmp_path / "bk", dry_run=False)
    assert doc["written"] == 1 and "  assets:\n    - assets/x.png\n" in (proj / "pubspec.yaml").read_text(encoding="utf-8")
    assert au.scan(str(proj))["findings"] == []


def test_backslash_and_case_mismatch_entries_are_missing(tmp_path: Path):
    proj = tmp_path / "w"
    (proj / "assets").mkdir(parents=True)
    (proj / "pubspec.yaml").write_text(
        "name: w\nflutter:\n  assets:\n    - assets\\a.png\n    - assets/B.png\n    - assets/c/\n", encoding="utf-8")
    _noise(32, 32, 1).save(proj / "assets" / "a.png")
    _noise(32, 32, 1).save(proj / "assets" / "b.png")
    (proj / "assets" / "c").mkdir()
    rep = au.scan(str(proj))
    missing = {f["rel"]: f["msg"] for f in _by(rep, "asset_missing")}
    assert set(missing) == {"assets\\a.png", "assets/B.png"} and "dùng dấu /" in missing["assets\\a.png"]


async def test_api_scan_fix_undo(tmp_path: Path):
    from studio_core.app import build_app

    proj = _flutter(tmp_path)
    app = build_app(_settings(tmp_path / "data"), TOKEN, engine=StubEngine(), load_plugins=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            rep = (await c.post("/api/audit", json={"path": str(proj)}, headers=AUTH)).json()
            assert rep["engine"] == "flutter"
            acts = [f["fix"] for f in rep["findings"] if f.get("fix")]
            dry = (await c.post("/api/audit/fix", json={"path": str(proj), "actions": acts}, headers=AUTH)).json()
            assert dry["dry_run"] and dry["written"] == 0 and len(dry["files"]) == 5
            done = (await c.post("/api/audit/fix", json={"path": str(proj), "actions": acts, "dry_run": False},
                                 headers=AUTH)).json()
            assert done["written"] == 5 and len(done["report"]["findings"]) == 3
            rows = (await c.get("/api/audit/backups", params={"path": str(proj)}, headers=AUTH)).json()["backups"]
            assert rows[0]["engine"] == "flutter" and rows[0]["bytes_after"] < rows[0]["bytes_before"]
            u = (await c.post("/api/audit/undo", json={"backup_id": done["backup_id"]}, headers=AUTH)).json()
            assert "assets/big/hero.png" in u["restored"]
            last = (await c.get("/api/audit/last", params={"path": str(proj / "lib")}, headers=AUTH)).json()
            assert last["engine"] == "flutter"


def test_missing_variant_of_a_webp_is_a_webp(tmp_path: Path):
    proj = tmp_path / "w"
    (proj / "lib").mkdir(parents=True)
    (proj / "assets" / "3.0x").mkdir(parents=True)
    (proj / "pubspec.yaml").write_text("name: w\nflutter:\n  assets:\n    - assets/a.webp\n", encoding="utf-8")
    (proj / "lib" / "m.dart").write_text("var a = 'assets/a.webp';", encoding="utf-8")
    _noise(60, 90, 1).save(proj / "assets" / "a.webp", lossless=True)
    _noise(180, 270, 1).save(proj / "assets" / "3.0x" / "a.webp", lossless=True)
    rep = au.scan(str(proj))
    assert [(f["code"], f["fix"]["kind"]) for f in rep["findings"]] == [("variant_missing", "2.0x")]
    doc = au.apply_fixes(rep, [rep["findings"][0]["fix"]], tmp_path / "bk", dry_run=False)
    assert doc["written"] == 1
    with Image.open(proj / "assets" / "2.0x" / "a.webp") as im:
        assert im.format == "WEBP" and im.size == (120, 180)
    assert au.scan(str(proj))["findings"] == []
    au.undo(tmp_path / "bk", doc["backup_id"])
    assert not (proj / "assets" / "2.0x").exists()
