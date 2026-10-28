import asyncio
import io
import json
from pathlib import Path

import blake3
import numpy as np
import pytest
from PIL import Image

from studio_core.contracts import BlobRef, ExecutorRegistry, Executor, Lane, StepError
from studio_core.ops import register
from studio_core.ops.executors import Sheet, ops_source_hash


class FakeBlobs:
    def __init__(self, root: Path):
        self.root = root

    async def put_bytes(self, data: bytes, name: str | None = None, mime: str | None = None) -> BlobRef:
        h = blake3.blake3(data).hexdigest()
        (self.root / h).write_bytes(data)
        return BlobRef(hash=h, mime=mime or "application/octet-stream", size=len(data), name=name)

    async def put_file(self, src: Path, name: str | None = None, mime: str | None = None) -> BlobRef:
        return await self.put_bytes(src.read_bytes(), name or src.name, mime)

    async def get(self, hash: str) -> BlobRef | None:
        return None

    def path(self, hash: str) -> Path:
        return self.root / hash


class FakeCtx:
    def __init__(self, root: Path):
        self.run_id = "r"
        self.step_id = "s"
        self.job_dir = root / "job"
        self.blobs = FakeBlobs(root)
        self.cancelled = asyncio.Event()
        self.progress_calls: list[tuple[float, float]] = []

    def progress(self, value: float, max: float = 1.0, label: str | None = None, node: str | None = None) -> None:
        self.progress_calls.append((value, max))

    def log(self, msg: str, level: str = "info") -> None:
        pass

    def preview(self, data: bytes, mime: str = "image/jpeg") -> None:
        pass


@pytest.fixture
def ctx(tmp_path: Path) -> FakeCtx:
    return FakeCtx(tmp_path)


@pytest.fixture
def reg() -> ExecutorRegistry:
    r = ExecutorRegistry()
    assert register(r, None, None, None) == []
    return r


async def put_img(ctx: FakeCtx, img: Image.Image, name: str) -> BlobRef:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return await ctx.blobs.put_bytes(buf.getvalue(), name=name, mime="image/png")


def load(ctx: FakeCtx, ref: BlobRef) -> Image.Image:
    with Image.open(ctx.blobs.path(ref.hash)) as i:
        return i.convert("RGBA")


def figure(w: int, h: int, box: tuple[int, int, int, int]) -> Image.Image:
    img = Image.new("RGBA", (w, h))
    img.paste((50, 120, 200, 255), box)
    return img


def test_register_all_ops(reg: ExecutorRegistry):
    kinds = {"op.trim_pad_resize", "op.normalize", "op.mirror_dirs", "op.sheet", "op.downscale", "op.pixelate"}
    assert kinds <= set(reg.kinds())
    for k in kinds:
        ex = reg.resolve(k)
        assert isinstance(ex, Executor)
        assert ex.lane == Lane.CPU and ex.version == "1" and ex.cacheable({}) and ex.cache_material({}) == ops_source_hash()


async def test_trim_pad_resize_executor(ctx: FakeCtx, reg: ExecutorRegistry):
    refs = [await put_img(ctx, figure(200, 100, (20, 10, 80, 90)), f"a{i}.png") for i in range(2)]
    res = await reg.resolve("op.trim_pad_resize").run(ctx, {"size": 64, "pad": 0.0}, {"images": refs})
    out = res.outputs["images"]
    assert len(out) == 2 and out[0].mime == "image/png" and out[0].name == "a0_fit.png"
    assert load(ctx, out[0]).size == (64, 64)
    assert ctx.progress_calls[-1] == (2, 2)
    flat = await reg.resolve("op.trim_pad_resize").run(ctx, {"size": 64, "background": "white"}, {"images": refs})
    a = np.asarray(load(ctx, flat.outputs["images"][0]).convert("RGBA"))
    assert (a[..., 3] == 255).all() and (a[0, 0, :3] == 255).all()


async def test_normalize_executor_meta_pivot(ctx: FakeCtx, reg: ExecutorRegistry):
    refs = [await put_img(ctx, figure(100, 100, (40, 10 + i * 20, 60, 90)), f"d{i}.png") for i in range(3)]
    res = await reg.resolve("op.normalize").run(ctx, {"size": 50, "pad": 0.1}, {"images": refs})
    assert res.meta["pivot"] == [25, 45]
    for ref in res.outputs["images"]:
        a = np.asarray(load(ctx, ref))[..., 3]
        assert np.nonzero(a.any(axis=1))[0].max() == 44


async def test_mirror_dirs_executor(ctx: FakeCtx, reg: ExecutorRegistry):
    refs = [await put_img(ctx, figure(20, 20, (i, 0, i + 2, 5)), f"d{i}.png") for i in range(5)]
    res = await reg.resolve("op.mirror_dirs").run(ctx, {"count": 8}, {"images": refs})
    out = res.outputs["images"]
    assert len(out) == 8 and out[:5] == refs
    assert res.meta["angles"] == [0, 45, 90, 135, 180, 225, 270, 315]
    assert out[5].name == "dir_225.png"
    assert load(ctx, out[5]).getpixel((20 - 3 - 1, 0))[3] == 255
    with pytest.raises(StepError):
        await reg.resolve("op.mirror_dirs").run(ctx, {"count": 8}, {"images": refs[:3]})


