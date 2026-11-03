import json
import re
import time
from pathlib import Path

from studio_core.contracts import Lane, RunRow, StepRow, StepStatus, VersionRow, VersionStatus
from studio_core.engine.exports import recipe_exports
from studio_core.store.pack_mobile import parse_kotlin
from test_api_basic import AUTH, app_ctx  # noqa: F401  (fixture reuse)
from test_pack import _image_run, _png, _project, _sprite_run


async def _ui_run(app, run_id: str, name: str, elements: list[tuple[str, tuple[int, int, int, int]]]) -> VersionRow:
    repo, blobs = app.state.repo, app.state.blobs
    await repo.create_run(RunRow(id=run_id, project_id="p1", recipe_id="ui-kit", recipe_hash="h", created_at=time.time()))
    imgs = [await blobs.put_bytes(_png((i * 40, 90, 200), (48, 32)), name=f"{n}.png", mime="image/png")
            for i, (n, _) in enumerate(elements)]
    doc = {"type": "asset_studio.ui_slices", "sliced": True,
           "elements": [{"name": n, "hash": r.hash, "border": list(b), "state": ""}
                        for (n, b), r in zip(elements, imgs)]}
    ref = await blobs.put_bytes(json.dumps(doc).encode(), name="ui_slices.json", mime="application/json")
    step = StepRow(id=f"{run_id}-ui", run_id=run_id, key="ui", kind="op.nine_slice", lane=Lane.CPU,
                   status=StepStatus.SUCCEEDED, params={"asset": name}, outputs={"slices": [ref], "images": imgs})
    await repo.create_steps([step])
    v = VersionRow(id=f"v-{run_id}", project_id="p1", asset_name=name, run_id=run_id, step_id=step.id, blob=imgs[0],
                   status=VersionStatus.APPROVED, created_at=time.time())
    await repo.add_version(v)
    return v


async def _build(app, client, tmp_path: Path, engines: list[str], **extra) -> tuple[dict, Path]:
    await _project(app)
    await _sprite_run(app, "r1", "Knight", (200, 0, 0))
    await _sprite_run(app, "r2", "Archer", (0, 0, 200))
    await _ui_run(app, "r3", "Kit A", [("button", (8, 6, 8, 6)), ("coin", (0, 0, 0, 0))])
    await _ui_run(app, "r4", "Kit B", [("button", (4, 4, 4, 4)), ("heart", (0, 0, 0, 0))])
    await _image_run(app, "r5", "Icon")
    r = await client.post("/api/projects/p1/pack", headers=AUTH,
                          json={"engines": engines, "out_dir": str(tmp_path), **extra})
    assert r.status_code == 201, r.text
    res = r.json()
    return res, Path(res["dir"])


async def test_pack_flutter_and_android_tree(app_ctx, tmp_path: Path) -> None:  # noqa: F811
    app, client, _ = app_ctx
    res, root = await _build(app, client, tmp_path, ["flutter", "android"], package="com.acme.game")

    fl = root / "flutter"
    assert not (root / "unity").exists() and not (root / "godot").exists()
    pub = (fl / "pubspec_assets.yaml").read_text(encoding="utf-8")
    assert "    - assets/images/sprites/" in pub and "    - assets/ui/" in pub and "    - assets/images/" in pub
    dart = (fl / "lib/gen/asset_studio_assets.dart").read_text(encoding="utf-8")
    assert dart.count("class AssetStudioAssets") == 1
    for needle in ("sprites/Knight.png", "sprites/Archer.png", "images/coin.png", "images/Kit_B_heart.png"):
        assert needle in dart, needle
    assert (fl / "lib/gen/asset_studio_flame.dart").is_file()

    docs = json.loads((fl / "assets/ui/ui_9slice.json").read_text(encoding="utf-8"))
    names = [d["name"] for d in docs]
    assert len(names) == len(set(names)) == 2 and "button" in names
    assert any(n.startswith("Kit_B_") for n in names)
    for n in names:
        assert (fl / f"assets/ui/{n}.png").is_file()
        assert n in (fl / "lib/gen/asset_studio_ui.dart").read_text(encoding="utf-8")
    assert len(list((fl / "lib/gen").glob("asset_studio_ui*.dart"))) == 1

    an = root / "android"
    kt = (an / "kotlin/AssetStudioAssets.kt").read_text(encoding="utf-8")
    assert kt.count("object AssetStudioAssets") == 1 and "import com.acme.game.R" in kt
    images, patches, clips = parse_kotlin(kt)
    allnames = [*images, *patches, *(c.name for c in clips)]
    assert len(allnames) == len(set(allnames)) and "kit_a_button" in patches and "kit_b_button" in patches
    assert {c.name.split("_")[0] for c in clips} == {"knight", "archer"}
    for n in [*images, *patches, *(f for c in clips for f in c.frames)]:
        assert list((an / "res").glob(f"drawable*/{n}.*")), n
    assert not list((an / "res").glob("drawable*/button.*"))
    assert re.search(r"package|com/acme/game", (an / "README_ANDROID.md").read_text(encoding="utf-8"))

    assert res["flutter"]["skipped"] == ["Icon"] and res["android"]["package"] == "com.acme.game"
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "## Flutter" in readme and "## Android" in readme and "Skipped for flutter" in readme
    prov = {a["folder"]: a for a in json.loads((root / "provenance.json").read_text(encoding="utf-8"))["assets"]}
    assert prov["Icon"]["files"]["flutter"] == [] and "assets/images/sprites/Knight.png" in prov["Knight"]["files"]["flutter"]


