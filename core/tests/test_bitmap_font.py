import json
import unicodedata
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from studio_core.contracts import ExecutorRegistry, StepError
from studio_core.engine.library import validate
from studio_core.engine.recipe import load_recipe_file
from studio_core.ops import register
from studio_core.ops.bitmap_font import (ASCII, BitmapFont, FontSheet, Style, build_font, charset, fnt_text, pack,
                                         parse_fnt, render_text, resolve_font, resolve_style, sample_texts,
                                         vietnamese_letters)
from studio_core.ops.sfnt_kern import kerning
from studio_core.ops.store_capsules import available_fonts
from test_ops_executors import FakeCtx, put_img

RECIPES = Path(__file__).resolve().parents[1] / "studio_core" / "recipes"
FONTS = available_fonts()
needs_font = pytest.mark.skipif(not any(f in FONTS for f in ("segoe_bold", "arial_bold", "dejavu_bold")),
                                reason="no Vietnamese-capable system font")


def _vi_font() -> tuple[str, str]:
    fid = next(f for f in ("arial_bold", "segoe_bold", "dejavu_bold") if f in FONTS)
    return fid, FONTS[fid]


def test_vietnamese_charset_is_complete_and_precomposed():
    vi = vietnamese_letters()
    assert len(vi) == 134 and len(set(vi)) == 134
    assert all(len(c) == 1 and unicodedata.normalize("NFC", c) == c for c in vi)
    for word in ("Điểm", "Việt", "ỨỰỬỮ", "ẮẶẲẴ", "ỳỷỹýỵ", "ơưđ"):
        assert all(c in ASCII + vi for c in word), word
    full = charset("vietnamese")
    assert full.startswith(" ") and len(full) == 95 + 134
    assert charset("digits").startswith("0123456789") and " " in charset("digits")
    assert charset("custom", "SCORE 0123 ★") == " SCORE0123★"
    assert charset("ascii", "é").endswith("é")
    with pytest.raises(StepError):
        charset("custom", "")
    with pytest.raises(StepError):
        charset("klingon")


def test_style_presets_scale_and_override():
    st = resolve_style({"preset": "gold"}, 48)
    assert not st.pixel and st.outline == pytest.approx(0.06 * 48) and st.tracking == 3
    st = resolve_style({"preset": "gold", "outline": 0, "colors": "#ff0000, #00ff00", "bevel": 0}, 48)
    assert st.outline == 0 and st.top == "#ff0000" and st.bottom == "#00ff00" and st.bevel == 0 and st.tracking == 0
    px = resolve_style({"preset": "pixel", "pixel_scale": 2}, 32)
    assert px.pixel and px.scale == 2 and px.outline == 1 and px.shadow == (1.0, 1.0) and px.blur == 0
    assert px.pads() == (1, 1, 2, 2)
    with pytest.raises(StepError):
        resolve_style({"preset": "lava"}, 48)


def test_pack_power_of_two_without_overlap():
    rng = np.random.default_rng(1)
    sizes = [(int(w), int(h)) for w, h in rng.integers(5, 60, (150, 2))] + [(0, 0)]
    W, H, pos = pack(sizes, 2)
    assert W & (W - 1) == 0 and H & (H - 1) == 0
    occ = np.zeros((H, W), np.int32)
    for (x, y), (w, h) in zip(pos, sizes):
        occ[y:y + h, x:x + w] += 1
    assert occ.max() == 1


def test_kerning_reads_gpos_or_kern_table():
    if "arial_bold" not in FONTS:
        pytest.skip("Arial Bold not installed")
    k = kerning(FONTS["arial_bold"], "AVTo.", 48)
    assert k[("A", "V")] < 0 and k[("T", "o")] < 0
    assert ("V", "V") not in k
    assert kerning("missing.ttf", "AV", 48) == {}


