import asyncio
from pathlib import Path

import pytest
from PIL import Image

from studio_core.export import audit as au
from studio_core.export import restyle as rs
from studio_core.scan_jobs import ScanCancelled, ScanJobs
from studio_core.store.events import MemoryEventBus
from test_api_basic import AUTH, app_ctx  # noqa: F401
from test_audit import _unity


def _scan_events(bus: MemoryEventBus) -> list[dict]:
    return [e.data for e in bus._buffer if e.type == "scan.progress"]


def test_audit_progress_is_ordered_with_stable_total(tmp_path: Path) -> None:
    proj = _unity(tmp_path)
    ticks: list[tuple[int, int, str]] = []
    au.scan(str(proj / "Assets"), progress=lambda d, t, p: ticks.append((d, t, p)))
    files = [t for t in ticks if t[2] == "files"]
    assert len(files) > 3 and {t[1] for t in files} == {len(files)}
    assert [t[0] for t in files] == list(range(len(files)))
    assert ticks[-1] == (files[0][1], files[0][1], "analyze")


def test_restyle_progress_counts_pngs(tmp_path: Path) -> None:
    for i in range(5):
        Image.new("RGBA", (64, 64), (i * 40, 10, 10, 255)).save(tmp_path / f"a{i}.png")
    ticks: list[tuple[int, int, str]] = []
    rs.scan_folder(tmp_path, progress=lambda d, t, p: ticks.append((d, t, p)))
    assert ticks[0][2] == "walk" and ticks[-1] == (5, 5, "files")
    done = [t[0] for t in ticks if t[2] == "files"]
    assert done == sorted(done)


def test_job_throttles_and_cancel_stops_next_unit() -> None:
    bus = MemoryEventBus()
    jobs = ScanJobs(bus)
    with pytest.raises(ScanCancelled):
        with jobs.open("audit", "k") as job:
            for i in range(1000):
                job(i, 1000)
                if i == 500:
                    assert jobs.cancel(kind="audit") == 1
    ev = _scan_events(bus)
    assert ev[0]["phase"] == "start" and ev[-1]["phase"] == "cancelled" and len({e["job"] for e in ev}) == 1
    assert len(ev) < 100
    assert jobs.cancel(kind="audit") == 0


def test_job_error_and_done_phases() -> None:
    bus = MemoryEventBus()
    jobs = ScanJobs(bus)
    with jobs.open("qa") as job:
        job(3, 3)
    with pytest.raises(ValueError):
        with jobs.open("qa"):
            raise ValueError("x")
    phases = [e["phase"] for e in _scan_events(bus)]
    assert phases == ["start", "files", "done", "start", "error"]


async def test_api_audit_emits_and_cancel_leaves_no_report(app_ctx, tmp_path: Path, monkeypatch) -> None:  # noqa: F811
    app, client, _ = app_ctx
    proj = _unity(tmp_path)
    r = await client.post("/api/audit", headers=AUTH, json={"path": str(proj / "Assets"), "duplicates": False})
    assert r.status_code == 200
    ev = _scan_events(app.state.bus)
    assert ev[0]["phase"] == "start" and ev[-1]["phase"] == "done" and ev[0]["kind"] == "audit"
    reports = list((Path(app.state.settings.data_dir) / "audit").glob("report_*.json"))
    assert len(reports) == 1
    before = reports[0].read_bytes()
    orig = au.scan

    def cancelling(path, engine, tri, dups, mx, emb, progress):
        def tick(d, t, p):
            if d >= 2:
                app.state.scan_jobs.cancel(kind="audit")
            progress(d, t, p)
        return orig(path, engine, tri, dups, mx, emb, tick)

    monkeypatch.setattr(au, "scan", cancelling)
    r = await client.post("/api/audit", headers=AUTH, json={"path": str(proj / "Assets"), "duplicates": False})
    assert r.status_code == 409 and r.json()["error"]["code"] == "cancelled"
    assert _scan_events(app.state.bus)[-1]["phase"] == "cancelled"
    assert reports[0].read_bytes() == before


async def test_api_restyle_and_qa_events_and_cancel_endpoint(app_ctx, tmp_path: Path) -> None:  # noqa: F811
    app, client, _ = app_ctx
    for i in range(3):
        Image.new("RGBA", (64, 64), (i * 50, 0, 0, 255)).save(tmp_path / f"s{i}.png")
    r = await client.post("/api/restyle/scan", headers=AUTH, json={"folder": str(tmp_path)})
    assert r.status_code == 200
    ev = [e for e in _scan_events(app.state.bus) if e["kind"] == "restyle"]
    assert ev[0]["phase"] == "start" and ev[-1]["phase"] == "done"
    assert [e for e in ev if e["phase"] == "files"][-1]["total"] == 3
    r = await client.post("/api/qa/recompute", headers=AUTH, json={})
    assert r.status_code == 200 and "cancelled" not in r.json()
    assert {e["kind"] for e in _scan_events(app.state.bus)} == {"restyle", "qa"}
    assert (await client.post("/api/scans/cancel", headers=AUTH, json={})).status_code == 422
    r = await client.post("/api/scans/cancel", headers=AUTH, json={"job": "nope"})
    assert r.json() == {"cancelled": 0}
    await asyncio.sleep(0)


