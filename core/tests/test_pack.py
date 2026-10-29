import io
import json
import re
import time
import zipfile
from pathlib import Path
from typing import Any

from PIL import Image

from studio_core.contracts import Lane, ProjectRow, RunRow, StepRow, StepStatus, VersionRow, VersionStatus
from studio_core.export.sheet import stable_hex
from studio_core.store import pack as pk
from test_api_basic import AUTH, app_ctx  # noqa: F401  (fixture reuse)

ANGLES = [0, 45, 90, 135, 180, 225, 270, 315]
GUID = re.compile(r"^guid: ([0-9a-f]{32})$", re.M)


def _png(color: tuple[int, int, int], size: tuple[int, int] = (16, 20)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", size, (*color, 255)).save(buf, "PNG")
    return buf.getvalue()


async def _project(app, name: str = "Rừng Đêm") -> str:
    now = time.time()
    await app.state.repo.create_project(ProjectRow(id="p1", name=name, created_at=now, updated_at=now))
    return "p1"


async def _sprite_run(app, run_id: str, asset: str, color: tuple[int, int, int], gen_kind: str = "comfy.t2i_zimage_turbo",
                      seed: int = 7) -> VersionRow:
    repo, blobs = app.state.repo, app.state.blobs
    await repo.create_run(RunRow(id=run_id, project_id="p1", recipe_id="sprite-8dir", recipe_hash="h",
                                 inputs={"prompt": f"a {asset} knight"}, created_at=time.time()))
    frames = [await blobs.put_bytes(_png((color[0], i * 20, color[2])), name=f"dir_{a:03d}.png", mime="image/png")
              for i, a in enumerate(ANGLES)]
    meta = {"frame_w": 16, "frame_h": 20, "cols": 8, "rows": 1, "count": 8, "padding": 0, "order": ANGLES,
            "pivot": [8, 18], "frames": [f.hash for f in frames]}
    meta_ref = await blobs.put_bytes(json.dumps(meta).encode(), name="sheet.json", mime="application/json")
    sheet = Image.new("RGBA", (16 * 8, 20), (*color, 255))
    buf = io.BytesIO()
    sheet.save(buf, "PNG")
    sheet_ref = await blobs.put_bytes(buf.getvalue(), name="sheet.png", mime="image/png")
    concept = StepRow(id=f"{run_id}-gen", run_id=run_id, key="concept", kind=gen_kind, lane=Lane.GPU,
                      status=StepStatus.SUCCEEDED, params={"57:27.text": f"{asset} hero", "57:3.seed": seed},
                      meta={"seeds": {"57:3.seed": seed}}, outputs={"images": [frames[0]]}, order=0)
    sheet_step = StepRow(id=f"{run_id}-sheet", run_id=run_id, key="sheet", kind="op.sheet", lane=Lane.CPU,
                         status=StepStatus.SUCCEEDED, deps=["concept"], params={"asset": asset},
                         outputs={"sheet": [sheet_ref], "meta": [meta_ref]}, order=1)
    await repo.create_steps([concept, sheet_step])
    v = VersionRow(id=f"v-{run_id}", project_id="p1", asset_name=asset, run_id=run_id, step_id=sheet_step.id,
                   blob=sheet_ref, status=VersionStatus.APPROVED, created_at=time.time())
    await repo.add_version(v)
    return v


async def _image_run(app, run_id: str, name: str) -> VersionRow:
    repo, blobs = app.state.repo, app.state.blobs
    await repo.create_run(RunRow(id=run_id, project_id="p1", recipe_id="audio-sfx", recipe_hash="h",
                                 created_at=time.time()))
    ref = await blobs.put_bytes(_png((9, 9, 9), (32, 32)), name="icon.png", mime="image/png")
    step = StepRow(id=f"{run_id}-gen", run_id=run_id, key="gen", kind="comfy.sfx_stable_audio", lane=Lane.GPU,
                   status=StepStatus.SUCCEEDED, outputs={"images": [ref]})
    await repo.create_steps([step])
    v = VersionRow(id=f"v-{run_id}", project_id="p1", asset_name=name, run_id=run_id, step_id=step.id, blob=ref,
                   status=VersionStatus.APPROVED, created_at=time.time())
    await repo.add_version(v)
    return v


def _guids(pkg: Path) -> list[str]:
    return [GUID.search(m.read_text(encoding="utf-8"))[1] for m in pkg.rglob("*.meta")]


async def test_pack_unity_and_godot(app_ctx, tmp_path: Path) -> None:  # noqa: F811
    app, client, _ = app_ctx
    await _project(app)
    await _sprite_run(app, "r1", "Hiệp sĩ", (200, 0, 0))
    await _sprite_run(app, "r2", "Cung thủ", (0, 0, 200))
    await _image_run(app, "r3", "Icon")

    listing = (await client.get("/api/projects/p1/packs", headers=AUTH)).json()
    assert listing["next_version"] == "1.0.0" and listing["slug"] == "rung-dem" and listing["packs"] == []
    assert {a["name"]: a["engine_ready"] for a in listing["assets"]} == {"Hiệp sĩ": True, "Cung thủ": True,
                                                                          "Icon": False}

    out_dir = tmp_path / "out"
    r = await client.post("/api/projects/p1/pack", headers=AUTH, json={
        "engines": ["unity", "godot"], "license": "cc-by-4.0", "author": "Studio X", "org": "Acme Games",
        "out_dir": str(out_dir)})
    assert r.status_code == 201, r.text
    res = r.json()
    assert res["version"] == "1.0.0" and res["previous"] is None
    root = Path(res["dir"])
    assert root == (out_dir / "rung-dem-1.0.0").resolve() and Path(res["zip"]).is_file()

    pkg = root / "unity" / "com.acme-games.rung-dem"
    manifest = json.loads((pkg / "package.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "com.acme-games.rung-dem" and manifest["version"] == "1.0.0"
    assert manifest["unity"] == "6000.0" and manifest["displayName"] == "Rừng Đêm"
    assert manifest["license"] == "CC-BY-4.0" and manifest["author"] == {"name": "Studio X"}
    assert re.fullmatch(r"com\.[a-z0-9-]+\.[a-z0-9.-]+", manifest["name"])
    for n in ("README.md", "CHANGELOG.md", "LICENSE.md", "provenance.json", "package.json"):
        assert (pkg / n).is_file() and (pkg / f"{n}.meta").is_file()
    assert (pkg / "Runtime" / "Hiep_si" / "Hiep_si.prefab").is_file()
    assert (pkg / "Runtime" / "Icon" / "icon.png.meta").is_file()
    assert (pkg / "Runtime.meta").is_file() and (pkg / "Runtime" / "Cung_thu.meta").is_file()
    guids = _guids(pkg)
    assert len(guids) == len(set(guids)) == res["unity"]["guids"]
    for p in pkg.rglob("*"):
        if p.suffix != ".meta":
            assert p.with_name(p.name + ".meta").exists(), p

    addon = root / "godot" / "addons" / "rung_dem"
    assert (addon / "Hiep_si" / "Hiep_si.tscn").is_file() and (addon / "Icon" / "icon.png").is_file()
    assert (addon / "plugin.cfg").exists() == res["godot"]["plugin"]
    assert (root / "godot" / "LICENSE.md").read_text(encoding="utf-8").startswith("# Creative Commons Attribution")

    prov = json.loads((root / "provenance.json").read_text(encoding="utf-8"))
    by = {a["folder"]: a for a in prov["assets"]}
    knight = by["Hiep_si"]
    assert knight["recipe_id"] == "sprite-8dir" and knight["run_id"] == "r1"
    assert {m["name"] for m in knight["models"]} >= {"z_image_turbo_bf16.safetensors", "qwen_3_4b.safetensors"}
    assert knight["commercial"] == "yes" and knight["seeds"] == {"concept:57:3.seed": 7}
    assert {p["text"] for p in knight["prompts"]} == {"a Hiệp sĩ knight", "Hiệp sĩ hero"}
    assert knight["blobs"][0]["hash"] and "Hiep_si.prefab" in knight["files"]["unity"]
    assert by["Icon"]["commercial"] == "restricted"
    assert prov["summary"]["restricted"] == ["Icon"] and prov["summary"]["non_commercial"] == []
    assert "Check before commercial use" in (root / "README.md").read_text(encoding="utf-8")

    with zipfile.ZipFile(res["zip"]) as z:
        assert "rung-dem-1.0.0/unity/com.acme-games.rung-dem/package.json" in z.namelist()


async def test_version_bump_changelog_and_noncommercial_flag(app_ctx, tmp_path: Path, monkeypatch) -> None:  # noqa: F811
    app, client, _ = app_ctx
    await _project(app)
    await _sprite_run(app, "r1", "Knight", (200, 0, 0))
    gone = await _sprite_run(app, "r2", "Archer", (0, 0, 200))
    body = {"engines": ["godot"], "out_dir": str(tmp_path)}
    first = (await client.post("/api/projects/p1/pack", headers=AUTH, json=body)).json()
    assert first["version"] == "1.0.0" and first["changes"]["added"] == ["Archer", "Knight"]

    await app.state.repo.set_version_status(gone.id, VersionStatus.REJECTED)
    await _sprite_run(app, "r4", "Mage", (0, 200, 0))
    newer = await app.state.blobs.put_bytes(_png((1, 2, 3)), name="knight2.png", mime="image/png")
    await app.state.repo.add_version(VersionRow(id="v-r1b", project_id="p1", asset_name="Knight", run_id="r1",
                                                step_id="r1-sheet", blob=newer, status=VersionStatus.APPROVED,
                                                created_at=time.time()))
    from studio_core.bootstrap.plan import manifest

    zit = next(m for m in manifest()["models"] if m["name"] == "z_image_turbo_bf16.safetensors")
    monkeypatch.setitem(zit, "license", "CC-BY-NC-4.0")
    monkeypatch.setitem(zit, "commercial", "no")
    r = await client.post("/api/projects/p1/pack", headers=AUTH, json=body)
    second = r.json()
    assert second["version"] == "1.0.1" and second["previous"] == "1.0.0"
    assert second["changes"] == {"added": ["Mage"], "removed": ["Archer"], "updated": ["Knight"]}
    assert sorted(second["provenance"]["non_commercial"]) == ["Knight", "Mage"]
    assert second["commercial"] == "no"
    log = (Path(second["dir"]) / "CHANGELOG.md").read_text(encoding="utf-8")
    assert log.index("## [1.0.1]") < log.index("## [1.0.0]")
    assert "### Added\n- Mage" in log and "### Removed\n- Archer" in log and "### Changed\n- Knight" in log

    third = (await client.post("/api/projects/p1/pack", headers=AUTH,
                               json={**body, "version": "2.0.0", "include": ["v-r4"]})).json()
    assert third["version"] == "2.0.0" and [i["name"] for i in third["items"]] == ["Mage"]
    listing = (await client.get("/api/projects/p1/packs", headers=AUTH)).json()
    assert [p["version"] for p in listing["packs"]] == ["2.0.0", "1.0.1", "1.0.0"]
    assert listing["next_version"] == "2.0.1"

    bad = await client.post("/api/projects/p1/pack", headers=AUTH, json={**body, "version": "1.0"})
    assert bad.status_code == 422
    bad = await client.post("/api/projects/p1/pack", headers=AUTH, json={**body, "include": ["v-r2"]})
    assert bad.status_code == 422
    bad = await client.post("/api/projects/p1/pack", headers=AUTH, json={**body, "out_dir": "relative/dir"})
    assert bad.status_code == 422
    assert (await client.post("/api/projects/nope/pack", headers=AUTH, json=body)).status_code == 404


def _meta(g: str) -> str:
    return f"fileFormatVersion: 2\nguid: {g}\nMonoImporter:\n  externalObjects: {{}}\n"


def test_finalize_unity_dedupes_shared_scripts_and_guids(tmp_path: Path) -> None:
    pkg = tmp_path / "com.x.y"
    rt = pkg / "Runtime"
    shared = "a" * 32
    for asset, shader_guid in (("A", "b" * 32), ("B", "c" * 32)):
        d = rt / asset
        (d / "Scripts").mkdir(parents=True)
        (d / "Scripts" / "NpcData.cs").write_text("class NpcData {}", encoding="utf-8")
        (d / "Scripts" / "NpcData.cs.meta").write_text(_meta(shared), encoding="utf-8")
        (d / "Editor").mkdir()
        (d / "Editor" / "AssetStudioLodGroup.cs").write_text("class Lod {}", encoding="utf-8")
        (d / "Shaders").mkdir()
        (d / "Shaders" / "HitFlash.shader").write_text("Shader \"X\" {}", encoding="utf-8")
        (d / "Shaders" / "HitFlash.shader.meta").write_text(_meta(shader_guid), encoding="utf-8")
        (d / f"{asset}.mat").write_text(f"m_Shader: {{fileID: 4800000, guid: {shader_guid}, type: 3}}\n",
                                        encoding="utf-8")
        (d / f"{asset}.mat.meta").write_text(_meta("d" * 32), encoding="utf-8")
        (d / f"{asset}.prefab").write_text(f"m_Material: {{guid: {'d' * 32}}}\n", encoding="utf-8")
        (d / f"{asset}.prefab.meta").write_text(_meta("e" * 31 + asset.lower()), encoding="utf-8")

    rep = pk.finalize_unity(pkg, "com.x.y", "X.Y")
    assert (rt / "Scripts" / "NpcData.cs").is_file() and len(list(pkg.rglob("NpcData.cs"))) == 1
    assert len(list(pkg.rglob("AssetStudioLodGroup.cs"))) == 1 and (pkg / "Editor" / "AssetStudioLodGroup.cs").is_file()
    assert len(list(pkg.rglob("HitFlash.shader"))) == 1
    scoped = {g: stable_hex("pack-guid", "com.x.y", g) for g in ("a" * 32, "b" * 32, "d" * 32)}
    assert "c" * 32 not in (rt / "B" / "B.mat").read_text(encoding="utf-8")
    assert scoped["b" * 32] in (rt / "B" / "B.mat").read_text(encoding="utf-8")
    assert rep["remapped"] == ["Runtime/B/B.mat.meta"]
    new = GUID.search((rt / "B" / "B.mat.meta").read_text(encoding="utf-8"))[1]
    assert new in (rt / "B" / "B.prefab").read_text(encoding="utf-8")
    assert scoped["d" * 32] in (rt / "A" / "A.prefab").read_text(encoding="utf-8")
    # No GUID of a standalone export (same step + asset) or the fixed NpcData.cs GUID survives into the package.
    assert GUID.search((rt / "Scripts" / "NpcData.cs.meta").read_text(encoding="utf-8"))[1] == scoped["a" * 32]
    assert not {"a" * 32, "b" * 32, "d" * 32} & set(_guids(pkg))
    asm = json.loads((rt / "X.Y.asmdef").read_text(encoding="utf-8"))
    ed = json.loads((pkg / "Editor" / "X.Y.Editor.asmdef").read_text(encoding="utf-8"))
    assert asm["name"] == "X.Y" and ed["includePlatforms"] == ["Editor"] and ed["references"] == ["X.Y"]
    guids = _guids(pkg)
    assert len(guids) == len(set(guids)) == rep["guids"]
    assert not (rt / "A" / "Scripts").exists() and not (rt / "B" / "Editor").exists()


def test_helpers() -> None:
    assert pk.slugify("Rừng Đêm 2!") == "rung-dem-2" and pk.folder_name("9 lives", "x") == "_9_lives"
    assert pk.package_name("Acme Games", "rung-dem") == "com.acme-games.rung-dem"
    assert pk.next_version([]) == "1.0.0" and pk.next_version([{"version": "1.2.9"}, {"version": "1.10.0"}]) == "1.10.1"
    assert pk.diff_assets({"a": "1", "b": "1"}, {"b": "2", "c": "1"}) == {"added": ["c"], "removed": ["a"],
                                                                         "updated": ["b"]}
    assert pk.lineage(StepRow(id="s", run_id="r", key="s", kind="op.x", lane=Lane.CPU, deps=["a"]), []) != []
    assert "PLACEHOLDER" in pk.license_text("proprietary", "", 2026, "T")


class _FakeLink:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def request(self, method: str, path: str, **kw: Any) -> Any:
        self.calls.append((method, path, kw))
        return {"version": "1.0.0"}

    async def close(self) -> None:
        return None


async def test_mcp_build_asset_pack() -> None:
    from mcp import Client

    from studio_core.mcp.server import build_server

    link = _FakeLink()
    async with Client(build_server(link)) as c:  # type: ignore[arg-type]
        assert "build_asset_pack" in {t.name for t in (await c.list_tools()).tools}
        await c.call_tool("build_asset_pack", {"project_id": "p1", "engines": ["unity", "godot"],
                                               "license": "cc0", "version": "1.2.3"})
        method, path, kw = link.calls[-1]
        assert (method, path) == ("POST", "/api/projects/p1/pack")
        assert kw["json"]["engines"] == ["unity", "godot"] and kw["json"]["version"] == "1.2.3"
        assert "include" not in kw["json"]


def test_finalize_unity_scopes_namespace_and_detects_dependencies(tmp_path: Path) -> None:
    from studio_core.export import npc, unity
    from studio_core.export.autotile import RULE_TILE_GUID

    pkg = tmp_path / "com.x.y"
    d = pkg / "Runtime" / "Npc"
    (d / "Scripts").mkdir(parents=True)
    (d / "Scripts" / "NpcData.cs").write_text(npc.UNITY_SCRIPT, encoding="utf-8")
    (d / "Editor").mkdir()
    (d / "Editor" / "AssetStudioLodGroup.cs").write_text(unity.LOD_GROUP_SCRIPT, encoding="utf-8")
    assert pk.unity_dependencies(pkg) == {}

    rep = pk.finalize_unity(pkg, "com.x.y", "X.2dPack")
    assert rep["namespace"] == "X._2dPack"
    code = (pkg / "Runtime" / "Scripts" / "NpcData.cs").read_text(encoding="utf-8")
    assert "namespace X._2dPack\n{" in code and "namespace AssetStudio\n" not in code
    assert "namespace X._2dPack" in (pkg / "Editor" / "AssetStudioLodGroup.cs").read_text(encoding="utf-8")
    assert json.loads((pkg / "Runtime" / "X.2dPack.asmdef").read_text(encoding="utf-8"))["rootNamespace"] == "X._2dPack"

    d.mkdir()
    (d / "Meadow.asset").write_text(f"m_Script: {{fileID: 11500000, guid: {RULE_TILE_GUID}, type: 3}}\n",
                                    encoding="utf-8")
    (d / "knight.glb").write_bytes(b"glTF")
    (d / "Lit.mat").write_text("m_Shader: {fileID: 4800000, guid: 933532a4fcc9baf4fa0491de14d08ed7, type: 3}\n",
                               encoding="utf-8")
    (d / "btn.png.meta").write_text("  spriteBorder: {x: 0, y: 0, z: 0, w: 0}\n", encoding="utf-8")
    deps = pk.unity_dependencies(pkg)
    assert deps == {"com.unity.2d.tilemap.extras": "4.1.0", "com.unity.cloud.gltfast": "6.14.1",
                    "com.unity.render-pipelines.universal": "17.0.3"}
    (d / "btn.png.meta").write_text("  spriteBorder: {x: 12, y: 0, z: 12, w: 0}\n", encoding="utf-8")
    assert pk.unity_dependencies(pkg)["com.unity.ugui"] == "2.0.0"
