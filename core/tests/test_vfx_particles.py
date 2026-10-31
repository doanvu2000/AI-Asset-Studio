import asyncio
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image, ImageDraw

from studio_core.contracts import RunStatus, StepError, StepResult
from studio_core.engine.kinds import CATALOG
from studio_core.engine.library import validate
from studio_core.engine.recipe import load_recipe_file
from studio_core.ops import vfx_particles as vp
from test_api_basic import AUTH, app_ctx  # noqa: F401  (fixture reuse)
from test_engine_runner import Env, FakeExec
from test_ops_executors import FakeCtx, load, put_img

RECIPES = Path(__file__).resolve().parents[1] / "studio_core" / "recipes"


def _alpha(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("RGBA"))[..., 3]


def test_procedural_textures_are_clean_and_transparent_at_the_border():
    tex = vp.procedural(64)
    assert set(tex) == set(vp.TEXTURES)
    for k, img in tex.items():
        a = _alpha(img)
        assert img.size == ((128, 128) if k == "smoke" else (64, 64)), k
        assert a.max() > 200, k
        assert a[0].max() < 8 and a[-1].max() < 8 and a[:, 0].max() < 8 and a[:, -1].max() < 8, k
    spark = _alpha(tex["spark"])
    assert (spark > 64).any(0).sum() < (spark > 64).any(1).sum() / 3
    cells = [_alpha(tex["smoke"])[y:y + 64, x:x + 64] for y in (0, 64) for x in (0, 64)]
    assert all(c.max() > 100 for c in cells) and np.abs(cells[0].astype(int) - cells[1]).mean() > 3


def test_styled_cell_keys_black_and_rejects_empty():
    img = Image.new("RGB", (256, 256), (0, 0, 0))
    ImageDraw.Draw(img).ellipse((80, 80, 176, 176), fill=(255, 120, 20))
    cell = vp.styled_cell(img, 64)
    assert cell is not None
    a = np.asarray(cell)
    assert a[32, 32, 3] > 200 and a[2, 2, 3] == 0 and a[32, 32, 0] == 255 and a[32, 32, 2] < 60
    assert vp.styled_cell(Image.new("RGB", (256, 256)), 64) is None
    assert vp.styled_cell(Image.new("RGB", (256, 256), (255, 255, 255)), 64) is None


def test_presets_tint_and_styled_swap():
    ps = vp.build_presets(["heal", "explosion"], vp.parse_tint("#3080ff"), ["explosion"], True)
    import colorsys

    r, g, b, _ = vp._hex_rgba(ps[0]["emitters"][0]["colors"][1][1])
    assert abs(colorsys.rgb_to_hsv(r, g, b)[0] - colorsys.rgb_to_hsv(0x30 / 255, 0x80 / 255, 1)[0]) < 0.02
    fire = next(e for e in ps[1]["emitters"] if e["name"] == "fire")
    assert fire["texture"] == vp.STYLED and all(h.startswith("#ffffff") for _, h in fire["colors"])
    assert "styled" not in fire
    assert vp.parse_tint("auto") is None
    with pytest.raises(StepError):
        vp.parse_tint("red")
    with pytest.raises(StepError):
        vp.parse_list("explosion, nuke", vp.DEFAULT_PRESETS, "preset")
    for p in vp.PRESETS.values():
        for em in p["emitters"]:
            assert max(v for _, v in em["size_curve"]) == pytest.approx(1.0), (p, em["name"])
            assert len(em["colors"]) <= 8 and not (em["burst"] and em["rate"])


