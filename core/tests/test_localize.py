import asyncio
import io
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from studio_core.adapters.claude import ClaudeReply
from studio_core.adapters.localize import ClaudeLocalizeExecutor, build_prompt, normalize_plan, parse_languages, \
    send_scale
from studio_core.contracts import RunStatus, StepError, StepResult
from studio_core.engine.library import validate
from studio_core.engine.recipe import load_recipe_file
from studio_core.ops import localize as L
from test_engine_runner import Env, FakeExec

RECIPES = Path(__file__).resolve().parents[1] / "studio_core" / "recipes"
WIN_FONTS = Path(r"C:\Windows\Fonts")
HAS_WIN_FONTS = sys.platform == "win32" and (WIN_FONTS / "arial.ttf").is_file()
LANGS = ["vi", "ja", "zh-Hans", "ko", "de"]
TRANS = {"vi": ("CHƠI NGAY", "CHƠI"), "ja": ("今すぐプレイ", "プレイ"), "zh-Hans": ("立即开始", "开始"),
         "ko": ("지금 플레이", "플레이"), "de": ("JETZT SPIELEN UND GEWINNEN", "SPIELEN")}


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return ImageFont.truetype(str(WIN_FONTS / "arialbd.ttf"), size) if HAS_WIN_FONTS else ImageFont.load_default(size)


def button(textured: bool = False) -> tuple[Image.Image, list[int]]:
    """Gradient (or noisy) banner with an orange button labelled PLAY NOW in white with a brown outline."""
    w, h = 320, 180
    y, x = np.mgrid[0:h, 0:w] / np.array([h, w])[:, None, None]
    rgb = np.stack([40 + 120 * x, 60 + 60 * y, 140 + 80 * x * y], -1)
    if textured:
        rgb = rgb + np.random.default_rng(1).integers(-40, 40, rgb.shape)
    img = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8)).convert("RGBA")
    d = ImageDraw.Draw(img)
    if not textured:
        d.rounded_rectangle((70, 100, 250, 160), 12, fill=(230, 140, 30, 255))
    d.text((160, 130), "PLAY NOW", font=_font(28), anchor="mm", fill=(255, 255, 255), stroke_width=2,
           stroke_fill=(90, 40, 0))
    return img, list(d.textbbox((160, 130), "PLAY NOW", font=_font(28), anchor="mm", stroke_width=2))


def reply(bbox: list[float], container: list[float], langs: list[str] = LANGS, scale: float = 1.0) -> dict[str, Any]:
    return {"source_lang": "en", "regions": [{
        "text": "PLAY NOW", "translate": True, "lines": 1,
        "bbox": [v * scale for v in bbox], "container": [v * scale for v in container],
        "style": {"font_px": 28 * scale, "weight": "bold", "align": "center", "color": "#FFF",
                  "stroke_color": "#5a2800", "stroke_px": 2 * scale, "shadow_color": ""},
        "translations": [{"lang": lg, "text": TRANS[lg][0], "short": TRANS[lg][1]} for lg in langs]},
        {"text": "x", "translate": True, "lines": 1, "bbox": [1, 1, 1, 1], "container": [0, 0, 1, 1], "style": {},
         "translations": []}]}


def test_languages_and_plan_normalize():
    assert parse_languages("vi, ja;zh_Hans  ko,VI") == ["vi", "ja", "zh-Hans", "ko"]
    with pytest.raises(StepError):
        parse_languages(" , ")
    assert send_scale((3000, 1500)) < 1 and send_scale((800, 600)) == 1.0
    raw = reply([100, 50, 200, 80], [90, 40, 210, 90], LANGS[:4], scale=0.5)
    plan = normalize_plan(raw, LANGS, (400, 300), scale=0.5)
    (r,) = plan["regions"]
    assert r["bbox"] == [100, 50, 200, 80] and r["container"] == [90, 40, 210, 90]
    assert r["style"]["font_px"] == 28 and r["style"]["stroke_px"] == 2 and r["style"]["color"] == "#ffffff"
    assert r["translations"]["ja"] == {"text": "今すぐプレイ", "short": "プレイ"}
    assert r["translations"]["de"] == {"text": "PLAY NOW", "short": "PLAY NOW"}
    assert plan["missing"] == ["de: PLAY NOW"]
    # A container smaller than its text is grown to hold it.
    tight = normalize_plan(reply([100, 50, 200, 80], [120, 55, 180, 70]), LANGS, (400, 300))
    assert tight["regions"][0]["container"] == [100, 50, 200, 80]
    with pytest.raises(StepError):
        normalize_plan({"regions": []}, LANGS, (10, 10))
    assert "zh-Hans" in build_prompt((400, 300), LANGS, "keep 'Dragon'") and "400x300" in build_prompt((400, 300), LANGS)


