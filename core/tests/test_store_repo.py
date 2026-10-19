from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from studio_core.contracts import (
    BlobRef,
    BlobRow,
    Lane,
    RunRow,
    RunStatus,
    StepRow,
    StepStatus,
    VersionRow,
    VersionStatus,
)
from studio_core.store import Database, SqliteRepo


@pytest.fixture
async def repo(tmp_path: Path) -> AsyncIterator[SqliteRepo]:
    db = Database(tmp_path / "t.db")
    await db.open()
    yield SqliteRepo(db)
    await db.close()


def _blob(h: str = "a" * 64) -> BlobRef:
    return BlobRef(hash=h, mime="image/png", size=3, width=1, height=1, name="x.png")


async def test_run_roundtrip_and_update(repo: SqliteRepo) -> None:
    run = RunRow(id="r1", recipe_id="concept-2d", recipe_hash="h", inputs={"prompt": "cat", "n": 2},
                 created_at=10.0)
    await repo.create_run(run)
    assert await repo.get_run("r1") == run
    await repo.update_run("r1", status=RunStatus.FAILED, error="boom", ended_at=11.0)
    got = await repo.get_run("r1")
    assert got is not None and got.status is RunStatus.FAILED and got.error == "boom" and got.ended_at == 11.0
    assert await repo.get_run("nope") is None


async def test_list_runs_filters(repo: SqliteRepo) -> None:
    for i in range(5):
        await repo.create_run(RunRow(id=f"r{i}", recipe_id="x", recipe_hash="h", created_at=float(i),
                                     status=RunStatus.SUCCEEDED if i % 2 else RunStatus.RUNNING))
    assert [r.id for r in await repo.list_runs(limit=2)] == ["r4", "r3"]
    assert [r.id for r in await repo.list_runs(before=3.0)] == ["r2", "r1", "r0"]
    assert [r.id for r in await repo.list_runs(status=RunStatus.SUCCEEDED)] == ["r3", "r1"]


async def test_step_roundtrip_json_fields(repo: SqliteRepo) -> None:
    steps = [StepRow(id=f"s{i}", run_id="r1", key=k, kind="op.x", lane=Lane.CPU, order=i, deps=["gen"] if i else [])
             for i, k in enumerate(["gen", "fit"])]
    await repo.create_steps(steps)
    assert await repo.get_steps("r1") == steps

    await repo.update_step("s1", status=StepStatus.FAILED, outputs={"images": [_blob()]},
                           error={"code": "x", "message": "m"}, meta={"waiting_for": "gpu"}, progress=0.5)
    s = await repo.get_step("s1")
    assert s is not None
    assert s.status is StepStatus.FAILED
    assert s.outputs["images"][0] == _blob()
    assert s.error == {"code": "x", "message": "m"} and s.meta == {"waiting_for": "gpu"} and s.progress == 0.5

    failed = await repo.list_steps_by_status([StepStatus.FAILED, StepStatus.RUNNING])
    assert [x.id for x in failed] == ["s1"]


async def test_update_rejects_unknown_column(repo: SqliteRepo) -> None:
    await repo.create_run(RunRow(id="r1", recipe_id="x", recipe_hash="h", created_at=1.0))
    with pytest.raises(ValueError):
        await repo.update_run("r1", bogus=1)


async def test_cache_versions_blobs(repo: SqliteRepo) -> None:
    assert await repo.cache_get("k") is None
    await repo.cache_put("k", {"images": [_blob()]})
    assert await repo.cache_get("k") == {"images": [_blob()]}

    v = VersionRow(id="v1", asset_name="hero", run_id="r1", step_id="s1", blob=_blob(), created_at=1.0)
    await repo.add_version(v)
    assert await repo.list_versions(run_id="r1") == [v]
    approved = await repo.set_version_status("v1", VersionStatus.APPROVED)
    assert approved is not None and approved.status is VersionStatus.APPROVED
    assert await repo.list_versions(status=VersionStatus.DRAFT) == []
    assert await repo.set_version_status("missing", VersionStatus.REJECTED) is None

    row = BlobRow(**_blob().model_dump(), created_at=2.0)
    await repo.put_blob_row(row)
    await repo.put_blob_row(row)
    assert await repo.get_blob_row(row.hash) == row


async def test_run_preview_hashes_pick_latest_image_output(repo: SqliteRepo) -> None:
    mesh = BlobRef(hash="m" * 64, mime="model/gltf-binary", size=9, name="x.glb")
    await repo.create_steps([
        StepRow(id="a0", run_id="ra", key="gen", kind="op.x", lane=Lane.GPU, order=0,
                outputs={"images": [_blob("1" * 64), _blob("2" * 64)]}),
        StepRow(id="a1", run_id="ra", key="fit", kind="op.x", lane=Lane.CPU, order=1,
                outputs={"images": [_blob("3" * 64)]}),
        StepRow(id="a2", run_id="ra", key="pending", kind="op.x", lane=Lane.CPU, order=2),
        StepRow(id="b0", run_id="rb", key="gen", kind="op.x", lane=Lane.GPU, order=0,
                outputs={"images": [_blob("4" * 64)]}),
        StepRow(id="b1", run_id="rb", key="mesh", kind="op.x", lane=Lane.GPU, order=1, outputs={"meshes": [mesh]}),
        StepRow(id="c0", run_id="rc", key="gen", kind="op.x", lane=Lane.GPU, order=0),
        StepRow(id="d0", run_id="rd", key="down", kind="op.x", lane=Lane.CPU, order=0,
                outputs={"images": [_blob("5" * 64), _blob("6" * 64)]}),
        StepRow(id="d1", run_id="rd", key="sheet", kind="op.sheet", lane=Lane.CPU, order=1,
                outputs={"sheet": [_blob("7" * 64)]}),
    ])
    got = await repo.run_preview_hashes(["ra", "rb", "rc", "rd", "zz"])
    assert got == {"ra": "3" * 64, "rb": "4" * 64, "rd": "5" * 64}
    assert await repo.run_preview_hashes([]) == {}


async def test_list_runs_batch_filters(repo: SqliteRepo) -> None:
    for i, batch in enumerate(["b1", None, "b2", "b1"]):
        params = {"$meta": {"batch_id": batch}} if batch else {}
        await repo.create_run(RunRow(id=f"r{i}", recipe_id="x", recipe_hash="h", created_at=float(i), params=params))
    assert [r.id for r in await repo.list_runs(batched=True)] == ["r3", "r2", "r0"]
    assert [r.id for r in await repo.list_runs(batch_id="b1")] == ["r3", "r0"]
