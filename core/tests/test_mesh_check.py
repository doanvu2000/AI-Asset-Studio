from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from studio_core.adapters.comfy import CheckedMeshExecutor
from studio_core.contracts import ExecutorRegistry, Lane, Outputs, StepError, StepResult
from test_adapters_fakes import FakeCtx
from test_p7_viewcheck import figure, png

KEYS = ["122.image", "401.image", "402.image", "403.image"]
AZIMUTHS = dict(zip(KEYS, [0, 90, 180, 270]))


class FakeMesh:
    """Stands in for comfy.img2mesh_*: each call returns a GLB whose bytes say good/bad per the script."""
    kind = "comfy.fake_mesh"
    lane = Lane.GPU
    version = "1"
    entry = {"images": [{"key": k} for k in KEYS], "outputs": [{"node": "322"}]}
    workflow_path = None

    def __init__(self, script: list[bool]):
        self.script = script
        self.calls: list[dict[str, Any]] = []

    def cacheable(self, params: dict[str, Any]) -> bool:
        return False

    def cache_material(self, params: dict[str, Any]) -> str:
        return ""

    async def run(self, ctx: Any, params: dict[str, Any], inputs: Outputs) -> StepResult:
        good = self.script[min(len(self.calls), len(self.script) - 1)]
        self.calls.append(params)
        ref = await ctx.blobs.put_bytes(f"{'good' if good else 'bad'} {len(self.calls)}".encode(), name="m.glb")
        return StepResult(outputs={"meshes": [ref]}, meta={"seeds": {"s": len(self.calls)}, "seconds": 1.0})


class FakeBlender:
    """render_dirs: a good mesh renders the input figure at every angle, a bad one a squat blob."""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    async def call(self, cmd: str, params: dict[str, Any], **kw: Any) -> dict[str, Any]:
        self.calls.append(params)
        if self.fail:
            raise StepError("blender_crashed", "boom")
        good = Path(params["glb"]).read_bytes().startswith(b"good")
        out = Path(params["out_dir"])
        out.mkdir(parents=True, exist_ok=True)
        frames = []
        for a in (0, 90, 180, 270):
            img = figure(width=120 if a % 180 == 0 else 70, alpha=True) if good else figure(lying=True, alpha=True)
            f = out / f"dir_{a:03d}.png"
            img.save(f)
            frames.append({"angle": float(a), "file": str(f)})
        return {"frames": frames}


async def run_mesh(tmp_path: Path, script: list[bool], blender: Any = None, **params: Any
                   ) -> tuple[FakeMesh, FakeCtx, StepResult]:
    gen = FakeMesh(script)
    reg = ExecutorRegistry()
    reg.register(gen)
    ex = CheckedMeshExecutor(reg.resolve, blender=blender if blender is not None else FakeBlender())
    ctx = FakeCtx(tmp_path)
    images = [await ctx.blobs.put_bytes(png(figure(width=w)), name=f"{i}.png")
              for i, w in enumerate((120, 70, 120, 70))]
    res = await ex.run(ctx, {"workflow": "fake_mesh", "azimuths": AZIMUTHS, "186.target_face_count": 9000,
                             "_restart_before": True, **params}, {"image": images})
    return gen, ctx, res


def report_of(ctx: FakeCtx, res: StepResult) -> dict[str, Any]:
    return json.loads(ctx.blobs.path(res.outputs["report"][0].hash).read_text(encoding="utf-8"))


async def test_good_mesh_first_try(tmp_path: Path) -> None:
    gen, ctx, res = await run_mesh(tmp_path, [True])
    assert len(gen.calls) == 1
    assert gen.calls[0] == {"186.target_face_count": 9000, "_restart_before": True}
    rep = report_of(ctx, res)
    assert rep["passed"] and rep["regenerated"] == 0
    assert set(rep["attempts"][0]["iou"]) == {"0", "90", "180", "270"} and rep["attempts"][0]["min_iou"] > 0.9


async def test_bad_mesh_regenerated(tmp_path: Path) -> None:
    gen, ctx, res = await run_mesh(tmp_path, [False, True])
    assert len(gen.calls) == 2
    assert ctx.blobs.path(res.outputs["meshes"][0].hash).read_bytes() == b"good 2"
    rep = report_of(ctx, res)
    assert [a["ok"] for a in rep["attempts"]] == [False, True] and res.meta["seeds"] == {"s": 2}


async def test_never_good_keeps_best_or_fails(tmp_path: Path) -> None:
    gen, ctx, res = await run_mesh(tmp_path, [False], attempts=2)
    assert len(gen.calls) == 2 and not report_of(ctx, res)["passed"] and res.meta["passed"] is False
    with pytest.raises(StepError) as e:
        await run_mesh(tmp_path / "f", [False], attempts=2, on_fail="fail")
    assert e.value.code == "mesh_rejected"


async def test_render_error_fails_open(tmp_path: Path) -> None:
    gen, ctx, res = await run_mesh(tmp_path, [False], blender=FakeBlender(fail=True))
    assert len(gen.calls) == 1 and report_of(ctx, res)["attempts"][0]["error"] == "blender_crashed"


async def test_unmapped_images_not_scored(tmp_path: Path) -> None:
    gen = FakeMesh([True])
    reg = ExecutorRegistry()
    reg.register(gen)
    ex = CheckedMeshExecutor(reg.resolve, blender=FakeBlender())
    assert ex._image_azimuths(gen, {"azimuths": {"122.image": 0}}, 3) == [0.0, None, None]
