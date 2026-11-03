from pathlib import Path

from studio_core.store.bundle import still_name
from studio_core.store.pack_mobile import parse_kotlin
from test_api_basic import app_ctx  # noqa: F401  (fixture reuse)
from test_pack_mobile import _build


def test_still_name_keeps_one_asset_prefix():
    assert still_name("hero", "hero") == "hero"
    assert still_name("hero", "Hero-2") == "Hero-2"
    assert still_name("hero", "hero_alt") == "hero_alt"
    assert still_name("hero", "shield") == "hero_shield"


async def test_android_pack_names_a_single_still_after_the_asset(app_ctx, tmp_path: Path) -> None:  # noqa: F811
    app, client, _ = app_ctx
    _, root = await _build(app, client, tmp_path, ["android", "ios"])
    an = root / "android"
    images, _patches, clips = parse_kotlin((an / "kotlin/AssetStudioAssets.kt").read_text(encoding="utf-8"))
    assert "icon" in images and "icon_icon" not in images
    assert list((an / "res").glob("drawable*/icon.*")) and not list((an / "res").glob("drawable*/icon_icon.*"))
    assert all(not c.name.startswith("knight_knight") for c in clips)
    ios = sorted(p.name for p in (root / "ios" / "AssetStudio.xcassets").glob("*.imageset"))
    assert "Icon.imageset" in ios or "icon.imageset" in ios
