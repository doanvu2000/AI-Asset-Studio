from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from studio_core.adapters.claude import ClaudeReply
from studio_core.adapters.image_layers import (DEFAULT_CAPTION, ClaudeLayersCaptionExecutor,
                                               ClaudeLayersNamesExecutor)
from studio_core.bootstrap import plan
from studio_core.contracts import ExecutorRegistry, RunStatus, StepError, StepResult
from studio_core.engine import register as register_gates
from studio_core.engine.kinds import CATALOG, comfy_entries
from studio_core.engine.library import lint, validate
from studio_core.engine.recipe import load_recipe_file
from studio_core.ops import register
from studio_core.ops.image_layers import (LAYERS_TYPE, PSD_MIME, ImageLayers, LayersExport, LayersPrep, alpha_clean,
                                          composite, crop_back, detect_background, fit_to_grid, layer_names,
                                          occlusion_order, recompose_error)
from studio_core.ops.psd import packbits_row, write_psd
from test_engine_runner import Env, FakeExec
from test_ops_executors import FakeCtx, put_img

ROOT = Path(__file__).resolve().parents[1] / "studio_core"
RECIPES = ROOT / "recipes"
W, H = 300, 200


def _shapes() -> tuple[np.ndarray, np.ndarray]:
    """Back: a blue box; front: a red disc overlapping it. Straight-alpha float RGBA at W x H."""
    y, x = np.mgrid[0:H, 0:W]
    back = np.zeros((H, W, 4), np.float32)
    back[40:160, 40:200] = (0.1, 0.3, 0.9, 1.0)
    front = np.zeros((H, W, 4), np.float32)
    disc = (x - 190) ** 2 + (y - 110) ** 2 < 55 ** 2
    front[disc] = (0.9, 0.15, 0.1, 1.0)
    return back, front


def _source() -> np.ndarray:
    back, front = _shapes()
    return composite([back, front])


def _u8img(a: np.ndarray) -> Image.Image:
    return Image.fromarray(np.round(np.clip(a, 0, 1) * 255).astype(np.uint8), "RGBA")


def _to_grid(a: np.ndarray, crop: dict[str, Any]) -> np.ndarray:
    gw, gh = crop["grid"]
    x, y, cw, ch = crop["box"]
    out = np.zeros((gh, gw, 4), np.float32)
    out[y:y + ch, x:x + cw] = np.asarray(_u8img(a).resize((cw, ch), Image.Resampling.NEAREST), np.float32) / 255
    return out


def _model_layers(crop: dict[str, Any], swap: bool = True) -> list[np.ndarray]:
    """What Qwen-Image-Layered returns: a white plate first, then the shapes (front before back when `swap`)."""
    gw, gh = crop["grid"]
    plate = np.ones((gh, gw, 4), np.float32)
    back, front = _shapes()
    shapes = [front, back] if swap else [back, front]
    return [plate] + [_to_grid(s, crop) for s in shapes]


