from __future__ import annotations

import asyncio
import io
import json
import zipfile
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image, ImageDraw

from studio_core.adapters.claude import ClaudeReply
from studio_core.adapters.store_layout import ClaudeStoreLayoutExecutor, build_prompt, schema
from studio_core.contracts import RunStatus, StepResult
from studio_core.engine.library import validate
from studio_core.engine.recipe import load_recipe_file
from studio_core.ops import store_capsules as sc
from studio_core.ops import store_icons as si
from studio_core.ops.store_kit import STORE_OPS, build_kit, parse_targets, prep_frames, zip_bytes
from test_engine_runner import Env, FakeExec

RECIPES = Path(__file__).resolve().parents[1] / "studio_core" / "recipes"


def icon_img(alpha: bool = False) -> Image.Image:
    img = Image.new("RGBA", (1024, 1024), (40, 90, 160, 255))
    ImageDraw.Draw(img).ellipse((300, 250, 724, 800), fill=(240, 180, 60, 255))
    if alpha:
        ImageDraw.Draw(img).rectangle((0, 0, 1023, 40), fill=(0, 0, 0, 0))
    return img


def subject_img(box: tuple[int, int, int, int] = (300, 250, 724, 800)) -> Image.Image:
    img = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse(box, fill=(240, 180, 60, 255))
    return img


def art_img(w: int = 2688, h: int = 1536) -> Image.Image:
    img = Image.new("RGB", (w, h), (20, 30, 60))
    d = ImageDraw.Draw(img)
    for x in range(0, w, 120):
        d.line((x, 0, x, h), fill=(30, 50, 90), width=5)
    d.ellipse((int(w * 0.6), int(h * 0.3), int(w * 0.76), int(h * 0.8)), fill=(230, 80, 60))
    return img


def files_of(kit: dict[str, Any]) -> dict[str, bytes]:
    return dict(kit["files"])


def test_ios_appiconset_contents_and_sizes() -> None:
    files = dict(si.ios_files(icon_img().convert("RGB")))
    contents = json.loads(files["ios/AppIcon.appiconset/Contents.json"])
    assert contents["info"] == {"author": "xcode", "version": 1}
    assert len(contents["images"]) == len(si.IOS_ICONS) == 18
    marketing = [e for e in contents["images"] if e["idiom"] == "ios-marketing"]
    assert marketing == [{"filename": "Icon-1024@1x.png", "idiom": "ios-marketing", "scale": "1x",
                          "size": "1024x1024"}]
    for e in contents["images"]:
        im = Image.open(io.BytesIO(files[f"ios/AppIcon.appiconset/{e['filename']}"]))
        pt = float(e["size"].split("x")[0])
        assert im.size == (round(pt * int(e["scale"][0])),) * 2
        assert im.mode == "RGB"
    assert "ios/AppIcon.appiconset/Icon-83.5@2x.png" in files
    assert Image.open(io.BytesIO(files["ios/AppIcon.appiconset/Icon-83.5@2x.png"])).size == (167, 167)


def test_ios_flattens_transparent_master() -> None:
    kit = build_kit(icon_img(alpha=True), None, None, None, "Game", ["ios"])
    files = files_of(kit)
    ios = [p for p in files if p.endswith(".png") and p.startswith("ios/")]
    assert ios and all(Image.open(io.BytesIO(files[p])).mode == "RGB" for p in ios)
    assert kit["report"]["checks"]["ios"]["flattened_transparency"] > 0.03
    assert any("iOS" in f for f in kit["report"]["flagged"])