@needs_font
def test_build_metrics_padding_and_fnt_round_trip():
    fid, path = _vi_font()
    chars = charset("vietnamese")
    st = resolve_style({"preset": "gold"}, 40)
    fb = build_font(chars, fid, path, 40, st, "gold")
    doc = fb.doc
    H, W = fb.atlas.shape[:2]
    assert W == doc["scale_w"] and H == doc["scale_h"] and W & (W - 1) == 0 and H & (H - 1) == 0
    from studio_core.ops.store_capsules import load_font

    font = load_font(fid, 40)
    pl, pt, pr, pb = st.pads()
    assert doc["padding"] == [pl, pt, pr, pb] and doc["base"] == font.getmetrics()[0]
    by = {c["char"]: c for c in doc["chars"]}
    assert set(by) == set(chars)
    for ch in ("A", "g", "Đ", "ặ", "1"):
        l, t, r, b = font.getbbox(ch, anchor="la")
        c = by[ch]
        assert c["xadvance"] == round(font.getlength(ch)) + st.tracking
        assert (c["xoffset"], c["yoffset"]) == (l - pl, t - pt)
        assert (c["width"], c["height"]) == (r - l + pl + pr, b - t + pt + pb)
        cell = fb.atlas[c["y"]:c["y"] + c["height"], c["x"]:c["x"] + c["width"], 3]
        # Padding holds the outline + shadow: ink reaches past the glyph box but never the cell edge.
        assert cell[pt:-pb, pl:-pr].max() == 255 and cell[:, 0].max() < 40 and cell[0, :].max() < 40
    assert by[" "]["width"] == 0 and by[" "]["xadvance"] > 0
    for a, b in zip(doc["chars"], doc["chars"][1:]):
        ra = (a["x"], a["y"], a["x"] + a["width"], a["y"] + a["height"])
        rb = (b["x"], b["y"], b["x"] + b["width"], b["y"] + b["height"])
        apart = ra[2] <= rb[0] or rb[2] <= ra[0] or ra[3] <= rb[1] or rb[3] <= ra[1]
        assert a["width"] == 0 or b["width"] == 0 or apart

    text = fnt_text(doc, "gold.png")
    lines = text.splitlines()
    assert lines[0].startswith("info face=") and "unicode=1" in lines[0]
    assert lines[1].startswith(f"common lineHeight={doc['line_height']} base={doc['base']} scaleW={W} scaleH={H}")
    assert lines[2] == 'page id=0 file="gold.png"' and lines[3] == f"chars count={len(chars)}"
    fnt = parse_fnt(text)
    assert len(fnt["chars"]) == len(chars) and fnt["pages"] == {0: "gold.png"}
    g = fnt["chars"][ord("ặ")]
    assert {k: g[k] for k in ("x", "y", "width", "height", "xoffset", "yoffset", "xadvance")} == \
        {k: by["ặ"][k] for k in ("x", "y", "width", "height", "xoffset", "yoffset", "xadvance")}
    assert fnt["kernings"] == {(a, b): v for a, b, v in doc["kernings"]}
    if fid == "arial_bold":
        assert fnt["kernings"][(ord("A"), ord("V"))] < 0
    atlas = Image.fromarray(fb.atlas, "RGBA")
    av, aa = render_text(fnt, atlas, "AV"), render_text(fnt, atlas, "AA")
    assert av.width <= aa.width


@needs_font
def test_pixel_mode_is_hard_edged_and_scaled():
    fid, path = _vi_font()
    st = resolve_style({"preset": "pixel", "pixel_scale": 2}, 32)
    fb = build_font(charset("digits"), fid, path, 32, st)
    a = fb.atlas[..., 3]
    assert set(np.unique(a)) <= {0, 255}
    c = next(c for c in fb.doc["chars"] if c["char"] == "8")
    assert c["width"] % 2 == 0 and c["x"] % 2 == 0 and fb.doc["pixel"] and "smooth=0" in fnt_text(fb.doc, "p.png")
    cell = fb.atlas[c["y"]:c["y"] + c["height"], c["x"]:c["x"] + c["width"]]
    assert (cell[0::2, 0::2] == cell[1::2, 1::2]).all()


def test_resolve_font_checks_coverage():
    if "impact" in FONTS:
        with pytest.raises(StepError):
            resolve_font("impact", "gold", charset("vietnamese"))
    with pytest.raises(StepError):
        resolve_font("no_such_font", "gold", "A")
    if any(f in FONTS for f in ("segoe_black", "arial_bold", "segoe_bold")):
        fid, _ = resolve_font("auto", "gold", charset("vietnamese"))
        assert fid != "impact"


def test_samples_skip_partially_covered_text():
    assert "Điểm: 12345" in sample_texts(charset("vietnamese"))
    s = sample_texts(charset("ascii"))
    assert "SCORE: 12345" in s and not any("Đ" in t for t in s)
    d = sample_texts(charset("digits"))
    assert "+250 x3 99% 1:30" in d and len(d) >= 2


async def _run_font(tmp_path: Path, **params):
    ctx = FakeCtx(tmp_path)
    fid, _ = _vi_font()
    res = await BitmapFont().run(ctx, {"preset": "wood", "charset": "vietnamese", "size": 32, "font": fid,
                                       "name": "Wood Font", **params}, {})
    return ctx, res


