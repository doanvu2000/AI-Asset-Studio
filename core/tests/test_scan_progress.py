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
