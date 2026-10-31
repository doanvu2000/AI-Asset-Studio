from __future__ import annotations

import asyncio
import io
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import blake3
import numpy as np
import pytest
from PIL import Image

from studio_core.adapters import wfc
from studio_core.adapters.claude import ClaudeReply
from studio_core.adapters.room_rules import ClaudeRoomRulesExecutor
from studio_core.contracts import Lane, RunStatus, StepError, StepRow, StepStatus
from studio_core.engine.kinds import CATALOG
from studio_core.engine.library import validate
from studio_core.engine.recipe import load_recipe_file
from studio_core.export import room_kit as ex
from studio_core.ops import playable_scene as ps
from studio_core.ops import room_kit as rk
from test_ops_executors import FakeBlobs, FakeCtx

RECIPES = Path(__file__).resolve().parents[1] / "studio_core" / "recipes"
STUDIO = Path(__file__).resolve().parents[1] / "studio_core"
TILE = 16
PARAMS = {"rooms": 20, "seed": 3, "asset": "dg", "map_rooms": 10}
OPP = rk.OPPOSITE


def _put(root: Path, data: bytes) -> str:
    h = blake3.blake3(data).hexdigest()
    (root / h).write_bytes(data)
    return h


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def scene_parts(style: str) -> tuple[dict[str, Any], bytes, bytes]:
    """A playable_scene.json shaped doc with a flat-coloured atlas and one prop, the way op.playable_scene writes it;
    the hashes are filled in by the caller."""
    spec = ([("floor", 16, False, (90, 140, 70)), ("path", 16, False, (180, 160, 110)), ("wall", 16, True, (90, 85, 90)),
             ("wall_face", 16, True, (60, 55, 60)), ("shadow", 1, False, (20, 20, 20))] if style == "topdown" else
            [("wall", 16, True, (110, 80, 60)), ("top", 16, True, (90, 150, 60)), ("shadow", 2, False, (20, 20, 20))])
    sets, solid, colors = {}, [], []
    for name, n, is_solid, rgb in spec:
        ids = list(range(len(colors), len(colors) + n))
        sets[name] = ids
        solid += ids if is_solid else []
        colors += [rgb] * n
    rows = -(-len(colors) // ps.ATLAS_COLS)
    atlas = Image.new("RGBA", (ps.ATLAS_COLS * TILE, rows * TILE), (0, 0, 0, 0))
    for i, rgb in enumerate(colors):
        atlas.paste(Image.new("RGBA", (TILE, TILE), (*rgb, 255)), ((i % ps.ATLAS_COLS) * TILE, (i // ps.ATLAS_COLS) * TILE))
    prop = Image.new("RGBA", (16, 24), (200, 120, 40, 255))
    doc = {"type": ps.SCENE_TYPE, "asset": "Scene", "style": style, "tile": TILE, "cols": 32, "rows": 20,
           "tileset": {"columns": ps.ATLAS_COLS, "count": len(colors), "solid": solid, "sets": sets},
           "prop_kinds": [{"name": "Barrel", "w": 16, "h": 24,
                           "collider": [[-6.0, -2.0], [6.0, -2.0], [6.0, 0.0], [-6.0, 0.0]]}]}
    return doc, _png(atlas), _png(prop)


def make_scene(root: Path, style: str = "topdown") -> dict[str, Any]:
    doc, atlas, prop = scene_parts(style)
    doc["tileset"]["hash"], doc["prop_kinds"][0]["hash"] = _put(root, atlas), _put(root, prop)
    return doc


async def amake_scene(blobs: Any, style: str = "topdown") -> dict[str, Any]:
    doc, atlas, prop = scene_parts(style)
    doc["tileset"]["hash"] = (await blobs.put_bytes(atlas, name="tiles.png", mime="image/png")).hash
    doc["prop_kinds"][0]["hash"] = (await blobs.put_bytes(prop, name="Barrel.png", mime="image/png")).hash
    return doc


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = {}
    for style in rk.STYLES:
        root = tmp_path_factory.mktemp(style)
        scene = make_scene(root, style)
        kit, images, tmx, ldtk = rk.build_kit(FakeBlobs(root), scene, None, None, PARAMS)
        out[style] = (root, scene, kit, images, tmx, ldtk)
    return out


@pytest.fixture(params=rk.STYLES)
def style(request) -> str:
    return request.param


def test_wfc_is_stable_honours_pins_and_changes_with_the_seed():
    samples = [wfc.sample_array(rk.SAMPLES["topdown"]["pillars"], ".#")]
    model = wfc.Wfc(samples, 14, 10, 3)
    pins = {(x, y): 0 for x in range(5, 9) for y in range(10)}
    a, b = model.solve(7, pins), model.solve(7, pins)
    assert a.shape == (10, 14) and set(np.unique(a)) <= {0, 1} and np.array_equal(a, b)
    assert all(a[y, x] == 0 for (x, y) in pins)
    assert any(not np.array_equal(a, model.solve(s, pins)) for s in range(8, 14))


def test_wfc_adjacency_is_symmetric_and_overlap_based():
    pats, weights = wfc.extract_patterns([wfc.sample_array(["..#", "..#", "###"], ".#")], 2)
    adj = wfc.adjacency(pats)
    assert adj.shape == (4, len(pats), len(pats)) and (weights > 0).all()
    for d in range(4):
        assert np.array_equal(adj[d], adj[(d + 2) % 4].T)
    assert adj[0].any() and adj[1].any()


def test_wfc_reports_unsatisfiable_pins_and_bad_sizes_clearly():
    solid = [wfc.sample_array(["###", "###", "###"], ".#")]
    with pytest.raises(wfc.WfcError, match="pinned"):
        wfc.Wfc(solid, 6, 6, 3).solve(1, {(2, 2): 0})
    with pytest.raises(wfc.WfcError, match="smaller"):
        wfc.Wfc(solid, 2, 6, 3)
    with pytest.raises(wfc.WfcError, match="outside"):
        wfc.Wfc(solid, 6, 6, 3).solve(1, {(9, 9): 1})
    with pytest.raises(wfc.WfcError, match="unknown symbols"):
        wfc.sample_array(["x"], ".#")
    assert issubclass(wfc.WfcContradiction, wfc.WfcError)


def test_samples_are_rectangular_and_every_shape_exists_for_both_styles():
    for st in rk.STYLES:
        assert set(rk.SAMPLES[st]) == set(rk.SHAPES)
        for shape, rows in rk.SAMPLES[st].items():
            assert len({len(r) for r in rows}) == 1 and len(rows) >= rk.WFC_PATTERN, (st, shape)


def test_door_cells_line_up_across_every_pair_of_sides():
    for cols, rows in ((20, 14), (12, 10), (48, 32)):
        d = {s: rk.door_cells("topdown", cols, rows, s) for s in rk.SIDES}
        assert [x for x, _ in d["N"]] == [x for x, _ in d["S"]] and {y for _, y in d["N"]} == {0}
        assert [y for _, y in d["W"]] == [y for _, y in d["E"]] and {x for x, _ in d["E"]} == {cols - 1}
        assert d["N"][0][0] == cols // 2 - 1 and d["W"][0][1] == rows // 2 - 1
        sv = {s: rk.door_cells("sideview", cols, rows, s) for s in ("E", "W")}
        assert [y for _, y in sv["W"]] == [y for _, y in sv["E"]] == [rows - 4, rows - 3, rows - 2]


def test_default_rules_and_normalize_clamp_whatever_claude_says():
    d = rk.normalize_rules(None, "topdown")
    assert d["source"] == "default" and set(d["kinds"]) == set(rk.KINDS)
    raw = {"rooms": [{"kind": "BOSS", "min_doors": 9, "max_doors": -3, "shape": "zzz", "props": 99, "share": "x",
                      "arena": 50},
                     {"kind": "corridor", "min_doors": 1, "max_doors": 4, "straight": True},
                     {"kind": "dragon"}, "junk", {"kind": "treasure", "shape": "ring", "share": 5}],
           "reason": "r" * 900}
    n = rk.normalize_rules(raw, "topdown")
    boss, cor, tre = n["kinds"]["boss"], n["kinds"]["corridor"], n["kinds"]["treasure"]
    assert n["source"] == "claude" and len(n["reason"]) == 600
    assert (boss["min_doors"], boss["max_doors"], boss["shape"], boss["props"], boss["arena"]) == (4, 4, "arena", 6, 6)
    assert cor["min_doors"] == cor["max_doors"] == 2 and tre["share"] == 1.0
    side = rk.normalize_rules(raw, "sideview")
    assert side["kinds"]["boss"]["max_doors"] == 2
    assert rk.normalize_rules({"rooms": "x"}, "topdown")["source"] == "default"
    assert rk.door_sets("sideview", side["kinds"]["corridor"]) == [("E", "W")]
    for st, kinds in (("topdown", d["kinds"]), ("sideview", d["kinds"]), ("topdown", n["kinds"]),
                      ("sideview", side["kinds"])):
        assert all(rk.door_sets(st, kinds[k]) for k in rk.KINDS), st
    assert rk.normalize_rules(n, "topdown") == n and rk.normalize_rules(d, "topdown") == d


def test_kind_counts_always_give_a_start_a_boss_and_a_hub():
    rules = rk.normalize_rules(None, "topdown")["kinds"]
    for n in (5, 6, 7, 20, 33, 64):
        c = rk.kind_counts(n, rules)
        assert sum(c.values()) == n and c["start"] >= 1 and c["boss"] >= 1 and c["combat"] >= 1, (n, c)
    plan = rk.plan_rooms(20, rules, "topdown", 4)
    assert plan == rk.plan_rooms(20, rules, "topdown", 4) and len(plan) == 20
    hub = [doors for kind, doors in plan if kind == "combat"][0]
    assert hub == ("N", "E", "S", "W")


def test_twenty_rooms_every_one_walkable_door_to_door(built, style):
    _, _, kit, _, _, _ = built[style]
    assert len(kit["rooms"]) == 20 and kit["qa"]["unjoined"] == []
    for room in kit["rooms"]:
        grid = [list(r) for r in room["grid"]]
        assert rk.doors_joined(style, grid, tuple(room["doors"]), kit["jump"]), room["name"]
        assert room["source"] in ("wfc", "grammar") and 1 <= room["attempts"] <= rk.ROOM_TRIES


def test_topdown_rooms_connect_doors_by_an_independent_flood_fill(built):
    _, _, kit, _, _, _ = built["topdown"]
    for room in kit["rooms"]:
        grid = room["grid"]
        cells = {tuple(c) for s in room["doors"] for c in room["door_cells"][s]}
        start = next(iter(sorted(cells)))
        seen, todo = {start}, [start]
        while todo:
            x, y = todo.pop()
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if 0 <= nx < kit["cols"] and 0 <= ny < kit["rows"] and (nx, ny) not in seen and grid[ny][nx] in ".=":
                    seen.add((nx, ny))
                    todo.append((nx, ny))
        assert cells <= seen, room["name"]


def test_doors_open_exactly_where_the_kit_says_and_the_border_is_wall_elsewhere(built, style):
    _, _, kit, _, _, _ = built[style]
    cols, rows = kit["cols"], kit["rows"]
    for room in kit["rooms"]:
        grid, doors = room["grid"], set()
        for side in room["doors"]:
            cells = [tuple(c) for c in room["door_cells"][side]]
            assert cells == rk.door_cells(style, cols, rows, side)
            doors.update(cells)
        for y in range(rows):
            for x in range(cols):
                if x in (0, cols - 1) or y in (0, rows - 1):
                    assert (grid[y][x] != "#") == ((x, y) in doors), (room["name"], x, y)
        assert room["door_cells"].keys() == set(room["doors"])


def test_kind_door_rules_hold_in_the_built_rooms(built, style):
    _, _, kit, _, _, _ = built[style]
    sides = set(rk.sides_for(style))
    for room in kit["rooms"]:
        rule = kit["rules"]["kinds"][room["kind"]]
        assert rule["min_doors"] <= len(room["doors"]) <= rule["max_doors"] or style == "sideview"
        assert set(room["doors"]) <= sides and list(room["doors"]) == sorted(room["doors"], key=rk.SIDES.index)
        if room["kind"] == "corridor":
            assert len(room["doors"]) == 2 and OPP[room["doors"][0]] == room["doors"][1]
        if room["kind"] in ("treasure", "boss"):
            assert len(room["doors"]) == 1


def test_seed_makes_the_same_kit_and_another_seed_a_different_one(built, tmp_path):
    root, scene, kit, _, _, _ = built["topdown"]
    again = rk.build_kit(FakeBlobs(root), scene, None, None, PARAMS)[0]
    assert [r["grid"] for r in again["rooms"]] == [r["grid"] for r in kit["rooms"]]
    assert again["map"] == kit["map"] and json.dumps(again, sort_keys=True) == json.dumps(kit, sort_keys=True)
    other = rk.build_kit(FakeBlobs(root), scene, None, None, {**PARAMS, "seed": 4})[0]
    assert [r["grid"] for r in other["rooms"]] != [r["grid"] for r in kit["rooms"]]


def test_sideview_jump_height_decides_whether_a_pillar_blocks_the_way():
    def pillar(height: int) -> list[list[str]]:
        g = rk.make_grid("sideview", 20, 14, ("W", "E"))
        for y in range(13 - height, 13):
            g[y][10] = "#"
        return g

    both = ("W", "E")
    assert rk.doors_joined("sideview", pillar(0), both, 3)
    assert rk.doors_joined("sideview", pillar(2), both, 3)
    assert not rk.doors_joined("sideview", pillar(4), both, 3)
    assert rk.doors_joined("sideview", pillar(4), both, 4)
    assert not rk.doors_joined("sideview", pillar(6), both, 5)


def test_sideview_gap_reach_and_drops():
    g = rk.make_grid("sideview", 20, 14, ("W", "E"))
    for y in range(8, 13):
        g[y][10] = "#"
    g[7][9] = g[7][10] = g[7][11] = "#"
    assert not rk.doors_joined("sideview", g, ("W", "E"), 3)
    ledge = rk.make_grid("sideview", 20, 14, ("W", "E"))
    ledge[10][5] = ledge[10][6] = ledge[10][7] = "#"
    start = (1, 12)
    reach = rk.reach_sideview(ledge, start, 3)
    assert (6, 9) in reach and (8, 12) in reach and (19, 12) in reach and (1, 12) in reach


def test_topdown_wall_across_the_room_leaves_the_doors_unjoined():
    g = rk.make_grid("topdown", 20, 14, ("W", "E"))
    assert rk.doors_joined("topdown", g, ("W", "E"), 3)
    for y in range(1, 13):
        g[y][10] = "#"
    assert not rk.doors_joined("topdown", g, ("W", "E"), 3)


def _links(rooms, plan):
    """Effective doors (not sealed) per cell."""
    sealed = rk.sealed_doors(rooms, plan)
    return {c: [s for s in rooms[i]["doors"] if s not in sealed.get(c, [])] for c, i in plan.items()}


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_generator_joins_ten_rooms_without_overlap_or_dead_doors(built, style, seed):
    _, _, kit, _, _, _ = built[style]
    rooms = kit["rooms"]
    lay = rk.generate_layout(rooms, 10, seed)
    plan = lay["cells"]
    assert len(plan) == 10 and lay["stopped"] == ""
    assert sum(rooms[i]["kind"] == "start" for i in plan.values()) == 1 and rooms[plan[(0, 0)]]["kind"] == "start"
    rects = [(c[0] * kit["cols"], c[1] * kit["rows"]) for c in plan]
    assert len(set(rects)) == len(rects)
    eff = _links(rooms, plan)
    for c, sides in eff.items():
        for s in sides:
            n = (c[0] + rk.STEP[s][0], c[1] + rk.STEP[s][1])
            assert n in plan and OPP[s] in eff[n], (c, s)
    seen, todo = {(0, 0)}, [(0, 0)]
    while todo:
        c = todo.pop()
        for s in eff[c]:
            n = (c[0] + rk.STEP[s][0], c[1] + rk.STEP[s][1])
            if n not in seen:
                seen.add(n)
                todo.append(n)
    assert seen == set(plan)
    again = rk.generate_layout(rooms, 10, seed)
    assert again["cells"] == plan


def test_generator_seed_changes_the_map_and_the_boss_takes_the_last_slot(built):
    _, _, kit, _, _, _ = built["topdown"]
    rooms = kit["rooms"]
    maps = {tuple(sorted(rk.generate_layout(rooms, 10, s)["cells"].items())) for s in range(6)}
    assert len(maps) > 1
    placed_boss = 0
    for s in range(12):
        plan = rk.generate_layout(rooms, 10, s)["cells"]
        kinds = [rooms[i]["kind"] for i in plan.values()]
        assert kinds.count("boss") <= 1
        placed_boss += "boss" in kinds
    assert placed_boss >= 6


def test_generator_stops_with_a_reason_instead_of_looping_or_failing():
    dead = [{"kind": "start", "doors": ["N"], "weight": 1.0}, {"kind": "treasure", "doors": ["S"], "weight": 1.0}]
    lay = rk.generate_layout(dead, 5, 1)
    assert len(lay["cells"]) == 1 and "gave up after 200 attempts with 1 of 5 rooms" in lay["stopped"]
    two = rk.generate_layout(dead, 2, 1)
    assert len(two["cells"]) == 2 and two["stopped"] == "" and rk.sealed_doors(dead, two["cells"]) == {}
    shut = rk.generate_layout([{"kind": "start", "doors": [], "weight": 1.0}], 3, 0)
    assert len(shut["cells"]) == 1 and "no open door left after 1 of 3 rooms" in shut["stopped"]
    assert "no start room" in rk.generate_layout([dead[1]], 3, 0)["stopped"]
    no_fit = [{"kind": "start", "doors": ["N", "E"], "weight": 1.0}, {"kind": "combat", "doors": ["W"], "weight": 1.0}]
    lay = rk.generate_layout(no_fit, 6, 0, max_attempts=3)
    assert len(lay["cells"]) == 2 and lay["stopped"] and lay["tries"] == 6 * 3 + 1
    assert rk.generate_layout(dead, 1, 0) == {"cells": {(0, 0): 0}, "stopped": "", "tries": 0}


def test_sealed_doors_are_the_ones_without_a_matching_door_across():
    rooms = [{"kind": "start", "doors": ["E", "S"], "weight": 1.0},
             {"kind": "combat", "doors": ["W", "N"], "weight": 1.0}]
    plan = {(0, 0): 0, (1, 0): 1, (0, 1): 1}
    assert rk.sealed_doors(rooms, plan) == {(1, 0): ["N"], (0, 1): ["W"]}


def test_params_are_validated_with_clear_errors(built):
    root, scene, *_ = built["topdown"]
    blobs = FakeBlobs(root)
    for bad, text in (({"rooms": 0}, "rooms"), ({"rooms": 500}, "rooms"), ({"cols": 4}, "cols"),
                      ({"jump": 9}, "jump"), ({"style": "isometric"}, "style"), ({"style": "sideview"}, "match")):
        with pytest.raises(StepError, match=text):
            rk.build_kit(blobs, scene, None, None, {**PARAMS, **bad})
    broken = {**scene, "tileset": {**scene["tileset"], "sets": {"floor": scene["tileset"]["sets"]["floor"]}}}
    with pytest.raises(StepError, match="lacks"):
        rk.build_kit(blobs, broken, None, None, PARAMS)
    small = rk.build_kit(blobs, scene, None, None, {"rooms": 5, "cols": 13, "rows": 11, "seed": 1})[0]
    assert (small["cols"], small["rows"], len(small["rooms"])) == (12, 10, 5) and small["qa"]["unjoined"] == []


def test_claude_rules_reach_the_kit_and_default_rules_still_work(built):
    root, scene, *_ = built["topdown"]
    rules = rk.normalize_rules({"rooms": [{"kind": "combat", "min_doors": 3, "max_doors": 4, "shape": "blocks",
                                           "props": 0, "share": 0.5}], "reason": "x"}, "topdown")
    kit = rk.build_kit(FakeBlobs(root), scene, None, {**rules}, PARAMS)[0]
    combat = [r for r in kit["rooms"] if r["kind"] == "combat"]
    assert kit["rules"]["source"] == "claude" and all(len(r["doors"]) >= 3 and not r["props"] for r in combat)
    assert len(combat) > 7


def test_props_stay_on_floor_clear_of_the_door_lanes(built, style):
    _, _, kit, _, _, _ = built[style]
    t = kit["tile"]
    seen = 0
    for room in kit["rooms"]:
        for p in room["props"]:
            x, y = p["cell"]
            seen += 1
            if style == "topdown":
                assert room["grid"][y][x] == "."
            else:
                assert room["grid"][y][x] == " " and room["grid"][y + 1][x] == "#"
                assert rk.LANE + 1 <= x <= kit["cols"] - 2 - rk.LANE
            assert p["pos"] == [(x + 0.5) * t, (y + (1 if style == "sideview" else 0.85)) * t]
    assert seen > 0


def test_plugs_close_every_door_with_solid_tiles(built, style):
    _, scene, kit, _, _, _ = built[style]
    solid = set(scene["tileset"]["solid"])
    for room in kit["rooms"]:
        assert set(room["plugs"]) == set(room["doors"])
        for side, cells in room["plugs"].items():
            assert cells and all(tid in solid for _, _, tid in cells)
            door = {tuple(c) for c in room["door_cells"][side]}
            assert door <= {(x, y) for x, y, _ in cells}


def test_op_outputs_every_file_and_the_documents_parse(built, style):
    _, _, kit, images, tmx, ldtk = built[style]
    assert len(tmx) == len(ldtk) == len(kit["rooms"])
    assert images["map"].width > 0 and images["sheet"].width > 0
    for room, (tname, ttext), (lname, ltext) in zip(kit["rooms"], tmx, ldtk):
        assert tname == f"{room['name']}.tmx" and lname == f"{room['name']}.ldtk"
        root = ET.fromstring(ttext.split("\n", 1)[1])
        doors = [o for o in root.iter("object") if o.get("type") == "door"]
        assert sorted(o.get("name") for o in doors) == sorted(room["doors"])
        ids = [o.get("id") for o in root.iter("object")]
        assert len(ids) == len(set(ids)) and int(root.get("nextobjectid")) > max(map(int, ids))
        assert root.get("width") == str(kit["cols"]) and root.find("properties").find("property[@name='kind']") is not None
        doc = json.loads(ltext)
        assert any(e["identifier"] == "Door" for e in doc["defs"]["entities"])
        ents = next(la for la in doc["levels"][0]["layerInstances"] if la["__identifier"] == "Entities")
        assert sorted(e["__identifier"] for e in ents["entityInstances"] if e["__identifier"] == "Door") == \
            ["Door"] * len(room["doors"])


def test_map_preview_matches_the_layout(built, style):
    _, _, kit, images, _, _ = built[style]
    m = kit["map"]
    assert m["count"] == len(m["cells"]) == 10 and m["seed"] == PARAMS["seed"]
    plan = {tuple(c["cell"]): c["room"] for c in m["cells"]}
    assert plan == rk.generate_layout(kit["rooms"], 10, PARAMS["seed"])["cells"]
    assert 0 < images["map"].width <= 3201 and 0 < images["map"].height <= 3201
    assert np.asarray(images["map"])[..., 3].min() == 255


@pytest.mark.asyncio
async def test_executor_writes_blobs_and_meta(tmp_path: Path):
    scene = make_scene(tmp_path)
    ctx = FakeCtx(tmp_path)
    ref = await ctx.blobs.put_bytes(json.dumps(scene).encode(), name=ps.SCENE_FILE, mime="application/json")
    ex_ = rk.RoomKit()
    assert ex_.kind == "op.room_kit" and ex_.lane == Lane.CPU
    res = await ex_.run(ctx, {**PARAMS, "rooms": 6}, {"scene": [ref]})
    kit = json.loads(ctx.blobs.path(res.outputs["rooms"][0].hash).read_bytes())
    assert kit["type"] == rk.KIT_TYPE and res.outputs["rooms"][0].name == rk.KIT_FILE
    assert len(res.outputs["tmx"]) == len(res.outputs["ldtk"]) == 6 and len(res.outputs["images"]) == 2
    assert res.meta["rooms"] == 6 and res.meta["rules"] == "default"
    with pytest.raises(StepError, match="scene"):
        await ex_.run(ctx, PARAMS, {})
    rules = rk.normalize_rules({"rooms": [{"kind": "boss", "min_doors": 1, "max_doors": 1, "shape": "arena"}]},
                               "topdown")
    rref = await ctx.blobs.put_bytes(json.dumps(rules).encode(), name=rk.RULES_FILE, mime="application/json")
    res = await ex_.run(ctx, {**PARAMS, "rooms": 6}, {"scene": [ref], "rules": [rref]})
    assert res.meta["rules"] == "claude"
    bogus = await ctx.blobs.put_bytes(b'{"type": "other"}', name="x.json", mime="application/json")
    assert (await ex_.run(ctx, {**PARAMS, "rooms": 6}, {"scene": [ref], "rules": [bogus]})).meta["rules"] == "default"


class FakeQuery:
    def __init__(self, reply: dict[str, Any]):
        self.reply, self.calls = reply, []

    async def __call__(self, content: list[dict[str, Any]], options: Any) -> ClaudeReply:
        self.calls.append((content, options))
        return ClaudeReply(self.reply, "", {"cost_usd": 0.02, "model": options.model})


class FailingQuery(FakeQuery):
    async def __call__(self, content: list[dict[str, Any]], options: Any) -> ClaudeReply:
        self.calls.append((content, options))
        raise StepError("claude_error", "overloaded", retryable=True)


GOOD = {"rooms": [{"kind": "boss", "min_doors": 1, "max_doors": 1, "shape": "arena", "props": 1, "arena": 5,
                   "share": 0.2}, {"kind": "combat", "min_doors": 2, "max_doors": 4, "shape": "blocks"}],
        "reason": "boss arena"}


@pytest.mark.asyncio
async def test_claude_step_is_skipped_when_off_and_validates_the_reply(tmp_path: Path):
    ctx = FakeCtx(tmp_path)
    ref = await ctx.blobs.put_bytes(json.dumps(make_scene(tmp_path, "sideview")).encode(), name=ps.SCENE_FILE,
                                    mime="application/json")
    q = FakeQuery(GOOD)
    step = ClaudeRoomRulesExecutor(None, q)
    assert step.kind == "claude.room_rules" and step.lane == Lane.CLOUD
    assert (await step.run(ctx, {"claude": False}, {"scene": [ref]})).outputs == {"json": []} and q.calls == []
    res = await step.run(ctx, {"rooms": 12, "brief": "old crypt"}, {"scene": [ref]})
    prompt = json.dumps(q.calls[0][0])
    assert "old crypt" in prompt and "E, W" in prompt and "Barrel" in prompt and "side-view" in prompt
    doc = json.loads(ctx.blobs.path(res.outputs["json"][0].hash).read_bytes())
    assert doc["type"] == rk.RULES_TYPE and doc["source"] == "claude" and doc["kinds"]["boss"]["arena"] == 5
    with pytest.raises(StepError) as e:
        await ClaudeRoomRulesExecutor(None, FakeQuery({"rooms": [], "reason": ""})).run(ctx, {}, {"scene": [ref]})
    assert e.value.code == "claude_bad_output" and e.value.retryable
    with pytest.raises(StepError):
        await ClaudeRoomRulesExecutor(None, FailingQuery({})).run(ctx, {}, {"scene": [ref]})
    with pytest.raises(StepError):
        await step.run(ctx, {}, {})


def test_recipe_loads_and_lints():
    r = load_recipe_file(RECIPES / "room-kit.yaml")
    assert [s.id for s in r.steps] == ["rules", "kit", "approve"]
    rules, kit = r.steps[0], r.steps[1]
    assert rules.allow_fail and rules.kind == "claude.room_rules" and kit.kind == "op.room_kit" and not kit.allow_fail
    assert rules.params["model"] == "opus" and str(kit.bindings["rules"]) == "rules.json"
    assert r.inputs["scene"]["type"] == "file" and r.inputs["rooms"]["min"] == 5 and r.inputs["rooms"]["max"] == 64
    assert r.inputs["style"]["options"] == ["auto", *rk.STYLES]
    _, issues = validate((RECIPES / "room-kit.yaml").read_text(encoding="utf-8"), None)
    assert issues == []


@pytest.mark.skipif("op.room_kit" not in CATALOG or "claude.room_rules" not in CATALOG,
                    reason="catalog entries are added by the integrator")
def test_catalog_declares_the_ports_and_params_the_recipe_uses():
    spec, rules = CATALOG["op.room_kit"], CATALOG["claude.room_rules"]
    assert {"scene", "autotile", "rules"} <= set(spec["in"]) and {"images", "preview", "rooms", "tmx", "ldtk"} <= set(
        spec["out"])
    assert {"style", "rooms", "cols", "rows", "jump", "map_rooms", "seed", "asset"} <= set(spec["params"])
    assert rules["in"].keys() >= {"scene"} and "json" in rules["out"]
    r = load_recipe_file(RECIPES / "room-kit.yaml")
    assert set(r.steps[1].params) <= set(spec["params"]) and set(r.steps[0].params) - {"_auto_retry"} <= set(
        rules["params"])
    _, issues = validate((RECIPES / "room-kit.yaml").read_text(encoding="utf-8"), None)
    assert issues == []


def _src(built, style: str, asset: str = "dg"):
    root, _, kit, *_ = built[style]
    files = {"atlas": FakeBlobs(root).path(kit["tileset"]["hash"])}
    files.update({f"prop:{k['name']}": FakeBlobs(root).path(k["hash"]) for k in kit["prop_kinds"]})
    return ex.source_from(kit, files, asset, "u1")


def test_room_kit_set_reads_the_gate_passthrough(built):
    root, _, kit, *_ = built["topdown"]
    blobs = FakeBlobs(root)
    ref = asyncio.run(blobs.put_bytes(json.dumps(kit).encode(), name=rk.KIT_FILE, mime="application/json"))
    row = StepRow(id="id-approve", run_id="r", key="approve", kind="gate.approve", lane=Lane.CPU,
                  status=StepStatus.SUCCEEDED, deps=[], inputs={}, outputs={"rooms": [ref]})
    doc, files = ex.room_kit_set(row, blobs)
    assert doc is not None and doc["type"] == rk.KIT_TYPE and set(files) == {"atlas", "prop:Barrel"}
    wrong = asyncio.run(blobs.put_bytes(b'{"type": "nope"}', name=rk.KIT_FILE, mime="application/json"))
    other = StepRow(id="id-x", run_id="r", key="x", kind="gate.approve", lane=Lane.CPU, status=StepStatus.SUCCEEDED,
                    deps=[], inputs={}, outputs={"rooms": [wrong]})
    assert ex.room_kit_set(other, blobs) == (None, {})
    empty = StepRow(id="id-y", run_id="r", key="y", kind="gate.approve", lane=Lane.CPU, status=StepStatus.SUCCEEDED,
                    deps=[], inputs={}, outputs={})
    assert ex.room_kit_set(empty, blobs) == (None, {})


def _ext_ids(text: str) -> list[str]:
    return re.findall(r'^\[ext_resource [^\]]*id="([^"]+)"\]$', text, re.M)


def test_godot_export_has_every_scene_and_valid_tscn(built, style):
    src = _src(built, style)
    files, notes = ex.godot_files(src)
    paths = [p for p, _ in files]
    assert len(paths) == len(set(paths)) and notes
    kit = src.room_kit
    for room in kit["rooms"]:
        assert f"Rooms/{room['name']}.tscn" in paths
    for rel in ("room_generator.gd", "dg_tileset.tres", "dg_rooms.json", "dg_demo.tscn", "Tiles/dg_tiles.png",
                "Props/Barrel.png"):
        assert rel in paths, rel
    by = dict(files)
    manifest = json.loads(by["dg_rooms.json"])
    assert [r["scene"] for r in manifest["rooms"]] == [f"Rooms/{r['name']}.tscn" for r in kit["rooms"]]
    assert all(set(r) == {"name", "kind", "doors", "weight", "scene"} for r in manifest["rooms"])
    for room in kit["rooms"]:
        text = by[f"Rooms/{room['name']}.tscn"].decode()
        assert text.startswith("[gd_scene load_steps=") and "format=3]" in text.splitlines()[0]
        ids = _ext_ids(text)
        assert len(ids) == len(set(ids)) and int(re.search(r"load_steps=(\d+)", text)[1]) == len(ids) + 1
        used = set(re.findall(r'ExtResource\("([^"]+)"\)', text))
        assert used == set(ids), (room["name"], used, ids)
        assert 'path="../dg_tileset.tres"' in text and ('path="../Props/' in text) == bool(room["props"])
        heads = re.findall(r"^\[node ([^\]]*)\]$", text, re.M)
        keys = [(re.search(r'name="([^"]+)"', h)[1], (re.search(r'parent="([^"]*)"', h) or [0, ""])[1]) for h in heads]
        assert len(keys) == len(set(keys)) and keys[0][1] == ""
        assert f'metadata/kind = "{room["kind"]}"' in text and "PlayerSpawn" in text
        for side in room["doors"]:
            blk = text.split(f'[node name="Plug_{side}"')[1].split("\n[node")[0]
            assert "enabled = false" in blk and "tile_map_data = PackedByteArray(0, 0" in blk
            assert f'[node name="{side}" type="Marker2D" parent="Doors"]' in text
        for side in set(rk.SIDES) - set(room["doors"]):
            assert f'Plug_{side}"' not in text
        assert text.count("type=\"StaticBody2D\"") == len(room["props"])


def test_tile_map_data_of_a_plug_holds_only_its_cells(built):
    src = _src(built, "topdown")
    room = src.room_kit["rooms"][0]
    side = room["doors"][0]
    text = dict(ex.godot_files(src)[0])[f"Rooms/{room['name']}.tscn"].decode()
    blk = text.split(f'[node name="Plug_{side}"')[1].split("\n[node")[0]
    nums = [int(v) for v in re.search(r"PackedByteArray\(([^)]*)\)", blk)[1].split(",")]
    assert nums[:2] == [0, 0] and (len(nums) - 2) == 12 * len(room["plugs"][side])


def test_generator_gd_uses_tabs_one_class_and_every_runtime_function():
    gd = ex.GENERATOR_GD
    lines = gd.split("\n")
    assert not any(ln.startswith(" ") for ln in lines) and gd.count("class_name RoomGenerator") == 1
    assert lines[0] == "class_name RoomGenerator" and lines[1] == "extends Node2D"
    for fn in ("generate", "clear", "_plan", "_open_doors", "_pick", "_spawn_rooms", "_fit_camera", "_resolve"):
        assert re.search(rf"^func {fn}\(", gd, re.M), fn
    assert "max_attempts_per_room" in gd and "no open door left" in gd and "gave up after" in gd
    assert "Plug_" in gd and "enabled = true" in gd and "while placed.size() < want" in gd
    assert all(ln.rstrip() == ln for ln in lines)
    assert gd.count("(") == gd.count(")") and gd.count("[") == gd.count("]") and gd.count("{") == gd.count("}")
    for ln in lines:
        stripped = ln.strip()
        if re.match(r"^(if|elif|else|for|while|func)\b", stripped) and not stripped.endswith(","):
            assert stripped.endswith(":") or stripped.endswith("-> int:"), stripped
    assert "_boss_slots" in gd and "var last := placed.size() == want - 1" in gd


def test_godot_generator_logic_matches_the_python_port_step_by_step():
    gd = ex.GENERATOR_GD
    for needle in ('r["kind"] == "start" or not (need in r["doors"])', "if not last and others + fresh == 0:",
                   'if r["kind"] == "boss":', "pool = bosses", "placed.size() == want - 1",
                   'for d in r["doors"]:', "if not placed.has(target + DIRS[d]):", "if not linked:"):
        assert needle in gd, needle
    py = open(STUDIO / "ops" / "room_kit.py", encoding="utf-8").read()
    for needle in ('r["kind"] == "start" or need not in r["doors"]', "if not last and others + fresh == 0:",
                   'if r["kind"] == "boss":', "pool = bosses if last and bosses else pool", "len(plan) == count - 1"):
        assert needle in py, needle


def _balanced(code: str) -> bool:
    code = re.sub(r"//[^\n]*", "", code)
    code = re.sub(r'"(?:\\.|[^"\\])*"', '""', code)
    code = re.sub(r"'(?:\\.|[^'\\])*'", "''", code)
    return all(code.count(a) == code.count(b) for a, b in ("{}", "()", "[]"))


def test_unity_export_files_guids_and_scripts(built, style):
    src = _src(built, style)
    files, notes = ex.unity_files(src)
    paths = [p for p, _ in files]
    assert len(paths) == len(set(paths)) and notes
    by = dict(files)
    uid = ex._uid(src)
    for rel in ("Scripts/RoomData.cs", "Scripts/RoomGenerator.cs", f"Editor/RoomKitBuilder{uid}.cs", f"roomkit_{uid}.json",
                "Tiles/dg_tiles.png", "Props/Barrel.png"):
        assert rel in paths and (rel.endswith(".png") or f"{rel}.meta" in paths), rel
    for rel in paths:
        if rel.endswith(".cs"):
            assert f"{rel}.meta" in paths
    guids = [m[1] for p in paths if p.endswith(".meta") for m in [re.search(rb"guid: ([0-9a-f]{32})", by[p])] if m]
    guids = [g.decode() for g in guids]
    assert len(guids) == len(set(guids)) and len(guids) == len([p for p in paths if p.endswith(".meta")])
    assert f"guid: {ex.DATA_GUID}".encode() in by["Scripts/RoomData.cs.meta"]
    assert f"guid: {ex.GENERATOR_GUID}".encode() in by["Scripts/RoomGenerator.cs.meta"]
    for rel in ("Scripts/RoomData.cs", "Scripts/RoomGenerator.cs", f"Editor/RoomKitBuilder{uid}.cs"):
        code = by[rel].decode()
        assert "namespace AssetStudio" in code and _balanced(code), rel
    builder = by[f"Editor/RoomKitBuilder{uid}.cs"].decode()
    assert "__ID__" not in builder and "__ASSET__" not in builder and f"RoomKitBuilder{uid}" in builder
    assert f'"roomkit_{uid}.json"' in builder and "dg_RoomKit.unity" in builder and "Room Kit dg" in builder
    doc = json.loads(by[f"roomkit_{uid}.json"])
    assert doc["asset"] == "dg" and len(doc["rooms"]) == 20 and doc["tileCount"] == src.room_kit["tileset"]["count"]
    for r, room in zip(doc["rooms"], src.room_kit["rooms"]):
        assert r["doors"] == room["doors"] and [p["side"] for p in r["plugs"]] == room["doors"]
        assert all(len(p["cells"]) % 3 == 0 and p["cells"] for p in r["plugs"])
        assert len(r["solidLayer"]) == doc["cols"] * doc["rows"] and len(r["props"]) == len(room["props"])


def test_unity_fixed_guids_are_unique_in_the_whole_package():
    text = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in STUDIO.rglob("*.py"))
    for g in (ex.DATA_GUID, ex.GENERATOR_GUID):
        assert re.fullmatch(r"[0-9a-f]{32}", g) and text.count(g) == 1, g
    assert ex.DATA_GUID != ex.GENERATOR_GUID


def test_unity_scripts_follow_the_generator_contract():
    gen, data = ex.UNITY_GENERATOR, ex.UNITY_DATA
    for needle in ("public RoomData[] rooms;", "public string Generate(int count, int newSeed)", "public void Clear()",
                   "gave up after", "no open door left after", "if (!linked) room.Seal(side);",
                   "if (r.kind == \"start\" || !r.HasDoor(need)) continue;", "if (!last && others + fresh == 0) continue;",
                   "for (int tries = 1; plan.Count < want; tries++)"):
        assert needle in gen, needle
    assert "public void Seal(string side)" in data and "public bool HasDoor(string side)" in data
    assert "UnityEngine.Object.DestroyImmediate(root)" in ex.UNITY_BUILDER and "BossSlots" in gen


def test_tiled_and_ldtk_copies_ship_with_their_atlas(built, style):
    src = _src(built, style)
    files = ex.extra_files(src)
    paths = [p for p, _ in files]
    assert len(paths) == len(set(paths))
    for room in src.room_kit["rooms"]:
        assert f"Tiled/{room['name']}.tmx" in paths and f"Tiled/{room['name']}.ldtk" in paths
    assert "Tiled/dg_tiles.png" in paths and "Tiled/Props/Barrel.png" in paths
    tmx = dict(files)[f"Tiled/{src.room_kit['rooms'][0]['name']}.tmx"].decode()
    assert 'source="dg_tiles.png"' in tmx and 'source="Props/Barrel.png"' in tmx


def test_export_uses_the_export_name_not_the_kit_name(built):
    src = _src(built, "topdown", asset="Crypt")
    paths = [p for p, _ in ex.godot_files(src)[0]] + [p for p, _ in ex.extra_files(src)]
    assert "Crypt_tileset.tres" in paths and "Tiled/Crypt_tiles.png" in paths and not any("dg_" in p for p in paths)
    text = dict(ex.godot_files(src)[0])[f"Rooms/{src.room_kit['rooms'][0]['name']}.tscn"].decode()
    assert "../Crypt_tileset.tres" in text


def _env(tmp_path: Path, query: FakeQuery):
    from test_engine_runner import Env

    tmp_path.mkdir(parents=True, exist_ok=True)
    env = Env(tmp_path, [], {"room-kit": (RECIPES / "room-kit.yaml").read_text(encoding="utf-8")})
    env.registry.register(ClaudeRoomRulesExecutor(env.settings, query))
    env.registry.register(rk.RoomKit())
    return env


async def _run(env: Any, **extra: Any) -> tuple[str, dict[str, Any]]:
    scene = await amake_scene(env.blobs)
    h = (await env.blobs.put_bytes(json.dumps(scene).encode(), name=ps.SCENE_FILE, mime="application/json")).hash
    run_id = await env.engine.start_run("room-kit", {"scene": h, "review": False, "rooms": 6, **extra})
    await asyncio.wait_for(env.engine.wait_idle(run_id), 180)
    return run_id, env.steps(run_id)


@pytest.mark.asyncio
async def test_recipe_runs_with_claude_and_survives_its_failure(tmp_path: Path):
    env = _env(tmp_path / "a", FakeQuery(GOOD))
    run_id, steps = await _run(env)
    assert env.status(run_id) == RunStatus.SUCCEEDED, {k: s.error for k, s in steps.items() if s.error}
    assert steps["kit"].meta["rules"] == "claude" and steps["approve"].outputs["rooms"] and steps["approve"].outputs["tmx"]

    env = _env(tmp_path / "b", FailingQuery({}))
    run_id, steps = await _run(env)
    assert env.status(run_id) == RunStatus.SUCCEEDED, {k: s.error for k, s in steps.items() if s.error}
    assert steps["rules"].status == StepStatus.SKIPPED and steps["kit"].meta["rules"] == "default"

    q = FakeQuery(GOOD)
    env = _env(tmp_path / "c", q)
    run_id, steps = await _run(env, claude=False)
    assert q.calls == [] and steps["kit"].meta["rules"] == "default"