async def test_pack_ios_merges_catalog(app_ctx, tmp_path: Path) -> None:  # noqa: F811
    app, client, _ = app_ctx
    res, root = await _build(app, client, tmp_path, ["ios", "godot"])
    ios = root / "ios"
    cat = ios / "AssetStudio.xcassets"
    assert (cat / "Contents.json").is_file()
    sets = sorted(p.name for p in cat.glob("*.imageset"))
    assert len(sets) == len(set(sets)) and "button.imageset" in sets and any(s.startswith("Kit_B_button") for s in sets)
    swift = (ios / "Sources/AssetStudioAssets.swift").read_text(encoding="utf-8")
    assert swift.count("enum AssetStudioAssets") == 1 and "coin" in swift and "Kit_B_button" in swift
    assert len(list((ios / "Sources").glob("AssetStudioKit.swift"))) == 1
    kit = (ios / "Sources/AssetStudioKit.swift").read_text(encoding="utf-8")
    assert "static let button = EdgeInsets" in kit and "static let kitBButton = EdgeInsets" in kit
    assert (cat / "Knight.spriteatlas").is_dir() and (cat / "Archer.spriteatlas").is_dir()
    assert (root / "godot").is_dir() and "ios" not in res.get("mobile", {})


async def test_pack_mobile_atlas_and_unity_unchanged(app_ctx, tmp_path: Path) -> None:  # noqa: F811
    app, client, _ = app_ctx
    res, root = await _build(app, client, tmp_path, ["flutter", "unity"], atlas=True)
    assert res["atlas"] and (root / "flutter/assets/images/Atlas").is_dir()
    assert "assets/images/Atlas/" in (root / "flutter/pubspec_assets.yaml").read_text(encoding="utf-8")
    assert not list((root / "flutter").rglob("*.import")) and not list((root / "flutter").rglob("*.meta"))
    assert any((root / "unity").rglob("*.prefab"))


async def test_project_engine_targets(app_ctx) -> None:  # noqa: F811
    _, client, _ = app_ctx
    made = (await client.post("/api/projects", headers=AUTH, json={"name": "Flutter game"})).json()
    assert made["engine_target"] == "flutter"
    for target in ("ios", "android", "unity", "none"):
        r = await client.patch(f"/api/projects/{made['id']}", headers=AUTH, json={"engine_target": target})
        assert r.status_code == 200 and r.json()["engine_target"] == target


def test_recipe_exports_tag_mobile_targets() -> None:
    assert recipe_exports(["op.sheet"])[:5] == ["flutter", "android", "ios", "unity", "godot"]
    assert "flutter" not in recipe_exports(["blender.game_ready"])


