import io
import json
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from studio_core.contracts import StepError
from studio_core.engine.flows import FlowLibrary, validate_def
from studio_core.engine.library import RecipeLibrary, validate
from studio_core.engine.recipe import load_recipe_file
from studio_core.ops.engine_pack import EnginePack
from test_ops_executors import FakeCtx, put_img

CORE = Path(__file__).resolve().parents[1] / "studio_core"
FW, FH = 16, 24


@pytest.fixture
def ctx(tmp_path: Path) -> FakeCtx:
    return FakeCtx(tmp_path)


def _frame(color: tuple[int, int, int]) -> Image.Image:
    im = Image.new("RGBA", (FW, FH), (0, 0, 0, 0))
    im.paste(Image.new("RGBA", (10, 18), (*color, 255)), (3, 4))
    return im


async def _anim(ctx: FakeCtx, n: int, seed: int):
    sheet = Image.new("RGBA", (n * FW, FH), (0, 0, 0, 0))
    for i in range(n):
        sheet.alpha_composite(_frame((seed * 20 + i * 9, 60, 200)), (i * FW, 0))
    meta = {"frame_w": FW, "frame_h": FH, "cols": n, "rows": 1, "count": n, "padding": 0,
            "width": sheet.width, "height": sheet.height, "pivot": [8, 22]}
    return ([await put_img(ctx, sheet, "sheet.png")],
            [await ctx.blobs.put_bytes(json.dumps(meta).encode(), name="sheet.json", mime="application/json")])


async def _dirs(ctx: FakeCtx, n: int = 8):
    return [await put_img(ctx, _frame((i * 25, 200, 40)), f"d{i}.png") for i in range(n)]


def _names(blob_bytes: bytes) -> list[str]:
    return zipfile.ZipFile(io.BytesIO(blob_bytes)).namelist()


async def test_packs_directions_and_clips(ctx):
    inputs = {"frames": await _dirs(ctx)}
    for seed, clip in enumerate(("idle", "attack")):
        inputs[f"sheet_{clip}"], inputs[f"meta_{clip}"] = await _anim(ctx, 4, seed)
    res = await EnginePack().run(ctx, {"asset": "hero"}, inputs)
    man = json.loads(ctx.blobs.path(res.outputs["manifest"][0].hash).read_bytes())
    assert len(man["directions"]) == 8 and man["directions"]["S"]["angle"] == 0
    assert list(man["clips"]) == ["idle", "attack"]
    assert man["clips"]["idle"]["loop"] and not man["clips"]["attack"]["loop"]
    assert len(man["clips"]["idle"]["frames"]) == 4
    unity = _names(ctx.blobs.path(res.outputs["unity"][0].hash).read_bytes())
    godot = _names(ctx.blobs.path(res.outputs["godot"][0].hash).read_bytes())
    assert "hero/hero.png" in unity and "hero/hero.png.meta" in unity and "hero/clips.json" in unity
    assert any(n.endswith("hero_idle_00.tres") for n in godot) and any(n.endswith("hero_dir_E.tres") for n in godot)
    assert res.meta["sprites"] <= 8 + 8 and res.meta["clips"] == ["idle", "attack"]
    readme = zipfile.ZipFile(io.BytesIO(ctx.blobs.path(res.outputs["unity"][0].hash).read_bytes())).read(
        "hero/README.txt").decode()
    assert "từ 10 texture" in readme


async def test_single_engine_and_frames_only(ctx):
    res = await EnginePack().run(ctx, {"asset": "hero", "engines": "godot", "clips": "idle"},
                                 {"frames": await _dirs(ctx)})
    assert "unity" not in res.outputs and "godot" in res.outputs
    assert json.loads(ctx.blobs.path(res.outputs["manifest"][0].hash).read_bytes())["clips"] == {}


async def test_rejects_empty_and_bad_clip(ctx):
    with pytest.raises(StepError):
        await EnginePack().run(ctx, {"clips": "idle"}, {})
    sheet, _ = await _anim(ctx, 4, 0)
    with pytest.raises(StepError):
        await EnginePack().run(ctx, {"clips": "idle"}, {"sheet_idle": sheet})
    bad = await ctx.blobs.put_bytes(json.dumps({"frame_w": FW, "frame_h": FH, "cols": 9, "count": 9}).encode())
    with pytest.raises(StepError):
        await EnginePack().run(ctx, {"clips": "idle"}, {"sheet_idle": sheet, "meta_idle": [bad]})


def test_recipe_valid_and_character_pack_wired(tmp_path):
    r = load_recipe_file(CORE / "recipes" / "character-engine-pack.yaml")
    _, issues = validate((CORE / "recipes" / "character-engine-pack.yaml").read_text(encoding="utf-8"), None)
    assert not [i for i in issues if i.get("level", "error") == "error"]
    recipes = RecipeLibrary(CORE / "recipes", tmp_path / "r")
    flow = FlowLibrary(CORE / "flows", tmp_path / "f").flows["character-pack"]
    node = next(n for n in flow.nodes if n.id == "engine")
    assert set(node.map) <= set(r.inputs) and "frames" in node.map
    assert not [i for i in validate_def(flow, recipes.get) if i.get("level", "error") == "error"]


def _zip_text(ctx, res, engine: str, suffix: str) -> str:
    z = zipfile.ZipFile(io.BytesIO(ctx.blobs.path(res.outputs[engine][0].hash).read_bytes()))
    return next(z.read(n).decode() for n in z.namelist() if n.endswith(suffix))


