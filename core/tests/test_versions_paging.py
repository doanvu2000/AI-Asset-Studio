from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from studio_core.app import build_app
from studio_core.contracts import BlobRef, VersionRow, VersionStatus
from studio_core.store import Database, SqliteRepo
from test_api_basic import AUTH, StubEngine, _settings

TOTAL = 450


def _row(i: int, project: str = "p1", status: VersionStatus = VersionStatus.DRAFT) -> VersionRow:
    blob = BlobRef(hash=f"{i:064x}", mime="image/png", size=1, width=1, height=1, name=f"{i}.png")
    # Every 10 rows share created_at so only the id tie-break keeps pages stable.
    return VersionRow(id=f"v{i:04d}", project_id=project, asset_name=f"a{i}", run_id="r", step_id="s",
                      blob=blob, status=status, created_at=float(i // 10))


@pytest.fixture
async def repo(tmp_path: Path) -> AsyncIterator[SqliteRepo]:
    db = Database(tmp_path / "t.db")
    await db.open()
    r = SqliteRepo(db)
    for i in range(TOTAL):
        await r.add_version(_row(i, "p1" if i < 300 else "p2",
                                 VersionStatus.APPROVED if i % 3 == 0 else VersionStatus.DRAFT))
    yield r
    await db.close()


async def _all(repo: SqliteRepo, size: int, **kw) -> list[VersionRow]:
    out: list[VersionRow] = []
    while True:
        page = await repo.list_versions(limit=size, offset=len(out), **kw)
        out += page
        if len(page) < size:
            return out


async def test_pages_cover_everything_once(repo: SqliteRepo) -> None:
    got = await _all(repo, 200)
    assert len(got) == TOTAL and len({v.id for v in got}) == TOTAL
    assert [v.id for v in got] == [v.id for v in await repo.list_versions(limit=TOTAL)]
    assert await repo.count_versions() == TOTAL


async def test_filters_and_count(repo: SqliteRepo) -> None:
    p1 = await _all(repo, 64, project_id="p1")
    assert len(p1) == 300 and {v.project_id for v in p1} == {"p1"}
    assert await repo.count_versions(project_id="p2") == 150
    appr = await _all(repo, 50, project_id="p1", status=VersionStatus.APPROVED)
    assert len(appr) == 100 == await repo.count_versions(project_id="p1", status=VersionStatus.APPROVED)


async def test_edges(repo: SqliteRepo) -> None:
    assert await repo.list_versions(limit=0) == []
    assert await repo.list_versions(offset=TOTAL) == []
    assert await repo.list_versions(offset=TOTAL + 1000) == []
    assert len(await repo.list_versions(limit=10, offset=TOTAL - 3)) == 3
    assert await repo.count_versions(project_id="nope") == 0
    assert await repo.list_versions(project_id="nope", limit=5) == []


async def test_insert_between_pages_never_skips(repo: SqliteRepo) -> None:
    first = await repo.list_versions(limit=200)
    await repo.add_version(_row(9000))
    rest = await _all(repo, 200)
    seen = {v.id for v in first} | {v.id for v in rest[200:]}
    # A newer row shifts later pages by one (one repeat is fine, a gap is not).
    assert {f"v{i:04d}" for i in range(TOTAL)} <= seen


async def test_default_matches_old_behaviour(repo: SqliteRepo) -> None:
    assert len(await repo.list_versions()) == 200


async def test_index_migration_idempotent_and_used(tmp_path: Path) -> None:
    path = tmp_path / "m.db"
    for _ in range(2):
        db = Database(path)
        await db.open()
        await db.close()
    db = Database(path)
    await db.open()
    plan = await db.fetchall(
        "EXPLAIN QUERY PLAN SELECT * FROM versions WHERE project_id = ? AND status = ? "
        "ORDER BY created_at DESC LIMIT 50", ("p1", "approved"))
    await db.close()
    assert "idx_versions_project_status" in " ".join(str(tuple(r)) for r in plan)


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[tuple[object, httpx.AsyncClient]]:
    app = build_app(_settings(tmp_path), "test-token", engine=StubEngine(), load_plugins=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                     headers=AUTH) as c:
            yield app, c


async def test_http_paging_and_legacy_shape(client) -> None:
    app, c = client
    for i in range(TOTAL):
        await app.state.repo.add_version(_row(i, "p1" if i < 300 else "p2"))
    legacy = await c.get("/api/versions")
    assert legacy.status_code == 200 and isinstance(legacy.json(), list) and len(legacy.json()) == 200
    assert legacy.headers["X-Total-Count"] == str(TOTAL)
    ids: list[str] = []
    while True:
        r = await c.get("/api/versions", params={"limit": 200, "offset": len(ids), "project_id": "p1"})
        assert r.headers["X-Total-Count"] == "300"
        ids += [v["id"] for v in r.json()]
        if len(r.json()) < 200:
            break
    assert len(ids) == 300 and len(set(ids)) == 300
    assert (await c.get("/api/versions", params={"limit": 0})).json() == []
    assert (await c.get("/api/versions", params={"offset": 9999})).json() == []
    assert (await c.get("/api/versions", params={"offset": -1})).status_code == 422
