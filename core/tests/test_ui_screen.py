from __future__ import annotations

import asyncio
import io
import json
import re
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from studio_core.adapters.claude import ClaudeReply
from studio_core.adapters.ui_layout import ClaudeUiLayoutExecutor, build_prompt, schema
from studio_core.contracts import RunStatus
from studio_core.engine.library import validate
from studio_core.engine.recipe import load_recipe_file
from studio_core.ops import ui_screen as us
from studio_core.ops.ui import SLICES_TYPE, make_states, nine_slice_borders
from studio_core.store.bundle import engine_ready, export_engine
from test_engine_runner import Env
from test_ui_kit import button

RECIPES = Path(__file__).resolve().parents[1] / "studio_core" / "recipes"


@pytest.mark.parametrize("screen", us.SCREENS)
def test_templates_fit_every_aspect(screen: str) -> None:
    lay = us.normalize_layout(us.template(screen, "Ember Knight"), screen)
    res = us.build(lay, us.default_kit(), {}, None, None, "#f0a020")
    assert set(res["report"]) == {"16x9", "20x9", "4x3"}
    for rep in res["report"].values():
        assert rep["overflow"] == [] and rep["small_touch"] == []
    assert res["text_issues"] == []
    assert [im.size for _, im in res["shots"]] == [(1080, 1920), (1080, 2400), (1536, 2048)]
    # Safe area of the 20:9 phone: the top notch strip stays background.
    top = res["shots"][1][1].crop((0, 0, 1080, 90))
    assert all(n["name"] not in ("", None) for n in lay["nodes"])
    assert top.size == (1080, 90)


def test_canvas_scaler_math_matches_unity() -> None:
    s = us.canvas_scale((1080, 2400), (1080, 1920))
    assert abs(1080 / s - 966.0) < 1 and abs(2400 / s - 2146.6) < 1
    assert us.canvas_scale((1080, 1920), (1080, 1920)) == 1


def test_normalize_clamps_renames_and_expands_lists() -> None:
    raw = {"nodes": [
        {"name": "Win dow", "parent": "", "type": "panel", "rect": [0.8, -1, 0.5, 2]},
        {"name": "Win dow", "parent": "nope", "type": "weird", "rect": [0, 0, 1, 1], "text": "Hi"},
        {"name": "Grid", "parent": "Win dow", "type": "list", "rect": [0, 0, 1, 1], "count": 5, "columns": 2,
         "item": "slot", "item_texts": ["1", "2"], "item_icons": ["coin"]},
        {"name": "Btn", "parent": "Grid", "type": "button", "rect": [0, 0, 1, 1], "text": "x" * 200,
         "font_size": 999, "color": "red"}]}
    lay = us.normalize_layout(raw, "shop")
    names = [n["name"] for n in lay["nodes"]]
    assert names[:2] == ["Win_dow", "Win_dow_2"]
    win = lay["nodes"][0]
    x, y, w, h = win["rect"]
    assert 0 <= x and x + w <= 1 and 0 <= y and y + h <= 1
    assert lay["nodes"][1]["parent"] == "" and lay["nodes"][1]["type"] == "label"
    items = [n for n in lay["nodes"] if n["parent"] == "Grid"]
    assert [n["name"] for n in items if n["type"] == "slot"] == [f"Grid_{i:02d}" for i in range(1, 6)]
    assert {"Grid_01_icon", "Grid_01_text", "Grid_02_text"} <= set(names)
    btn = next(n for n in lay["nodes"] if n["name"] == "Btn")
    assert len(btn["text"]) == 60 and btn["font_size"] == 180 and btn["color"] == "#ffffff"
    assert us.normalize_layout(lay, "shop") == lay
    odd = us.normalize_layout({"nodes": [
        {"name": "Icon", "parent": "", "type": "button", "rect": [0, 0, 1, 1], "icon": "⚔", "text": 'Say "Hi"'},
        {"name": "Text", "parent": "?", "type": "label", "rect": [0, 0, 1, 1], "text": "a"}]}, "shop")
    assert not {"icon", "text", "fill"} & {n["name"].lower() for n in odd["nodes"]}


def test_long_text_shrinks_to_fit() -> None:
    raw = {"nodes": [{"name": "B", "parent": "", "type": "button", "rect": [0.3, 0.4, 0.4, 0.06],
                      "text": "Collect Daily Reward Now", "font_size": 90}]}
    lay = us.normalize_layout(raw, "home")
    res = us.build(lay, us.default_kit(), {}, None, None, "#f0a020")
    b = lay["nodes"][0]
    assert b["font_size"] < 90 and b["font_size_max"] == 90 and res["text_issues"] == []