def test_android_foreground_fits_safe_zone() -> None:
    # A subject reaching the corners would be cut by every launcher mask until it is refitted.
    big = subject_img((0, 0, 1023, 1023))
    files, info, imgs = si.android_files(icon_img().convert("RGB"), big)
    assert info["safe_zone"]["ok"] and info["safe_zone"]["outside_safe"] <= si.SAFE_TOLERANCE
    fg = imgs["foreground"]
    assert fg.size == (432, 432)
    ys, xs = np.nonzero(np.asarray(fg.getchannel("A")) > 32)
    r = np.sqrt((xs + 0.5 - 216) ** 2 + (ys + 0.5 - 216) ** 2).max()
    assert r <= si.SAFE_RATIO * 216 + 1.5
    full = Image.new("RGBA", (432, 432), (255, 0, 0, 255))
    assert not si.safe_zone(full)["ok"]
    names = dict(files)
    for dens, k in si.ANDROID_DENSITIES.items():
        for layer in ("foreground", "background", "monochrome"):
            im = Image.open(io.BytesIO(names[f"android/res/mipmap-{dens}/ic_launcher_{layer}.png"]))
            assert im.size == (round(108 * k),) * 2
        assert Image.open(io.BytesIO(names[f"android/res/mipmap-{dens}/ic_launcher.png"])).size == (round(48 * k),) * 2
        rnd = Image.open(io.BytesIO(names[f"android/res/mipmap-{dens}/ic_launcher_round.png"]))
        assert rnd.getpixel((0, 0))[3] == 0
    xml = names["android/res/mipmap-anydpi-v26/ic_launcher.xml"].decode()
    assert "<monochrome" in xml and "@mipmap/ic_launcher_foreground" in xml
    play = Image.open(io.BytesIO(names["android/play_store_512.png"]))
    assert play.size == (512, 512)
    mono = Image.open(io.BytesIO(names["android/res/mipmap-xxxhdpi/ic_launcher_monochrome.png"])).convert("RGBA")
    a = np.asarray(mono)
    assert (a[..., :3][a[..., 3] > 0] == 255).all()


def test_android_without_subject_is_background_only() -> None:
    files, info, _ = si.android_files(icon_img().convert("RGB"), None)
    names = dict(files)
    assert not info["subject"] and not info["safe_zone"]["ok"]
    assert "android/res/mipmap-mdpi/ic_launcher_foreground.png" not in names
    assert "foreground" not in names["android/res/mipmap-anydpi-v26/ic_launcher.xml"].decode()


def test_ico_holds_every_size() -> None:
    for sizes in (si.WINDOWS_ICO, si.WEB_ICO, si.STEAM_CLIENT_ICO):
        ico = Image.open(io.BytesIO(si.ico_bytes(icon_img(), sizes)))
        assert ico.format == "ICO"
        assert sorted(ico.info["sizes"]) == sorted((s, s) for s in sizes)


def test_web_files() -> None:
    files = dict(si.web_files(icon_img().convert("RGB"), "Ember Knight"))
    for name, px in si.WEB_PNG.items():
        assert Image.open(io.BytesIO(files[f"web/{name}"])).size == (px, px)
    assert Image.open(io.BytesIO(files["web/apple-touch-icon.png"])).mode == "RGB"
    manifest = json.loads(files["web/site.webmanifest"])
    assert manifest["name"] == "Ember Knight" and len(manifest["icons"]) == 2


def test_crop_box_keeps_focus_opposite_title() -> None:
    focus = [0.6, 0.3, 0.76, 0.8]
    box, kept = sc.crop_box((2688, 1536), focus, 600 / 900, "top")
    assert kept == 1.0 and round((box[2] - box[0]) / (box[3] - box[1]), 2) == round(600 / 900, 2)
    assert box[0] <= focus[0] * 2688 and box[2] >= focus[2] * 2688
    left, _ = sc.crop_box((2688, 1536), [0.4, 0.3, 0.6, 0.7], 2.0, "left")
    right, _ = sc.crop_box((2688, 1536), [0.4, 0.3, 0.6, 0.7], 2.0, "right")
    assert left[1] < right[1] or left == right  # same width here; the vertical anchor is centred
    wide, kept = sc.crop_box((1000, 1000), [0.0, 0.4, 1.0, 0.6], 0.5, None)
    assert kept < 1.0 and wide[0] == 250