def test_fit_to_grid_keeps_aspect_and_pads_white() -> None:
    img = _u8img(_source())
    canvas, crop = fit_to_grid(img, 1024)
    gw, gh = crop["grid"]
    assert canvas.size == (gw, gh) and gw % 32 == 0 and gh % 32 == 0
    assert abs(gw * gh - 1024 * 1024) / (1024 * 1024) < 0.08
    x, y, cw, ch = crop["box"]
    assert abs(cw / ch - W / H) < 0.02 and x + cw <= gw and y + ch <= gh and crop["source"] == [W, H]
    tall, c2 = fit_to_grid(Image.new("RGBA", (100, 1000), (0, 0, 0, 0)), 640)
    assert c2["grid"][1] / c2["grid"][0] <= 2.01 and c2["box"][0] > 0
    assert tall.getpixel((0, 0)) == (255, 255, 255) and tall.getpixel((c2["grid"][0] // 2, 50)) == (255, 255, 255)


def test_crop_back_round_trip() -> None:
    src = _source()
    _, crop = fit_to_grid(_u8img(src), 640)
    back = crop_back(_to_grid(src, crop), crop)
    assert back.shape == (H, W, 4)
    assert recompose_error([back], src) < 6


def test_detect_background() -> None:
    _, crop = fit_to_grid(_u8img(_source()), 640)
    layers = _model_layers(crop)
    assert detect_background(layers, tuple(crop["box"])) == (0, True)
    rng = np.random.default_rng(1)
    noisy = np.concatenate([rng.random(layers[0].shape[:2] + (3,)), np.ones(layers[0].shape[:2] + (1,))], -1)
    assert detect_background([layers[1], noisy.astype(np.float32)], tuple(crop["box"])) == (1, False)
    assert detect_background(layers[1:], tuple(crop["box"])) == (None, False)


def test_alpha_clean_bleeds_edge_colour() -> None:
    a = np.zeros((20, 20, 4), np.float32)
    a[5:15, 5:15] = (0.2, 0.6, 0.3, 1.0)
    a[0, 0] = (1, 1, 1, 0.004)
    out = alpha_clean(a)
    assert out[0, 0, 3] == 0
    assert np.allclose(out[4, 10, :3], (0.2, 0.6, 0.3), atol=1e-5) and out[4, 10, 3] == 0
    assert np.allclose(out[0, 19, :3], (0.2, 0.6, 0.3), atol=1e-5)
    assert np.array_equal(out[5:15, 5:15], a[5:15, 5:15])


def test_occlusion_order_and_recompose_error() -> None:
    back, front = _shapes()
    src = composite([back, front])
    assert occlusion_order([front, back], src) == [1, 0]
    assert occlusion_order([back, front], src) == [0, 1]
    assert recompose_error([back, front], src) == 0
    assert recompose_error([front, back], src) > 5
    assert recompose_error([back], src) > 5


def test_layer_names() -> None:
    assert layer_names(["Back Cape & Arms", "legs", "legs", ""], 4) == ["back_cape_arms", "legs", "legs_2", "layer_04"]
    assert layer_names(None, 2) == ["layer_01", "layer_02"]


def decode_packbits(data: bytes, size: int) -> bytes:
    out, i = bytearray(), 0
    while i < len(data):
        n = data[i]
        if n < 128:
            out += data[i + 1:i + 2 + n]
            i += 2 + n
        elif n > 128:
            out += bytes([data[i + 1]]) * (257 - n)
            i += 2
        else:
            i += 1
    assert len(out) == size
    return bytes(out)


def test_packbits_runs_and_literals() -> None:
    for row in (bytes(300), bytes(range(256)) * 2, b"\x01\x01\x02\x03\x03\x03\x03" * 40 + b"\x09"):
        assert decode_packbits(packbits_row(row), len(row)) == row


def test_psd_round_trip_with_psd_tools() -> None:
    from psd_tools import PSDImage

    back, front = _shapes()
    front[150:190, 10:30] = (0.0, 1.0, 0.0, 0.5)
    layers = [("lưng & áo", np.round(back * 255).astype(np.uint8)), ("front", np.round(front * 255).astype(np.uint8))]
    data = write_psd(layers)
    psd = PSDImage.open(io.BytesIO(data))
    assert psd.size == (W, H) and [la.name for la in psd] == ["lưng & áo", "front"]
    assert psd[0].bbox == (40, 40, 200, 160) and psd[1].kind == "pixel"
    px = np.asarray(psd[1].topil().convert("RGBA"))
    ox, oy = psd[1].left, psd[1].top
    assert tuple(px[160 - oy, 20 - ox]) == (0, 255, 0, 128)
    merged = np.asarray(psd.topil().convert("RGBA"))
    assert tuple(merged[110, 190]) == (230, 38, 26, 255) and merged[5, 5, 3] == 0
    with pytest.raises(ValueError):
        write_psd([])


async def _pipeline(tmp_path: Path, names: dict[str, Any] | None = None, background: str = "auto"):
    ctx = FakeCtx(tmp_path)
    src_ref = await put_img(ctx, _u8img(_source()), "char.png")
    prep = await LayersPrep().run(ctx, {"resolution": 640}, {"images": [src_ref]})
    crop = json.loads(ctx.blobs.path(prep.outputs["json"][0].hash).read_bytes())
    assert crop["max_side"] == max(crop["grid"])
    layer_refs = [await put_img(ctx, _u8img(a), f"l_{i:05d}_.png") for i, a in enumerate(_model_layers(crop))]
    post = await ImageLayers().run(ctx, {"background": background},
                                   {"images": layer_refs, "source": [src_ref], "crop": prep.outputs["json"]})
    extra = {}
    if names is not None:
        extra["names"] = [await ctx.blobs.put_bytes(json.dumps(names).encode(), name="layer_names.json",
                                                    mime="application/json")]
    exp = await LayersExport().run(ctx, {"asset": "Hero"}, {"images": post.outputs["images"],
                                                            "info": post.outputs["info"], **extra})
    return ctx, post, exp


async def test_ops_pipeline(tmp_path: Path) -> None:
    from psd_tools import PSDImage

    ctx, post, exp = await _pipeline(tmp_path, {"source": "claude", "names": ["Blue Box", "red disc"]})
    info = json.loads(ctx.blobs.path(post.outputs["info"][0].hash).read_bytes())
    assert info["background"] == {"index": 0, "plain": True, "dropped": True}
    assert [e["source_index"] for e in info["layers"]] == [2, 1]
    assert info["metrics"]["recompose_mae"] < 3 and info["metrics"]["recompose_mae_kept"] < 3
    doc = json.loads(ctx.blobs.path(exp.outputs["layers"][0].hash).read_bytes())
    assert doc["type"] == LAYERS_TYPE and doc["size"] == [W, H] and doc["names_source"] == "claude"
    assert [la["name"] for la in doc["layers"]] == ["blue_box", "red_disc"]
    assert [r.name for r in exp.outputs["images"]] == ["Hero_01_blue_box.png", "Hero_02_red_disc.png"]
    box = doc["layers"][0]["bbox"]
    assert abs(box[0] - 40) <= 4 and abs(box[2] - 160) <= 8 and 0.2 < doc["layers"][0]["coverage"] < 0.4
    psd_ref = exp.outputs["psd"][0]
    assert psd_ref.mime == PSD_MIME and psd_ref.name == "Hero.psd"
    psd = PSDImage.open(ctx.blobs.path(psd_ref.hash))
    assert psd.size == (W, H) and [la.name for la in psd] == ["blue_box", "red_disc"]
    with Image.open(ctx.blobs.path(exp.outputs["preview"][0].hash)) as im:
        assert im.width > 900


async def test_ops_keep_background_and_default_names(tmp_path: Path) -> None:
    ctx, post, exp = await _pipeline(tmp_path, None, background="keep")
    doc = json.loads(ctx.blobs.path(exp.outputs["layers"][0].hash).read_bytes())
    assert [la["name"] for la in doc["layers"]] == ["background", "layer_01", "layer_02"]
    assert doc["layers"][0]["background"] and doc["layers"][0]["coverage"] > 0.99
    with pytest.raises(StepError):
        await ImageLayers().run(FakeCtx(tmp_path / "x"), {"background": "nope"}, {})


async def test_export_unity_and_godot(tmp_path: Path) -> None:
    from test_export_engines import _row

    from studio_core.export import godot, unity
    from studio_core.store.bundle import engine_ready, engine_source

    ctx, _, exp = await _pipeline(tmp_path, {"names": ["back", "front"]})
    step = _row("approve", "gate.approve", {k: exp.outputs[k] for k in ("preview", "images", "psd", "layers")})
    assert engine_ready(step, ctx.blobs)
    src = engine_source(step, [], ctx.blobs, "Hero")
    assert [n for n, _ in src.image_layers] == ["back", "front"] and src.image_layers_size == (W, H)
    u = dict(unity.export(src))
    assert {"Layers/Hero_01_back.png", "Layers/Hero_02_front.png", "Layers/Hero_layers.prefab",
            "Layers/Hero_layers.json"} <= set(u)
    prefab = u["Layers/Hero_layers.prefab"].decode()
    assert prefab.count("SpriteRenderer:") == 2 and "m_SortingOrder: 1" in prefab and "m_Name: front" in prefab
    assert "spriteMode: 1" in u["Layers/Hero_01_back.png.meta"].decode()
    assert "Layers/" in u["README.txt"].decode()
    g = dict(godot.export(src))
    scene = g["Hero_layers.tscn"].decode()
    assert scene.count('type="Sprite2D"') == 2 and "z_index = 1" in scene and 'path="Layers/Hero_02_front.png"' in scene


def test_recipe_lints_and_pack() -> None:
    path = RECIPES / "image-layers.yaml"
    r = load_recipe_file(path)
    assert [s.id for s in r.steps] == ["prep", "caption", "split", "post", "names", "export", "approve"]
    text = path.read_text(encoding="utf-8")
    assert validate(text, None)[1] == []
    reg = ExecutorRegistry()
    register(reg)
    register_gates(reg)
    reg.register(ClaudeLayersCaptionExecutor())
    reg.register(ClaudeLayersNamesExecutor())
    reg.register(FakeExec("comfy.qwen_image_layered"))
    assert lint(r, reg, ROOT / "workflows") == []
    assert "comfy.qwen_image_layered" in comfy_entries(ROOT / "workflows")
    for kind in ("op.layers_prep", "op.image_layers", "op.layers_export", "claude.layers_caption",
                 "claude.layers_names"):
        assert kind in CATALOG
    assert plan.recipe_packs(text) == {"image_layers"}
    models = {m["name"]: m for m in plan.pack_models(["image_layers"])}
    assert {"qwen_image_layered_fp8mixed.safetensors", "qwen_image_layered_vae.safetensors",
            "qwen_2.5_vl_7b_fp8_scaled.safetensors"} <= set(models)
    assert all(len(m["sha256"]) == 64 and m["license"] == "Apache-2.0" for m in models.values()
               if m["name"].startswith("qwen_image_layered"))
    inputs = r.coerce_inputs({"image": "h"})
    assert inputs["resolution"] == 1024 and inputs["layers"] == 4


class FakeQuery:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[list[dict[str, Any]], Any]] = []
        self.fail = fail

    async def __call__(self, content: list[dict[str, Any]], options: Any) -> ClaudeReply:
        self.calls.append((content, options))
        if self.fail:
            raise StepError("claude_failed", "offline", retryable=True)
        text = next(c["text"] for c in content if c.get("type") == "text")
        if "caption" in text:
            return ClaudeReply({"caption": "A blue box behind a red disc."}, "", {"cost_usd": 0.01})
        return ClaudeReply({"names": ["blue box", "red disc"]}, "", {"cost_usd": 0.01})


class FakeLayered(FakeExec):
    """Stands in for Qwen-Image-Layered: plate + the two shapes on the prepped grid, front first."""

    def __init__(self) -> None:
        super().__init__("comfy.qwen_image_layered")
        self.params: list[dict[str, Any]] = []

    async def run(self, ctx: Any, params: dict[str, Any], inputs: Any) -> StepResult:
        self.params.append(params)
        with Image.open(ctx.blobs.path(inputs["image"][0].hash)) as im:
            gw, gh = im.size
        assert params["82.largest_size"] == max(gw, gh)
        _, crop = fit_to_grid(_u8img(_source()), 640)
        assert crop["grid"] == [gw, gh]
        out = []
        for i, a in enumerate(_model_layers(crop)):
            buf = io.BytesIO()
            _u8img(a).save(buf, "PNG")
            out.append(await ctx.blobs.put_bytes(buf.getvalue(), name=f"layer_{i:05d}_.png", mime="image/png"))
        return StepResult(outputs={"images": out})


def _env(tmp_path: Path, q: FakeQuery) -> tuple[Env, FakeLayered]:
    fake = FakeLayered()
    env = Env(tmp_path, [fake], {"image-layers": (RECIPES / "image-layers.yaml").read_text(encoding="utf-8")})
    register(env.registry)
    env.registry.register(ClaudeLayersCaptionExecutor(env.settings, q))
    env.registry.register(ClaudeLayersNamesExecutor(env.settings, q))
    return env, fake


async def _run_recipe(env: Env, inputs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    buf = io.BytesIO()
    _u8img(_source()).save(buf, "PNG")
    img = await env.blobs.put_bytes(buf.getvalue(), name="char.png", mime="image/png")
    run_id = await env.engine.start_run("image-layers", {"image": img.hash, "resolution": 640, **inputs},
                                        params={"approve": {"enabled": False}})
    await asyncio.wait_for(env.engine.wait_idle(run_id), 120)
    return run_id, env.steps(run_id)


async def test_recipe_runs_with_fakes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from studio_core.adapters import claude as cl

    monkeypatch.setattr(cl, "_image_block", lambda d, m: {"type": "image", "source": {"type": "base64"}})
    q = FakeQuery()
    env, fake = _env(tmp_path, q)
    run_id, steps = await _run_recipe(env, {"layers": 3, "asset": "Hero"})
    assert env.status(run_id) == RunStatus.SUCCEEDED, {k: s.error for k, s in steps.items() if s.error}
    p = fake.params[0]
    assert p["83:6.text"] == "A blue box behind a red disc." and p["83:118.layers"] == 3 and p["_free_before"]
    assert len(q.calls) == 2 and q.calls[0][1].model == "claude-opus-5-5"
    out = steps["approve"].outputs
    assert sorted(r.name for r in out["images"]) == ["Hero_01_blue_box.png", "Hero_02_red_disc.png"]
    assert out["psd"][0].name == "Hero.psd"


async def test_recipe_user_prompt_and_claude_down(tmp_path: Path) -> None:
    q = FakeQuery(fail=True)
    env, fake = _env(tmp_path, q)
    run_id, steps = await _run_recipe(env, {"prompt": "  two shapes  "})
    assert env.status(run_id) == RunStatus.SUCCEEDED, {k: s.error for k, s in steps.items() if s.error}
    assert fake.params[0]["83:6.text"] == "two shapes" and len(q.calls) == 1
    doc = json.loads(env.blobs.path(steps["approve"].outputs["layers"][0].hash).read_bytes())
    assert [la["name"] for la in doc["layers"]] == ["layer_01", "layer_02"] and doc["names_source"] == "default"
    run2, steps2 = await _run_recipe(env, {"name_layers": False})
    assert env.status(run2) == RunStatus.SUCCEEDED
    assert json.loads(env.blobs.path(steps2["caption"].outputs["json"][0].hash).read_bytes()) == \
        {"source": "default", "prompt": DEFAULT_CAPTION}