def test_simulator_moves_particles_and_flipbook_has_frames():
    tex = vp.procedural(64)
    preset = dict(vp.PRESETS["explosion"], name="explosion")
    sims = vp.simulate(preset)
    sparks = next((em, p) for em, p in sims if em["name"] == "sparks")
    early, late = vp.state_at(*sparks, 0.05), vp.state_at(*sparks, 0.25)
    assert len(early["u"]) == 30 and np.abs(late["pos"]).mean() > np.abs(early["pos"]).mean()
    assert [em["blend"] for em, _ in sims][0] == "alpha"
    frames = vp.flipbook(dict(vp.PRESETS["hit_spark"], name="hit_spark"), tex, 64)
    assert len(frames) == round(0.45 * vp.HIT_FPS) and all(f.size == (64, 64) for f in frames)
    cover = [float((_alpha(f) > 20).mean()) for f in frames]
    assert max(cover) > 0.02 and cover[-1] < max(cover)
    img = vp.render(dict(vp.PRESETS["magic_aura"], name="magic_aura"), tex, 1.0, 128, vp.BG)
    assert np.asarray(img).std() > 5


async def _pack(ctx, **params: Any):
    return await vp.VfxParticles().run(ctx, {"asset": "Phép", "size": 64, **params}, {"styled": []})


async def test_executor_writes_doc_textures_preview_and_hit_frames(tmp_path: Path):
    ctx = FakeCtx(tmp_path)
    res = await _pack(ctx)
    doc = json.loads(ctx.blobs.path(res.outputs["vfx"][0].hash).read_bytes())
    assert doc["type"] == vp.VFX_TYPE and [p["name"] for p in doc["presets"]] == list(vp.PRESETS)
    assert set(doc["textures"]) == set(vp.TEXTURES) and doc["textures"]["smoke"]["cols"] == 2
    assert [r.name for r in res.outputs["images"]][0] == "phep_soft_dot.png"
    assert load(ctx, res.outputs["preview"][0]).width == 4 * vp.PREVIEW_CELL
    assert len(res.outputs["hit_frames"]) == doc["hit"]["frames"] > 4
    small = await _pack(ctx, presets="dust_step", tint="#ff0000")
    d2 = json.loads(ctx.blobs.path(small.outputs["vfx"][0].hash).read_bytes())
    assert set(d2["textures"]) == {"smoke"} and d2["tint"] == "#ff0000"
    with pytest.raises(StepError):
        await _pack(ctx, size=100)


async def test_styled_images_become_a_sheet(tmp_path: Path):
    ctx = FakeCtx(tmp_path)
    renders = []
    for i in range(3):
        img = Image.new("RGB", (256, 256))
        ImageDraw.Draw(img).ellipse((70 + i * 5, 70, 180, 186), fill=(255, 140 + i * 30, 30))
        renders.append(await put_img(ctx, img, f"s{i}.png"))
    renders.append(await put_img(ctx, Image.new("RGB", (256, 256)), "empty.png"))
    res = await vp.VfxParticles().run(ctx, {"size": 64, "presets": "explosion, heal"}, {"styled": renders})
    doc = json.loads(ctx.blobs.path(res.outputs["vfx"][0].hash).read_bytes())
    assert doc["styled"] and doc["styled_rejected"] == 1 and vp.STYLED in doc["textures"]
    fire = next(e for e in doc["presets"][0]["emitters"] if e["name"] == "fire")
    assert fire["texture"] == vp.STYLED
    assert all(e["texture"] != vp.STYLED for e in doc["presets"][1]["emitters"])


async def test_style_plan_is_empty_without_style(tmp_path: Path):
    ctx = FakeCtx(tmp_path)
    for style, n in (("", 0), ("  blue arcane fire ", 1)):
        res = await vp.VfxStylePlan().run(ctx, {"style": style}, {})
        doc = json.loads(ctx.blobs.path(res.outputs["plan"][0].hash).read_bytes())
        assert len(doc["prompts"]) == n
    assert "blue arcane fire" in doc["prompts"][0] and "black background" in doc["prompts"][0]