async def test_qa_cancel_stops_between_versions(app_ctx, monkeypatch) -> None:  # noqa: F811
    from studio_core.api import qa
    from studio_core.contracts import BlobRef, VersionRow

    app, client, _ = app_ctx
    rows = [VersionRow(id=f"v{i}", project_id="p", asset_name="a", run_id="r", step_id="s",
                       blob=BlobRef(hash=f"{i:064x}", mime="image/png", size=1, width=1, height=1, name="x.png"),
                       created_at=float(i)) for i in range(10)]
    seen: list[str] = []

    async def fake_to_score(**_):
        return rows

    async def fake_rescore(request, v, steps):
        seen.append(v.id)
        if len(seen) == 3:
            app.state.scan_jobs.cancel(kind="qa")
        return v.model_copy(update={"qa": {"score": 1.0}})

    monkeypatch.setattr(app.state.repo, "versions_to_score", fake_to_score)
    monkeypatch.setattr(qa, "_rescore", fake_rescore)
    r = await client.post("/api/qa/recompute", headers=AUTH, json={})
    assert r.json() == {"updated": 3, "skipped": 0, "total": 10, "cancelled": True}
    assert seen == ["v0", "v1", "v2"] and _scan_events(app.state.bus)[-1]["phase"] == "cancelled"


def test_phase_handover_reports_full_count() -> None:
    from studio_core.scan_jobs import ScanJob

    class Bus:
        def __init__(self) -> None:
            self.events: list[dict] = []

        def publish(self, _ev: str, data: dict, **_kw: object) -> None:
            self.events.append(data)

    bus = Bus()
    job = ScanJob(bus, "audit", "k")
    for n in range(50):
        job(n, 50, "files")
    job(0, 0, "analyze")
    files = [e for e in bus.events if e["phase"] == "files"]
    assert files[-1]["done"] == 50 and files[-1]["total"] == 50
    assert bus.events[-1]["phase"] == "analyze"


def _fix_acts() -> list[dict]:
    return [{"type": "max_size", "rel": f"Assets/Art/{n}.png", "max_size": 256} for n in ("bg", "hero", "icon")]


def test_fix_progress_ordered_and_cancel_keeps_written_undoable(tmp_path: Path) -> None:
    proj = _unity(tmp_path)
    rep = au.scan(str(proj / "Assets"))
    metas = {n: (proj / "Assets/Art" / f"{n}.png.meta") for n in ("bg", "hero", "icon")}
    before = {n: p.read_bytes() for n, p in metas.items()}

    ticks: list[tuple[int, int, str]] = []
    full = au.apply_fixes(rep, _fix_acts(), tmp_path / "bk0", False, progress=lambda d, t, p: ticks.append((d, t, p)))
    assert full["written"] == 3 and not full.get("cancelled")
    assert ticks == [(0, 3, "files"), (1, 3, "files"), (2, 3, "files")]
    au.undo(tmp_path / "bk0", full["backup_id"])
    assert {n: p.read_bytes() for n, p in metas.items()} == before

    bus = MemoryEventBus()
    jobs = ScanJobs(bus)
    with jobs.open("audit_fix", "k") as job:
        def stop_at_two(d: int, t: int, p: str) -> None:
            if d == 2:
                jobs.cancel(kind="audit_fix")
            job(d, t, p)

        doc = au.apply_fixes(rep, _fix_acts(), tmp_path / "bk", False, progress=stop_at_two)
    assert doc["cancelled"] and doc["written"] == 2 and doc["skipped"] == 1
    assert [f["action"] for f in doc["files"]].count("skip") == 1 and doc["files"][-1]["why"] == "cancelled"
    assert _scan_events(bus)[-1]["phase"] == "cancelled"
    changed = {n for n, p in metas.items() if p.read_bytes() != before[n]}
    assert len(changed) == 2
    out = au.undo(tmp_path / "bk", doc["backup_id"])
    assert len(out["restored"]) == 2 and out["conflicts"] == []
    assert {n: p.read_bytes() for n, p in metas.items()} == before