@needs_font
async def test_op_outputs_and_engine_export(tmp_path: Path):
    from test_export_engines import _row

    from studio_core.export import godot, unity
    from studio_core.store.bundle import engine_ready, engine_source

    ctx, res = await _run_font(tmp_path)
    assert [r.name for r in res.outputs["images"]] == ["Wood_Font.png"]
    assert [r.name for r in res.outputs["fnt"]] == ["Wood_Font.fnt"]
    doc = json.loads(ctx.blobs.path(res.outputs["font"][0].hash).read_bytes())
    assert doc["type"] == "asset-studio/bitmap-font" and doc["atlas"]["hash"] == res.outputs["images"][0].hash
    with Image.open(ctx.blobs.path(res.outputs["preview"][0].hash)) as im:
        assert im.width > 300 and im.height > 100

    step = _row("approve", "gate.approve", {k: res.outputs[k] for k in ("preview", "images", "fnt", "font")})
    assert engine_ready(step, ctx.blobs)
    src = engine_source(step, [], ctx.blobs, "Wood")
    assert src.font is not None and src.font_atlas == ctx.blobs.path(doc["atlas"]["hash"])

    g = dict(godot.export(src))
    fnt = parse_fnt(g["Fonts/Wood.fnt"].decode())
    assert fnt["pages"] == {0: "Wood.png"} and len(fnt["chars"]) == 229 and "Fonts/Wood.png" in g
    scene = g["Wood_font_demo.tscn"].decode()
    assert 'type="FontFile" path="Fonts/Wood.fnt"' in scene and f"font_size = {doc['size']}" in scene
    assert "Fonts/Wood.fnt" in g["README.txt"].decode()

    u = dict(unity.export(src))
    fs = u["Fonts/Wood.fontsettings"].decode()
    assert "--- !u!128 &12800000" in fs and fs.count("index: ") == 229 and f"m_FontSize: {doc['size']}" in fs
    tex_guid = u["Fonts/Wood.png.meta"].decode().split("guid: ")[1].split()[0]
    mat_guid = u["Fonts/Wood_font.mat.meta"].decode().split("guid: ")[1].split()[0]
    assert f"guid: {tex_guid}, type: 3" in fs and f"guid: {mat_guid}, type: 2" in fs
    assert f"guid: {tex_guid}, type: 3" in u["Fonts/Wood_font.mat"].decode()
    assert "fileID: 10770" in u["Fonts/Wood_font.mat"].decode()
    assert "Fonts/Wood.fnt" in u and "TextMeshPro" in u["README.txt"].decode()


@needs_font
async def test_klein_texture_keeps_ttf_alpha(tmp_path: Path):
    ctx = FakeCtx(tmp_path)
    fid, _ = _vi_font()
    params = {"preset": "plain", "charset": "digits", "size": 40, "font": fid, "outline": 0, "shadow": 0,
              "colors": "#204080",
              "bevel": 0, "glow": 0, "pixel": "off"}
    sheet = await FontSheet().run(ctx, params, {})
    lay = json.loads(ctx.blobs.path(sheet.outputs["sheet"][0].hash).read_bytes())
    assert lay["width"] <= 1024 and lay["height"] <= 1024 and lay["size"] > 40
    with Image.open(ctx.blobs.path(sheet.outputs["images"][0].hash)) as im:
        base = np.asarray(im.convert("RGB"))
    # A "Klein" result: glyph interiors painted red, the white backdrop turned grey, at a different resolution.
    ink = base.mean(-1) < 250
    fake = np.where(ink[..., None], [200, 30, 30], [128, 128, 128]).astype(np.uint8)
    k_ref = await put_img(ctx, Image.fromarray(fake).resize((base.shape[1] * 3 // 4, base.shape[0] * 3 // 4)), "k.png")
    tex = await BitmapFont().run(ctx, params, {"texture": [k_ref], "sheet": sheet.outputs["sheet"]})
    plain = await BitmapFont().run(ctx, params, {})
    with Image.open(ctx.blobs.path(tex.outputs["images"][0].hash)) as im:
        t = np.asarray(im.convert("RGBA")).astype(int)
    with Image.open(ctx.blobs.path(plain.outputs["images"][0].hash)) as im:
        p = np.asarray(im.convert("RGBA")).astype(int)
    assert (t[..., 3] == p[..., 3]).all()
    solid = t[..., 3] > 200
    assert t[solid, 0].mean() > 170 and t[solid, 1].mean() < 70
    with pytest.raises(StepError):
        await BitmapFont().run(ctx, {**params, "charset": "ascii"},
                               {"texture": [k_ref], "sheet": sheet.outputs["sheet"]})


def test_recipes_load_and_lint():
    reg = ExecutorRegistry()
    register(reg)
    assert reg.resolve("op.bitmap_font") and reg.resolve("op.font_sheet")
    for rid, steps in (("bitmap-font", ["font", "approve"]),
                       ("bitmap-font-klein", ["sheet", "paint", "font", "approve"])):
        path = RECIPES / f"{rid}.yaml"
        r = load_recipe_file(path)
        assert [s.id for s in r.steps] == steps
        _, issues = validate(path.read_text(encoding="utf-8"), None)
        assert issues == [], (rid, issues)
    r = load_recipe_file(RECIPES / "bitmap-font-klein.yaml")
    prompt = r.resolve_params(r.step("paint"), r.coerce_inputs({"style": "icy crystal"}))["75:74.text"]
    assert "icy crystal" in prompt and "same shape" in prompt


def test_style_dataclass_pads_cover_shadow_blur():
    st = Style("#ffffff", "#ffffff", 2, "#000000", (3.0, 4.0), 2.0, "#000000", 0.5, 0, 0, "#ffffff")
    left, top, right, bottom = st.pads()
    assert right >= 2 + 6 + 3 and bottom >= 2 + 6 + 4 and left >= 2 + 6 - 3