async def test_engine_files_content_and_deterministic(ctx):
    inputs = {"frames": await _dirs(ctx)}
    inputs["sheet_idle"], inputs["meta_idle"] = await _anim(ctx, 3, 1)
    a = await EnginePack().run(ctx, {"asset": "hero"}, inputs)
    b = await EnginePack().run(ctx, {"asset": "hero"}, inputs)
    assert a.outputs["unity"][0].hash == b.outputs["unity"][0].hash
    meta = _zip_text(ctx, a, "unity", "hero.png.meta")
    assert "guid:" in meta and "spriteMode" in meta
    assert "AtlasTexture" in _zip_text(ctx, a, "godot", "hero_idle_00.tres")


async def test_clip_names_dedupe_case_insensitive(ctx):
    inputs = {"frames": await _dirs(ctx)}
    inputs["sheet_idle"], inputs["meta_idle"] = await _anim(ctx, 2, 1)
    res = await EnginePack().run(ctx, {"asset": "hero", "clips": "Idle, idle ,IDLE"}, inputs)
    man = json.loads(ctx.blobs.path(res.outputs["manifest"][0].hash).read_bytes())
    assert list(man["clips"]) == ["idle"] and len(man["clips"]["idle"]["frames"]) == 2
    sprites = {s for d in man["directions"].values() for s in [d["sprite"]]} | set(man["clips"]["idle"]["frames"])
    assert len(sprites) == 10


async def test_mixed_sizes_and_bad_inputs(ctx):
    big = [await put_img(ctx, Image.new("RGBA", (40, 60), (255, 0, 0, 255)), "b.png")]
    small = await _dirs(ctx)
    res = await EnginePack().run(ctx, {"asset": "hero"}, {"frames": small[:4] + big * 4})
    assert res.meta["sprites"] <= 8
    with pytest.raises(StepError):
        await EnginePack().run(ctx, {}, {"frames": small[:5]})
    with pytest.raises(StepError):
        await EnginePack().run(ctx, {"fps": "abc"}, {"frames": small})
    sheet, _ = await _anim(ctx, 2, 0)
    for meta in ({"frame_w": 0, "frame_h": FH, "cols": 2, "count": 2}, {"frame_w": FW, "frame_h": FH, "cols": -1},
                 {"frame_w": FW, "frame_h": FH, "cols": 2, "rows": 1, "count": 5}):
        m = [await ctx.blobs.put_bytes(json.dumps(meta).encode())]
        with pytest.raises(StepError):
            await EnginePack().run(ctx, {"clips": "idle"}, {"sheet_idle": sheet, "meta_idle": m})


async def _half_anim(ctx: FakeCtx, n: int):
    fw, fh = 32, 48
    sheet = Image.new("RGBA", (n * fw, fh), (0, 0, 0, 0))
    for i in range(n):
        sheet.alpha_composite(Image.new("RGBA", (10, 18), (9, 60, 200, 255)), (i * fw + 11, 20))
    meta = {"frame_w": fw, "frame_h": fh, "cols": n, "rows": 1, "count": n, "padding": 0, "pivot": [16, 38]}
    return ([await put_img(ctx, sheet, "s.png")],
            [await ctx.blobs.put_bytes(json.dumps(meta).encode(), name="s.json", mime="application/json")])


async def test_anim_scale_matches_direction_height(ctx):
    inputs = {"frames": [await put_img(ctx, _tall(), f"t{i}.png") for i in range(8)]}
    inputs["sheet_idle"], inputs["meta_idle"] = await _half_anim(ctx, 2)
    res = await EnginePack().run(ctx, {"asset": "hero", "engines": "godot", "clips": "idle"}, inputs)
    assert res.meta["scaled"] == {"idle": 2.0}
    off = await EnginePack().run(ctx, {"asset": "hero", "engines": "godot", "clips": "idle", "match_scale": False},
                                 inputs)
    assert off.meta["scaled"] == {}


def _tall() -> Image.Image:
    im = Image.new("RGBA", (32, 48), (0, 0, 0, 0))
    im.paste(Image.new("RGBA", (20, 36), (200, 0, 0, 255)), (6, 6))
    return im


async def test_anim_scale_refuses_big_upscale(ctx):
    big = Image.new("RGBA", (32, 96), (0, 0, 0, 0))
    big.paste(Image.new("RGBA", (20, 88), (200, 0, 0, 255)), (6, 4))
    inputs = {"frames": [await put_img(ctx, big, f"b{i}.png") for i in range(8)]}
    inputs["sheet_idle"], inputs["meta_idle"] = await _half_anim(ctx, 2)
    res = await EnginePack().run(ctx, {"asset": "hero", "engines": "godot", "clips": "idle"}, inputs)
    assert res.meta["scaled"] == {} and res.meta["warnings"]


async def test_stray_sized_direction_is_rescaled_on_its_feet(ctx):
    small = Image.new("RGBA", (32, 48), (0, 0, 0, 0))
    small.paste(Image.new("RGBA", (10, 18), (0, 200, 0, 255)), (11, 28))
    frames = [await put_img(ctx, small if i == 0 else _tall(), f"s{i}.png") for i in range(8)]
    res = await EnginePack().run(ctx, {"asset": "hero", "engines": "godot", "clips": "idle"}, {"frames": frames})
    assert res.meta["scaled"] == {"dir_S": 2.0}


async def test_median_direction_height_is_clip_reference(ctx):
    small = Image.new("RGBA", (32, 48), (0, 0, 0, 0))
    small.paste(Image.new("RGBA", (10, 18), (0, 200, 0, 255)), (11, 28))
    inputs = {"frames": [await put_img(ctx, small if i == 0 else _tall(), f"m{i}.png") for i in range(8)]}
    inputs["sheet_idle"], inputs["meta_idle"] = await _half_anim(ctx, 2)
    res = await EnginePack().run(ctx, {"asset": "hero", "engines": "godot", "clips": "idle"}, inputs)
    assert res.meta["scaled"]["idle"] == 2.0
