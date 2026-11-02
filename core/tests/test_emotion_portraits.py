from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image, ImageDraw

from studio_core.contracts import RunStatus, StepResult
from studio_core.engine.exports import recipe_exports
from studio_core.ops.portrait import PORTRAIT_OPS, atlas, frame_crop, subject_box
from test_engine_runner import Env, FakeExec

RECIPES = Path(__file__).resolve().parents[1] / "studio_core" / "recipes"
EMOTIONS = "happy, warm smile\nsad, downturned mouth\nangry, furrowed brows"


def cutout(w: int = 300, h: int = 520, dx: int = 0, dy: int = 0, smile: bool = False) -> Image.Image:
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((100 + dx, 20 + dy, 200 + dx, 140 + dy), fill=(230, 190, 160, 255), outline=(40, 30, 20, 255), width=3)
    d.rectangle((90 + dx, 140 + dy, 210 + dx, 519), fill=(120, 60, 40, 255))
    d.ellipse((130 + dx, 60 + dy, 140 + dx, 70 + dy), fill=(20, 20, 20, 255))
    d.ellipse((160 + dx, 60 + dy, 170 + dx, 70 + dy), fill=(20, 20, 20, 255))
    d.arc((130 + dx, 90 + dy, 170 + dx, 120 + dy), 0 if smile else 180, 180 if smile else 360, fill=(120, 30, 30, 255),
          width=3)
    return img


def png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class FakeKlein(FakeExec):
    def __init__(self) -> None:
        super().__init__("comfy.edit_flux2_klein4b")
        self.texts: list[str] = []
        self.sizes: list[tuple[int, int]] = []

    async def run(self, ctx: Any, params: dict[str, Any], inputs: Any) -> StepResult:
        self.texts.append(params["75:74.text"])
        src = Image.open(ctx.blobs.path(inputs["image"][0].hash))
        self.sizes.append(src.size)
        return StepResult(outputs={"images": [await ctx.blobs.put_bytes(png(src.convert("RGB")), name="klein.png",
                                                                        mime="image/png")]})


class FakeRmbg(FakeExec):
    """Makes the grey backdrop transparent and drifts the result a few pixels, like Klein + BiRefNet would."""

    def __init__(self) -> None:
        super().__init__("comfy.remove_bg_birefnet")

    async def run(self, ctx: Any, params: dict[str, Any], inputs: Any) -> StepResult:
        img = Image.open(ctx.blobs.path(inputs["image"][0].hash)).convert("RGBA")
        a = np.asarray(img).copy()
        bg = (np.abs(a[..., :3].astype(int) - 128).sum(axis=2) < 6)
        a[..., 3] = np.where(bg, 0, 255)
        out = Image.fromarray(a, "RGBA")
        shifted = Image.new("RGBA", out.size, (0, 0, 0, 0))
        shifted.paste(out, (5, -4))
        return StepResult(outputs={"images": [await ctx.blobs.put_bytes(png(shifted), name="rmbg.png",
                                                                        mime="image/png")]})


def test_subject_box_and_frame_crop() -> None:
    img = cutout()
    x0, y0, x1, y1 = subject_box(img)
    assert (x0, y0) == (90, 20) and y1 == 520
    full, info = frame_crop(img, "head", "auto", 0.08, 256)
    assert info["source"] == "full_body" and full.size == (256, 256)
    # Head frame of a full-body figure shows the head, not the torso: face at the centre, transparent margin on top.
    assert full.getpixel((128, 128))[:3] == (230, 190, 160) and np.asarray(full.getchannel("A"))[:6].max() == 0
    bust, binfo = frame_crop(img, "bust", "auto", 0.08, 256)
    assert bust.size == (256, 256) and binfo["box"][3] > info["box"][3]
    wide = Image.new("RGBA", (300, 300), (0, 0, 0, 0))
    wide.paste((200, 100, 100, 255), (50, 20, 250, 280))
    port, pinfo = frame_crop(wide, "bust", "auto", 0.0, 128)
    assert pinfo["source"] == "portrait"
    blank, _ = frame_crop(Image.new("RGBA", (64, 64), (0, 0, 0, 0)), "bust", "auto", 0.1, 32)
    assert blank.size == (32, 32)


