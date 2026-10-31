from __future__ import annotations

import asyncio
import io
import json
import re
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from studio_core.adapters.card_list import (SCHEMA, ClaudeCardArtExecutor, ClaudeCardListExecutor, build_art_prompt,
                                            build_prompt, to_art_map, to_card_list)
from studio_core.adapters.claude import ClaudeReply
from studio_core.contracts import Lane, RunStatus, StepResult
from studio_core.engine.kinds import CATALOG
from studio_core.engine.library import validate
from studio_core.engine.recipe import load_recipe_file
from studio_core.ops import card_set as cs
from studio_core.ops.rarity import parse_colors
from studio_core.store.bundle import engine_ready, export_engine

RECIPES = Path(__file__).resolve().parents[1] / "studio_core" / "recipes"

CSV = """tên,mô tả,chi phí,công,máu,độ hiếm,loại,art
Rồng Lửa Cổ Đại,"Khi ra sân: gây 3 sát thương cho mọi kẻ địch.",8,7,7,Huyền thoại,Sinh vật,ancient fire dragon
Thầy Phù Thuỷ,Rút một thẻ.,3,2,4,Sử thi,Sinh vật,
Mưa Băng,Đóng băng tất cả quái của đối thủ.,4,,,Hiếm,Phép,
Kiếm Gỗ Sồi,+2 công cho một sinh vật.,1,,,Thường,Trang bị,
Bẫy Gai Độc,"Bí mật: khi kẻ địch tấn công, tiêu diệt nó.",2,,,Không phổ biến,Bẫy,
Nữ Hoàng Bóng Đêm Ở Vương Quốc Phương Bắc Xa Xôi,"Khiêu khích. Lá chắn thần thánh. Khi chết: triệu hồi hai Hồn Ma 1/1 có Khiêu khích, rồi rút một thẻ phép ngẫu nhiên.",9,8,8,Huyền thoại,Anh hùng,
"""


def test_parse_csv_vietnamese_headers_and_aliases() -> None:
    cards = cs.parse_cards(CSV.encode("utf-8-sig"), "set.csv")
    assert [c["id"] for c in cards][:3] == ["rong_lua_co_dai", "thay_phu_thuy", "mua_bang"]
    assert [c["rarity"] for c in cards] == ["legendary", "epic", "rare", "common", "uncommon", "legendary"]
    assert [c["type"] for c in cards] == ["creature", "creature", "spell", "item", "trap", "hero"]
    assert cards[0]["attack"] == 7 and cards[2]["attack"] is None and cards[2]["cost"] == 4
    assert cards[0]["art"] == "ancient fire dragon" and cards[0]["type_label"] == "Sinh vật"


def test_parse_json_and_rarity_edge_cases() -> None:
    doc = {"cards": [{"Name": "A", "Rarity": "khá hiếm", "Type": "Spell"}, {"name": "A", "rarity": "3"},
                     {"title": "B", "tier": "mythic", "atk": "x", "hp": 2.0}]}
    cards = cs.parse_cards(json.dumps(doc).encode(), "c.json")
    assert [c["id"] for c in cards] == ["a", "a_2", "b"]
    assert [c["rarity_index"] for c in cards] == [1, 2, 4]
    assert cards[2]["attack"] is None and cards[2]["health"] == 2
    with pytest.raises(Exception):
        cs.parse_cards(b"[]", "c.json")


def _plan() -> dict[str, Any]:
    return {"type": cs.PLAN_TYPE, "cards": cs.parse_cards(CSV.encode(), "set.csv")}


