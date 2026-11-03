import json
from pathlib import Path

from studio_core.export import theme as th
from test_api_basic import AUTH, app_ctx  # noqa: F401  (fixture reuse)
from test_pack import _project, _sprite_run


async def _build(app, client, tmp_path: Path, engines: list[str], **extra) -> tuple[dict, Path]:
    await _project(app)
    await _sprite_run(app, "r1", "Knight", (200, 0, 0))
    r = await client.post("/api/projects/p1/pack", headers=AUTH,
                          json={"engines": engines, "out_dir": str(tmp_path), **extra})
    assert r.status_code == 201, r.text
    return r.json(), Path(r.json()["dir"])


async def test_pack_theme_files_in_all_three_targets(app_ctx, tmp_path: Path) -> None:  # noqa: F811
    app, client, _ = app_ctx
    await _project(app)
    await _sprite_run(app, "r1", "Knight", (200, 0, 0))
    await app.state.repo.save_style_bible("p1", {"version": 1, "palette": ["#c0392b", "#2c3e50", "#27ae60"]})
    r = await client.post("/api/projects/p1/pack", headers=AUTH, json={
        "engines": ["flutter", "android", "ios"], "out_dir": str(tmp_path), "theme": True, "package": "com.acme.game"})
    assert r.status_code == 201, r.text
    res = r.json()
    root = Path(res["dir"])
    assert res["theme"]["source"] == "bible"
    assert (root / "flutter/lib/gen/app_theme.dart").is_file()
    assert (root / "android/kotlin/theme/Theme.kt").read_text(encoding="utf-8").startswith("package com.acme.game.theme")
    assert (root / "ios/Sources/AppTheme.swift").is_file()
    assert len(list((root / "ios/AssetStudio.xcassets").glob("Theme*.colorset"))) == len(th.ROLES)
    assert (root / "ios/AssetStudio.xcassets/Contents.json").is_file()
    assert res["theme"]["flutter"][0] == "lib/gen/app_theme.dart"
    assert not [w for w in res["warnings"] if w.startswith("theme")]


async def test_pack_theme_without_bible_uses_fallback(app_ctx, tmp_path: Path) -> None:  # noqa: F811
    app, client, _ = app_ctx
    res, root = await _build(app, client, tmp_path, ["flutter"], theme=True)
    assert res["theme"]["source"] == "fallback"
    assert any(w.startswith("theme:") for w in res["warnings"])
    assert (root / "flutter/lib/gen/app_theme.dart").is_file()


async def test_pack_without_theme_option_has_no_theme(app_ctx, tmp_path: Path) -> None:  # noqa: F811
    app, client, _ = app_ctx
    res, root = await _build(app, client, tmp_path, ["flutter", "ios"])
    assert "theme" not in res
    assert not (root / "flutter/lib/gen/app_theme.dart").exists()
    assert not list(root.rglob("AppTheme.swift"))


async def test_theme_endpoint(app_ctx) -> None:  # noqa: F811
    app, client, _ = app_ctx
    pid = await _project(app)
    r = await client.get(f"/api/projects/{pid}/style/theme", headers=AUTH, params={"target": "flutter"})
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "fallback" and min(body["contrast"].values()) >= th.MIN_CONTRAST
    assert [f["path"] for f in body["files"]] == ["lib/gen/app_theme.dart", "README_THEME.md"]

    await app.state.repo.save_style_bible(pid, {"version": 1, "palette": ["#336699"]})
    r = await client.get(f"/api/projects/{pid}/style/theme", headers=AUTH,
                         params={"target": "android", "package": "com.acme.game"})
    body = r.json()
    assert body["source"] == "bible" and "package com.acme.game.theme" in body["files"][0]["content"]
    assert (await client.get(f"/api/projects/{pid}/style/theme", headers=AUTH, params={"target": "unity"})
            ).status_code == 422
    assert (await client.get(f"/api/projects/{pid}/style/theme", headers=AUTH,
                             params={"target": "android", "package": "Bad Pkg"})).status_code == 422
    json.dumps(body)
