import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from studio_core.adapters import normal_ai
from studio_core.contracts import StepError
from studio_core.engine.cache import executor_cache_key
from studio_core.export.normal2d import Normal2DOptions, normal_image
from studio_core.export.sheet import Cell
from studio_core.ops.normal2d import Normal2D

from test_ops_executors import FakeCtx, put_img  # noqa: F401

SNAP = Path("hf") / "hub" / "models--Ruicheng--moge-2-vits-normal" / "snapshots"


def _disc(size: int = 64, r: int = 24) -> np.ndarray:
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    c = size // 2
    ImageDraw.Draw(im).ellipse([c - r, c - r, c + r - 1, c + r - 1], fill=(180, 120, 90, 255))
    return np.asarray(im)


def _fake_install(root: Path) -> Path:
    (root / ".venv" / "Scripts").mkdir(parents=True)
    (root / ".venv" / "Scripts" / "python.exe").write_bytes(b"")
    (root / "src" / "moge" / "moge").mkdir(parents=True)
    (root / SNAP / "abc123").mkdir(parents=True)
    (root / SNAP / "abc123" / "model.pt").write_bytes(b"x")
    return root


def _dome(frames: list[np.ndarray]) -> list[np.ndarray]:
    out = []
    for f in frames:
        h, w = f.shape[:2]
        ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
        u, v = (xs - w / 2) / (w / 2), (h / 2 - ys) / (h / 2)
        z = np.sqrt(np.clip(1 - u * u - v * v, 0.05, 1))
        n = np.stack([u * 0.6, v * 0.6, z], -1)
        out.append(n / np.linalg.norm(n, axis=-1, keepdims=True))
    return out


@pytest.fixture
def installed(monkeypatch, tmp_path):
    root = _fake_install(tmp_path / "normal-ai")
    monkeypatch.setenv("STUDIO_NORMAL_AI_DIR", str(root))

    async def fake(ai, frames, cancelled=None):
        return _dome(frames)

    monkeypatch.setattr(normal_ai, "estimate", fake)
    return root


def test_missing_model_is_reported(tmp_path) -> None:
    assert "venv not found" in normal_ai.NormalAi(tmp_path / "nope").missing()
    root = _fake_install(tmp_path / "ok")
    ai = normal_ai.NormalAi(root)
    assert ai.missing() is None and ai.snapshot() == "abc123"
    (root / SNAP / "abc123" / "model.pt").unlink()
    assert "weights" in normal_ai.NormalAi(root).missing()


async def test_ai_normal_is_unit_flat_outside_alpha_and_uses_model(installed) -> None:
    rgba = _disc()
    arr, fell = await normal_ai.normal_image_ai(rgba, Normal2DOptions(), None, normal_ai.locate())
    assert fell == 0
    mask = rgba[..., 3] > 0
    n = arr.astype(np.float32) / 127.5 - 1
    assert np.allclose(np.linalg.norm(n[mask], axis=-1), 1, atol=0.02)
    assert (arr[~mask] == (128, 128, 255)).all()
    assert (arr != normal_image(rgba, Normal2DOptions())).any()
    assert n[32, 50, 0] > 0.2 and n[14, 32, 1] > 0.2


async def test_tiny_and_empty_frames_skip_the_model(installed, monkeypatch) -> None:
    called = []

    async def spy(ai, frames, cancelled=None):
        called.append(len(frames))
        return _dome(frames)

    monkeypatch.setattr(normal_ai, "estimate", spy)
    ai = normal_ai.locate()
    one = np.full((1, 1, 4), 255, np.uint8)
    arr, fell = await normal_ai.normal_image_ai(one, Normal2DOptions(), None, ai)
    assert arr.shape == (1, 1, 3) and fell == 1 and called == []
    arr, fell = await normal_ai.normal_image_ai(np.zeros((32, 32, 4), np.uint8), Normal2DOptions(), None, ai)
    assert fell == 0 and (arr == (128, 128, 255)).all() and called == []
    opaque = np.dstack([_disc()[..., :3], np.full((64, 64), 255, np.uint8)])
    arr, fell = await normal_ai.normal_image_ai(opaque, Normal2DOptions(), None, ai)
    assert fell == 0 and called == [1]


async def test_sheet_cells_each_reach_the_model(installed, monkeypatch) -> None:
    sizes = []

    async def spy(ai, frames, cancelled=None):
        sizes.extend(f.shape[:2] for f in frames)
        return _dome(frames)

    monkeypatch.setattr(normal_ai, "estimate", spy)
    sheet = np.concatenate([_disc(), _disc()], axis=1)
    cells = [Cell("a", 0, 0, 64, 64), Cell("b", 64, 0, 64, 64)]
    arr, fell = await normal_ai.normal_image_ai(sheet, Normal2DOptions(), cells, normal_ai.locate())
    assert arr.shape == (64, 128, 3) and fell == 0 and sizes == [(64, 64), (64, 64)]