def test_build_set_fits_text_and_keeps_card_ratio() -> None:
    res = cs.build_set(_plan(), [], None, 378, parse_colors("auto"), (201, 162, 74))
    w, h = res["fronts"][0].size
    assert (w, h) == (378, 528) and abs(h / w - 88 / 63) < 0.004
    assert res["issues"] == []
    for e in res["entries"]:
        for role, t in e["texts"].items():
            assert t["fits"], (e["id"], role)
            x, y, bw, bh = t["rect"]
            ix, iy, iw, ih = t["ink"]
            assert ix >= x - 0.002 and iy >= y - 0.002 and ix + iw <= x + bw + 0.002 and iy + ih <= y + bh + 0.002
    long_name = next(e for e in res["entries"] if e["type"] == "hero")
    assert len(long_name["texts"]["name"]["lines"]) == 2
    assert "Ữ" in long_name["texts"]["name"]["text"].upper()
    # Art window is see-through on the frame, opaque on the baked front.
    e = res["entries"][0]
    ax, ay, aw, ah = e["art_rect"]
    cx, cy = int((ax + aw / 2) * w), int((ay + ah / 2) * h)
    assert res["frames"][e["frame"]].getpixel((cx, cy))[3] == 0
    assert res["fronts"][0].getpixel((cx, cy))[3] == 255
    assert len(res["frames"]) == 6 and res["back"].size == (w, h)


def test_overlong_rules_text_is_cut_and_reported() -> None:
    plan = _plan()
    plan["cards"][1]["description"] = " ".join(["Gây sát thương liên hoàn cho mọi kẻ địch."] * 30)
    res = cs.build_set(plan, [], None, 378, parse_colors("auto"), (201, 162, 74))
    issue = [i for i in res["issues"] if i["kind"] == "text_overflow"]
    assert issue and issue[0]["card"] == plan["cards"][1]["id"] and issue[0]["role"] == "desc"
    assert res["entries"][1]["texts"]["desc"]["text"].endswith("…")


def test_claude_card_list_prompt_and_normalize() -> None:
    p = build_prompt("thần thoại Việt", 6, "vi", "Sinh vật, Phép", "", True)
    assert "exactly 6 cards" in p and "Vietnamese (vi)" in p and "attached image" in p
    assert SCHEMA["properties"]["cards"]["items"]["properties"]["rarity"]["enum"][-1] == "legendary"
    doc = to_card_list({"set_name": "X", "cards": [
        {"name": "Sơn Tinh", "description": "Khiêu khích.", "cost": 5, "attack": 4, "health": 6, "rarity": "epic",
         "type": "Sinh vật", "art": "mountain god"},
        {"name": "Bão", "description": "Gây 2.", "cost": 2, "attack": -1, "health": -1, "rarity": "common",
         "type": "Phép", "art": "storm"}]}, 6, "vi")
    assert doc["type"] == cs.LIST_TYPE and doc["cards"][1]["attack"] is None and doc["cards"][0]["rarity"] == "Sử thi"
    cards = cs.parse_cards(json.dumps(doc, ensure_ascii=False).encode(), "card_list.json")
    assert cards[0]["id"] == "son_tinh" and cards[1]["type"] == "spell" and cards[0]["rarity"] == "epic"


def test_claude_art_prompt_and_map() -> None:
    cards = cs.parse_cards(CSV.encode(), "set.csv")
    missing = [c for c in cards if not c["art"]]
    p = build_art_prompt(cards, missing)
    assert "rong_lua_co_dai |" not in p and "thay_phu_thuy | Thầy Phù Thuỷ | Sinh vật | Rút một thẻ." in p
    assert cards[-1]["id"] in p and "Rồng Lửa Cổ Đại" in p
    got = to_art_map({"cards": [{"id": "thay_phu_thuy", "art": "  an old  wizard "}, {"id": "rong_lua_co_dai",
                                "art": "x"}, {"id": "ghost", "art": "y"}, {"id": "mua_bang", "art": ""},
                                {"id": "thay_phu_thuy", "art": "dup"}]}, missing)
    assert got == {"thay_phu_thuy": "an old wizard"}
    for bad in ({"cards": []}, {"cards": [{"id": "ghost", "art": "y"}]}, None):
        with pytest.raises(Exception):
            to_art_map(bad, missing)


def test_recipes_and_kinds() -> None:
    for rid in ("card-set", "card-set-prompt"):
        load_recipe_file(RECIPES / f"{rid}.yaml")
        _, issues = validate((RECIPES / f"{rid}.yaml").read_text(encoding="utf-8"), None)
        assert issues == []
    for kind in ("claude.card_list", "claude.card_art", "op.card_plan", "op.card_set"):
        assert CATALOG[kind]["label"]


