from __future__ import annotations

import asyncio
import io
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image, ImageDraw
from test_engine_runner import Env, FakeExec
from test_ops_executors import FakeCtx, put_img

from studio_core.contracts import Lane, RunStatus, StepError, StepResult
from studio_core.engine.kinds import CATALOG
from studio_core.engine.library import validate
from studio_core.engine.recipe import load_recipe_file
from studio_core.export.sheet import layout
from studio_core.ops import item_fx as fx

RECIPES = Path(__file__).resolve().parents[1] / "studio_core" / "recipes"


def coin(size: int = 300) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((10, 10, size - 10, size - 10), fill=(230, 170, 40, 255), outline=(120, 70, 10, 255), width=8)
    d.rectangle((size // 2 - 40, 80, size // 2 + 20, size - 80), fill=(255, 220, 90, 255))
    # Soft edge so the fringe check has semi-transparent pixels to measure.
    return Image.fromarray(np.asarray(img.resize((size // 2, size // 2), Image.Resampling.LANCZOS)), "RGBA")


def button() -> Image.Image:
    img = Image.new("RGBA", (400, 160), (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle((4, 4, 395, 155), 40, fill=(110, 86, 207, 255), outline=(60, 40, 140, 255),
                                          width=8)
    return img.resize((200, 80), Image.Resampling.LANCZOS)


def turntable(n: int, res: int = 200) -> list[Image.Image]:
    """An off-centre box turning about the frame's centre column, as Blender's ortho turntable renders it."""
    out = []
    for i in range(n):
        a = 2 * math.pi * i / n
        img = Image.new("RGBA", (res, res), (0, 0, 0, 0))
        corners = [(-30, -12), (50, -12), (50, 12), (-30, 12)]
        xs = [cx * math.cos(a) - cz * math.sin(a) for cx, cz in corners]
        x0, x1 = res / 2 + min(xs), res / 2 + max(xs)
        ImageDraw.Draw(img).rectangle((x0, 60, x1, 160), fill=(90, 160, 220, 255))
        out.append(img)
    return out


def test_spin_2d_loops_keeps_axis_and_edges() -> None:
    cells, pivot = fx.spin_2d(coin(), 16, 128, thickness=6)
    assert len(cells) == 16 and pivot == (64, 64) and all(c.size == (128, 128) for c in cells)
    seam = fx.loop_seam(cells)
    assert seam["seam_ok"] and seam["seam_diff"] <= seam["step_diff_max"]
    assert fx.axis_error(cells, pivot[0]) <= 1.0
    ratio = fx.fringe_ratio(cells, [coin()])
    assert ratio is not None and ratio >= 0.85
    # Edge-on frame still shows the rim, the back half is darker than the front.
    a4 = np.asarray(cells[4])[..., 3]
    assert 3 <= (a4.max(0) > 128).sum() <= 12
    lum = [float(np.asarray(c.convert("L"))[np.asarray(c)[..., 3] > 200].mean()) for c in (cells[0], cells[8])]
    assert lum[1] < lum[0] * 0.8
    with pytest.raises(ValueError):
        fx.spin_2d(coin(), 16, 128, back="shiny")


def test_spin_cells_center_the_turntable_axis() -> None:
    renders = turntable(12)
    cells, pivot = fx.spin_cells(renders, 100.0, 96)
    assert pivot == (48, 48) and len(cells) == 12
    assert fx.axis_error(cells, pivot[0]) <= 1.0
    assert fx.loop_seam(cells)["seam_ok"]


def test_ui_juice_is_periodic_and_still() -> None:
    effects = ["shine", "glow", "bob", "sparkle"]
    cells, pivot, tops = fx.ui_juice(button(), 24, 256, effects, fx.parse_hex("#fff4c2"), seed=3)
    w, h = cells[0].size
    assert max(w, h) <= 258 and w % 2 == 0 and h % 2 == 0 and pivot == (w // 2, h // 2)
    assert fx.loop_seam(cells)["seam_ok"]
    assert fx.core_drift(cells, tops) <= 1.0
    again, _, _ = fx.ui_juice(button(), 24, 256, effects, fx.parse_hex("#fff4c2"), seed=3)
    assert all(np.array_equal(np.asarray(a), np.asarray(b)) for a, b in zip(cells, again))
    corner = np.asarray(cells[6])[0:3, 0:3, 3]
    assert corner.max() == 0
    with pytest.raises(ValueError):
        fx.parse_effects("shine,wobble")
    with pytest.raises(ValueError):
        fx.parse_hex("#zzz")


def test_previews_are_animated() -> None:
    cells, _ = fx.spin_2d(coin(), 12, 64)
    gif, webp = fx.previews(cells, 12)
    g, w = Image.open(io.BytesIO(gif)), Image.open(io.BytesIO(webp))
    assert g.n_frames == 12 and w.n_frames == 12 and w.mode == "RGBA"


async def test_executors_report(tmp_path: Path) -> None:
    ctx = FakeCtx(tmp_path)
    ctx.job_dir.mkdir(parents=True)
    ref = await put_img(ctx, coin(), "coin.png")
    res = await fx.ItemSpin().run(ctx, {"frames": 12, "size": 96}, {"images": [ref]})
    rep = json.loads(ctx.blobs.path(res.outputs["json"][0].hash).read_bytes())
    assert rep["cols"] == 4 and rep["seam_ok"] and rep["axis_error_px"] <= 1.0 and len(res.outputs["images"]) == 12
    assert res.outputs["webp"][0].mime == "image/webp" and res.outputs["gif"][0].name == "spin_loop.gif"
    piv = json.loads(ctx.blobs.path(res.outputs["pivot"][0].hash).read_bytes())
    assert piv == {"pivot": [48, 48], "width": 96, "height": 96}
    res = await fx.UiJuice().run(ctx, {"effects": "glow,bob", "frames": 16, "size": 128}, {"images": [ref]})
    assert res.meta["drift_px"] <= 1.0 and res.meta["seam_ok"]
    with pytest.raises(StepError):
        await fx.UiJuice().run(ctx, {"effects": "boom"}, {"images": [ref]})
    with pytest.raises(StepError):
        await fx.ItemSpin().run(ctx, {"frames": 2}, {"images": [ref]})


def test_recipes_lint() -> None:
    for name in ("item-spin", "item-spin-image", "ui-juice"):
        text = (RECIPES / f"{name}.yaml").read_text(encoding="utf-8")
        assert validate(text, None)[1] == []
        load_recipe_file(RECIPES / f"{name}.yaml")
    for kind in ("op.spin_frames", "op.item_spin", "op.ui_juice"):
        assert CATALOG[kind]["label"]


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class FakeCleanup(FakeExec):
    def __init__(self) -> None:
        super().__init__("blender.cleanup", Lane.BLENDER)

    async def run(self, ctx: Any, params: dict[str, Any], inputs: Any) -> StepResult:
        return StepResult(outputs={"meshes": list(inputs["mesh"])})


class FakeTurntable(FakeExec):
    def __init__(self) -> None:
        super().__init__("blender.render_dirs", Lane.BLENDER)
        self.params: list[dict[str, Any]] = []

    async def run(self, ctx: Any, params: dict[str, Any], inputs: Any) -> StepResult:
        self.params.append(params)
        n = int(params["count"])
        imgs = [await ctx.blobs.put_bytes(_png(f), name=f"dir_{round(i * 360 / n):03d}.png", mime="image/png")
                for i, f in enumerate(turntable(n))]
        meta = {"pivot": [100.0, 160.0], "width": 200, "height": 200, "angles": [i * 360 / n for i in range(n)]}
        ref = await ctx.blobs.put_bytes(json.dumps(meta).encode(), name="render.json", mime="application/json")
        return StepResult(outputs={"images": imgs, "meta": [ref]})


def _env(tmp_path: Path, execs: list[FakeExec]) -> Env:
    from studio_core.ops.executors import Sheet

    env = Env(tmp_path, execs, {n: (RECIPES / f"{n}.yaml").read_text(encoding="utf-8")
                                for n in ("item-spin", "item-spin-image", "ui-juice")})
    for cls in fx.ITEM_FX_OPS:
        env.registry.register(cls())
    env.registry.register(Sheet())
    return env


async def _wait(env: Env, recipe: str, inputs: dict[str, Any]) -> dict[str, Any]:
    run_id = await env.engine.start_run(recipe, inputs)
    await asyncio.wait_for(env.engine.wait_idle(run_id), 60)
    steps = env.steps(run_id)
    assert env.status(run_id) == RunStatus.SUCCEEDED, {k: s.error for k, s in steps.items() if s.error}
    return steps


async def test_item_spin_recipe_runs_and_exports(tmp_path: Path) -> None:
    from studio_core.export import godot, unity
    from studio_core.store.bundle import engine_source

    tt = FakeTurntable()
    env = _env(tmp_path, [FakeCleanup(), tt])
    mesh = await env.blobs.put_bytes(b"glTF", name="coin.glb", mime="model/gltf-binary")
    steps = await _wait(env, "item-spin", {"mesh": mesh.hash, "frames": 12, "size": 64, "review": False})
    assert tt.params[0]["count"] == 12
    meta = json.loads(env.blobs.path(steps["spin"].outputs["meta"][0].hash).read_bytes())
    assert meta["flipbook"] == {"fps": 12.0, "loop": True} and meta["cols"] == 4 and meta["pivot"] == [32, 32]
    lay = layout(meta, "spin")
    assert [t.name for t in lay.tracks] == ["spin"] and len(lay.tracks[0].cells) == 12 and lay.tracks[0].loop
    src = engine_source(steps["spin"], list(steps.values()), env.blobs, "Coin")
    u = dict(unity.export(src))
    clip = u["Animations/spin.anim"].decode()
    assert clip.count("    - time:") == 12 and "m_LoopTime: 1" in clip
    tres = dict(godot.export(src))["Coin.tres"].decode()
    assert '"spin"' in tres and '"loop": true' in tres


async def test_item_spin_image_and_ui_juice_recipes(tmp_path: Path) -> None:
    env = _env(tmp_path, [])
    ref = await env.blobs.put_bytes(_png(coin()), name="coin.png", mime="image/png")
    steps = await _wait(env, "item-spin-image", {"image": ref.hash, "frames": 16, "size": 64, "review": False})
    meta = json.loads(env.blobs.path(steps["spin"].outputs["meta"][0].hash).read_bytes())
    assert meta["count"] == 16 and meta["cols"] == 4
    steps = await _wait(env, "ui-juice", {"image": ref.hash, "effects": "shine,glow,sparkle", "frames": 16,
                                          "size": 128, "review": False})
    meta = json.loads(env.blobs.path(steps["juice"].outputs["meta"][0].hash).read_bytes())
    assert meta["count"] == 16 and meta["flipbook"]["loop"]