async def test_op_without_model_falls_back_to_filter(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("STUDIO_NORMAL_AI_DIR", str(tmp_path / "none"))
    ctx = FakeCtx(tmp_path)
    ref = await put_img(ctx, Image.fromarray(_disc(), "RGBA"), "s.png")
    res = await Normal2D().run(ctx, {}, {"images": [ref]})
    assert res.meta["method"] == "filter" and res.meta["warnings"] == [] and "ai_unavailable" in res.meta
    res = await Normal2D().run(ctx, {"method": "ai"}, {"images": [ref]})
    assert res.meta["method"] == "filter" and "unavailable" in res.meta["warnings"][0]
    with pytest.raises(StepError):
        await Normal2D().run(ctx, {"method": "bogus"}, {"images": [ref]})


async def test_op_ai_runs_and_worker_failure_warns(installed, tmp_path, monkeypatch) -> None:
    ctx = FakeCtx(tmp_path)
    ref = await put_img(ctx, Image.fromarray(_disc(), "RGBA"), "s.png")
    res = await Normal2D().run(ctx, {"method": "ai"}, {"images": [ref]})
    assert res.meta["method"] == "ai" and res.meta["model"] == normal_ai.MODEL_ID and res.meta["warnings"] == []

    async def boom(ai, frames, cancelled=None):
        raise normal_ai.NormalAiError("worker exited 1")

    monkeypatch.setattr(normal_ai, "estimate", boom)
    res = await Normal2D().run(ctx, {"method": "auto"}, {"images": [ref]})
    assert res.meta["method"] == "filter" and "failed" in res.meta["warnings"][0]
    assert np.asarray(Image.open(ctx.blobs.path(res.outputs["images"][0].hash))).shape == (64, 64, 3)


async def test_estimate_mocks_the_subprocess(tmp_path, monkeypatch) -> None:
    async def fake_run(args, env, cancelled, timeout):
        m = json.loads(Path(args[-1]).read_text(encoding="utf-8"))
        assert env["CUDA_VISIBLE_DEVICES"] == "" and env["HF_HUB_OFFLINE"] == "1"
        for it in m["items"]:
            Image.new("RGB", (64, 64), (128, 128, 255)).save(it["out"])
        return ""

    monkeypatch.setattr(normal_ai, "_run", fake_run)
    ai = normal_ai.NormalAi(_fake_install(tmp_path / "other"))
    out = await normal_ai.estimate(ai, [_disc()])
    assert out[0].shape == (64, 64, 3) and abs(out[0][0, 0, 2] - 1) < 0.01

    async def dead(args, env, cancelled, timeout):
        raise normal_ai.NormalAiError("boom")

    monkeypatch.setattr(normal_ai, "_run", dead)
    with pytest.raises(normal_ai.NormalAiError):
        await normal_ai.estimate(ai, [_disc()])


def test_cache_key_depends_on_method_and_model(installed, monkeypatch, tmp_path) -> None:
    op = Normal2D()
    key = lambda m: executor_cache_key(op, op.kind, {"method": m}, {})  # noqa: E731
    ai_key = key("ai")
    assert ai_key != key("filter")
    assert op.cache_material({"method": "auto"}) == op.cache_material({"method": "ai"})
    (installed / SNAP / "abc123").rename(installed / SNAP / "def456")
    assert key("ai") != ai_key
    monkeypatch.setenv("STUDIO_NORMAL_AI_DIR", str(tmp_path / "gone"))
    assert "model" not in op.cache_material({"method": "ai"}) and "Ruicheng" not in op.cache_material({"method": "ai"})


@pytest.mark.skipif(normal_ai.NormalAi(normal_ai.DEFAULT_ROOT).missing() is not None, reason="normal-ai venv not installed")
async def test_live_worker_on_cpu(monkeypatch) -> None:
    monkeypatch.delenv("STUDIO_NORMAL_AI_DIR", raising=False)
    arr, fell = await normal_ai.normal_image_ai(_disc(128, 48), Normal2DOptions(), None, normal_ai.locate())
    mask = _disc(128, 48)[..., 3] > 0
    n = arr.astype(np.float32) / 127.5 - 1
    assert fell == 0 and np.allclose(np.linalg.norm(n[mask], axis=-1), 1, atol=0.02)
