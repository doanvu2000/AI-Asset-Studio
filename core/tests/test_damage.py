import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from studio_core.contracts import StepError
from studio_core.engine.library import validate
from studio_core.engine.recipe import load_recipe_file
from studio_core.ops.damage import (DamageDebris, DamagePrep, DamageStates, align_shift, components, crack_mask,
                                    cut_pieces, debris_index, finalize, hit_flash, label_pieces, parse_states,
                                    translate)
from test_ops_executors import FakeCtx, put_img

RECIPES = Path(__file__).resolve().parents[1] / "studio_core" / "recipes"


def crate(w: int = 120, h: int = 100) -> Image.Image:
    """Brown box with a dark outline and three dark plank seams on a transparent canvas."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle((10, 10, w - 11, h - 11), fill=(170, 110, 60, 255), outline=(30, 20, 10, 255), width=2)
    for y in (30, 50, 70):
        d.line((12, y, w - 13, y), fill=(35, 22, 12, 255), width=2)
    return img


def damaged(img: Image.Image, level: int) -> Image.Image:
    out = img.copy()
    d = ImageDraw.Draw(out)
    for k in range(level):
        x = 30 + 25 * k
        d.line((x, 12, x + 8, img.height - 13), fill=(25, 15, 8, 255), width=2)
    if level >= 2:
        d.rectangle((img.width - 30, 12, img.width - 11, 30), fill=(0, 0, 0, 0))
    return out


def test_params():
    assert parse_states("") == ["intact", "cracked", "broken", "wrecked"]
    assert parse_states("ok, hurt, bad shape, gone")[2] == "bad_shape"
    with pytest.raises(StepError):
        parse_states("a, b")
    with pytest.raises(StepError):
        parse_states("a, a, b, c")
    st = ["intact", "cracked", "broken", "wrecked"]
    assert debris_index("broken", st) == 2 and debris_index("wrecked", st) == 3
    assert debris_index("wrecked", ["a", "b", "c", "d"]) == 3 and debris_index("1", st) == 1
    assert debris_index("intact", st) == 1
    with pytest.raises(StepError):
        debris_index("nope", st)


def test_align_shift_and_translate():
    src = crate()
    oa = np.asarray(src)[..., 3]
    moved = translate(src, 5, -3)
    dx, dy = align_shift(oa, np.asarray(moved)[..., 3])
    assert abs(dx + 5) <= 1 and abs(dy - 3) <= 1


def test_components_crack_and_finalize():
    mask = np.zeros((20, 30), bool)
    mask[2:8, 2:10] = True
    mask[12:18, 15:28] = True
    lab = components(mask)
    assert lab.max() == 2 and (lab[mask] > 0).all() and (lab[~mask] == 0).all()
    rgba = np.asarray(crate())
    alpha = rgba[..., 3] > 127
    cm = crack_mask(rgba, alpha)
    assert cm[30, 60] and cm[50, 60] and not cm[40, 60]
    seeds = components(alpha & ~cm)
    lab = finalize(seeds, alpha, 4, 8, 0.01)
    n = lab.max()
    assert 4 <= n <= 8 and (lab[alpha] > 0).all() and (lab[~alpha] == 0).all()
    few = finalize(np.where(alpha, 1, 0).astype(np.int32), alpha, 5, 8, 0.01)
    assert few.max() == 5 and (few[alpha] > 0).all()


def test_pieces_tile_the_state_and_carry_offsets():
    src = damaged(crate(), 2)
    labels, method = label_pieces(src, None, 4, 8, 0.01)
    assert method == "cpu"
    pieces = cut_pieces(src, labels)
    assert 4 <= len(pieces) <= 8
    canvas = Image.new("RGBA", src.size, (0, 0, 0, 0))
    for im, info in pieces:
        x, y, w, h = info["rect"]
        assert im.size == (w, h)
        assert 0 <= info["pivot"][0] <= 1 and 0 <= info["pivot"][1] <= 1
        assert info["offset"] == [round(x + w / 2 - src.width / 2, 2), round(y + h / 2 - src.height / 2, 2)]
        assert abs(np.hypot(*info["dir"]) - 1) < 1e-3
        canvas.alpha_composite(im, (x, y))
    assert np.array_equal(np.asarray(canvas)[..., 3] > 0, np.asarray(src)[..., 3] > 0)
    assert abs(sum(i["mass"] for _, i in pieces) - 1) < 1e-2


def test_sam3_masks_drive_pieces():
    src = crate()
    w, h = src.size
    masks = []
    for x0 in (10, 40, 70):
        m = np.zeros((h, w), bool)
        m[10:h - 10, x0:x0 + 28] = True
        masks.append(m)
    labels, method = label_pieces(src, masks, 3, 3, 0.01)
    assert method == "sam3" and labels.max() == 3
    labels, _ = label_pieces(src, masks[:1], 2, 8, 0.01)
    assert 3 <= labels.max() <= 8
    _, method = label_pieces(src, [np.zeros((h, w), bool)], 4, 8, 0.01)
    assert method == "cpu"


def test_hit_flash_keeps_alpha():
    src = crate()
    f = np.asarray(hit_flash(src))
    a = np.asarray(src)
    assert np.array_equal(f[..., 3], a[..., 3]) and (f[a[..., 3] > 0][:, :3] == 255).all()


async def _run(tmp_path: Path, raw: bool = True, split: str = "auto", masks: bool = False):
    ctx = FakeCtx(tmp_path)
    src = crate()
    ref = await put_img(ctx, src, "crate.png")
    a = await DamagePrep().run(ctx, {}, {"images": [ref]})
    lay = json.loads(ctx.blobs.path(a.outputs["layout"][0].hash).read_bytes())
    x, y, bw, bh = lay["box"]
    # Fake Klein (white background, drifted a few px) + BiRefNet (same, transparent around).
    edits, raws = [], []
    for level, drift in ((1, 6), (2, -8), (3, 4)):
        im = damaged(src, level).resize((bw, bh), Image.Resampling.NEAREST)
        rgba = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
        rgba.alpha_composite(im, (x + drift, y))
        white = Image.new("RGBA", (1024, 1024), (255, 255, 255, 255))
        white.alpha_composite(rgba)
        edits.append(await put_img(ctx, rgba, f"rmbg_{level}.png"))
        raws.append(await put_img(ctx, white.convert("RGB"), f"edit_{level}.png"))
    b = await DamageStates().run(ctx, {"debris_from": "broken"},
                                 {"images": a.outputs["images"], "edits": edits, "raw": raws if raw else [],
                                  "layout": a.outputs["layout"]})
    mask_refs = []
    if masks:
        for x0 in (10, 40, 70):
            m = Image.new("L", src.size, 0)
            ImageDraw.Draw(m).rectangle((x0, 10, x0 + 27, src.height - 11), fill=255)
            mm = Image.new("RGB", (1024, 1024), (0, 0, 0))
            mm.paste(m.resize((bw, bh), Image.Resampling.NEAREST).convert("RGB"),
                     (x + (-8 if raw else 0), y))
            mask_refs.append(await put_img(ctx, mm, f"mask_{x0}.png"))
    c = await DamageDebris().run(ctx, {"split": split, "max_pieces": 6},
                                 {"images": b.outputs["images"], "masks": mask_refs, "info": b.outputs["info"]})
    return ctx, a, b, c


async def test_states_align_and_debris_outputs(tmp_path: Path):
    ctx, a, b, c = await _run(tmp_path)
    assert [r.name for r in b.outputs["images"]] == ["crate_intact.png", "crate_cracked.png", "crate_broken.png",
                                                    "crate_wrecked.png"]
    shifts = b.meta["shifts"]
    assert shifts[0] == [0, 0] and all(abs(s[0]) > 0 for s in shifts[1:])
    assert all(v > 0.9 for v in list(b.meta["iou"].values())[:1])
    assert b.outputs["debris_src"][0].name == "edit_2.png"
    doc = json.loads(ctx.blobs.path(c.outputs["damage"][0].hash).read_bytes())
    assert doc["type"] == "asset-studio/damage-states" and doc["canvas"] == [120, 100]
    assert [s["state"] for s in doc["states"]] == ["intact", "cracked", "broken", "wrecked"]
    assert all(s["hit"]["file"].endswith("_hit.png") for s in doc["states"])
    assert doc["debris"]["from"] == "broken" and doc["debris"]["method"] == "cpu"
    assert 4 <= len(doc["debris"]["pieces"]) <= 6
    assert len(c.outputs["pieces"]) == len(doc["debris"]["pieces"]) and len(c.outputs["hit"]) == 4
    strip = Image.open(ctx.blobs.path(c.outputs["strip"][0].hash))
    assert strip.size == (480, 100)
    prev = Image.open(ctx.blobs.path(c.outputs["preview"][0].hash))
    assert prev.width > 500 and prev.height > 300


async def test_debris_uses_sam3_masks(tmp_path: Path):
    ctx, _, _, c = await _run(tmp_path, masks=True)
    assert c.meta["method"] == "sam3" and 4 <= c.meta["pieces"] <= 6


async def test_cpu_split_ignores_masks_and_no_raw_fallback(tmp_path: Path):
    ctx, _, b, c = await _run(tmp_path, raw=False, split="cpu", masks=True)
    assert c.meta["method"] == "cpu"
    assert b.outputs["debris_src"][0].name == "crate_broken_ref.png"


async def test_export_unity_and_godot(tmp_path: Path):
    from test_export_engines import _row

    from studio_core.export import godot, unity
    from studio_core.store.bundle import engine_ready, engine_source

    ctx, _, _, c = await _run(tmp_path)
    step = _row("approve", "gate.approve", {k: c.outputs[k] for k in ("preview", "images", "pieces", "hit", "strip",
                                                                        "damage")})
    assert engine_ready(step, ctx.blobs)
    src = engine_source(step, [], ctx.blobs, "Crate")
    assert [s for s, _, _ in src.damage_states] == ["intact", "cracked", "broken", "wrecked"]
    n = len(src.damage_pieces)
    assert n >= 4 and src.damage_canvas == (120, 100)

    u = dict(unity.export(src))
    assert {"Damage/Crate_intact.png", "Damage/Crate_wrecked_hit.png", "Damage/Crate_piece_0.png",
            "Damage/Crate_damage.prefab", "Damage/Crate_damage.prefab.meta"} <= set(u)
    assert "spriteMode: 1" in u["Damage/Crate_piece_0.png.meta"].decode()
    pf = u["Damage/Crate_damage.prefab"].decode()
    assert pf.startswith("%YAML 1.1") and pf.count("Rigidbody2D:") == n and pf.count("BoxCollider2D:") == n
    assert pf.count("SpriteRenderer:") == n + 1 and "m_IsActive: 0" in pf and "m_Name: Pieces" in pf
    guid = next(line.split()[1] for line in u["Damage/Crate_intact.png.meta"].decode().splitlines()
                if line.startswith("guid:"))
    assert f"{{fileID: 21300000, guid: {guid}, type: 3}}" in pf
    ids = {line.split("&")[1] for line in pf.splitlines() if line.startswith("--- !u!")}
    refs = {r for r in __import__("re").findall(r"fileID: (-?\d+)\}", pf) if r not in ("0", "10754", "21300000")}
    assert refs <= ids, refs - ids
    idx = json.loads(u["Damage/Crate_damage.json"])
    assert len(idx["pieces"]) == n and idx["states"][0]["sprite"] == "Crate_intact.png"
    assert "Damage/" in u["README.txt"].decode()

    g = dict(godot.export(src))
    tscn = g["Crate_damage.tscn"].decode()
    assert tscn.startswith("[gd_scene format=3]") and 'path="asset_studio_damage.gd"' in tscn
    assert tscn.count('type="RigidBody2D"') == n and tscn.count('type="Sprite2D" parent="States"') == 4
    assert 'metadata/hit = ExtResource("h3")' in tscn and "freeze = true" in tscn
    assert "func break_apart()" in g["asset_studio_damage.gd"].decode()
    assert "Damage/Crate_piece_0.png" in g and "Damage/Crate_cracked_hit.png" in g


def test_recipe_loads_and_lints():
    r = load_recipe_file(RECIPES / "damage-states.yaml")
    assert [s.id for s in r.steps] == ["prep", "cracked", "broken", "wrecked", "rmbg", "align", "segment", "debris",
                                       "approve"]
    _, issues = validate((RECIPES / "damage-states.yaml").read_text(encoding="utf-8"), None)
    assert issues == []