def test_normalize_layout_clamps_and_falls_back() -> None:
    lay = sc.normalize_layout({"focus": {"x0": 0.8, "y0": 1.4, "x1": 0.2, "y1": 0.1}, "font": "nope",
                               "fill": "fff", "outline": "red", "outline_width": 3, "wide_anchor": "middle",
                               "tall_size": 0.1, "case": "upper"}, "Ember Knight")
    assert lay["focus"] == [0.2, 0.1, 0.8, 1.0]
    assert lay["fill"] == "#ffffff" and lay["outline"] == "#1a1020"
    assert lay["outline_width"] == 0.15 and lay["wide_anchor"] == "top" and lay["tall_size"] == 0.5
    assert lay["font"] in (sc.font_choices("Ember Knight") or [""])
    assert sc.normalize_layout(None, "x", [0.1, 0.1, 0.5, 0.5])["focus"] == [0.1, 0.1, 0.5, 0.5]
    vi = sc.font_choices("Hiệp Sĩ Lửa")
    assert all(sc.FONT_FILES[f][2] for f in vi)


def test_detect_focus_finds_the_subject() -> None:
    f = sc.detect_focus(art_img())
    cx, cy = (f[0] + f[2]) / 2, (f[1] + f[3]) / 2
    assert 0.5 < cx < 0.8 and 0.35 < cy < 0.75


def test_kit_steam_capsules_and_report() -> None:
    layout = sc.normalize_layout({"focus": [0.6, 0.3, 0.76, 0.8], "wide_anchor": "left", "tall_anchor": "top",
                                  "wide_size": 0.45, "plate": True}, "Ember Knight")
    kit = build_kit(icon_img(), subject_img(), art_img(), layout, "Ember Knight", list(parse_targets("")))
    files = files_of(kit)
    for name, w, h, _ in sc.STEAM_CAPSULES:
        assert Image.open(io.BytesIO(files[f"steam/{name}_{w}x{h}.png"])).size == (w, h)
    logo = Image.open(io.BytesIO(files["steam/library_logo.png"]))
    assert logo.mode == "RGBA" and logo.width <= 1280 and logo.height <= 720
    assert logo.getpixel((0, 0))[3] < 255 or logo.getpixel((logo.width - 1, logo.height - 1))[3] < 255
    assert Image.open(io.BytesIO(files["steam/community_icon_184x184.jpg"])).size == (184, 184)
    caps = {c["name"]: c for c in kit["report"]["checks"]["steam"]["capsules"]}
    assert caps["header_capsule"]["anchor"] == "left" and caps["header_capsule"]["text_over_focus"] <= 0.3
    assert not caps["library_hero"]["title"] and "text_box" not in caps["library_hero"]
    assert all(c["focus_kept"] >= 0.6 for c in caps.values())
    assert kit["report"]["checks"]["android"]["ok"] and kit["report"]["checks"]["ios"]["ok"]
    assert {"windows/app.ico", "web/favicon.ico", "steam/client_icon.ico", "master/key_art.jpg",
            "report.json"} <= set(files)
    assert kit["sheet"].width == 1800 and kit["sheet"].height > 600


def test_zip_is_deterministic_and_rooted() -> None:
    files = [("ios/a.png", b"x" * 10), ("report.json", b"{}")]
    a, b = zip_bytes(files), zip_bytes(list(files))
    assert a == b
    with zipfile.ZipFile(io.BytesIO(a)) as z:
        assert z.namelist() == ["store_kit/ios/a.png", "store_kit/report.json"]


def test_prep_frames_centres_subject() -> None:
    sq, wide = prep_frames(subject_img((0, 0, 200, 600)))
    assert sq.size == (1024, 1024) and wide.size == (1344, 768)
    assert sq.getpixel((5, 5)) == (255, 255, 255) and sq.getpixel((512, 512)) != (255, 255, 255)