async def _approve_step(tmp_path: Path, **params: Any):
    from test_export_engines import _row

    ctx = FakeCtx(tmp_path)
    res = await _pack(ctx, **params)
    step = _row("approve", "gate.approve", {"images": res.outputs["images"], "preview": res.outputs["preview"],
                                            "vfx": res.outputs["vfx"]})
    return ctx, step


async def test_export_unity(tmp_path: Path):
    from studio_core.export import unity
    from studio_core.export.vfx_particles import EDITOR_FILE, LEGACY_SHADER
    from studio_core.store.bundle import engine_ready, engine_source

    ctx, step = await _approve_step(tmp_path)
    assert engine_ready(step, ctx.blobs)
    src = engine_source(step, [], ctx.blobs, "Spells")
    assert src.vfx and set(src.vfx_files) == set(vp.TEXTURES) and src.sheet is None
    u = dict(unity.export(src))
    for p in vp.PRESETS:
        assert f"VFX/Prefabs/Spells_{p}.prefab" in u and f"VFX/Prefabs/Spells_{p}.prefab.meta" in u
    assert {"VFX/Spells_vfx_demo.prefab", "VFX/Spells_vfx.json", f"Editor/{EDITOR_FILE}",
            "VFX/Materials/Spells_smoke_alpha.mat", "VFX/Materials/Spells_spark_add.mat"} <= set(u)
    ex = u["VFX/Prefabs/Spells_explosion.prefab"].decode()
    assert ex.count("--- !u!198 ") == 5 and ex.count("--- !u!199 ") == 5 and "stopAction: 2" in ex
    assert "tilesX: 2" in ex and "m_RenderMode: 1" in ex and "m_BurstCount: 1" in ex
    assert "TrailRenderer" in u["VFX/Prefabs/Spells_projectile_trail.prefab"].decode()
    assert f"fileID: {LEGACY_SHADER['add']}," in u["VFX/Materials/Spells_spark_add.mat"].decode()
    demo = u["VFX/Spells_vfx_demo.prefab"].decode()
    assert demo.count("--- !u!198 ") == 7 + sum(len(p["emitters"]) for p in vp.PRESETS.values())
    assert "stopAction: 2" not in demo
    ids = [ln.split("&")[1] for ln in ex.splitlines() if ln.startswith("--- !u!")]
    assert len(ids) == len(set(ids))
    guids = [ln for k, b in u.items() if k.endswith(".meta") for ln in b.decode().splitlines() if ln.startswith("guid:")]
    assert len(guids) == len(set(guids))
    tex_meta = u["VFX/Textures/Spells_smoke.png.meta"].decode()
    assert "enableMipMap: 1" in tex_meta and "wrapU: 1" in tex_meta and "textureType: 0" in tex_meta
    doc = json.loads(u["VFX/Spells_vfx.json"])
    assert doc["textures"]["spark"]["file"] == "Textures/Spells_spark.png" and "hash" not in doc["textures"]["spark"]
    assert "namespace AssetStudio" in u[f"Editor/{EDITOR_FILE}"].decode()
    assert "Fit VFX Materials" in u["README.txt"].decode()


async def test_export_godot(tmp_path: Path):
    from studio_core.export import godot
    from studio_core.store.bundle import engine_source

    ctx, step = await _approve_step(tmp_path)
    g = dict(godot.export(engine_source(step, [], ctx.blobs, "Spells")))
    for p in vp.PRESETS:
        assert f"VFX/Presets/Spells_{p}.tscn" in g and f"VFX/Presets3D/Spells_{p}_3d.tscn" in g
    ex = g["VFX/Presets/Spells_explosion.tscn"].decode()
    assert ex.count('type="GPUParticles2D"') == 4 and ex.count("ParticleProcessMaterial") == 4
    assert "particles_anim_h_frames = 2" in ex and "explosiveness = 1.0" in ex and "metadata/delay = 0.1" in ex
    assert 'path="../Textures/Spells_smoke.png"' in ex and "particle_flag_align_y = true" in ex
    assert ex.index('name="smoke"') < ex.index('name="fire"')
    heal = g["VFX/Presets/Spells_heal.tscn"].decode()
    assert "one_shot = false" in heal and "preprocess" in heal and "direction = Vector3(0, -1, 0)" in heal
    trail = g["VFX/Presets/Spells_projectile_trail.tscn"].decode()
    assert "local_coords = false" in trail and "trail_enabled" not in trail
    d3 = g["VFX/Presets3D/Spells_explosion_3d.tscn"].decode()
    assert 'type="GPUParticles3D"' in d3 and "billboard_mode = 3" in d3 and "direction = Vector3(0, 1, 0)" in d3
    demo = g["VFX/Spells_vfx_demo.tscn"].decode()
    assert demo.count("instance=ExtResource") == 7 and "Camera2D" in demo and "loop_demo = true" in demo
    assert "func play()" in g["VFX/asset_studio_vfx.gd"].decode()


