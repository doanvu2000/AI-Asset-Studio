import json
import struct
import zlib
from pathlib import Path

import httpx
import numpy as np
import pytest
from PIL import Image

from studio_core.export import audit as au
from studio_core.export.audit_media import _fbx_ascii, _fbx_binary, audio_seconds, mesh_tris
from studio_core.export.restyle import file_hash
from test_api_basic import AUTH, StubEngine, TOKEN, _settings

GUIDS = {"hero": "a" * 32, "hero_copy": "b" * 32, "bg": "c" * 32, "orphan": "d" * 32, "icon": "e" * 32}


def _meta(guid: str, max_size: int = 2048, alpha_usage: int = 1, sprite_mode: int = 1, crlf: bool = False) -> str:
    text = f"""fileFormatVersion: 2
guid: {guid}
TextureImporter:
  serializedVersion: 13
  mipmaps:
    enableMipMap: 0
  textureFormat: 1
  maxTextureSize: {max_size}
  textureSettings:
    serializedVersion: 2
    filterMode: 1
    wrapU: 1
  spriteMode: {sprite_mode}
  spriteBorder: {{x: 0, y: 0, z: 0, w: 0}}
  alphaUsage: {alpha_usage}
  textureType: 8
  textureShape: 1
  platformSettings:
  - serializedVersion: 4
    buildTarget: DefaultTexturePlatform
    maxTextureSize: {max_size}
    textureFormat: -1
    textureCompression: 0
    crunchedCompression: 0
    overridden: 0
  - serializedVersion: 4
    buildTarget: Standalone
    maxTextureSize: {max_size}
    textureFormat: -1
    textureCompression: 1
    overridden: 0
  spriteSheet:
    serializedVersion: 2
    sprites: []
  userData:
"""
    return text.replace("\n", "\r\n") if crlf else text