async def test_sheet_executor(ctx: FakeCtx, reg: ExecutorRegistry):
    refs = [await put_img(ctx, figure(32, 32, (0, 0, 32, 32)), f"d{i}.png") for i in range(8)]
    res = await reg.resolve("op.sheet").run(ctx, {"cols": 8, "pivot": [16, 30]}, {"images": refs})
    sheet = load(ctx, res.outputs["sheet"][0])
    assert sheet.size == (256, 32)
    meta_ref = res.outputs["meta"][0]
    assert meta_ref.mime == "application/json"
    meta = json.loads(ctx.blobs.path(meta_ref.hash).read_bytes())
    assert meta["cols"] == 8 and meta["rows"] == 1 and meta["frame_w"] == 32 and meta["pivot"] == [16, 30]
    assert meta["frames"] == [r.hash for r in refs]
    flip = await reg.resolve("op.sheet").run(ctx, {"cols": 4, "flipbook": "loop", "fps": 12}, {"images": refs})
    fmeta = json.loads(ctx.blobs.path(flip.outputs["meta"][0].hash).read_bytes())
    assert fmeta["flipbook"] == {"fps": 12.0, "loop": True} and fmeta["pivot"] == [16, 16]


async def test_downscale_and_pixelate_executors(ctx: FakeCtx, reg: ExecutorRegistry):
    ref = await put_img(ctx, figure(128, 128, (32, 32, 96, 96)), "x.png")
    down = await reg.resolve("op.downscale").run(ctx, {"factor": 2}, {"images": [ref]})
    assert load(ctx, down.outputs["images"][0]).size == (64, 64)
    px = await reg.resolve("op.pixelate").run(ctx, {"size": 32, "palette_colors": 8, "outline": "#101010"},
                                              {"images": [ref]})
    img = load(ctx, px.outputs["images"][0])
    assert img.size == (32, 32) and img.getpixel((7, 16)) == (16, 16, 16, 255)


async def test_empty_input_raises(ctx: FakeCtx, reg: ExecutorRegistry):
    with pytest.raises(StepError) as e:
        await reg.resolve("op.sheet").run(ctx, {}, {"images": []})
    assert e.value.code == "op_no_input"


def test_op_cache_material_follows_source(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from studio_core.ops import executors

    assert len(ops_source_hash()) == 64
    assert Sheet().cache_material({"preset": "unity"}).startswith(ops_source_hash() + "|")
    (tmp_path / "a.py").write_text("x = 1")
    monkeypatch.setattr(executors, "__file__", str(tmp_path / "executors.py"))
    ops_source_hash.cache_clear()
    try:
        first = ops_source_hash()
        (tmp_path / "a.py").write_text("x = 2")
        ops_source_hash.cache_clear()
        assert ops_source_hash() != first
    finally:
        monkeypatch.undo()
        ops_source_hash.cache_clear()


async def test_sheet_row_angles_from_clipless_anim(ctx: FakeCtx, reg: ExecutorRegistry):
    refs = [await put_img(ctx, figure(16, 16, (0, 0, 16, 16)), f"f{i}.png") for i in range(8 * 4)]
    angles = [0, 45, 90, 135, 180, 225, 270, 315]
    anim = await ctx.blobs.put_bytes(json.dumps({"pivot": [8, 15], "width": 16, "height": 16,
                                                 "angles": angles}).encode(), name="anim.json")
    res = await reg.resolve("op.sheet").run(ctx, {"cols": 4}, {"images": refs, "pivot": [anim]})
    assert res.meta["rows"] == 8 and res.meta["row_angles"] == [float(a) for a in angles]
    assert "angles" not in res.meta

    short = await ctx.blobs.put_bytes(json.dumps({"pivot": [8, 15], "angles": [0, 90]}).encode(), name="a.json")
    res = await reg.resolve("op.sheet").run(ctx, {"cols": 4}, {"images": refs, "pivot": [short]})
    assert "row_angles" not in res.meta


async def test_static_sheet_writes_angles(ctx: FakeCtx, reg: ExecutorRegistry):
    refs = [await put_img(ctx, figure(16, 16, (0, 0, 16, 16)), f"d{i}.png") for i in range(8)]
    order = [0, 45, 90, 135, 180, 225, 270, 315]
    res = await reg.resolve("op.sheet").run(ctx, {"cols": 8, "order": order}, {"images": refs})
    assert res.meta["angles"] == order and "row_angles" not in res.meta
    res = await reg.resolve("op.sheet").run(ctx, {"cols": 1, "order": order}, {"images": refs})
    assert res.meta["row_angles"] == order
    res = await reg.resolve("op.sheet").run(ctx, {"preset": "unity"}, {"images": refs})
    assert len(res.meta["angles"]) == 8 and sorted(res.meta["angles"]) == order