def test_script_detection_and_candidates():
    assert L.script_of("ja", "今すぐプレイ") == "jpan" and L.script_of("ja", "宝物") == "jpan"
    assert L.script_of("zh-Hans", "立即开始") == "hans" and L.script_of("zh-Hant", "立即開始") == "hant"
    assert L.script_of("ko", "플레이") == "kore" and L.script_of("vi", "Chơi ngay") == "latn"
    assert L.script_of("th", "เล่น") == "thai"
    c = L.candidates("kore", "bold")
    assert c[0] == "malgunbd.ttf" and "malgun.ttf" in c and "segoeuib.ttf" in c
    assert L.candidates("latn", "black")[0] == "seguibl.ttf"


@pytest.mark.skipif(not HAS_WIN_FONTS, reason="needs Windows fonts")
def test_font_fallback_needs_glyph_coverage(tmp_path: Path):
    # Only a Latin font on disk, named like the Korean one: the Hangul text must not pick it.
    shutil.copy(WIN_FONTS / "arial.ttf", tmp_path / "malgun.ttf")
    shutil.copy(WIN_FONTS / "arial.ttf", tmp_path / "segoeui.ttf")
    path, script = L.pick_font("ko", "보물 상점", dirs=[tmp_path])
    assert script == "kore" and path == ""
    path, _ = L.pick_font("vi", "Cửa Hàng Kho Báu", dirs=[tmp_path])
    assert Path(path).name.lower() in ("malgun.ttf", "segoeui.ttf")
    real = L.font_dirs()
    want = {"vi": "segoeuib.ttf", "ja": "yugothb.ttc", "zh-Hans": "msyhbd.ttc", "ko": "malgunbd.ttf"}
    for lang, (text, _) in TRANS.items():
        if lang in want:
            p, _ = L.pick_font(lang, text, "bold", dirs=real)
            assert Path(p).name.lower() == want[lang], (lang, p)


def test_wrap_and_fit_to_box():
    assert L.wrap("JETZT SPIELEN UND GEWINNEN", 2) == ["JETZT SPIELEN", "UND GEWINNEN"]
    assert L.wrap("今すぐプレイ", 2) == ["今すぐ", "プレイ"]
    font, _ = L.pick_font("de", "JETZT SPIELEN UND GEWINNEN", "bold")
    style = {"font_px": 28, "color": "#ffffff", "stroke_color": "#000000", "stroke_px": 2}
    small, big = (L.render_block(["SPIELEN"], font, s, style) for s in (12, 24))
    assert big.width > small.width and big.height > small.height
    ink, cont = [110, 118, 210, 142], [70, 100, 250, 160]
    cap = L.source_size("PLAY NOW", font, style, ink[3] - ink[1])
    assert cap >= L.MIN_PX
    fit = L.fit_text({"text": "OK", "short": "OK"}, font, style, ink, cont, cap)
    assert fit["variant"] == "text" and fit["scale"] == 1.0 and not fit["overflow"]
    long = L.fit_text({"text": "JETZT SPIELEN UND GEWINNEN", "short": "SPIELEN"}, font, style, ink, cont, cap,
                      allow_wrap=False, min_scale=0.9)
    assert long["variant"] == "short" and long["lines"] == ["SPIELEN"]
    block = L.render_block(long["lines"], font, long["size"], style)
    assert block.width <= long["avail_w"] and block.height <= (ink[3] - ink[1]) * 1.3 + 1
    wrapped = L.fit_text({"text": "JETZT SPIELEN UND GEWINNEN", "short": "JETZT SPIELEN UND GEWINNEN"}, font, style,
                         ink, cont, cap)
    assert len(wrapped["lines"]) == 2
    x, y = L.place(block, ink, cont, "center")
    assert cont[0] <= x and x + block.width <= cont[2] and cont[1] <= y and y + block.height <= cont[3]


def _plan(img: Image.Image, bbox: list[int], cont: list[int]) -> dict[str, Any]:
    return normalize_plan(reply(bbox, cont), LANGS, img.size)