class FakeComfy:
    lane = Lane.GPU
    version = "1"

    def __init__(self, kind: str, prompt_key: str):
        self.kind = kind
        self.key = prompt_key
        self.prompts: list[str] = []

    def cacheable(self, params: dict[str, Any]) -> bool:
        return False

    def cache_material(self, params: dict[str, Any]) -> str:
        return ""

    async def run(self, ctx: Any, params: dict[str, Any], inputs: Any) -> StepResult:
        self.prompts.append(params[self.key])
        size = (int(params.get("57:13.width") or 0), int(params.get("57:13.height") or 0))
        if inputs.get("image"):
            with Image.open(ctx.blobs.path(inputs["image"][0].hash)) as im:
                size = im.size
        img = Image.new("RGB", size, (40 + len(self.prompts) * 20, 90, 160))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return StepResult(outputs={"images": [await ctx.blobs.put_bytes(buf.getvalue(), name="gen.png",
                                                                         mime="image/png")]})


class FakeQuery:
    def __init__(self, reply: dict[str, Any]):
        self.reply = reply
        self.calls: list[Any] = []

    async def __call__(self, content: list[dict[str, Any]], options: Any) -> ClaudeReply:
        self.calls.append((content, options))
        return ClaudeReply(self.reply, "", {"cost_usd": 0.03, "model": options.model})


def _art_reply() -> dict[str, Any]:
    # Skips the last card and adds an unknown id: the skipped card keeps the name/rules fallback.
    missing = [c["id"] for c in cs.parse_cards(CSV.encode(), "set.csv") if not c["art"]]
    return {"cards": [{"id": cid, "art": f"painted subject for {cid}"} for cid in missing[:-1]] +
            [{"id": "ghost", "art": "nothing"}]}


def _env(tmp_path: Path, rid: str) -> tuple[Any, FakeComfy, FakeComfy]:
    from test_engine_runner import Env

    env = Env(tmp_path, [], {rid: (RECIPES / f"{rid}.yaml").read_text(encoding="utf-8")})
    z = FakeComfy("comfy.t2i_zimage_turbo", "57:27.text")
    k = FakeComfy("comfy.edit_flux2_klein4b", "75:74.text")
    env.art_query = FakeQuery(_art_reply())
    for ex in (z, k, ClaudeCardArtExecutor(env.settings, env.art_query), *(c() for c in cs.CARD_OPS)):
        env.registry.register(ex)
    return env, z, k


async def _put(env: Any, data: bytes, name: str, mime: str) -> str:
    return (await env.blobs.put_bytes(data, name=name, mime=mime)).hash


