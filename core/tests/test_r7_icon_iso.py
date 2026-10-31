from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image, ImageDraw
from test_engine_runner import Env, FakeExec
from test_ops_executors import FakeCtx, put_img

from studio_core.config import Settings
from studio_core.contracts import Lane, RunStatus, StepError, StepResult
from studio_core.engine.kinds import CATALOG
from studio_core.engine.library import validate
from studio_core.engine.recipe import load_recipe_file
from studio_core.ops import icon3d as ic
from studio_core.ops import iso

RECIPES = Path(__file__).resolve().parents[1] / "studio_core" / "recipes"
NEW = ("icon-3d", "iso-prop", "iso-tiles", "iso-tiles-image")


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def blob_render(w: int, h: int, x: int = 140, res: int = 512) -> Image.Image:
    """An off-centre object with a soft edge, as a Blender icon render would come out."""
    big = Image.new("RGBA", (res * 2, res * 2), (0, 0, 0, 0))
    ImageDraw.Draw(big).ellipse((x * 2, 180, x * 2 + w * 2, 180 + h * 2), fill=(200, 120, 60, 255))
    return big.resize((res, res), Image.Resampling.LANCZOS)


def ground_texture(side: int = 256) -> Image.Image:
    rng = np.random.default_rng(3)
    base = rng.normal(0, 1, (side // 16, side // 16, 3))
    img = Image.fromarray(np.uint8(np.clip(120 + base * 30, 0, 255)), "RGB").resize((side, side),
                                                                                     Image.Resampling.BICUBIC)
    return img


def test_icon_compose_centres_and_matches_fill() -> None:
    renders = [blob_render(300, 160), blob_render(90, 420, x=300), blob_render(200, 200, x=40)]
    for mode in ("transparent", "rarity", "frame"):
        cfg = ic.compose_params({"size": 128, "outline": 2, "background": mode, "tier": 5})
        finals, bare = ic.compose_all(renders, cfg)
        assert all(f.size == (128, 128) for f in finals)
        qa = ic.set_qa([ic.icon_stats(b) for b in bare], ic.fringe_ratio(bare, renders))
        assert qa["fill_ok"] and qa["center_ok"] and qa["alpha_ok"], qa
        rim = np.asarray(finals[0])[2, 64, 3]
        assert (rim == 0) == (mode == "transparent")
    # The outline grows the silhouette by about its width on each side.
    cfg = ic.compose_params({"size": 128, "outline": 4})
    finals, bare = ic.compose_all(renders[:1], cfg)
    a_out, a_in = (np.asarray(x)[..., 3] > 128 for x in (finals[0], bare[0]))
    assert a_out.sum() > a_in.sum() * 1.1
    with pytest.raises(ValueError):
        ic.compose_params({"background": "neon"})
    with pytest.raises(ValueError):
        ic.fit_icon(Image.new("RGBA", (64, 64)), 128, 0.08, 0)


async def test_icon_compose_executor(tmp_path: Path) -> None:
    ctx = FakeCtx(tmp_path)
    refs = [await put_img(ctx, blob_render(200 + 40 * i, 150), f"{n}.png") for i, n in enumerate(["sword", "shield",
                                                                                                    "sword"])]
    res = await ic.IconCompose().run(ctx, {"size": 128, "background": "rarity", "tier": 4, "asset": "loot"},
                                     {"images": refs})
    assert [r.name for r in res.outputs["images"]] == ["sword.png", "shield.png", "sword_b.png"]
    doc = json.loads(ctx.blobs.path(res.outputs["report"][0].hash).read_bytes())
    assert doc["type"] == ic.ICON3D_TYPE and doc["tier"] == 4 and doc["qa"]["fill_ok"] and len(doc["icons"]) == 3
    atlas = json.loads(ctx.blobs.path(res.outputs["atlas_json"][0].hash).read_bytes())
    assert len(atlas["frames"]) == 3 and res.meta["warnings"] == []


class FakeBlender:
    version = "fake"

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.req: dict[str, Any] = {}

    async def call(self, cmd: str, params: dict[str, Any], on_event=None, timeout: float = 0) -> dict[str, Any]:
        assert cmd == "script" and params["unrestricted"] and "icon3d_bpy.run(params)" in params["code"]
        self.req = params
        p = params["params"]
        out = Path(p["out_dir"])
        out.mkdir(parents=True, exist_ok=True)
        if p["mode"] == "icons":
            icons = []
            for i, m in enumerate(p["meshes"]):
                assert Path(m["path"]).is_file()
                f = out / f"icon_{i:03d}.png"
                blob_render(150 + 30 * i, 200).save(f)
                icons.append({"name": m["name"], "file": str(f)})
            rep: dict[str, Any] = {"icons": icons, "total_s": 1.0}
        else:
            frames = []
            for name, deg in (("sw", 0), ("se", 90), ("ne", 180), ("nw", 270)):
                f = out / f"iso_{name}.png"
                prop_render(128, 1, 2).save(f)
                frames.append({"dir": name, "rotation": deg, "file": str(f)})
            rep = {"tile_w": 128, "tile_h": 64, "cells": 1, "canvas": [128, 160], "supersample": 2,
                   "foot": [64, 128], "anchor": [64, 128], "frames": frames, "height_px": 90}
        Path(p["report"]).write_text(json.dumps(rep), encoding="utf-8")
        Path(params["out"]).write_bytes(b"glTF")
        return {"out": params["out"]}


def prop_render(tile_w: int, cells: int, ss: int, overhang: int = 0) -> Image.Image:
    """A crate standing on its footprint: canvas 128x160, foot (diamond centre) at (64, 128)."""
    w, h = 128 * ss, 160 * ss
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    fx, fy = 64 * ss, 128 * ss
    hw, hh = 40 * ss, 20 * ss
    d.polygon([(fx - hw, fy - 60 * ss), (fx, fy - 60 * ss - hh), (fx + hw, fy - 60 * ss), (fx + hw, fy),
               (fx, fy + hh), (fx - hw - overhang * ss, fy + overhang * ss)], fill=(150, 100, 60, 255))
    return img


async def test_icon_render_executor_stages_names_and_folder(tmp_path: Path) -> None:
    from studio_core.adapters.blender.icon3d import BlenderIconRenderExecutor

    ctx = FakeCtx(tmp_path)
    folder = tmp_path / "more"
    (folder / "sub").mkdir(parents=True)
    (folder / "sub" / "Potion.fbx").write_bytes(b"Kaydara")
    (folder / "notes.txt").write_text("x")
    meshes = [await ctx.blobs.put_bytes(b"glTF", name=n, mime="model/gltf-binary") for n in ("axe.glb", "axe.glb")]
    fake = FakeBlender("icons")
    ex = BlenderIconRenderExecutor(Settings(data_dir=tmp_path / "d"), fake)  # type: ignore[arg-type]
    res = await ex.run(ctx, {"angle": "top", "size": 128, "folder": str(folder)}, {"mesh": meshes})
    p = fake.req["params"]
    assert p["angle"] == "top" and p["res"] == 256 and [m["name"] for m in p["meshes"]] == ["axe", "axe_2", "Potion"]
    assert p["meshes"][2]["path"].endswith(".fbx")
    assert [r.name for r in res.outputs["images"]] == ["axe.png", "axe_2.png", "Potion.png"]
    assert not ex.cacheable({"folder": str(folder)}) and ex.cacheable({})
    with pytest.raises(StepError, match="angle"):
        await ex.run(ctx, {"angle": "side"}, {"mesh": meshes})
    with pytest.raises(StepError, match="no meshes"):
        await ex.run(ctx, {}, {"mesh": []})


def test_iso_tiles_partition_and_seams() -> None:
    tiles = iso.iso_tile_set([ground_texture()], 64, 6, seed=2)
    assert len(tiles) == 6 and all(t.size == (64, 32) for t in tiles)
    qa = iso.grid_qa(tiles, iso.periodic_resize(ground_texture(), 64), 4, 2)
    assert qa["gaps_px"] == 0 and qa["rim_max_diff"] <= 2 and qa["seamless"], qa
    a0, a3 = (np.asarray(t, np.float32) for t in (tiles[0], tiles[3]))
    assert np.abs(a0 - a3).mean() > 1.0
    mask = iso.diamond_mask(64, 32)
    alpha = np.asarray(tiles[0])[..., 3] > 0
    # Exact diamond = half the rect; the 1px bleed only grows it, and colour under alpha 0 is never black.
    assert mask.sum() == 64 * 32 // 2 and (alpha & mask).sum() == mask.sum() and mask.sum() < alpha.sum() < 1200
    assert np.asarray(tiles[0])[0, 0, :3].sum() > 0
    # Four tiles around a shared vertex leave no hole and draw no pixel twice.
    for w in (64, 128, 256):
        _, cover, _ = iso.compose_grid([Image.fromarray(np.dstack([np.full((w // 2, w, 3), 99, np.uint8),
                                                                   np.uint8(iso.diamond_mask(w, w // 2)) * 255]))],
                                       3, 0)
        assert cover.max() == 1 and (cover[iso.diamond_mask(3 * w, 3 * w // 2)] == 1).all()
    with pytest.raises(ValueError):
        iso.iso_tile_set([ground_texture()], 100, 4)


async def test_iso_tiles_executor_and_seamless_flag(tmp_path: Path) -> None:
    ctx = FakeCtx(tmp_path)
    refs = [await put_img(ctx, ground_texture(), "grass.png"), await put_img(ctx, ground_texture(128), "flowers.png")]
    res = await iso.IsoTiles().run(ctx, {"tile": 128, "variants": 4, "asset": "grass", "seamless": True},
                                   {"images": refs})
    doc = json.loads(ctx.blobs.path(res.outputs["iso"][0].hash).read_bytes())
    assert doc["type"] == iso.ISO_TILES_TYPE and doc["tile"] == [128, 64] and len(doc["variants"]) == 4
    assert doc["qa"]["seamless"] and len(doc["sample"]) == 16
    with Image.open(ctx.blobs.path(res.outputs["atlas"][0].hash)) as a:
        assert a.size == (512, 64)


async def test_iso_prop_executor_flags_overhang(tmp_path: Path) -> None:
    ctx = FakeCtx(tmp_path)
    meta = {"tile_w": 128, "cells": 1, "canvas": [128, 160], "supersample": 2, "foot": [64, 128],
            "anchor": [64, 128], "frames": [{"dir": d} for d in ("sw", "se", "ne", "nw")], "name": "crate"}
    meta_ref = await ctx.blobs.put_bytes(json.dumps(meta).encode(), name="iso_render.json")
    good = [await put_img(ctx, prop_render(128, 1, 2), f"iso_{d}.png") for d in ("sw", "se", "ne", "nw")]
    res = await iso.IsoProp().run(ctx, {}, {"images": good, "json": [meta_ref]})
    doc = json.loads(ctx.blobs.path(res.outputs["iso"][0].hash).read_bytes())
    assert doc["asset"] == "crate" and doc["qa"]["on_tile"] and [d["name"] for d in doc["dirs"]][0] == "crate_sw"
    with Image.open(ctx.blobs.path(res.outputs["images"][0].hash)) as im:
        assert im.size == (128, 160)
    bad = [await put_img(ctx, prop_render(128, 1, 2, overhang=30), f"iso_{d}.png") for d in ("sw", "se", "ne", "nw")]
    res = await iso.IsoProp().run(ctx, {"asset": "crate"}, {"images": bad, "json": [meta_ref]})
    assert not res.meta["on_tile"] and res.meta["warnings"]
    with pytest.raises(StepError):
        await iso.IsoProp().run(ctx, {}, {"images": good[:3], "json": [meta_ref]})


def test_tile_map_data_matches_godot() -> None:
    from studio_core.export.iso import tile_map_data

    # Bytes Godot 4.7 saved for cells (0,0) atlas (0,0); (1,2) atlas (1,0); (-1,3) atlas (1,0).
    want = ("PackedByteArray(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 2, 0, 0, 0, 1, 0, 0, 0, 0, 0, 255, 255, "
            "3, 0, 0, 0, 1, 0, 0, 0, 0, 0)")
    assert tile_map_data([(0, 0, 0, 0), (1, 2, 0, 1), (-1, 3, 0, 1)]) == want


def test_recipes_lint_and_kinds() -> None:
    for name in NEW:
        text = (RECIPES / f"{name}.yaml").read_text(encoding="utf-8")
        assert validate(text, None)[1] == []
        load_recipe_file(RECIPES / f"{name}.yaml")
    for kind in ("blender.icon_render", "blender.iso_render", "op.icon_compose", "op.iso_tiles", "op.iso_prop"):
        assert CATALOG[kind]["label"]


class FakeBlenderStep(FakeExec):
    def __init__(self, kind: str, mode: str) -> None:
        super().__init__(kind, Lane.BLENDER)
        self.mode = mode

    async def run(self, ctx: Any, params: dict[str, Any], inputs: Any) -> StepResult:
        from studio_core.adapters.blender.icon3d import BlenderIconRenderExecutor, BlenderIsoRenderExecutor

        cls = BlenderIconRenderExecutor if self.mode == "icons" else BlenderIsoRenderExecutor
        return await cls(Settings(data_dir=ctx.job_dir / "s"), FakeBlender(self.mode)).run(  # type: ignore[arg-type]
            ctx, params, inputs)


class FakeCleanup(FakeExec):
    def __init__(self) -> None:
        super().__init__("blender.cleanup", Lane.BLENDER)

    async def run(self, ctx: Any, params: dict[str, Any], inputs: Any) -> StepResult:
        return StepResult(outputs={"meshes": list(inputs["mesh"])})


def _env(tmp_path: Path) -> Env:
    from studio_core.ops.ui import NineSlice

    env = Env(tmp_path, [FakeBlenderStep("blender.icon_render", "icons"),
                         FakeBlenderStep("blender.iso_render", "iso"), FakeCleanup()],
              {n: (RECIPES / f"{n}.yaml").read_text(encoding="utf-8") for n in NEW})
    for cls in (*ic.ICON3D_OPS, *iso.ISO_OPS, NineSlice):
        env.registry.register(cls())
    return env


async def _wait(env: Env, recipe: str, inputs: dict[str, Any]) -> dict[str, Any]:
    run_id = await env.engine.start_run(recipe, inputs)
    await asyncio.wait_for(env.engine.wait_idle(run_id), 60)
    steps = env.steps(run_id)
    assert env.status(run_id) == RunStatus.SUCCEEDED, {k: s.error for k, s in steps.items() if s.error}
    return steps


async def test_recipes_run_and_export(tmp_path: Path) -> None:
    from studio_core.export import godot, unity
    from studio_core.store.bundle import engine_ready, engine_source

    env = _env(tmp_path)
    meshes = [await env.blobs.put_bytes(b"glTF" + n.encode(), name=f"{n}.glb", mime="model/gltf-binary")
              for n in ("sword", "bow")]
    steps = await _wait(env, "icon-3d", {"meshes": [m.hash for m in meshes], "size": 128, "outline": 2,
                                         "background": "frame", "review": False})
    assert [r.name for r in steps["approve"].outputs["images"]] == ["sword.png", "bow.png"]
    src = engine_source(steps["approve"], list(steps.values()), env.blobs, "Loot")
    assert [n for n, *_ in src.ui] == ["sword", "bow"] and engine_ready(steps["approve"], env.blobs)
    assert "UI/sword.png.meta" in dict(unity.export(src)) and "UI/bow.png" in dict(godot.export(src))

    steps = await _wait(env, "iso-prop", {"mesh": meshes[0].hash, "tile": 128, "review": False})
    src = engine_source(steps["approve"], list(steps.values()), env.blobs, "Crate")
    assert src.iso and src.iso["type"] == iso.ISO_PROP_TYPE
    u = dict(unity.export(src))
    assert "Iso/Crate_sw.asset" in u and "Iso/Editor/AssetStudioIso.cs" in u
    meta = u["Iso/Crate_iso_prop.png.meta"].decode()
    assert "spritePixelsToUnits: 128" in meta and meta.count("name: Crate_") == 4
    cs = u["Iso/Editor/AssetStudioIso.cs"].decode()
    assert "namespace AssetStudio.Iso" in cs and "TransparencySortMode.Default" in cs and '"Crate_iso.json"' in cs and "CellLayout.Isometric" in cs
    g = dict(godot.export(src))
    tres = g["Crate_iso_prop_tileset.tres"].decode()
    assert "tile_shape = 1" in tres and "texture_region_size = Vector2i(128, 160)" in tres
    assert "texture_origin = Vector2i(0, 48)" in tres
    assert "y_sort_enabled = true" in g["Crate_iso.tscn"].decode()

    tex = await env.blobs.put_bytes(_png(ground_texture()), name="grass.png", mime="image/png")
    steps = await _wait(env, "iso-tiles-image", {"image": [tex.hash], "tile": 64, "variants": 4, "seamless": False,
                                                 "review": False})
    src = engine_source(steps["approve"], list(steps.values()), env.blobs, "Grass")
    g = dict(godot.export(src))
    tres = g["Grass_iso_tiles_tileset.tres"].decode()
    assert "tile_layout = 5" in tres and "tile_size = Vector2i(64, 32)" in tres and "3:0/0 = 0" in tres
    scene = g["Grass_iso.tscn"].decode()
    assert scene.count("PackedByteArray(") == 1 and len(scene.split("PackedByteArray(")[1].split(",")) == 2 + 16 * 12
    u = dict(unity.export(src))
    assert "Iso/Grass_tile_03.asset" in u and json.loads(u["Iso/Grass_iso.json"])["kind"] == "tiles"