def test_flat_background_cpu_clean_is_mask_only():
    img, bbox = button()
    clean, holes, labels, regions = L.prep(img, _plan(img, bbox, [70, 100, 250, 160]))
    assert not holes and [r["removal"] for r in regions] == ["cpu"] and regions[0]["flat"]
    a, c = np.asarray(img), np.asarray(clean)
    changed = (a != c).any(-1)
    assert changed.any() and not (changed & (labels == 0)).any()
    # Text gone: the masked area is close to the button's orange.
    m = labels == 1
    assert np.abs(c[m][:, :3].astype(int) - [230, 140, 30]).mean() < 12
    x0, y0, x1, y1 = regions[0]["ink"]
    assert x0 <= bbox[0] + 2 and x1 >= bbox[2] - 2 and y0 <= bbox[1] + 3 and y1 >= bbox[3] - 3


def test_textured_background_goes_to_klein_and_paste_stays_in_mask():
    img, bbox = button(textured=True)
    clean, holes, labels, regions = L.prep(img, _plan(img, bbox, [0, 0, 320, 180]))
    (reg,) = regions
    assert reg["removal"] == "klein" and len(holes) == 1
    hole = holes[0]
    assert all(v % 16 == 0 and 512 <= v <= 1024 for v in [max(hole.size)]) and min(hole.size) % 16 == 0
    alpha = np.asarray(hole)[..., 3]
    assert (alpha == 0).any() and (alpha == 255).any()
    assert list(hole.size) == reg["crop_size"]
    rgba = np.asarray(clean).copy()
    before = rgba.copy()
    L.paste_fill(rgba, Image.new("RGB", hole.size, (0, 255, 0)), reg["crop"], labels == reg["label"])
    changed = (rgba != before).any(-1)
    assert changed.any() and not (changed & (labels != reg["label"])).any()
    assert (rgba[labels == reg["label"]][:, 1] > 200).mean() > 0.5
    forced = L.prep(button()[0], _plan(img, bbox, [70, 100, 250, 160]), removal="klein")[3]
    assert forced[0]["removal"] == "klein"
    assert L.prep(img, _plan(img, bbox, [0, 0, 320, 180]), removal="cpu")[3][0]["removal"] == "cpu"