def test_layout_prompt_lists_fonts() -> None:
    fonts = sc.font_choices("Ember Knight")
    text = build_prompt("Ember Knight", fonts)
    assert '"Ember Knight"' in text and all(f in text for f in fonts)
    s = schema(fonts)
    assert set(s["required"]) >= {"focus", "font", "wide_anchor", "tall_anchor"}


@pytest.mark.parametrize("recipe,steps", [
    ("store-kit", ["icon", "pick_icon", "subject", "art", "pick_art", "upscale", "layout", "kit", "approve"]),
    ("store-kit-image", ["prep", "icon", "pick_icon", "subject", "art", "upscale", "layout", "kit", "approve"]),
])
def test_recipes_load_and_lint(recipe: str, steps: list[str]) -> None:
    r = load_recipe_file(RECIPES / f"{recipe}.yaml")
    assert sorted(s.id for s in r.steps) == sorted(steps)
    _, issues = validate((RECIPES / f"{recipe}.yaml").read_text(encoding="utf-8"), None)
    assert issues == []


class FakeQuery:
    def __init__(self, reply: dict[str, Any]):
        self.reply = reply
        self.calls: list[tuple[list[dict[str, Any]], Any]] = []

    async def __call__(self, content: list[dict[str, Any]], options: Any) -> ClaudeReply:
        self.calls.append((content, options))
        return ClaudeReply(self.reply, "", {"cost_usd": 0.03, "model": options.model})


class FakeImageGen(FakeExec):
    """Z-Image / Klein / BiRefNet / ESRGAN stand-ins that return real PNGs of the right shape."""

    def __init__(self, kind: str, make: Any):
        super().__init__(kind)
        self.make = make
        self.params: list[dict[str, Any]] = []

    async def run(self, ctx: Any, params: dict[str, Any], inputs: Any) -> StepResult:
        self.params.append(params)
        srcs = [Image.open(ctx.blobs.path(r.hash)) for refs in inputs.values() for r in refs]
        imgs = self.make(params, srcs)
        out = []
        for i, im in enumerate(imgs):
            buf = io.BytesIO()
            im.save(buf, "PNG")
            out.append(await ctx.blobs.put_bytes(buf.getvalue(), name=f"gen_{i}.png", mime="image/png"))
        return StepResult(outputs={"images": out})


def _zimage(params: dict[str, Any], srcs: list[Image.Image]) -> list[Image.Image]:
    n = int(params.get("57:13.batch_size", 1))
    return [art_img(1344, 768) if params.get("57:13.width") == 1344 else icon_img().convert("RGB")
            for _ in range(n)]


def _klein(params: dict[str, Any], srcs: list[Image.Image]) -> list[Image.Image]:
    src = srcs[0]
    return [art_img(*src.size) if src.width > src.height else icon_img().convert("RGB")]


def _rmbg(params: dict[str, Any], srcs: list[Image.Image]) -> list[Image.Image]:
    return [subject_img()]


def _esrgan(params: dict[str, Any], srcs: list[Image.Image]) -> list[Image.Image]:
    return [s.resize((s.width * 2, s.height * 2)) for s in srcs]