async def test_export_godot_delay_player_and_3d_stretch(tmp_path: Path):
    from studio_core.export import godot
    from studio_core.export.vfx_particles import STRETCH_MAX
    from studio_core.store.bundle import engine_source

    ctx, step = await _approve_step(tmp_path)
    g = dict(godot.export(engine_source(step, [], ctx.blobs, "Spells")))
    ex = g["VFX/Presets/Spells_explosion.tscn"].decode()
    # The delayed smoke is switched on by an autoplaying AnimationPlayer, not only by the script's timers.
    assert 'type="AnimationPlayer"' in ex and 'autoplay = &"play"' in ex
    assert 'NodePath("smoke:emitting")' in ex and '"times": PackedFloat32Array(0, 0.1)' in ex
    assert ex.index("[sub_resource") < ex.index('[sub_resource type="Animation"') < ex.index("[node ")
    assert "scale_over_velocity_curve" in ex and f"Vector2(1, {STRETCH_MAX:g})" in ex
    assert 'type="AnimationPlayer"' not in g["VFX/Presets/Spells_heal.tscn"].decode()
    d3 = g["VFX/Presets3D/Spells_explosion_3d.tscn"].decode()
    assert 'path="../asset_studio_vfx_stretch_add.gdshader"' in d3 and 'shader = ExtResource("stretch_add")' in d3
    shader = g["VFX/asset_studio_vfx_stretch_add.gdshader"].decode()
    assert "blend_add" in shader and "MODEL_MATRIX[1]" in shader and "POSITION =" in shader
    assert "billboard_mode = 3" in d3
    assert 'get_node_or_null("Delays")' in g["VFX/asset_studio_vfx.gd"].decode()


async def test_export_unity_stretched_billboard_scales_with_speed(tmp_path: Path):
    from studio_core.export import unity
    from studio_core.export.vfx_particles import STRETCH_VELOCITY
    from studio_core.store.bundle import engine_source

    ctx, step = await _approve_step(tmp_path)
    u = dict(unity.export(engine_source(step, [], ctx.blobs, "Spells")))
    ex = u["VFX/Prefabs/Spells_explosion.prefab"].decode()
    assert f"m_VelocityScale: {STRETCH_VELOCITY:g}" in ex and ex.count("m_VelocityScale: 0\n") == 4


class FakeZImage(FakeExec):
    def __init__(self) -> None:
        super().__init__("comfy.t2i_zimage_turbo")
        self.params: list[dict[str, Any]] = []

    async def run(self, ctx: Any, params: dict[str, Any], inputs: Any) -> StepResult:
        self.params.append(params)
        out = []
        for i in range(int(params["57:13.batch_size"])):
            img = Image.new("RGB", (params["57:13.width"], params["57:13.height"]))
            ImageDraw.Draw(img).ellipse((250, 250 + i * 10, 520, 520), fill=(80, 160, 255))
            out.append(await ctx.blobs.put_bytes(_png(img), name=f"z_{i}.png", mime="image/png"))
        return StepResult(outputs={"images": out})