@pytest.mark.asyncio
@pytest.mark.parametrize("art,z_calls,k_calls", [("none", 0, 0), ("zimage", 7, 0), ("klein", 0, 7)])
async def test_card_set_recipe_runs_and_exports(tmp_path: Path, art: str, z_calls: int, k_calls: int) -> None:
    env, z, k = _env(tmp_path, "card-set")
    cards = await _put(env, CSV.encode("utf-8"), "set.csv", "text/csv")
    inputs: dict[str, Any] = {"cards": cards, "art": art, "width": 378, "review": False}
    if art == "klein":
        buf = io.BytesIO()
        Image.new("RGB", (600, 600), (200, 120, 40)).save(buf, "PNG")
        inputs["image"] = await _put(env, buf.getvalue(), "style.png", "image/png")
    run_id = await env.engine.start_run("card-set", inputs)
    await asyncio.wait_for(env.engine.wait_idle(run_id), 120)
    steps = env.steps(run_id)
    assert env.status(run_id) == RunStatus.SUCCEEDED, {k_: s.error for k_, s in steps.items() if s.error}
    assert (len(z.prompts), len(k.prompts)) == (z_calls, k_calls)
    assert len(env.art_query.calls) == (0 if art == "none" else 1)
    if art != "none":
        assert "opus" in str(env.art_query.calls[0][1].model)
        filled = steps["plan"].meta["claude_art"]
        assert len(filled) == 4 and "rong_lua_co_dai" not in filled and "ghost" not in filled
        assert any("painted subject for thay_phu_thuy" in p for p in z.prompts + k.prompts)
        assert not any("Rút một thẻ" in p for p in z.prompts + k.prompts)
    # art_* and back_* share the GPU lane; which of them takes it first is up to the scheduler.
    if z_calls:
        dragon = next(p for p in z.prompts if "ancient fire dragon" in p)
        assert "no text" in dragon
    if k_calls:
        assert all("art style of the reference" in p and "Replace the whole scene" in p for p in k.prompts)
        plan = steps["plan"]
        # Only the card Claude skipped is still painted from its name and rules.
        assert plan.meta["no_art_prompt"] == [cs.parse_cards(CSV.encode(), "set.csv")[-1]["id"]]
    doc = json.loads(env.blobs.path(steps["render"].outputs["cards"][0].hash).read_bytes())
    assert doc["ok"] and len(doc["cards"]) == 6 and doc["size"] == [378, 528] and doc["art_mode"] == art
    assert len(steps["render"].outputs["images"]) == 7 and steps["render"].outputs["atlas"]
    step = steps["approve"]
    assert engine_ready(step, env.blobs)
    for engine in ("unity", "godot"):
        out = export_engine(step, list(steps.values()), env.blobs, tmp_path / "exp", "Myth", engine)
        root = Path(out["dir"])
        rels = {Path(f).relative_to(root).as_posix() for f in out["files"]}
        (_check_unity if engine == "unity" else _check_godot)(root, rels)


def _check_unity(root: Path, rels: set[str]) -> None:
    assert {"Cards/Prefabs/Myth_rong_lua_co_dai.prefab", "Cards/Myth_cards.asset", "Scripts/CardSetData.cs",
            "Cards/card_back.png", "Cards/Fronts/mua_bang.png", "Cards/Atlas/Myth_cards_atlas.png"} <= rels
    metas = {re.search(r"guid: ([0-9a-f]{32})", (root / r).read_text(encoding="utf-8")).group(1)
             for r in rels if r.endswith(".meta")}
    assert len(metas) == len([r for r in rels if r.endswith(".meta")])
    builtin = {"fe87c0e1cc204ed48ad3b37840f39efc", "5f7201a12d95ffc409449d95f23cf332",
               "e19747de3f5aca642ab2be37e372fb86", "0cd44c1031e13a943bb63640046fad76", "0000000000000000e000000000000000"}
    for r in [r for r in rels if r.endswith(".prefab")]:
        pf = (root / r).read_text(encoding="utf-8")
        local = set(re.findall(r"^--- !u!\d+ &(-?\d+)", pf, re.M))
        refs = {m for m in re.findall(r"\{fileID: (-?\d+)\}", pf)} - {"0"}
        assert refs <= local, (r, refs - local)
        assert {g for g in re.findall(r"guid: ([0-9a-f]{32})", pf)} - builtin <= metas
    pf = (root / "Cards/Prefabs/Myth_rong_lua_co_dai.prefab").read_text(encoding="utf-8")
    assert "m_Name: Card_rong_lua_co_dai" in pf and "m_Text: \"Rồng Lửa Cổ Đại\"" in pf
    demo = (root / "Myth_CardsDemo.prefab").read_text(encoding="utf-8")
    assert "Canvas:" in demo and demo.count("m_Name: Card_") == 5 and "m_Name: CardBack" in demo
    asset = (root / "Cards/Myth_cards.asset").read_text(encoding="utf-8")
    assert asset.count("  - id: ") == 6 and "attack: -1" in asset
    assert {g for g in re.findall(r"guid: ([0-9a-f]{32})", asset)} <= metas
    markers = {r for r in rels if r.endswith("_ui_tmp.json")}
    cards = {r for r in rels if r.startswith("Cards/Prefabs/") and r.endswith(".prefab") and "_back." not in r}
    assert len(cards) == 6 and markers == {r[:-len(".prefab")] + "_ui_tmp.json" for r in cards} | {
        "Myth_CardsDemo_ui_tmp.json"}
    for m in markers:
        assert m[:-len("_ui_tmp.json")] + ".prefab" in rels
    assert {"Editor/AssetStudioCardTmp_Myth.cs", "Editor/AssetStudioCardTmp_Myth.cs.meta"} <= rels


