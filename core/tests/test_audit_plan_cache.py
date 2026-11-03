from pathlib import Path

import httpx

from studio_core.export import audit as au
from studio_core.export import audit_flutter as af
from studio_core.export.audit_plans import PlanCache
from test_api_basic import AUTH, StubEngine, TOKEN, _settings
from test_audit import _unity
from test_audit_flutter import _flutter


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


ACTS = [{"type": "max_size", "rel": "a.png", "max_size": 256}]


def test_cache_expires_evicts_and_checks_files(tmp_path: Path):
    clock = Clock()
    cache = PlanCache(ttl=10, max_entries=2, clock=clock)
    f = tmp_path / "a.meta"
    f.write_text("x", encoding="utf-8")
    plan = [{"rel": "a.png", "action": "write"}]

    pid = cache.put("P", ACTS, plan, [f])
    assert cache.get(pid, "P", ACTS) is plan
    assert cache.get(pid, "Q", ACTS) is None
    assert cache.get(pid, "P", [{**ACTS[0], "max_size": 512}]) is None
    assert cache.get("nope", "P", ACTS) is None and cache.get(None, "P", ACTS) is None

    f.write_text("changed size", encoding="utf-8")
    assert cache.get(pid, "P", ACTS) is None and len(cache) == 0

    pid = cache.put("P", ACTS, plan, [f])
    clock.now = 11
    assert cache.get(pid, "P", ACTS) is None

    ids = [cache.put("P", ACTS, plan, []) for _ in range(3)]
    assert len(cache) == 2 and cache.get(ids[0], "P", ACTS) is None and cache.get(ids[2], "P", ACTS) is plan

    gone = tmp_path / "gone"
    pid = cache.put("P", ACTS, plan, [gone])
    gone.write_text("appeared", encoding="utf-8")
    assert cache.get(pid, "P", ACTS) is None


async def _client(tmp_path: Path):
    from studio_core.app import build_app

    app = build_app(_settings(tmp_path / "data"), TOKEN, engine=StubEngine(), load_plugins=False)
    return app


async def test_api_unity_apply_reuses_plan_unless_a_file_changed(tmp_path: Path, monkeypatch):
    app = await _client(tmp_path)
    proj = _unity(tmp_path)
    acts = [{"type": "max_size", "rel": "Assets/Art/icon.png", "max_size": 256}]
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            await c.post("/api/audit", json={"path": str(proj / "Assets")}, headers=AUTH)
            body = {"path": str(proj), "actions": acts}
            dry = (await c.post("/api/audit/fix", json=body, headers=AUTH)).json()
            assert dry["plan_id"]

            calls: list[int] = []
            real = au.plan_fixes
            monkeypatch.setattr(au, "plan_fixes", lambda *a, **k: calls.append(1) or real(*a, **k))

            junk = await c.post("/api/audit/fix", json={**body, "dry_run": False, "plan_id": "bogus"}, headers=AUTH)
            assert junk.status_code == 200 and calls == [1] and junk.json()["written"] == 1
            await c.post("/api/audit/undo", json={"backup_id": junk.json()["backup_id"]}, headers=AUTH)

            calls.clear()
            await c.post("/api/audit", json={"path": str(proj / "Assets")}, headers=AUTH)
            dry = (await c.post("/api/audit/fix", json=body, headers=AUTH)).json()
            calls.clear()
            done = (await c.post("/api/audit/fix", json={**body, "dry_run": False, "plan_id": dry["plan_id"]},
                                 headers=AUTH)).json()
            assert calls == [] and done["written"] == 1

            await c.post("/api/audit/undo", json={"backup_id": done["backup_id"]}, headers=AUTH)
            await c.post("/api/audit", json={"path": str(proj / "Assets")}, headers=AUTH)
            dry = (await c.post("/api/audit/fix", json=body, headers=AUTH)).json()
            meta = proj / "Assets" / "Art" / "icon.png.meta"
            meta.write_text(meta.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
            calls.clear()
            again = (await c.post("/api/audit/fix", json={**body, "dry_run": False, "plan_id": dry["plan_id"]},
                                  headers=AUTH)).json()
            assert calls == [1] and again["written"] == 0


async def test_api_flutter_apply_skips_the_slow_plan_with_a_valid_plan_id(tmp_path: Path, monkeypatch):
    app = await _client(tmp_path)
    proj = _flutter(tmp_path)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            rep = (await c.post("/api/audit", json={"path": str(proj)}, headers=AUTH)).json()
            acts = [f["fix"] for f in rep["findings"] if f.get("fix")]
            calls: list[int] = []
            real = af.plan_fixes
            monkeypatch.setattr(af, "plan_fixes", lambda *a, **k: calls.append(1) or real(*a, **k))
            body = {"path": str(proj), "actions": acts}
            dry = (await c.post("/api/audit/fix", json=body, headers=AUTH)).json()
            assert dry["plan_id"] and calls == [1]

            other = [dict(acts[0])]
            wrong = (await c.post("/api/audit/fix", json={"path": str(proj), "actions": other, "dry_run": False,
                                                          "plan_id": dry["plan_id"]}, headers=AUTH)).json()
            assert calls == [1, 1] and wrong["written"] == 1
            await c.post("/api/audit/undo", json={"backup_id": wrong["backup_id"]}, headers=AUTH)

            await c.post("/api/audit", json={"path": str(proj)}, headers=AUTH)
            dry = (await c.post("/api/audit/fix", json=body, headers=AUTH)).json()
            calls.clear()
            done = (await c.post("/api/audit/fix", json={**body, "dry_run": False, "plan_id": dry["plan_id"]},
                                 headers=AUTH)).json()
            assert calls == [] and done["written"] == 5

            await c.post("/api/audit/undo", json={"backup_id": done["backup_id"]}, headers=AUTH)
            await c.post("/api/audit", json={"path": str(proj)}, headers=AUTH)
            dry = (await c.post("/api/audit/fix", json=body, headers=AUTH)).json()
            (proj / "assets" / "unused.png").write_bytes(b"x")
            pub = proj / "pubspec.yaml"
            pub.write_text(pub.read_text(encoding="utf-8") + "# touched\n", encoding="utf-8")
            calls.clear()
            await c.post("/api/audit/fix", json={**body, "dry_run": False, "plan_id": dry["plan_id"]}, headers=AUTH)
            assert calls == [1]