def _noise(w: int, h: int, seed: int, alpha: bool = True) -> Image.Image:
    rng = np.random.default_rng(seed)
    a = rng.integers(0, 255, (h // 8, w // 8, 4), dtype=np.uint8)
    a[..., 3] = 255
    img = Image.fromarray(a, "RGBA").resize((w, h), Image.Resampling.NEAREST)
    if alpha:
        arr = np.asarray(img).copy()
        arr[: h // 4, :, 3] = 0
        img = Image.fromarray(arr, "RGBA")
    return img


def _unity(tmp: Path) -> Path:
    proj = tmp / "Game"
    (proj / "ProjectSettings").mkdir(parents=True)
    (proj / "ProjectSettings" / "EditorBuildSettings.asset").write_text(
        f"%YAML 1.1\nEditorBuildSettings:\n  m_Scenes:\n  - enabled: 1\n    guid: {'f' * 32}\n", encoding="utf-8")
    art = proj / "Assets" / "Art"
    art.mkdir(parents=True)
    _noise(4096, 4096, 1).save(art / "hero.png")
    _noise(4096, 4096, 1).save(art / "hero_copy.png")
    _noise(512, 384, 2, alpha=False).convert("RGBA").save(art / "bg.png")
    _noise(64, 64, 3).save(art / "orphan.png")
    _noise(1024, 1024, 4).save(art / "icon.png")
    for name, g in GUIDS.items():
        (art / f"{name}.png.meta").write_text(_meta(g, crlf=name == "bg"), encoding="utf-8", newline="")
    ui = f"""%YAML 1.1
--- !u!1 &100
GameObject:
  m_Name: Icon
--- !u!224 &101
RectTransform:
  m_GameObject: {{fileID: 100}}
  m_LocalScale: {{x: 1, y: 1, z: 1}}
  m_AnchorMin: {{x: 0.5, y: 0.5}}
  m_AnchorMax: {{x: 0.5, y: 0.5}}
  m_SizeDelta: {{x: 96, y: 96}}
--- !u!114 &102
MonoBehaviour:
  m_GameObject: {{fileID: 100}}
  m_Sprite: {{fileID: 21300000, guid: {GUIDS['icon']}, type: 3}}
--- !u!114 &103
MonoBehaviour:
  m_GameObject: {{fileID: 104}}
  m_Sprite: {{fileID: 21300000, guid: {GUIDS['hero']}, type: 3}}
  m_Bg: {{fileID: 21300000, guid: {GUIDS['bg']}, type: 3}}
  m_Copy: {{fileID: 21300000, guid: {GUIDS['hero_copy']}, type: 3}}
"""
    (proj / "Assets" / "Main.unity").write_text(ui, encoding="utf-8")
    (proj / "Assets" / "Main.unity.meta").write_text(f"fileFormatVersion: 2\nguid: {'f' * 32}\n", encoding="utf-8")
    (art / "rock.obj").write_text("v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\n" + "f 1 2 3 4\n" * 9000, encoding="utf-8")
    (art / "rock.obj.meta").write_text(f"fileFormatVersion: 2\nguid: {'9' * 32}\nModelImporter:\n", encoding="utf-8")
    return proj


def _godot(tmp: Path) -> Path:
    proj = tmp / "gd"
    (proj / "art").mkdir(parents=True)
    (proj / "project.godot").write_text('[application]\nrun/main_scene="res://main.tscn"\n', encoding="utf-8")
    _noise(4096, 2048, 5).save(proj / "art" / "big.png")
    _noise(32, 32, 6).save(proj / "art" / "unused.png")
    for n in ("big", "unused"):
        (proj / "art" / f"{n}.png.import").write_text(
            f'[remap]\n\nimporter="texture"\ntype="CompressedTexture2D"\nuid="uid://{n}123"\n\n[params]\n\n'
            "compress/mode=0\ncompress/high_quality=false\nmipmaps/generate=false\nprocess/size_limit=0\n",
            encoding="utf-8")
    (proj / "main.tscn").write_text('[gd_scene format=3 uid="uid://main1"]\n\n'
                                    '[ext_resource type="Texture2D" path="art/big.png" id="1_t"]\n\n'
                                    '[node name="Main" type="Node2D"]\n\n'
                                    '[node name="S" type="Sprite2D" parent="."]\ntexture = ExtResource("1_t")\n'
                                    'scale = Vector2(0.1, 0.1)\n', encoding="utf-8")
    return proj


def _row(rep: dict, rel: str) -> dict:
    return next(t for t in rep["textures"] if t["rel"] == rel)


def test_unity_scan_finds_budget_display_dups_orphans_and_meshes(tmp_path: Path):
    proj = _unity(tmp_path)
    rep = au.scan(str(proj / "Assets"), tri_budget=10000)
    assert rep["engine"] == "unity" and Path(rep["project"]) == proj.resolve()
    hero = _row(rep, "Assets/Art/hero.png")
    assert hero["kind"] == "sprite" and any(i["code"] == "mobile" for i in hero["issues"])
    assert hero["gpu_after"] < hero["gpu_before"]
    icon = _row(rep, "Assets/Art/icon.png")
    disp = next(i for i in icon["issues"] if i["code"] == "display")
    assert icon["display"] == [96, 96] and disp["fix"] == {"type": "max_size", "rel": "Assets/Art/icon.png",
                                                          "max_size": 256}
    bg = _row(rep, "Assets/Art/bg.png")
    assert bg["alpha_used"] is False and any(i["code"] == "alpha" and i.get("fix") for i in bg["issues"])
    assert rep["duplicates"][0]["kind"] == "exact" and set(rep["duplicates"][0]["files"]) == {
        "Assets/Art/hero.png", "Assets/Art/hero_copy.png"}
    assert {u["rel"] for u in rep["unreferenced"]} == {"Assets/Art/orphan.png", "Assets/Art/rock.obj"}
    assert rep["meshes"][0]["tris"] == 18000 and rep["meshes"][0]["over"]
    assert rep["summary"]["gpu_after"] < rep["summary"]["gpu_before"]


def test_unity_fix_dry_run_apply_and_undo(tmp_path: Path):
    proj = _unity(tmp_path)
    rep = au.scan(str(proj))
    backups = tmp_path / "bk"
    png_before = {p.name: file_hash(p) for p in (proj / "Assets" / "Art").glob("*.png")}
    acts = [i["fix"] for t in rep["textures"] for i in t["issues"] if i.get("fix")]
    plan = au.apply_fixes(rep, acts, backups, dry_run=True)
    assert plan["written"] == 0 and plan["gpu_after"] < plan["gpu_before"] and not backups.exists()
    metas = {p.name: p.read_bytes() for p in (proj / "Assets" / "Art").glob("*.meta")}
    doc = au.apply_fixes(rep, acts, backups, dry_run=False)
    assert doc["written"] >= 4 and doc["backup_id"]
    hero = (proj / "Assets/Art/hero.png.meta").read_text(encoding="utf-8")
    assert "buildTarget: Android" in hero and "buildTarget: iPhone" in hero and f"guid: {GUIDS['hero']}" in hero
    assert "maxTextureSize: 256" in (proj / "Assets/Art/icon.png.meta").read_text(encoding="utf-8")
    bg = (proj / "Assets/Art/bg.png.meta").read_bytes()
    assert b"\r\n  alphaUsage: 0\r\n" in bg and b"\n  alphaUsage: 0\n" not in bg.replace(b"\r\n", b"\r")
    assert {p.name: file_hash(p) for p in (proj / "Assets" / "Art").glob("*.png")} == png_before
    again = au.apply_fixes(rep, acts, backups, dry_run=False)
    assert again["written"] == 0 and all("changed since the audit" in f["why"] for f in again["files"])
    (proj / "Assets/Art/icon.png.meta").write_text("edited by hand", encoding="utf-8")
    res = au.undo(backups, doc["backup_id"])
    assert "Assets/Art/icon.png" in {c["rel"] for c in res["conflicts"]}
    assert {p.name: p.read_bytes() for p in (proj / "Assets" / "Art").glob("*.meta")
            if p.name != "icon.png.meta"} == {k: v for k, v in metas.items() if k != "icon.png.meta"}
    with pytest.raises(au.AuditError):
        au.undo(backups, doc["backup_id"])
    assert au.list_backups(backups, str(proj.resolve()))[0]["undone_at"]


def test_fix_refuses_unknown_and_escaping_paths(tmp_path: Path):
    proj = _unity(tmp_path)
    rep = au.scan(str(proj))
    (tmp_path / "outside.png.meta").write_text("x", encoding="utf-8")
    rep["textures"].append({**_row(rep, "Assets/Art/hero.png"), "rel": "../outside.png"})
    acts = [{"type": "max_size", "rel": "../outside.png", "max_size": 256},
            {"type": "max_size", "rel": "Assets/Art/nope.png", "max_size": 256},
            {"type": "rm -rf", "rel": "Assets/Art/hero.png"},
            {"type": "max_size", "rel": "Assets/Art/hero.png", "max_size": 300}]
    doc = au.apply_fixes(rep, acts, tmp_path / "bk", dry_run=False)
    assert doc["written"] == 0 and len(doc["files"]) == 4
    with pytest.raises(au.AuditError):
        au.safe_importer(proj, "C:/Windows/x.png", "unity")
    with pytest.raises(au.AuditError):
        au.scan(str(tmp_path / "nowhere"))
    (tmp_path / "plain").mkdir()
    with pytest.raises(au.AuditError):
        au.scan(str(tmp_path / "plain"))


def test_godot_scan_fix_keeps_uid(tmp_path: Path):
    proj = _godot(tmp_path)
    rep = au.scan(str(proj))
    assert rep["engine"] == "godot"
    big = _row(rep, "art/big.png")
    assert big["display"] == [410, 205] and {i["code"] for i in big["issues"]} >= {"mobile", "display"}
    assert [u["rel"] for u in rep["unreferenced"]] == ["art/unused.png"]
    doc = au.apply_fixes(rep, [i["fix"] for i in big["issues"] if i.get("fix")], tmp_path / "bk", dry_run=False)
    text = (proj / "art/big.png.import").read_text(encoding="utf-8")
    assert doc["written"] == 1 and 'uid="uid://big123"' in text
    assert "compress/mode=2" in text and "process/size_limit=1024" in text and text.count("process/size_limit") == 1
    drop = au.apply_fixes(au.scan(str(proj)), [{"type": "drop_alpha", "rel": "art/big.png"}], tmp_path / "bk", False)
    assert drop["written"] == 0 and "Godot" in drop["files"][0]["why"]


def test_atlas_goes_outside_the_project(tmp_path: Path):
    proj = _godot(tmp_path)
    rep = au.scan(str(proj))
    out = au.build_atlas(rep, ["art/big.png", "art/unused.png"], "ui", tmp_path / "atlas", max_size=8192)
    assert Path(out["folder"]).is_relative_to(tmp_path / "atlas") and "ui.png" in out["files"]
    assert not list(proj.rglob("ui.png"))


def _fbx_node(name: bytes, props: bytes, nprops: int, children: bytes, start: int) -> bytes:
    head = 13 + len(name)
    end = start + head + len(props) + len(children) + (13 if children else 0)
    body = props + children + (b"\0" * 13 if children else b"")
    return struct.pack("<IIIB", end, nprops, len(props), len(name)) + name + body


def test_mesh_and_audio_readers(tmp_path: Path):
    idx = [0, 1, 2, -4, 0, 2, -4]
    raw = struct.pack(f"<{len(idx)}i", *idx)
    comp = zlib.compress(raw)
    prop = b"i" + struct.pack("<III", len(idx), 1, len(comp)) + comp
    start = 27
    geo_head = 13 + len(b"Geometry")
    pvi = _fbx_node(b"PolygonVertexIndex", prop, 1, b"", start + geo_head)
    geo = _fbx_node(b"Geometry", b"", 0, pvi, start)
    data = b"Kaydara FBX Binary  \x00\x1a\x00" + struct.pack("<I", 7400) + geo + b"\0" * 13
    assert _fbx_binary(data) == 3
    assert _fbx_ascii("PolygonVertexIndex: *7 {\n a: 0,1,2,-4,0,2,-4\n}") == 3
    gltf = {"asset": {"version": "2.0"}, "accessors": [{"count": 36}, {"count": 8}],
            "meshes": [{"primitives": [{"indices": 0, "attributes": {"POSITION": 1}}]}]}
    (tmp_path / "m.gltf").write_text(json.dumps(gltf), encoding="utf-8")
    assert mesh_tris(tmp_path / "m.gltf") == 12
    import wave

    with wave.open(str(tmp_path / "a.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\0\0" * 16000)
    assert audio_seconds(tmp_path / "a.wav") == pytest.approx(2.0)


async def test_api_scan_fix_undo(tmp_path: Path):
    from studio_core.app import build_app

    proj = _unity(tmp_path)
    app = build_app(_settings(tmp_path / "data"), TOKEN, engine=StubEngine(), load_plugins=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/audit/fix", json={"path": str(proj), "actions": [{"type": "drop_alpha",
                                                                                     "rel": "x.png"}]}, headers=AUTH)
            assert r.status_code == 409
            r = await c.post("/api/audit", json={"path": str(tmp_path)}, headers=AUTH)
            assert r.status_code == 422
            rep = (await c.post("/api/audit", json={"path": str(proj / "Assets")}, headers=AUTH)).json()
            assert rep["summary"]["textures"] == 5
            acts = [{"type": "max_size", "rel": "Assets/Art/icon.png", "max_size": 256}]
            dry = (await c.post("/api/audit/fix", json={"path": str(proj), "actions": acts}, headers=AUTH)).json()
            assert dry["dry_run"] and dry["written"] == 0
            done = (await c.post("/api/audit/fix", json={"path": str(proj), "actions": acts, "dry_run": False},
                                 headers=AUTH)).json()
            assert done["written"] == 1 and _row(done["report"], "Assets/Art/icon.png")["settings"]["max"] == 256
            rows = (await c.get("/api/audit/backups", params={"path": str(proj)}, headers=AUTH)).json()["backups"]
            assert rows[0]["backup_id"] == done["backup_id"]
            u = (await c.post("/api/audit/undo", json={"backup_id": done["backup_id"]}, headers=AUTH)).json()
            assert u["restored"] == ["Assets/Art/icon.png"]
            last = (await c.get("/api/audit/last", params={"path": str(proj)}, headers=AUTH)).json()
            assert last["type"] == "asset-studio/audit"


async def test_mcp_audit_tools(tmp_path: Path):
    from mcp import Client
    from test_p4b_mcp import make_data_dir, payload, spawn_core, stop_core

    from studio_core.mcp.link import CoreLink
    from studio_core.mcp.server import build_server

    proj = _godot(tmp_path)
    data = make_data_dir(tmp_path / "core")
    proc, _ = spawn_core(data)
    try:
        async with Client(build_server(CoreLink(data))) as c:
            rep = payload(await c.call_tool("audit_project", {"path": str(proj)}))
            assert rep["engine"] == "godot" and rep["fixes"] and rep["unreferenced"][0]["rel"] == "art/unused.png"
            dry = payload(await c.call_tool("audit_fix", {"path": str(proj), "actions": rep["fixes"]}))
            assert dry["dry_run"] and dry["written"] == 0
            done = payload(await c.call_tool("audit_fix", {"path": str(proj), "actions": rep["fixes"],
                                                           "dry_run": False}))
            assert done["written"] == 2 and set(done["report"]) == {"summary"}
            listed = payload(await c.call_tool("audit_undo", {"path": str(proj)}))
            assert listed["backups"][0]["backup_id"] == done["backup_id"]
            undone = payload(await c.call_tool("audit_undo", {"backup_id": done["backup_id"]}))
            assert sorted(undone["restored"]) == ["art/big.png", "art/unused.png"]
    finally:
        stop_core(proc, data)