def test_transparent_logo_text_becomes_transparent():
    img = Image.new("RGBA", (200, 80), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.text((100, 40), "LOGO", font=_font(36), anchor="mm", fill=(250, 200, 40, 255))
    bbox = list(d.textbbox((100, 40), "LOGO", font=_font(36), anchor="mm"))
    clean, holes, labels, regions = L.prep(img, _plan(img, bbox, [0, 0, 200, 80]))
    assert not holes and regions[0]["removal"] == "cpu"
    assert np.asarray(clean)[..., 3].max() == 0


class FakeQuery:
    def __init__(self, payload: dict[str, Any]):
        self.payload = payload
        self.calls: list[tuple[list[dict[str, Any]], Any]] = []

    async def __call__(self, content: list[dict[str, Any]], options: Any) -> ClaudeReply:
        self.calls.append((content, options))
        return ClaudeReply(self.payload, "", {"cost_usd": 0.05, "model": options.model})


class FakeKlein(FakeExec):
    """Repaints the transparent (masked) area of each crop flat magenta, keeps the rest."""

    def __init__(self) -> None:
        super().__init__("comfy.inpaint_flux2_klein4b")
        self.params: list[dict[str, Any]] = []

    async def run(self, ctx: Any, params: dict[str, Any], inputs: Any) -> StepResult:
        self.params.append(params)
        out = []
        for i, r in enumerate(inputs.get("image") or []):
            im = Image.open(ctx.blobs.path(r.hash)).convert("RGBA")
            a = np.asarray(im).copy()
            a[a[..., 3] == 0, :3] = (255, 0, 255)
            a[..., 3] = 255
            buf = io.BytesIO()
            Image.fromarray(a, "RGBA").convert("RGB").save(buf, "PNG")
            out.append(await ctx.blobs.put_bytes(buf.getvalue(), name=f"fill_{i}.png", mime="image/png"))
        return StepResult(outputs={"images": out})


async def _run_recipe(tmp_path: Path, textured: bool, monkeypatch: pytest.MonkeyPatch):
    from studio_core.adapters import claude as cl

    monkeypatch.setattr(cl, "_image_block", lambda d, m: {"type": "image", "source": {"type": "base64"}})
    img, bbox = button(textured)
    q = FakeQuery(reply(bbox, [70, 100, 250, 160] if not textured else [0, 0, 320, 180]))
    klein = FakeKlein()
    env = Env(tmp_path, [klein], {"image-localize": (RECIPES / "image-localize.yaml").read_text(encoding="utf-8")})
    env.registry.register(ClaudeLocalizeExecutor(env.settings, q))
    for cls in L.LOCALIZE_OPS:
        env.registry.register(cls())
    buf = io.BytesIO()
    img.save(buf, "PNG")
    src = await env.blobs.put_bytes(buf.getvalue(), name="play_button.png", mime="image/png")
    run_id = await env.engine.start_run("image-localize", {"image": src.hash, "languages": ", ".join(LANGS)},
                                        params={"approve": {"enabled": False}})
    await asyncio.wait_for(env.engine.wait_idle(run_id), 60)
    steps = env.steps(run_id)
    assert env.status(run_id) == RunStatus.SUCCEEDED, {k: s.error for k, s in steps.items() if s.error}
    return env, q, klein, img, steps


async def test_recipe_flat_runs_without_klein(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    env, q, klein, img, steps = await _run_recipe(tmp_path, False, monkeypatch)
    assert q.calls and q.calls[0][1].model == "claude-opus-5-5"
    assert not klein.params and not steps["prep"].outputs.get("holes")
    names = [r.name for r in steps["approve"].outputs["images"]]
    assert names == [f"play_button_{lg}.png" for lg in LANGS]
    doc = json.loads(env.blobs.path(steps["approve"].outputs["localize"][0].hash).read_bytes())
    assert doc["type"] == L.LOCALIZE_TYPE and doc["source_lang"] == "en" and doc["regions"][0]["removal"] == "cpu"
    assert [e["lang"] for e in doc["languages"]] == LANGS
    de = next(e for e in doc["languages"] if e["lang"] == "de")["regions"][0]
    assert de["variant"] in ("text", "short") and de["size"] >= L.MIN_PX
    ja = Image.open(env.blobs.path(steps["approve"].outputs["images"][1].hash)).convert("RGBA")
    assert ja.size == img.size and (np.asarray(ja) != np.asarray(img)).any()
    prev = Image.open(env.blobs.path(steps["render"].outputs["preview"][0].hash))
    assert prev.width > 1000 and prev.height > 600

    from test_export_engines import _row

    from studio_core.export import godot, unity
    from studio_core.store.bundle import engine_ready, engine_source

    step = _row("approve", "gate.approve", steps["approve"].outputs)
    assert engine_ready(step, env.blobs)
    src = engine_source(step, [], env.blobs, "PlayButton")
    assert [n for n, _ in src.localized] == [*LANGS, "notext"] and src.localized_source_lang == "en"
    u = dict(unity.export(src))
    assert {"Localized/PlayButton_ja.png", "Localized/PlayButton_ja.png.meta", "Localized/PlayButton_notext.png"} <= \
        set(u)
    assert "spriteMode: 1" in u["Localized/PlayButton_ko.png.meta"].decode()
    assert "LocalizeSpriteEvent" in u["README.txt"].decode()
    g = dict(godot.export(src))
    assert "Localized/PlayButton_zh-Hans.png" in g
    assert 'PlayButton_zh-Hans.png:zh_Hans' in g["README.txt"].decode()


async def test_recipe_textured_pastes_klein_inside_mask(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    env, _, klein, img, steps = await _run_recipe(tmp_path, True, monkeypatch)
    assert len(klein.params) == 1 and "Remove the text" in klein.params[0]["14.text"]
    clean = np.asarray(Image.open(env.blobs.path(steps["render"].outputs["clean"][0].hash)).convert("RGBA"))
    labels = np.asarray(Image.open(env.blobs.path(steps["prep"].outputs["mask"][0].hash)).convert("L"))
    orig = np.asarray(img)
    changed = (clean != orig).any(-1)
    assert not (changed & (labels == 0)).any()
    magenta = (clean[..., 0] > 200) & (clean[..., 1] < 60) & (clean[..., 2] > 200)
    assert magenta[labels > 0].mean() > 0.5 and not magenta[labels == 0].any()


def test_recipe_loads_and_lints():
    r = load_recipe_file(RECIPES / "image-localize.yaml")
    assert [s.id for s in r.steps] == ["detect", "prep", "fill", "render", "approve"]
    assert r.step("fill").map == "image"
    _, issues = validate((RECIPES / "image-localize.yaml").read_text(encoding="utf-8"), None)
    assert issues == []