def _kit_doc(put: Any) -> dict[str, Any]:
    els = []
    for name, img in (("button_01", button(160, 56)), ("panel_01", button(200, 200, gem=False)),
                      ("bar_01", button(200, 30, radius=6, gem=False))):
        border, _ = nine_slice_borders(img)
        els.append({"name": name, "border": list(border), "hash": put(img), "states": []})
        if name == "button_01":
            for st, simg in make_states(img).items():
                els.append({"name": f"{name}_{st}", "state": st, "state_of": name, "border": list(border),
                            "hash": put(simg)})
    return {"type": SLICES_TYPE, "elements": els}


def test_kit_from_slices_and_tab_falls_back_to_button() -> None:
    store: dict[str, Image.Image] = {}

    def put(img: Image.Image) -> str:
        store[str(len(store))] = img
        return str(len(store) - 1)

    kit = us.kit_from_slices(_kit_doc(put), store.get)
    assert set(kit) == {"button", "panel", "bar"}
    assert set(kit["button"][0].states) == {"hover", "pressed", "disabled"}
    assert us.pick_skin(kit, "tab").name == "button_01"
    assert us.pick_skin(kit, "slot").name == "panel_01"
    assert us.bar_fill(kit) is None
    with pytest.raises(Exception):
        us.kit_from_slices({"type": "x"}, store.get)


def test_icon_match_and_placeholder() -> None:
    pack = {"gold_coin": Image.new("RGBA", (8, 8)), "settings_gear": Image.new("RGBA", (8, 8))}
    assert us.match_icon("coin", pack) == "gold_coin"
    assert us.match_icon("settings", pack) == "settings_gear"
    assert us.match_icon("trophy", pack) is None
    im = us.placeholder_icon("star", 64)
    a = im.getchannel("A")
    assert a.getpixel((0, 0)) == 0 and a.getpixel((32, 32)) == 255


def test_prompt_and_schema() -> None:
    p = build_prompt("shop", "portrait", "candy game", {"type": SLICES_TYPE, "elements": [
        {"name": "button_01"}, {"name": "button_01_hover", "state": "hover"}, {"name": "slot_01"}]})
    assert "button: 1" in p and "slot: 1" in p and "1080x1920" in p and '"Coins"' in p
    assert set(schema()["properties"]["nodes"]["items"]["properties"]["type"]["enum"]) == set(us.NODE_TYPES)


@pytest.mark.parametrize("recipe", ["ui-screen", "ui-screen-prompt"])
def test_recipes_load_and_lint(recipe: str) -> None:
    load_recipe_file(RECIPES / f"{recipe}.yaml")
    _, issues = validate((RECIPES / f"{recipe}.yaml").read_text(encoding="utf-8"), None)
    assert issues == []


class FakeQuery:
    def __init__(self, reply: dict[str, Any]):
        self.reply = reply
        self.calls: list[Any] = []

    async def __call__(self, content: list[dict[str, Any]], options: Any) -> ClaudeReply:
        self.calls.append((content, options))
        return ClaudeReply(self.reply, "", {"cost_usd": 0.02, "model": options.model})


async def _png(env: Env, img: Image.Image, name: str) -> str:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return (await env.blobs.put_bytes(buf.getvalue(), name=name, mime="image/png")).hash