def _png(img: Image.Image) -> bytes:
    import io

    buf = io.BytesIO()
    img.save(buf, "PNG", compress_level=1)
    return buf.getvalue()


@pytest.mark.parametrize("style,calls", [("", 0), ("blue arcane flames", 1)])
async def test_recipe_runs_with_fakes(tmp_path: Path, style: str, calls: int):
    z = FakeZImage()
    from studio_core.ops.executors import Sheet

    env = Env(tmp_path, [z], {"vfx-particles": (RECIPES / "vfx-particles.yaml").read_text(encoding="utf-8")})
    for cls in (*vp.VFX_PARTICLE_OPS, Sheet):
        env.registry.register(cls())
    run_id = await env.engine.start_run("vfx-particles", {"style": style, "size": 64, "review": False,
                                                          "presets": "explosion, hit_spark"})
    await asyncio.wait_for(env.engine.wait_idle(run_id), 60)
    steps = env.steps(run_id)
    assert env.status(run_id) == RunStatus.SUCCEEDED, {k: s.error for k, s in steps.items() if s.error}
    assert len(z.params) == calls
    if calls:
        assert "blue arcane flames" in z.params[0]["57:27.text"] and z.params[0]["57:13.batch_size"] == 4
    doc = json.loads(env.blobs.path(steps["approve"].outputs["vfx"][0].hash).read_bytes())
    assert doc["styled"] is bool(calls)
    meta = json.loads(env.blobs.path(steps["hit_sheet"].outputs["meta"][0].hash).read_bytes())
    assert meta["frame_w"] == 64 and not meta.get("clips")


def test_recipe_loads_and_lints():
    r = load_recipe_file(RECIPES / "vfx-particles.yaml")
    assert [s.id for s in r.steps] == ["plan", "styled", "pack", "hit_sheet", "approve"]
    _, issues = validate((RECIPES / "vfx-particles.yaml").read_text(encoding="utf-8"), None)
    assert issues == []
    for kind in ("op.vfx_style_plan", "op.vfx_particles"):
        assert CATALOG[kind]["label"]


async def test_hit_spark_flipbook_is_listed_for_hit_fx(app_ctx, tmp_path: Path) -> None:  # noqa: F811
    import time

    from studio_core.contracts import Lane, RunRow, StepRow, StepStatus
    from studio_core.ops.executors import Sheet

    app, client, _ = app_ctx
    ctx = FakeCtx(tmp_path)
    frames = vp.flipbook(dict(vp.PRESETS["hit_spark"], name="hit_spark"), vp.procedural(64), 64)
    refs = [await put_img(ctx, f, f"h{i}.png") for i, f in enumerate(frames)]
    res = await Sheet().run(ctx, {"cols": 8, "flipbook": "oneshot", "fps": 24}, {"images": refs})
    put = app.state.blobs.put_bytes
    s = await put(ctx.blobs.path(res.outputs["sheet"][0].hash).read_bytes(), name="sheet.png", mime="image/png")
    m = await put(ctx.blobs.path(res.outputs["meta"][0].hash).read_bytes(), name="sheet.json",
                  mime="application/json")
    await app.state.repo.create_run(RunRow(id="rp", recipe_id="vfx-particles", recipe_hash="h", created_at=time.time(),
                                           inputs={"asset": "spells"}))
    await app.state.repo.create_steps([StepRow(id="hs-1", run_id="rp", key="hit_sheet", kind="op.sheet",
                                               lane=Lane.CPU, status=StepStatus.SUCCEEDED,
                                               outputs={"sheet": [s], "meta": [m]})])
    lib = (await client.get("/api/hitfx/vfx", headers=AUTH)).json()["vfx"]
    assert [(x["step_id"], x["name"], x["frames"]) for x in lib] == [("hs-1", "spells_hit_spark", len(frames))]
