import asyncio
import io
import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image, ImageDraw
from test_ops_executors import FakeCtx

from studio_core.adapters.claude import ClaudeReply
from studio_core.adapters.scene_layout import ClaudeSceneLayoutExecutor
from studio_core.config import Settings
from studio_core.contracts import Lane, RunRow, RunStatus, StepError, StepRow, StepStatus
from studio_core.engine.kinds import CATALOG
from studio_core.engine.library import validate
from studio_core.engine.recipe import load_recipe_file
from studio_core.ops import playable_scene as ps
from studio_core.ops.minimap import load_source

RECIPES = Path(__file__).resolve().parents[1] / "studio_core" / "recipes"
ANGLES = [0, 45, 90, 135, 180, 225, 270, 315]


def sheet_image(frame: int = 96) -> Image.Image:
    img = Image.new("RGBA", (frame * 8, frame), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for i in range(8):
        d.rectangle((i * frame + frame // 3, frame // 6, i * frame + 2 * frame // 3, frame - 6),
                    fill=(40 + 25 * i, 90, 160, 255))
    return img


def sheet_meta(frame: int = 96) -> dict[str, Any]:
    return {"frame_w": frame, "frame_h": frame, "cols": 8, "rows": 1, "count": 8, "padding": 0, "order": ANGLES,
            "angles": ANGLES, "width": frame * 8, "height": frame, "pivot": [frame / 2, frame - 6]}


def texture(rgb: tuple[int, int, int], seed: int) -> Image.Image:
    rng = np.random.default_rng(seed)
    a = np.clip(np.asarray(rgb, np.float32) + rng.normal(0, 12, (64, 64, 3)), 0, 255).astype(np.uint8)
    return Image.fromarray(a, "RGB")


def prop() -> Image.Image:
    img = Image.new("RGBA", (80, 60), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse((10, 8, 70, 56), fill=(150, 100, 50, 255))
    return img


async def put(ctx: FakeCtx, data: Image.Image | dict | bytes, name: str):
    if isinstance(data, Image.Image):
        buf = io.BytesIO()
        data.save(buf, "PNG")
        return await ctx.blobs.put_bytes(buf.getvalue(), name=name, mime="image/png")
    if isinstance(data, dict):
        return await ctx.blobs.put_bytes(json.dumps(data).encode(), name=name, mime="application/json")
    return await ctx.blobs.put_bytes(data, name=name, mime="application/octet-stream")


async def inputs_for(ctx: FakeCtx) -> dict[str, Any]:
    return {"character": [await put(ctx, sheet_image(), "hero.png"), await put(ctx, sheet_meta(), "hero.json")],
            "tiles": [await put(ctx, texture((90, 150, 70), 1), "grass.png"),
                      await put(ctx, texture((150, 140, 120), 2), "stone_wall.png"),
                      await put(ctx, texture((190, 170, 120), 3), "dirt_path.png")],
            "props": [await put(ctx, prop(), "barrel.png")]}


async def build(ctx: FakeCtx, style: str = "topdown", planner_raw: dict | None = None, **params: Any):
    g = await ps.SceneGather(None).run(ctx, {"style": style, "project": "none"}, await inputs_for(ctx))
    assets = json.loads(ctx.blobs.path(g.outputs["assets"][0].hash).read_bytes())
    lay = (ps.normalize_layout(planner_raw, style, 24, 14, len(assets["props"])) if planner_raw is not None
           else ps.procedural_layout(style, 24, 14, len(assets["props"]), 5))
    lref = await put(ctx, lay, ps.LAYOUT_FILE)
    res = await ps.PlayableScene().run(ctx, {"asset": "Test Level", **params},
                                       {"assets": g.outputs["assets"], "layout": [lref]})
    scene = json.loads(ctx.blobs.path(res.outputs["scene"][0].hash).read_bytes())
    return g, lay, res, scene


def test_normalize_closes_edges_and_joins_islands():
    raw = {"rows": ["....#....."] * 6, "spawn": [1, 1],
           "props": [{"prop": 0, "x": 7, "y": 2}, {"prop": 5, "x": 2, "y": 2}, {"prop": 0, "x": 99, "y": 99}]}
    lay = ps.normalize_layout(raw, "topdown", 10, 6, 1)
    g = lay["grid"]
    assert all(c == "#" for c in g[0] + g[-1]) and all(r[0] == "#" and r[-1] == "#" for r in g)
    reach = ps._reach([list(r) for r in g], tuple(lay["spawn"]), "topdown")
    walk = {(x, y) for y, r in enumerate(g) for x, c in enumerate(r) if c in ".="}
    assert walk == reach, "every walkable cell is reachable from the spawn"
    assert "=" in "".join(g), "the wall line was carved through"
    assert [p["prop"] for p in lay["props"]] == [0] and g[lay["props"][0]["y"]][lay["props"][0]["x"]] == "."


def test_normalize_sideview_and_garbage():
    lay = ps.normalize_layout({"rows": ["xx", 7], "spawn": "here"}, "sideview", 12, 8, 0)
    g = lay["grid"]
    assert g[-1] == "#" * 12 and all(r[0] == "#" and r[-1] == "#" for r in g)
    x, y = lay["spawn"]
    assert g[y][x] == " " and g[y + 1][x] == "#"
    empty = ps.normalize_layout(None, "topdown", 12, 8, 0)
    assert empty["grid"][3][3] == "." and empty["props"] == []


@pytest.mark.parametrize("style", ["topdown", "sideview"])
def test_procedural_layout_is_playable_and_seeded(style: str):
    a = ps.procedural_layout(style, 32, 20, 2, seed=7)
    assert a == ps.procedural_layout(style, 32, 20, 2, seed=7)
    assert a != ps.procedural_layout(style, 32, 20, 2, seed=8)
    assert len(a["grid"]) == 20 and all(len(r) == 32 for r in a["grid"]) and a["props"]
    if style == "topdown":
        assert "#" in "".join(r[1:-1] for r in a["grid"][1:-1]), "a few wall blocks inside"
    else:
        assert all(a["grid"][p["y"] + 1][p["x"]] == "#" for p in a["props"])


def test_tile_map_data_matches_godot_encoding():
    from studio_core.export.playable_scene import tile_map_data

    # Godot 4.7 wrote this for set_cell((1, 2), 0, (1, 0)) and set_cell((-1, 3)...) — we only emit x, y >= 0.
    ids = [-1] * 12
    ids[2 * 4 + 1] = 1
    assert tile_map_data(ids, 4) == "PackedByteArray(0, 0, 1, 0, 2, 0, 0, 0, 1, 0, 0, 0, 0, 0)"


async def test_gather_from_inputs_assigns_roles(tmp_path: Path):
    ctx = FakeCtx(tmp_path)
    res = await ps.SceneGather(None).run(ctx, {"style": "topdown", "project": "none"}, await inputs_for(ctx))
    doc = json.loads(ctx.blobs.path(res.outputs["assets"][0].hash).read_bytes())
    assert doc["type"] == ps.ASSETS_TYPE and doc["character"]["name"] == "hero"
    assert {t["label"]: t["role"] for t in doc["textures"]} == {"grass": "floor", "stone_wall": "wall",
                                                               "dirt_path": "path"}
    assert len(doc["props"]) == 1 and res.outputs["preview"]
    with pytest.raises(StepError) as e:
        await ps.SceneGather(None).run(ctx, {"project": "none"}, {"tiles": doc and []})
    assert e.value.code == "op_no_input"


async def test_gather_reads_the_runs_project(tmp_path: Path):
    from studio_core.store import Database, SqliteRepo

    settings = Settings(data_dir=tmp_path / "data")
    settings.data_dir.mkdir(parents=True)
    db = Database(settings.db_path)
    await db.open()
    repo = SqliteRepo(db)
    ctx = FakeCtx(tmp_path)
    now = time.time()
    sheet, meta = await put(ctx, sheet_image(), "sprite_sheet.png"), await put(ctx, sheet_meta(), "sheet.json")
    tex = await put(ctx, texture((90, 150, 70), 4), "texture_seamless.png")
    report = await put(ctx, {"tileable": True}, "tile_check.json")
    barrel = await put(ctx, prop(), "barrel.png")
    runs = [RunRow(id="r-sprite", project_id="p1", recipe_id="sprite-8dir-3d", recipe_hash="h",
                   inputs={"prompt": "knight"}, status=RunStatus.SUCCEEDED, created_at=now - 30),
            RunRow(id="r-tex", project_id="p1", recipe_id="texture-seamless", recipe_hash="h",
                   inputs={"prompt": "grass meadow"}, status=RunStatus.SUCCEEDED, created_at=now - 20),
            RunRow(id="r-other", project_id="p2", recipe_id="texture-seamless", recipe_hash="h",
                   status=RunStatus.SUCCEEDED, created_at=now - 10),
            RunRow(id="r-self", project_id="p1", recipe_id="playable-scene", recipe_hash="h", created_at=now)]
    for r in runs:
        await repo.create_run(r)
    await repo.create_steps([
        StepRow(id="s1", run_id="r-sprite", key="sheet", kind="op.sheet", lane=Lane.CPU,
                status=StepStatus.SUCCEEDED, outputs={"sheet": [sheet], "meta": [meta]}),
        StepRow(id="s2", run_id="r-tex", key="check", kind="op.tile_check", lane=Lane.CPU,
                status=StepStatus.SUCCEEDED, outputs={"images": [tex], "report": [report]}),
        StepRow(id="s3", run_id="r-other", key="check", kind="op.tile_check", lane=Lane.CPU,
                status=StepStatus.SUCCEEDED, outputs={"images": [barrel], "report": [report]})])
    from studio_core.contracts import VersionRow

    await repo.add_version(VersionRow(id="v1", project_id="p1", asset_name="barrel", run_id="r-x", step_id="s",
                                      blob=barrel, created_at=now))
    await db.close()
    ctx.run_id = "r-self"
    res = await ps.SceneGather(settings).run(ctx, {"style": "topdown"}, {})
    doc = json.loads(ctx.blobs.path(res.outputs["assets"][0].hash).read_bytes())
    assert doc["project"] == "p1" and doc["character"]["hash"] == sheet.hash
    assert [t["hash"] for t in doc["textures"]] == [tex.hash] and doc["textures"][0]["role"] == "floor"
    assert [p["hash"] for p in doc["props"]] == [barrel.hash]
    assert not ps.SceneGather(settings).cacheable({"project": ""})
    with pytest.raises(StepError):
        await ps.SceneGather(settings).run(ctx, {"project": "nope"}, {})


@pytest.mark.parametrize("style", ["topdown", "sideview"])
async def test_build_scene_mockup_tiled_ldtk(tmp_path: Path, style: str):
    ctx = FakeCtx(tmp_path)
    _, lay, res, scene = await build(ctx, style)
    assert scene["type"] == ps.SCENE_TYPE and scene["asset"] == "TestLevel" and scene["tile"] == 32
    with Image.open(ctx.blobs.path(res.outputs["images"][0].hash)) as m:
        assert m.size == (24 * 32, 14 * 32), "mockup is exactly the grid"
    solid = set(scene["tileset"]["solid"])
    solid_layer = ps.layer_tiles(scene, "Solid")
    for i, c in enumerate("".join(lay["grid"])):
        assert (solid_layer[i] >= 0) == (c == "#")
        if c == "#":
            assert solid_layer[i] in solid
    assert not solid & {t for la in scene["layers"] if not la["collide"] for t in la["tiles"] if t >= 0}
    pl = scene["player"]
    assert pl["meta"]["frame_h"] < 96 and 0 < pl["collider"]["w"] <= 32
    tmx = load_source(ctx.blobs.path(res.outputs["tmx"][0].hash).read_bytes(), "x.tmx")
    assert tmx.source_size == (24 * 32, 14 * 32)
    assert [p.type for p in tmx.pois] == ["player"] + ["prop"] * len(scene["props"])
    ldtk_raw = json.loads(ctx.blobs.path(res.outputs["ldtk"][0].hash).read_bytes())
    assert ldtk_raw["jsonVersion"].startswith("1.5") and ldtk_raw["defs"]["tilesets"][0]["relPath"] == "TestLevel_tiles.png"
    level = ldtk_raw["levels"][0]
    col = next(li for li in level["layerInstances"] if li["__identifier"] == "Collision")
    assert col["intGridCsv"] == [1 if c == "#" else 0 for c in "".join(lay["grid"])]
    ld = load_source(json.dumps(ldtk_raw).encode(), "x.ldtk")
    assert ld.source_size == (24 * 32, 14 * 32) and len(ld.pois) == 1 + len(scene["props"])
    if style == "sideview":
        assert "top" in scene["tileset"]["sets"] and scene["physics"]["jump"] > 0


async def test_autotile_drives_the_path_edges(tmp_path: Path):
    from studio_core.ops.autotile import AUTOTILE_TYPE
    from studio_core.ops.autotile import layout as blob_layout

    ctx = FakeCtx(tmp_path)
    atlas = Image.new("RGBA", (8 * 16, 6 * 16), (120, 90, 60, 255))
    aref = await put(ctx, atlas, "autotile.png")
    doc = {"type": AUTOTILE_TYPE, "tile_size": 16, "atlas": {"hash": aref.hash}, "tiles": blob_layout()}
    inp = await inputs_for(ctx)
    inp["autotile"] = [await put(ctx, doc, "autotile.json")]
    inp["tiles"] = inp["tiles"][:1]
    g = await ps.SceneGather(None).run(ctx, {"project": "none"}, inp)
    assets = json.loads(ctx.blobs.path(g.outputs["assets"][0].hash).read_bytes())
    assert assets["autotile"]["type"] == AUTOTILE_TYPE
    lay = ps.procedural_layout("topdown", 20, 12, 1, 2)
    res = await ps.PlayableScene().run(ctx, {}, {"assets": g.outputs["assets"], "layout": [await put(ctx, lay, "l.json")]})
    scene = json.loads(ctx.blobs.path(res.outputs["scene"][0].hash).read_bytes())
    auto = set(scene["tileset"]["sets"]["auto"])
    assert set(scene["layers"][0]["tiles"]) <= auto and len(set(scene["layers"][0]["tiles"])) > 2


async def test_engine_exports(tmp_path: Path):
    from test_export_engines import _row

    from studio_core.export import godot, unity
    from studio_core.store.bundle import engine_ready, engine_source

    ctx = FakeCtx(tmp_path)
    _, _, res, scene = await build(ctx, "topdown")
    step = _row("approve", "gate.approve", {"scene": res.outputs["scene"], "images": res.outputs["images"]})
    assert engine_ready(step, ctx.blobs)
    src = engine_source(step, [], ctx.blobs, "TestLevel")
    assert src.playable and {"atlas", "sheet"} <= set(src.playable_files)
    g = dict(godot.export(src))
    tscn = g["TestLevel.tscn"].decode()
    assert tscn.count('type="TileMapLayer"') == 3 and tscn.count("collision_enabled = false") == 2
    assert 'type="CharacterBody2D"' in tscn and "motion_mode = 1" in tscn and "Camera2D" in tscn
    assert tscn.count('type="StaticBody2D"') == len(scene["props"]) == tscn.count("CollisionPolygon2D")
    assert 'path="Player/Hero.tscn"' in tscn and "Player/Hero.tres" in g and "player.gd" in g
    assert "physics_layer_0/polygon_0/points" in g["TestLevel_tileset.tres"].decode()
    assert {"Tiled/TestLevel.tmx", "Tiled/TestLevel.ldtk", "Tiled/TestLevel_tiles.png"} <= set(g)
    u = dict(unity.export(src))
    builder = next(k for k in u if k.startswith("Editor/PlayableSceneBuilder") and k.endswith(".cs"))
    uid = builder.removeprefix("Editor/PlayableSceneBuilder").removesuffix(".cs")
    assert "namespace AssetStudio" in u[builder].decode() and f"playable_{uid}.json" in u[builder].decode()
    doc = json.loads(u[f"playable_{uid}.json"])
    assert doc["player"]["prefab"] == "Player/Hero.prefab" and "Player/Hero.prefab" in u
    assert len(doc["ground"]) == len(doc["shadowLayer"]) == len(doc["solidLayer"]) == 24 * 14
    assert doc["tileCount"] == scene["tileset"]["count"]
    assert doc["player"]["walkClip"] == "" and doc["player"]["idleClip"] == "idle" and doc["player"]["bob"] > 0
    assert 'idle_clip = "idle"' in tscn and "bob = " in tscn and "tilt = " in tscn
    assert "nhún" in g["README.txt"].decode() and "layer Shadow" in u["README.txt"].decode()
    assert f"namespace AssetStudio.Playable{uid}" in u["Scripts/PlayerController.cs"].decode()
    sheet_meta_txt = u["Tiles/TestLevel_tiles.png.meta"].decode()
    assert sheet_meta_txt.count("name: tile_") == scene["tileset"]["count"]
    guids = [ln for k, b in u.items() if k.endswith(".meta") for ln in b.decode().splitlines() if ln.startswith("guid:")]
    assert len(guids) == len(set(guids))


async def test_engine_check_skips_missing_engines(tmp_path: Path):
    ctx = FakeCtx(tmp_path)
    _, _, res, _ = await build(ctx)
    settings = Settings(data_dir=tmp_path / "d", godot_exe=tmp_path / "none.exe", unity_exe=tmp_path / "none.exe")
    out = await ps.SceneEngineCheck(settings).run(ctx, {"engine": "both"}, {"scene": res.outputs["scene"]})
    rep = json.loads(ctx.blobs.path(out.outputs["report"][0].hash).read_bytes())
    assert set(rep["engines"]) == {"godot", "unity"} and all(r["skipped"] for r in rep["engines"].values())
    assert out.outputs["images"] == [] and rep["ok"] is None
    none = await ps.SceneEngineCheck(settings).run(ctx, {"engine": "none"}, {"scene": res.outputs["scene"]})
    assert none.meta["engines"] == {}


class FakeQuery:
    def __init__(self, reply: Any):
        self.reply = reply
        self.calls: list[Any] = []

    async def __call__(self, content: list[dict[str, Any]], options: Any) -> ClaudeReply:
        self.calls.append((content, options))
        return ClaudeReply(self.reply, "", {"cost_usd": 0.01, "model": options.model})


async def test_claude_layout_normalizes_and_rejects_empty(tmp_path: Path):
    ctx = FakeCtx(tmp_path)
    g = await ps.SceneGather(None).run(ctx, {"project": "none"}, await inputs_for(ctx))
    rows = ["#" * 16] + ["#" + "." * 14 + "#"] * 8 + ["#" * 16]
    q = FakeQuery({"rows": rows, "props": [{"prop": 0, "x": 5, "y": 5}], "spawn": [2, 2], "reason": "test"})
    ex = ClaudeSceneLayoutExecutor(None, query=q)
    res = await ex.run(ctx, {"cols": 16, "rows": 10, "brief": "forest camp"},
                       {"assets": g.outputs["assets"], "images": g.outputs["preview"]})
    lay = json.loads(ctx.blobs.path(res.outputs["json"][0].hash).read_bytes())
    assert lay["planner"] == "claude" and lay["spawn"] == [2, 2] and lay["props"] == [{"prop": 0, "x": 5, "y": 5}]
    assert "forest camp" in str(q.calls[0][0]) and res.meta["changed_cells"] == 0
    with pytest.raises(StepError) as e:
        await ClaudeSceneLayoutExecutor(None, query=FakeQuery({"rows": []})).run(
            ctx, {"cols": 16, "rows": 10}, {"assets": g.outputs["assets"]})
    assert e.value.retryable
    proc = await ClaudeSceneLayoutExecutor(None, query=q).run(ctx, {"planner": "procedural", "style": "sideview"},
                                                              {"assets": g.outputs["assets"]})
    assert len(q.calls) == 1 and proc.meta["planner"] == "procedural"


async def test_recipe_runs_end_to_end(tmp_path: Path):
    from test_engine_runner import Env

    env = Env(tmp_path, [], {"playable-scene": (RECIPES / "playable-scene.yaml").read_text(encoding="utf-8")})
    from studio_core.ops.playable_scene import PlayableScene, SceneEngineCheck, SceneGather

    for op in (SceneGather(env.settings), PlayableScene(), SceneEngineCheck(env.settings)):
        env.registry.register(op)
    env.registry.register(ClaudeSceneLayoutExecutor(None, query=FakeQuery({})))
    ctx = FakeCtx(tmp_path / "x")
    (tmp_path / "x").mkdir()
    ins = await inputs_for(ctx)

    async def store(ref):
        return (await env.blobs.put_bytes(ctx.blobs.path(ref.hash).read_bytes(), name=ref.name, mime=ref.mime)).hash

    run_id = await env.engine.start_run("playable-scene", {
        "character": [await store(r) for r in ins["character"]], "tiles": [await store(r) for r in ins["tiles"]],
        "props": [await store(r) for r in ins["props"]], "planner": "procedural", "engine": "none",
        "project": "none", "review": False, "cols": 20, "rows": 12})
    await asyncio.wait_for(env.engine.wait_idle(run_id), 30)
    assert env.status(run_id) == RunStatus.SUCCEEDED, {k: (s.status, s.error) for k, s in env.steps(run_id).items()}
    steps = env.steps(run_id)
    assert steps["scene"].meta["grid"] == [20, 12] and steps["approve"].outputs["tmx"]


def test_recipe_loads_and_lints():
    r = load_recipe_file(RECIPES / "playable-scene.yaml")
    assert [s.id for s in r.steps] == ["gather", "layout", "scene", "check", "approve"]
    _, issues = validate((RECIPES / "playable-scene.yaml").read_text(encoding="utf-8"), None)
    assert issues == []
    for kind in ("op.scene_gather", "claude.scene_layout", "op.playable_scene", "op.scene_engine_check"):
        assert CATALOG[kind]["label"]


async def test_open_project_stages_without_launching(tmp_path: Path):
    from test_export_engines import _row

    from studio_core.export.playable_scene import open_project
    from studio_core.store.bundle import export_engine

    ctx = FakeCtx(tmp_path)
    _, _, res, _ = await build(ctx)
    step = _row("scene", "op.playable_scene", res.outputs)
    exe = tmp_path / "Godot_v4.7-stable_win64_console.exe"
    exe.write_bytes(b"")
    uproj = tmp_path / "uproj"
    (uproj / "Assets").mkdir(parents=True)
    (uproj / "ProjectSettings").mkdir()
    settings = Settings(data_dir=tmp_path / "d", godot_exe=exe, unity_exe=exe, unity_project=uproj)
    out = export_engine(step, [], ctx.blobs, tmp_path / "exp", "Camp", "godot")
    info = open_project(settings, Path(out["dir"]), "godot", "Camp", launch=False)
    proj = Path(info["project"])
    assert 'run/main_scene="res://Camp/Camp.tscn"' in (proj / "project.godot").read_text(encoding="utf-8")
    assert (proj / "Camp" / "Camp.tscn").is_file() and not info["launched"]
    out = export_engine(step, [], ctx.blobs, tmp_path / "exp", "Camp", "unity")
    info = open_project(settings, Path(out["dir"]), "unity", "Camp", launch=False)
    assert info["scene"] == "Assets/AssetStudio/Camp/Camp.unity"
    assert next((uproj / "Assets" / "AssetStudio" / "Camp").glob("playable_*.json"))


def wide_sheet(frame: int = 96) -> Image.Image:
    img = Image.new("RGBA", (frame * 8, frame), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for i in range(8):
        d.rectangle((i * frame + 8, frame // 2, i * frame + frame - 8, frame - 8), fill=(160, 110, 50, 255))
    return img


async def test_gather_library_imported_files(tmp_path: Path):
    from studio_core.contracts import VersionRow
    from studio_core.store import Database, SqliteRepo

    settings = Settings(data_dir=tmp_path / "data")
    settings.data_dir.mkdir(parents=True)
    db = Database(settings.db_path)
    await db.open()
    repo = SqliteRepo(db)
    ctx = FakeCtx(tmp_path)
    now = time.time()
    chest, chest_meta = await put(ctx, wide_sheet(), "chest.png"), await put(ctx, sheet_meta(), "chest.json")
    hero, hero_meta = await put(ctx, sheet_image(), "hero.png"), await put(ctx, sheet_meta(), "hero.json")
    grass = await put(ctx, texture((90, 150, 70), 9), "grass.png")
    await repo.create_run(RunRow(id="r-lib", project_id="p", recipe_id="library-import", recipe_hash="h",
                                 status=RunStatus.SUCCEEDED, created_at=now - 5))
    await repo.create_run(RunRow(id="r-me", project_id="p", recipe_id="playable-scene", recipe_hash="h", created_at=now))
    await repo.create_steps([StepRow(id="k", run_id="r-lib", key="keep", kind="gate.approve", lane=Lane.NONE,
                                     status=StepStatus.SUCCEEDED,
                                     outputs={"files": [chest, chest_meta, hero, hero_meta, grass]})])
    for i, b in enumerate((chest, hero, grass)):
        await repo.add_version(VersionRow(id=f"v{i}", project_id="p", asset_name=b.name, run_id="r-lib", step_id="k",
                                          blob=b, created_at=now - i))
    await db.close()
    ctx.run_id = "r-me"
    res = await ps.SceneGather(settings).run(ctx, {}, {})
    doc = json.loads(ctx.blobs.path(res.outputs["assets"][0].hash).read_bytes())
    assert doc["character"]["hash"] == hero.hash, "the standing figure, not the chest, is the player"
    assert [p.get("sheet") for p in doc["props"]] == [chest.hash]
    assert [t["hash"] for t in doc["textures"]] == [grass.hash]


def clip_meta(names: tuple[str, ...], dirs: int = 8, frames: int = 4) -> dict[str, Any]:
    angles = [i * 360 / dirs for i in range(dirs)]
    clips = [{"name": n, "row0": k * dirs, "rows": dirs, "frames": frames} for k, n in enumerate(names)]
    n = dirs * len(names)
    return {"frame_w": 64, "frame_h": 64, "cols": frames, "rows": n, "count": frames * n, "clips": clips,
            "row_angles": angles * len(names)}


def test_anim_clips_pick_walk_and_standing_clip():
    assert ps.anim_clips(sheet_meta()) == ("", "idle")
    assert ps.anim_clips(clip_meta(("idle", "walk"))) == ("walk", "idle")
    assert ps.anim_clips(clip_meta(("run", "attack"))) == ("run", "attack")
    assert ps.anim_clips(clip_meta(("walk",), dirs=4)) == ("walk", "walk")
    assert ps.anim_clips(clip_meta(("attack",), dirs=4)) == ("", "attack")


async def test_walking_sheet_keeps_its_walk_and_idle_sheet_bobs(tmp_path: Path):
    from studio_core.export.playable_scene import walk_notes

    ctx = FakeCtx(tmp_path)
    _, _, res, scene = await build(ctx)
    pl = scene["player"]
    assert pl["walk_clip"] == "" and pl["idle_clip"] == "idle" and not pl["walk"]
    assert pl["bob"]["px"] > 0 and pl["bob"]["tilt"] > 0 and scene["notes"] and res.meta["notes"]
    assert walk_notes(scene) and "sprite-8dir-walk" in walk_notes(scene)[0]
    assert walk_notes({**scene, "player": {**pl, "walk_clip": "walk", "bob": {"px": 0.0}}}) == []
    frame = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(frame).rectangle((20, 8, 44, 60), fill=(200, 60, 60, 255))
    img = Image.new("RGBA", (64 * 4, 64 * 16), (0, 0, 0, 0))
    for r in range(16):
        for c in range(4):
            img.paste(frame, (c * 64, r * 64))
    g = await ps.SceneGather(None).run(ctx, {"project": "none"}, {
        "character": [await put(ctx, img, "knight.png"), await put(ctx, clip_meta(("idle", "walk")), "knight.json")]})
    lay = ps.procedural_layout("topdown", 16, 10, 0, 1)
    out = await ps.PlayableScene().run(ctx, {}, {"assets": g.outputs["assets"],
                                                 "layout": [await put(ctx, lay, "l.json")]})
    knight = json.loads(ctx.blobs.path(out.outputs["scene"][0].hash).read_bytes())
    assert knight["player"]["walk_clip"] == "walk" and knight["player"]["bob"]["px"] == 0 and knight["notes"] == []


async def test_topdown_walls_have_a_face_a_top_and_a_shadow(tmp_path: Path):
    ctx = FakeCtx(tmp_path)
    _, lay, res, scene = await build(ctx, planner_raw={"rows": ["." * 24] * 14, "spawn": [5, 4]})
    grid, sets = lay["grid"], scene["tileset"]["sets"]
    cols, rows = scene["cols"], scene["rows"]
    solid, shadow = ps.layer_tiles(scene, "Solid"), ps.layer_tiles(scene, "Shadow")
    face, top = set(sets["wall_face"]), set(sets["wall"])
    assert face <= set(scene["tileset"]["solid"]) and not set(sets["shadow"]) & set(scene["tileset"]["solid"])
    for y in range(rows):
        for x in range(cols):
            i, c = y * cols + x, grid[y][x]
            if c == "#":
                assert solid[i] in (face if y + 1 < rows and grid[y + 1][x] != "#" else top), (x, y)
            assert (shadow[i] >= 0) == (c != "#" and y > 0 and grid[y - 1][x] == "#"), (x, y)
    assert all(solid[(rows - 1) * cols + x] in top for x in range(cols)), "the bottom edge has no floor below"
    assert all(solid[x] in face for x in range(1, cols - 1)), "the top edge faces the room"
    atlas = Image.open(ctx.blobs.path(scene["tileset"]["hash"])).convert("RGBA")
    t = scene["tile"]

    def tile_px(tid: int) -> np.ndarray:
        c, r = tid % ps.ATLAS_COLS, tid // ps.ATLAS_COLS
        return np.asarray(atlas.crop((c * t, r * t, (c + 1) * t, (r + 1) * t)))

    assert tile_px(sets["wall_face"][0])[..., :3].mean() < tile_px(sets["wall"][0])[..., :3].mean()
    sh = tile_px(sets["shadow"][0])[..., 3]
    assert sh[0].min() > 80 and sh[-1].max() == 0, "the shadow fades downwards"


TMX_RESERVED = {"id", "name", "type", "class", "x", "y", "width", "height", "rotation", "gid", "visible", "template",
                "image", "source"}


@pytest.mark.parametrize("style", ["topdown", "sideview"])
async def test_tiled_and_ldtk_files_follow_the_formats(tmp_path: Path, style: str):
    import xml.etree.ElementTree as ET

    ctx = FakeCtx(tmp_path)
    _, _, res, scene = await build(ctx, style)
    cols, rows, t, count = scene["cols"], scene["rows"], scene["tile"], scene["tileset"]["count"]
    root = ET.fromstring(ctx.blobs.path(res.outputs["tmx"][0].hash).read_bytes())
    ts = root.find("tileset")
    img = ts.find("image")
    assert int(img.get("width")) % t == 0 and int(img.get("height")) % t == 0
    assert int(ts.get("tilecount")) == count <= int(img.get("width")) // t * int(img.get("height")) // t
    layer_ids = [int(el.get("id")) for el in root if el.tag in ("layer", "imagelayer", "objectgroup")]
    assert len(layer_ids) == len(set(layer_ids)) and int(root.get("nextlayerid")) > max(layer_ids)
    assert [el.get("name") for el in root.findall("layer")] == [la["name"] for la in scene["layers"]]
    for el in root.findall("layer"):
        gids = [int(v) for v in el.find("data").text.replace("\n", "").split(",")]
        assert len(gids) == cols * rows and all(0 <= g <= count for g in gids)
    objs = root.find("objectgroup").findall("object")
    assert int(root.get("nextobjectid")) > max(int(o.get("id")) for o in objs)
    for o in objs:
        for prop in o.iter("property"):
            assert prop.get("name") not in TMX_RESERVED, "pytmx refuses object properties named like attributes"
    doc = json.loads(ctx.blobs.path(res.outputs["ldtk"][0].hash).read_bytes())
    defs = doc["defs"]
    uids = [d["uid"] for k in ("layers", "entities", "tilesets") for d in defs[k]] + \
        [f["uid"] for e in defs["entities"] for f in e["fieldDefs"]] + [lv["uid"] for lv in doc["levels"]]
    assert len(uids) == len(set(uids)) and max(uids) < doc["nextUid"]
    level = doc["levels"][0]
    assert [li["layerDefUid"] for li in level["layerInstances"]] == [d["uid"] for d in defs["layers"]]
    tcols = defs["tilesets"][0]["__cWid"]
    for li in level["layerInstances"]:
        assert (li["__cWid"], li["__cHei"], li["__gridSize"]) == (cols, rows, t)
        for gt in li["gridTiles"]:
            assert 0 <= gt["t"] < count
            assert gt["src"] == [gt["t"] % tcols * t, gt["t"] // tcols * t]
            assert gt["d"] == [gt["px"][1] // t * cols + gt["px"][0] // t]
        if li["__type"] == "IntGrid":
            assert len(li["intGridCsv"]) == cols * rows
    ents = {e["uid"]: e for e in defs["entities"]}
    for e in next(li for li in level["layerInstances"] if li["__type"] == "Entities")["entityInstances"]:
        d = ents[e["defUid"]]
        assert (e["width"], e["height"]) == (d["width"], d["height"]) or (d["resizableX"] and d["resizableY"])
        assert 0 <= e["px"][0] <= level["pxWid"] and 0 <= e["px"][1] <= level["pxHei"]


async def test_tmx_loads_in_pytmx(tmp_path: Path):
    pytmx = pytest.importorskip("pytmx")
    ctx = FakeCtx(tmp_path)
    _, _, res, scene = await build(ctx)
    path = tmp_path / "level.tmx"
    path.write_bytes(ctx.blobs.path(res.outputs["tmx"][0].hash).read_bytes())
    m = pytmx.TiledMap(str(path))
    assert [la.name for la in m.layers][:3] == ["Ground", "Shadow", "Solid"]
    assert len(m.objects_by_id) == 1 + len(scene["props"])