def test_fix_cancelled_before_any_write_leaves_no_backup(tmp_path: Path) -> None:
    proj = _unity(tmp_path)
    rep = au.scan(str(proj / "Assets"))

    def stop(d: int, t: int, p: str) -> None:
        raise ScanCancelled()

    doc = au.apply_fixes(rep, _fix_acts(), tmp_path / "bk", False, progress=stop)
    assert doc["cancelled"] and doc["written"] == 0 and "backup_id" not in doc
    assert list((tmp_path / "bk").glob("*")) == []


async def test_api_fix_emits_audit_fix_events(app_ctx, tmp_path: Path) -> None:  # noqa: F811
    app, client, _ = app_ctx
    proj = _unity(tmp_path)
    await client.post("/api/audit", headers=AUTH, json={"path": str(proj / "Assets"), "duplicates": False})
    n0 = len(_scan_events(app.state.bus))
    r = await client.post("/api/audit/fix", headers=AUTH, json={"path": str(proj), "actions": _fix_acts(), "dry_run": False})
    assert r.status_code == 200 and r.json()["written"] == 3
    ev = [e for e in _scan_events(app.state.bus)[n0:] if e["kind"] == "audit_fix"]
    assert ev[0]["phase"] == "start" and ev[-1]["phase"] == "done"
    assert [e["done"] for e in ev if e["phase"] == "files"][0] == 0 and ev[0]["key"] == str(proj)


def test_project_qa_analyse_reports_then_cancels(tmp_path: Path) -> None:
    from studio_core.api.project_qa import _analyse
    from studio_core.contracts import BlobRef, VersionRow

    class Blobs:
        def path(self, h: str) -> Path:
            return tmp_path / f"{h}.png"

    rows = []
    for i in range(6):
        h = f"{i:064x}"
        Image.new("RGBA", (32, 32), (i * 30, 90, 40, 255)).save(tmp_path / f"{h}.png")
        rows.append(VersionRow(id=f"v{i}", project_id="p", asset_name="a", run_id=f"r{i}", step_id="s",
                               blob=BlobRef(hash=h, mime="image/png", size=1, width=32, height=32, name="x.png"),
                               created_at=float(i)))
    ticks: list[tuple[int, int, str]] = []
    _analyse(Blobs(), rows, None, lambda d, t, p: ticks.append((d, t, p)))
    assert ticks == [(i, 6, "files") for i in range(6)]

    bus = MemoryEventBus()
    jobs = ScanJobs(bus)
    with pytest.raises(ScanCancelled):
        with jobs.open("project_qa", "p") as job:
            seen: list[int] = []

            def tick(d: int, t: int, p: str) -> None:
                seen.append(d)
                if d == 3:
                    jobs.cancel(kind="project_qa")
                job(d, t, p)

            _analyse(Blobs(), rows, None, tick)
    ev = _scan_events(bus)
    assert ev[-1]["phase"] == "cancelled" and ev[-1]["kind"] == "project_qa"
    assert seen == [0, 1, 2, 3]


async def test_project_qa_vision_pass_cancels_between_images_and_after_review(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from studio_core.api.project_qa import _vision_pass

    class Blobs:
        opened: list[str] = []

        def path(self, h: str) -> Path:
            self.opened.append(h)
            return tmp_path / f"{h}.png"

    class Reviewer:
        calls = 0

        async def review(self, *_a: object, **_k: object) -> dict:
            self.calls += 1
            jobs.cancel(kind="project_qa")
            return {"notes": {"v0": "n"}, "summary": "s"}

    assets = []
    for i in range(5):
        h = f"{i:064x}"
        Image.new("RGBA", (32, 32), (i * 30, 90, 40, 255)).save(tmp_path / f"{h}.png")
        assets.append({"version_id": f"v{i}", "score": 80.0, "blob": {"hash": h}})
    blobs, reviewer = Blobs(), Reviewer()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(blobs=blobs, project_qa_reviewer=reviewer)))
    jobs = ScanJobs(MemoryEventBus())

    class StopAtTwo:
        def __init__(self, job: object) -> None:
            self.job = job

        @property
        def cancelled(self) -> bool:
            return self.job.cancelled

        def __call__(self, d: int, t: int, p: str) -> None:
            if d == 2:
                jobs.cancel(kind="project_qa")
            self.job(d, t, p)

    with pytest.raises(ScanCancelled):
        with jobs.open("project_qa", "p") as job:
            await _vision_pass(request, {"assets": assets, "reference": {}}, None, None, StopAtTwo(job))
    assert len(blobs.opened) == 2 and reviewer.calls == 0

    blobs.opened.clear()
    report = {"assets": assets, "reference": {}}
    with pytest.raises(ScanCancelled):
        with jobs.open("project_qa", "p") as job:
            await _vision_pass(request, report, None, None, job)
    assert reviewer.calls == 1 and "vision" not in report and "vision_note" not in assets[0]