def _stage(root: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(data)
    return root


def test_flutter_pack_merges_icon_tiles_and_svg(tmp_path: Path) -> None:
    from studio_core.store.pack_mobile import FlutterPack

    warnings: list[str] = []
    pack = FlutterPack(tmp_path / "out", "Game", warnings)
    icon = {"flutter_launcher_icons.yaml": b"y", "assets/icon/app_icon.png": b"A", "assets/svg/logo.svg": b"<svg/>"}
    pack.add("One", _stage(tmp_path / "a", {**icon, "assets/tiles/m.tmx": b"<map/>", "assets/tiles/m_tiles.png": b"P",
                                            "README_FLUTTER.md": b"x\n\n2. Package: flame, flame_tiled, flutter_svg.\n\n- n\n"}))
    pack.add("Two", _stage(tmp_path / "b", {**icon, "assets/icon/app_icon.png": b"B", "assets/svg/logo.svg": b"<svg id='2'/>"}))
    pack.finish()
    out = tmp_path / "out"
    assert (out / "assets/icon/app_icon.png").read_bytes() == b"A" and (out / "flutter_launcher_icons.yaml").read_bytes() == b"y"
    assert any("assets/icon/app_icon.png" in w for w in warnings)
    assert (out / "assets/svg/logo.svg").is_file() and (out / "assets/svg/Two_logo.svg").read_bytes() == b"<svg id='2'/>"
    yaml = (out / "pubspec_assets.yaml").read_text(encoding="utf-8")
    assert "flame_tiled: ^" in yaml and "flutter_svg: ^" in yaml and "assets/icon" not in yaml and "    - assets/tiles/\n" in yaml
    assert "TiledComponent.load" in (out / "lib/gen/asset_studio_flame.dart").read_text(encoding="utf-8")
    assert "assets/icon" not in (out / "lib/gen/asset_studio_assets.dart").read_text(encoding="utf-8")


def test_android_pack_resource_names_never_start_with_digit(tmp_path: Path) -> None:
    from PIL import Image

    from studio_core.export import android
    from studio_core.export.source import Source
    from studio_core.store.pack_mobile import AndroidPack

    png = tmp_path / "c.png"
    Image.new("RGBA", (16, 16), (200, 40, 40, 255)).save(png)
    staged = tmp_path / "in"
    for rel, data in android.export(Source(asset="9 Lives", ui=[("coin", png, (0, 0, 0, 0), "")])):
        (staged / rel).parent.mkdir(parents=True, exist_ok=True)
        (staged / rel).write_bytes(data)
    out = tmp_path / "out"
    pack = AndroidPack(out, "Pack", "com.acme.game", [])
    pack.add("9 Lives", staged)
    pack.finish()
    names = [p.name.split(".")[0] for p in out.rglob("res/drawable-*/*")]
    assert names and all(re.fullmatch(r"[a-z][a-z0-9_]*", n) for n in names), names
    kt = (out / "kotlin/AssetStudioAssets.kt").read_text(encoding="utf-8")
    assert "R.drawable.asset_9_lives_coin" in kt


async def test_pack_flutter_demo_flag_is_forwarded(app_ctx, tmp_path: Path) -> None:  # noqa: F811
    app, client, _ = app_ctx
    res, root = await _build(app, client, tmp_path / "plain", ["flutter"])
    assert not (root / "flutter/demo").exists()
    r = await client.post("/api/projects/p1/pack", headers=AUTH,
                          json={"engines": ["flutter"], "out_dir": str(tmp_path / "demo"), "flutter_demo": True})
    assert r.status_code == 201, r.text
    assert "demo" in json.dumps(r.json())


async def test_pack_android_ios_still_image_only(app_ctx, tmp_path: Path) -> None:  # noqa: F811
    app, client, _ = app_ctx
    await _project(app)
    await _image_run(app, "r5", "Icon")
    r = await client.post("/api/projects/p1/pack", headers=AUTH,
                          json={"engines": ["android", "ios"], "out_dir": str(tmp_path)})
    assert r.status_code == 201, r.text
    res = r.json()
    root = Path(res["dir"])
    assert res["android"]["skipped"] == [] and res["ios"]["skipped"] == []
    assert len(list((root / "android/res").glob("drawable-*/icon.webp"))) == 5
    assert "R.drawable.icon" in (root / "android/kotlin/AssetStudioAssets.kt").read_text(encoding="utf-8")
    iset = root / "ios/AssetStudio.xcassets/Icon.imageset"
    doc = json.loads((iset / "Contents.json").read_text(encoding="utf-8"))
    assert [i["scale"] for i in doc["images"]] == ["1x", "2x", "3x"]
    assert all((iset / i["filename"]).is_file() for i in doc["images"])
    assert "Icon" in (root / "ios/Sources/AssetStudioAssets.swift").read_text(encoding="utf-8")


async def test_pack_android_ios_skips_glb_only(app_ctx, tmp_path: Path) -> None:  # noqa: F811
    app, client, _ = app_ctx
    await _project(app)
    repo, blobs = app.state.repo, app.state.blobs
    await repo.create_run(RunRow(id="r6", project_id="p1", recipe_id="prop-3d", recipe_hash="h", created_at=time.time()))
    ref = await blobs.put_bytes(b"glTF" + bytes(20), name="rock.glb", mime="model/gltf-binary")
    step = StepRow(id="r6-gen", run_id="r6", key="gen", kind="blender.game_ready", lane=Lane.CPU,
                   status=StepStatus.SUCCEEDED, outputs={"mesh": [ref]})
    await repo.create_steps([step])
    await repo.add_version(VersionRow(id="v-r6", project_id="p1", asset_name="Rock", run_id="r6", step_id=step.id,
                                      blob=ref, status=VersionStatus.APPROVED, created_at=time.time()))
    r = await client.post("/api/projects/p1/pack", headers=AUTH,
                          json={"engines": ["android", "ios"], "out_dir": str(tmp_path)})
    assert r.status_code == 201, r.text
    res = r.json()
    assert res["android"]["skipped"] == ["Rock"] and res["ios"]["skipped"] == ["Rock"]
    assert any("Rock" in w and "android" in w for w in res["warnings"])


def _still_step(tmp_path: Path, size: int, ext: str, name: str):
    from PIL import Image

    from studio_core.contracts import BlobRef, StepRow

    class Blobs:
        def path(self, h: str) -> Path:
            return tmp_path / h

    fmt = {"png": "PNG", "jpg": "JPEG", "webp": "WEBP"}[ext]
    Image.new("RGB", (size, size), (30, 120, 200)).save(tmp_path / f"h{size}{ext}", fmt)
    ref = BlobRef(hash=f"h{size}{ext}", mime="image/png", size=1, name=f"{name}.{ext}")
    step = StepRow(id="s", run_id="r", key="gen", kind="x", lane=Lane.CPU, status=StepStatus.SUCCEEDED,
                   outputs={"images": [ref]})
    return step, Blobs()


import pytest


@pytest.mark.parametrize("size,ext", [(256, "png"), (1024, "jpg"), (256, "webp")])
def test_still_image_density_and_names(tmp_path: Path, size: int, ext: str) -> None:
    from PIL import Image

    from studio_core.store.bundle import export_engine

    step, blobs = _still_step(tmp_path, size, ext, "9 Hero Icon")
    res = export_engine(step, [], blobs, tmp_path / "o", None, "android", package="com.acme.game")
    drawables = {Path(f).parent.name: Path(f) for f in res["files"] if "/drawable-" in f.replace("\\", "/")}
    assert sorted(drawables) == ["drawable-hdpi", "drawable-mdpi", "drawable-xhdpi", "drawable-xxhdpi", "drawable-xxxhdpi"]
    for d, k in (("mdpi", 1), ("hdpi", 1.5), ("xhdpi", 2), ("xxhdpi", 3), ("xxxhdpi", 4)):
        with Image.open(drawables[f"drawable-{d}"]) as im:
            assert im.width == round(size * k / 4)
    assert all(re.fullmatch(r"[a-z][a-z0-9_]*", p.stem) for p in drawables.values())

    res = export_engine(step, [], blobs, tmp_path / "o", None, "ios")
    contents = [Path(f) for f in res["files"] if f.replace("\\", "/").endswith(".imageset/Contents.json")]
    assert len(contents) == 1
    doc = json.loads(contents[0].read_text(encoding="utf-8"))
    sizes = {}
    for i in doc["images"]:
        with Image.open(contents[0].parent / i["filename"]) as im:
            sizes[i["scale"]] = im.width
    assert sizes == {"1x": round(size / 3), "2x": round(size * 2 / 3), "3x": size}