@pytest.mark.asyncio
@pytest.mark.parametrize("planner", ["claude", "template"])
async def test_recipe_runs_and_exports(tmp_path: Path, planner: str) -> None:
    reply = us.template("pause")
    reply["nodes"].append({"name": "Sound", "parent": "Window", "type": "bar", "rect": [0.1, 0.9, 0.8, 0.05],
                           "value": 0.5})
    q = FakeQuery({**reply, "reason": "centred pause window"})
    text = (RECIPES / "ui-screen.yaml").read_text(encoding="utf-8")
    env = Env(tmp_path, [], {"ui-screen": text})
    env.registry.register(ClaudeUiLayoutExecutor(env.settings, q))
    for cls in us.UI_SCREEN_OPS:
        env.registry.register(cls())
    hashes: list[str] = []
    doc: dict[str, Any] = {"type": SLICES_TYPE, "elements": []}
    for name, img in (("button_01", button(160, 56)), ("panel_01", button(200, 200, gem=False))):
        border, _ = nine_slice_borders(img)
        h = await _png(env, img, f"{name}.png")
        hashes.append(h)
        doc["elements"].append({"name": name, "border": list(border), "hash": h, "states": []})
        if name == "button_01":
            for st, simg in make_states(img).items():
                doc["elements"].append({"name": f"{name}_{st}", "state": st, "state_of": name,
                                        "border": list(border), "hash": await _png(env, simg, f"{name}_{st}.png")})
    kit = await env.blobs.put_bytes(json.dumps(doc).encode(), name="ui_slices.json", mime="application/json")
    run_id = await env.engine.start_run("ui-screen", {"screen": "pause", "kit": kit.hash, "planner": planner,
                                                      "title": "Paused", "review": False})
    await asyncio.wait_for(env.engine.wait_idle(run_id), 60)
    steps = env.steps(run_id)
    assert env.status(run_id) == RunStatus.SUCCEEDED, {k: s.error for k, s in steps.items() if s.error}
    assert bool(q.calls) == (planner == "claude")
    screen = json.loads(env.blobs.path(steps["screen"].outputs["screen"][0].hash).read_bytes())
    assert screen["ok"] and screen["screen"] == "pause"
    assert {n["name"] for n in screen["nodes"]} >= {"Window", "Title", "Buttons_01"}
    step = steps["approve"]
    assert engine_ready(step, env.blobs)
    for engine in ("unity", "godot"):
        out = export_engine(step, list(steps.values()), env.blobs, tmp_path / "exp", "PauseMenu", engine)
        root = Path(out["dir"])
        rels = {Path(f).relative_to(root).as_posix() for f in out["files"]}
        if engine == "unity":
            _check_unity(root, rels)
        else:
            _check_godot(root, rels)


def _check_unity(root: Path, rels: set[str]) -> None:
    assert {"PauseMenu.prefab", "Scripts/AssetStudioSafeArea.cs", "UIToolkit/PauseMenu.uxml",
            "UI/button_01.png", "UI/button_01_hover.png", "UI/panel_01.png"} <= rels
    pf = (root / "PauseMenu.prefab").read_text(encoding="utf-8")
    local = set(re.findall(r"^--- !u!\d+ &(-?\d+)", pf, re.M))
    refs = set(re.findall(r"\{fileID: (-?\d+)\}", pf)) - {"0"}
    assert refs <= local, refs - local
    guids = {m.group(1) for m in re.finditer(r"guid: ([0-9a-f]{32})", pf)}
    metas = {re.search(r"guid: ([0-9a-f]{32})", (root / r).read_text(encoding="utf-8")).group(1)
             for r in rels if r.endswith(".meta")}
    ugui = {"fe87c0e1cc204ed48ad3b37840f39efc", "5f7201a12d95ffc409449d95f23cf332", "4e29b1a8efbd4b44bb3f3716e73f07ff",
            "0cd44c1031e13a943bb63640046fad76", "dc42784cf147c0c48a680349fa168899", "e19747de3f5aca642ab2be37e372fb86",
            "86710e43de46f6f4bac7c8e50813a599", "0000000000000000e000000000000000"}
    assert guids - ugui <= metas, guids - ugui - metas
    assert "m_Transition: 2" in pf and "m_Type: 1" in pf and "m_MatchWidthOrHeight: 0.5" in pf
    uss = (root / "UIToolkit/PauseMenu.uss").read_text(encoding="utf-8")
    for url in re.findall(r'url\("\.\./(UI/[^"]+)"\)', uss):
        assert url in rels


def _check_godot(root: Path, rels: set[str]) -> None:
    assert {"PauseMenu.tscn", "PauseMenu_theme.tres", "safe_area.gd", "UI/button_01.png"} <= rels
    for f in ("PauseMenu.tscn", "PauseMenu_theme.tres"):
        text = (root / f).read_text(encoding="utf-8")
        for p in re.findall(r'path="([^"]+)"', text):
            assert p in rels, p
        ids = set(re.findall(r'id="([^"]+)"', text))
        for used in re.findall(r'(?:ExtResource|SubResource)\("([^"]+)"\)', text):
            assert used in ids, used
    scene = (root / "PauseMenu.tscn").read_text(encoding="utf-8")
    theme = (root / "PauseMenu_theme.tres").read_text(encoding="utf-8")
    for var in re.findall(r'theme_type_variation = &"([^"]+)"', scene):
        assert f"{var}/base_type" in theme
    assert 'button_01_btn/styles/hover = SubResource("StyleBoxTexture_button_01_hover")' in theme