@pytest.mark.parametrize("recipe", ["store-kit", "store-kit-image"])
async def test_recipe_runs_with_fakes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recipe: str) -> None:
    from studio_core.adapters import claude as cl

    monkeypatch.setattr(cl, "_image_block", lambda d, m: {"type": "image", "source": {"type": "base64"}})
    q = FakeQuery({"focus": {"x0": 0.6, "y0": 0.3, "x1": 0.76, "y1": 0.8}, "font": "x", "case": "upper",
                   "fill": "#ffe9a0", "outline": "#301010", "outline_width": 0.08, "shadow": True, "plate": False,
                   "wide_anchor": "left", "wide_size": 0.45, "tall_anchor": "top", "tall_size": 0.85,
                   "reason": "calm sky on the left"})
    gens = [FakeImageGen("comfy.t2i_zimage_turbo", _zimage), FakeImageGen("comfy.edit_flux2_klein4b", _klein),
            FakeImageGen("comfy.remove_bg_birefnet", _rmbg), FakeImageGen("comfy.upscale_esrgan", _esrgan)]
    text = (RECIPES / f"{recipe}.yaml").read_text(encoding="utf-8")
    env = Env(tmp_path, gens, {recipe: text})
    env.registry.register(ClaudeStoreLayoutExecutor(env.settings, q))
    for cls in STORE_OPS:
        env.registry.register(cls())
    inputs: dict[str, Any] = {"title": "Ember Knight", "prompt": "a knight", "scene": "a castle", "count": 2}
    if recipe == "store-kit-image":
        buf = io.BytesIO()
        subject_img().save(buf, "PNG")
        inputs["image"] = (await env.blobs.put_bytes(buf.getvalue(), name="hero.png", mime="image/png")).hash
    gates = {k: {"enabled": False} for k in ("pick_icon", "pick_art", "approve")}
    run_id = await env.engine.start_run(recipe, inputs, params=gates)
    await asyncio.wait_for(env.engine.wait_idle(run_id), 60)
    steps = env.steps(run_id)
    assert env.status(run_id) == RunStatus.SUCCEEDED, {k: s.error for k, s in steps.items() if s.error}
    assert q.calls and q.calls[0][1].model == "claude-opus-5-5"
    layout = json.loads(env.blobs.path(steps["layout"].outputs["json"][0].hash).read_bytes())
    assert layout["font"] in sc.font_choices("Ember Knight") or layout["font"] == ""
    report = json.loads(env.blobs.path(steps["kit"].outputs["report"][0].hash).read_bytes())
    assert report["layout"]["case"] == "upper" and report["layout"]["wide_anchor"] == "left"
    with zipfile.ZipFile(env.blobs.path(steps["approve"].outputs["zip"][0].hash)) as z:
        names = set(z.namelist())
    assert "store_kit/ios/AppIcon.appiconset/Contents.json" in names
    assert "store_kit/steam/library_hero_3840x1240.png" in names
    assert "store_kit/android/res/mipmap-xxxhdpi/ic_launcher_foreground.png" in names
    finals = {r.name for r in steps["approve"].outputs["images"]}
    assert {"app_icon_1024.png", "play_store_512.png", "steam_header_capsule_920x430.png"} <= finals
    if recipe == "store-kit-image":
        assert len(env.execs["comfy.edit_flux2_klein4b"].params) == 3  # 2 icon framings + key art


def test_icon_tile_on_backdrop_is_cropped() -> None:
    img = Image.new("RGB", (1024, 1024))
    grad = np.linspace(200, 250, 1024, dtype=np.float32)[:, None, None] * np.ones((1, 1024, 3), np.float32)
    img = Image.fromarray(grad.astype(np.uint8))
    ImageDraw.Draw(img).rounded_rectangle((180, 200, 840, 860), radius=120, fill=(40, 60, 150))
    box = si.tile_box(img)
    assert box is not None and 180 < box[0] < 260 and 760 < box[2] < 840
    assert si.tile_box(icon_img()) is None and si.tile_box(art_img(1024, 1024)) is None
    tile_subject = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    ImageDraw.Draw(tile_subject).rounded_rectangle((180, 200, 840, 860), radius=120, fill=(40, 60, 150, 255))
    kit = build_kit(img, tile_subject, None, None, "Game", ["android"])
    assert kit["report"]["checks"]["icon"] == {"tile_cropped": list(box), "subject": False}
    assert any("tile" in f for f in kit["report"]["flagged"])
    files = files_of(kit)
    assert "android/res/mipmap-xxxhdpi/ic_launcher_foreground.png" not in files
    bg = Image.open(io.BytesIO(files["android/res/mipmap-xxxhdpi/ic_launcher_background.png"]))
    assert bg.getpixel((216, 216)) == (40, 60, 150)