def test_tmp_converter_targets_every_card_prefab() -> None:
    from studio_core.export.card_set import tmp_cs
    from studio_core.export.ui_screen import tmp_cs as screen_cs

    cs_ = tmp_cs("Thần Thoại")
    assert "class CardTmp_Thần_Thoại : AssetPostprocessor" in cs_ and "namespace AssetStudio\n" in cs_
    assert 'const string Asset = "Th\\u1ea7n Tho\\u1ea1i";' in cs_ and "static bool Mine(string p)" in cs_
    assert cs_.count("Mine(p)") == 2 and 'Asset + "_ui_tmp.json"' not in cs_
    assert 'marker.Substring(0, marker.Length - "_ui_tmp.json".Length) + ".prefab"' in cs_
    assert "ToHtmlStringRGB(color)" in cs_ and "outline ?? OutlineMaterial" not in cs_
    # Same conversion body as ui-screen: size, colour, Auto Size, alignment, bitmap-font skip, no TMP -> keep Text.
    for keep in ('Set(t, "enableAutoSizing", best);', 'Set(t, "fontSizeMin", (float)min);', ".fontsettings",
                 "if (tmp == null || fontType == null) return 0;", "keeps legacy Text"):
        assert keep in cs_ and keep in screen_cs("x")
    assert "using TMPro" not in cs_ and cs_.count("{") == cs_.count("}")


def _check_godot(root: Path, rels: set[str]) -> None:
    assert {"Myth_rong_lua_co_dai.tscn", "Myth_cards.tres", "card_set_data.gd", "Cards/card_back.png",
            "Cards/Atlas/Myth_cards_atlas.png"} <= rels
    for r in [r for r in rels if r.endswith((".tscn", ".tres")) and "/" not in r]:
        text = (root / r).read_text(encoding="utf-8")
        for p in re.findall(r'path="([^"]+)"', text):
            assert p in rels, (r, p)
    scene = (root / "Myth_rong_lua_co_dai.tscn").read_text(encoding="utf-8")
    assert 'text = "Rồng Lửa Cổ Đại"' in scene and "SystemFont" in scene and "autowrap_mode = 3" in scene


@pytest.mark.asyncio
async def test_card_set_prompt_uses_claude_list(tmp_path: Path) -> None:
    env, z, _ = _env(tmp_path, "card-set-prompt")
    reply = {"set_name": "Thần thoại", "cards": [
        {"name": f"Thẻ số {i}", "description": "Khiêu khích. Khi ra sân: rút một thẻ.", "cost": i, "attack": i,
         "health": i + 1, "rarity": ["common", "uncommon", "rare", "epic", "legendary", "common"][i],
         "type": "Sinh vật", "art": f"hero number {i}"} for i in range(6)]}
    q = FakeQuery(reply)
    env.registry.register(ClaudeCardListExecutor(env.settings, q))
    run_id = await env.engine.start_run("card-set-prompt", {"prompt": "thần thoại Việt", "art": "zimage",
                                                            "back": "none", "width": 378, "review": False})
    await asyncio.wait_for(env.engine.wait_idle(run_id), 120)
    steps = env.steps(run_id)
    assert env.status(run_id) == RunStatus.SUCCEEDED, {k: s.error for k, s in steps.items() if s.error}
    assert len(q.calls) == 1 and len(z.prompts) == 6 and "hero number 0" in z.prompts[0]
    # Every card already has art: the art step finishes without asking Claude.
    assert env.art_query.calls == [] and steps["art_text"].outputs["json"] == []
    doc = json.loads(env.blobs.path(steps["render"].outputs["cards"][0].hash).read_bytes())
    assert [c["rarity"] for c in doc["cards"]][:5] == ["common", "uncommon", "rare", "epic", "legendary"]
    assert steps["approve"].outputs["list"]
