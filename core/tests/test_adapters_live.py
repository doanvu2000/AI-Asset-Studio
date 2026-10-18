"""Live adapter tests (real ComfyUI / Blender / Codex). Run: uv run pytest -m live tests/test_adapters_live.py -s"""
import asyncio
import subprocess
import time
from pathlib import Path

import httpx
import pytest
from PIL import Image

from studio_core.adapters.blender import BlenderRenderDirsExecutor, BlenderResource
from studio_core.adapters.codex import CodexImageExecutor, CodexResource
from studio_core.adapters.comfy import ComfyExecutor, ComfyResource
from studio_core.adapters._winproc import pid_alive
from studio_core.config import Settings
from studio_core.contracts import StepError
from test_adapters_fakes import FakeCtx, copy_blob

pytestmark = pytest.mark.live
GLB = Path(r"L:\AI\setup\comfyUI\output\3d\ComfyUI_00001.glb")


def vram_used_mb() -> int:
    out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                         capture_output=True, text=True).stdout.strip()
    return int(out or 0)


def report(**kw):
    print("\nLIVE", kw, flush=True)


async def test_live_comfy_zimage_managed_spawn_and_crash(tmp_path):
    s = Settings(data_dir=tmp_path / "data")
    res = ComfyResource(s)
    assert (await res.health()).status == "down"
    vram0 = vram_used_mb()
    t0 = time.perf_counter()
    await res.ensure_started()
    boot = time.perf_counter() - t0
    h = await res.health()
    assert res.managed and h.status == "up" and h.detail["vram_free_mb"] > 0
    pids = res.job.pids()
    assert len(pids) >= 2
    try:
        ex = ComfyExecutor("comfy.t2i_zimage_turbo", s, res)
        ctx = FakeCtx(tmp_path)
        t1 = time.perf_counter()
        r = await ex.run(ctx, {"57:27.text": "game icon of a red health potion, white background"}, {})
        cold = time.perf_counter() - t1
        img = ctx.blobs.path(r.outputs["images"][0].hash)
        assert Image.open(img).size[0] >= 512
        assert r.meta["files"][0].replace("\\", "/").find("studio/run1/step1") >= 0
        ctx2 = FakeCtx(tmp_path / "warm", step_id="step2")
        t2 = time.perf_counter()
        await ex.run(ctx2, {"57:27.text": "game icon of a blue mana potion, white background"}, {})
        warm = time.perf_counter() - t2
        vram_loaded = vram_used_mb()
        report(boot_s=round(boot, 1), cold_s=round(cold, 1), warm_s=round(warm, 1),
               progress_events=len(ctx.progress_calls), previews=len(ctx.previews), vram_loaded=vram_loaded)
        assert ctx.progress_calls and ctx.previews

        crashes = []
        res.add_crash_listener(crashes.append)
        ctx3 = FakeCtx(tmp_path / "crash", step_id="step3")
        task = asyncio.create_task(ex.run(ctx3, {"57:27.text": "a sword"}, {}))
        await asyncio.sleep(3)
        child = [p for p in res.job.pids() if p != res.proc.pid] or [res.proc.pid]
        subprocess.run(["taskkill", "/F", "/PID", str(child[-1])], capture_output=True)
        with pytest.raises(StepError) as e:
            await asyncio.wait_for(task, 60)
        assert e.value.code == "comfy_crashed" and e.value.retryable and crashes
        assert (await res.health()).status == "error"
    finally:
        await res.stop()
    await asyncio.sleep(3)
    alive = [p for p in pids if pid_alive(p)]
    vram_after = vram_used_mb()
    report(pids=pids, alive_after_stop=alive, vram_before=vram0, vram_after=vram_after)
    assert not alive
    assert vram_after < vram0 + 500
    with pytest.raises(httpx.HTTPError):
        httpx.get(f"http://127.0.0.1:{s.comfy_port}/system_stats", timeout=2)


async def test_live_blender_render_dirs(tmp_path):
    assert GLB.exists()
    s = Settings(data_dir=tmp_path / "data")
    blender = BlenderResource(s)
    ex = BlenderRenderDirsExecutor(s, blender, None)
    try:
        t0 = time.perf_counter()
        await blender.ensure_started()
        boot = time.perf_counter() - t0
        pids = blender.job.pids()
        ctx = FakeCtx(tmp_path)
        mesh = copy_blob(ctx, GLB, "model/gltf-binary")
        t1 = time.perf_counter()
        r = await ex.run(ctx, {"elevation": 30, "count": 8, "res": 256}, {"mesh": [mesh]})
        first = time.perf_counter() - t1
        assert r.meta["angles"] == [0, 45, 90, 135, 180, 225, 270, 315]
        im = Image.open(ctx.blobs.path(r.outputs["images"][0].hash))
        assert im.mode == "RGBA" and im.size == (256, 256) and im.getpixel((0, 0))[3] == 0
        ctx2 = FakeCtx(tmp_path / "m", step_id="s2")
        t2 = time.perf_counter()
        r2 = await ex.run(ctx2, {"elevation": 30, "count": 8, "res": 256, "mirror": True, "supersample": 2},
                          {"mesh": [copy_blob(ctx2, GLB)]})
        warm = time.perf_counter() - t2
        assert r2.meta["angles"] == [0, 45, 90, 135, 180]
        assert Image.open(ctx2.blobs.path(r2.outputs["images"][0].hash)).size == (512, 512)
        report(blender_boot_warmup_s=round(boot, 1), warmup_s=blender.warmup_s, first_job_s=round(first, 1),
               mirror_ss2_job_s=round(warm, 1), progress=len(ctx.progress_calls))
    finally:
        await blender.stop()
    await asyncio.sleep(1)
    assert not [p for p in pids if pid_alive(p)]


async def test_live_codex_one_image(tmp_path):
    s = Settings(data_dir=tmp_path / "data")
    ex = CodexImageExecutor(s, CodexResource(s))
    ctx = FakeCtx(tmp_path)
    t0 = time.perf_counter()
    r = await ex.run(ctx, {"prompt": "Generate an image: game inventory icon of a golden key, stylized 2D, centered",
                           "transparent": True}, {})
    im = Image.open(ctx.blobs.path(r.outputs["images"][0].hash))
    report(codex_s=round(time.perf_counter() - t0, 1), size=im.size, mode=im.mode, health=(await ex.resource.health()))
    assert im.size[0] >= 512


async def test_live_claude_schema_with_image(tmp_path):
    import json as _json

    from studio_core.adapters.claude import ClaudeTaskExecutor

    ctx = FakeCtx(tmp_path)
    src = tmp_path / "red.png"
    Image.new("RGB", (64, 64), (220, 20, 20)).save(src)
    schema = {"type": "object", "properties": {"color": {"type": "string"}}, "required": ["color"],
              "additionalProperties": False}
    t0 = time.perf_counter()
    r = await ClaudeTaskExecutor(Settings()).run(
        ctx, {"prompt": "What is the dominant color of the image? One lowercase word.", "model": "haiku",
              "schema": schema}, {"images": [copy_blob(ctx, src)]})
    out = _json.loads(ctx.blobs.path(r.outputs["json"][0].hash).read_text(encoding="utf-8"))
    report(claude_s=round(time.perf_counter() - t0, 1), out=out, cost=r.meta["cost_usd"])
    assert "red" in out["color"]