def test_atlas_layout() -> None:
    sheet, cols, rows = atlas([Image.new("RGBA", (10, 10), (255, 0, 0, 255))] * 7)
    assert (cols, rows) == (4, 2) and sheet.size == (40, 20)
    assert sheet.getpixel((35, 15))[3] == 0 and sheet.getpixel((5, 15))[3] == 255


async def test_recipe_runs_and_maps(tmp_path: Path) -> None:
    klein, rmbg = FakeKlein(), FakeRmbg()
    recipes = {"emotion-portraits": (RECIPES / "emotion-portraits.yaml").read_text(encoding="utf-8")}
    env = Env(tmp_path, [klein, rmbg], recipes)
    for cls in PORTRAIT_OPS:
        env.registry.register(cls())
    img = await env.blobs.put_bytes(png(cutout()), name="hero.png", mime="image/png")
    run_id = await env.engine.start_run("emotion-portraits", {"image": img.hash, "emotions": EMOTIONS, "size": 256,
                                                              "asset": "hero", "frame": "head"},
                                        params={"approve": {"enabled": False}})
    await asyncio.wait_for(env.engine.wait_idle(run_id), 60)
    steps = env.steps(run_id)
    assert env.status(run_id) == RunStatus.SUCCEEDED, {k: s.error for k, s in steps.items() if s.error}
    assert len(klein.texts) == 3 and "warm smile" in klein.texts[0] and "identity" in klein.texts[0]
    assert set(klein.sizes) == {(256, 256)}
    out = steps["approve"].outputs
    assert [r.name for r in out["images"]] == [f"hero_{n}.png" for n in ("neutral", "happy", "sad", "angry")]
    assert "flagged" not in steps["set"].meta, steps["set"].meta
    for r in out["images"]:
        im = Image.open(env.blobs.path(r.hash))
        assert im.size == (256, 256) and im.mode == "RGBA" and im.getpixel((0, 0))[3] == 0
    doc = json.loads(env.blobs.path(out["map"][0].hash).read_bytes())
    assert doc["type"] == "emotion_portraits" and doc["default"] == "neutral"
    assert list(doc["portraits"]) == ["neutral", "happy", "sad", "angry"]
    assert doc["sheet"] == {"file": "hero_sheet.png", "cols": 4, "rows": 1, "cell": [256, 256]}
    assert doc["portraits"]["angry"]["cell"]["x"] == 768 and doc["portraits"]["happy"]["file"] == "hero_happy.png"
    sheet = Image.open(env.blobs.path(out["sheet"][0].hash))
    assert sheet.size == (1024, 256)


async def test_set_rejects_count_mismatch(tmp_path: Path) -> None:
    from studio_core.contracts import StepError
    from studio_core.ops.portrait import PortraitSet

    env = Env(tmp_path, [], {})
    ref = await env.blobs.put_bytes(png(cutout()), name="b.png", mime="image/png")

    class Ctx:
        blobs = env.blobs

    with pytest.raises(StepError):
        await PortraitSet().run(Ctx(), {"emotions": EMOTIONS}, {"base": [ref], "edits": [ref]})


def test_recipe_kinds_and_exports() -> None:
    from studio_core.engine.kinds import CATALOG
    from studio_core.engine.recipe import parse_recipe

    recipe = parse_recipe((RECIPES / "emotion-portraits.yaml").read_bytes())
    kinds = [s.kind for s in recipe.steps]
    assert all(k in CATALOG for k in kinds if not k.startswith("comfy.")), kinds
    assert {"unity", "godot", "flutter", "android", "ios"} <= set(recipe_exports(kinds))
