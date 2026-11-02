import json
from pathlib import Path

from studio_core.store.ai_credits import build_ai_credits, disclosure, is_ai_step, license_warnings
from test_api_basic import AUTH, app_ctx  # noqa: F401  (fixture reuse)
from test_pack import _project, _sprite_run  # noqa: F401


def _prov(commercial: str = "yes", kind: str = "comfy.t2i_zimage_turbo") -> list[dict]:
    m = {"name": "m.safetensors", "license": "Custom", "commercial": commercial, "source": "x/y"}
    return [{"name": "Knight", "folder": "Knight", "recipe_id": "sprite-8dir", "models": [m], "commercial": commercial,
             "generators": [{"step": "c", "kind": kind}, {"step": "s", "kind": "op.sheet"}]}]


def test_disclosure_and_warnings() -> None:
    assert is_ai_step("comfy.x") and is_ai_step("codex.image") and not is_ai_step("op.sheet")
    d = disclosure(_prov())
    assert d["pre_generated"] and d["live_generated"] is False and d["ai_assets"] == 1
    assert not disclosure(_prov(kind="op.crop"))["pre_generated"]
    assert license_warnings(_prov()) == []
    md, warns = build_ai_credits("Rừng Đêm", _prov("no"), "1.0.0", 0)
    assert md.startswith("# AI Credits: Rừng Đêm 1.0.0") and "Pre-generated content:** yes" in md
    assert "## License warnings" in md and len(warns) == 1 and "non-commercial only" in warns[0]
    md, warns = build_ai_credits("X", [], None, 0)
    assert "No approved assets yet." in md and warns == []


async def test_endpoint_and_pack_file(app_ctx, tmp_path: Path) -> None:  # noqa: F811
    app, client, _ = app_ctx
    await _project(app)
    await _sprite_run(app, "r1", "Knight", (200, 0, 0))
    r = await client.get("/api/projects/p1/ai-credits", headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "# AI Credits: Rừng Đêm" in body["markdown"] and "Knight" in body["markdown"]
    assert body["summary"]["pre_generated"] is True and isinstance(body["warnings"], list)
    assert (await client.get("/api/projects/nope/ai-credits", headers=AUTH)).status_code == 404

    res = (await client.post("/api/projects/p1/pack", headers=AUTH,
                             json={"engines": ["godot"], "out_dir": str(tmp_path), "zip": False})).json()
    root = Path(res["dir"])
    assert "AI generated content disclosure" in (root / "AI_CREDITS.md").read_text(encoding="utf-8")
    prov = json.loads((root / "provenance.json").read_text(encoding="utf-8"))
    assert prov["format"] == "asset-studio-provenance/1" and prov["ai_disclosure"]["ai_assets"] == 1
