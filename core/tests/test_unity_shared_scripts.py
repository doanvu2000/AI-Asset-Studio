from __future__ import annotations

from pathlib import Path

from studio_core.export.npc import SCRIPT_GUID, script_meta
from studio_core.export.unity import copy_unity_export


def _export(root: Path, name: str, code: str) -> Path:
    d = root / "exports" / name
    (d / "Scripts").mkdir(parents=True)
    (d / "Scripts" / "NpcData.cs").write_text(code, encoding="utf-8")
    (d / "Scripts" / "NpcData.cs.meta").write_text(script_meta(SCRIPT_GUID), encoding="utf-8")
    (d / f"{name}_npc.asset").write_text(f"m_Script: {{fileID: 11500000, guid: {SCRIPT_GUID}, type: 3}}\n",
                                         encoding="utf-8")
    (d / "Own.cs").write_text("class Own {}", encoding="utf-8")
    (d / "Own.cs.meta").write_text("fileFormatVersion: 2\nguid: " + name[0] * 32 + "\n", encoding="utf-8")
    return d


def test_second_export_reuses_shared_script_and_refreshes_it(tmp_path: Path) -> None:
    assets = tmp_path / "Proj" / "Assets"
    a = assets / "AssetStudio" / "Ann"
    b = assets / "AssetStudio" / "Bob"
    assert copy_unity_export(_export(tmp_path, "Ann", "v1"), a, assets) == []
    assert (a / "Scripts" / "NpcData.cs").read_text(encoding="utf-8") == "v1"

    reused = copy_unity_export(_export(tmp_path, "Bob", "v2"), b, assets)
    assert reused == ["Assets/AssetStudio/Ann/Scripts/NpcData.cs"]
    assert not (b / "Scripts" / "NpcData.cs").exists() and not (b / "Scripts" / "NpcData.cs.meta").exists()
    assert (a / "Scripts" / "NpcData.cs").read_text(encoding="utf-8") == "v2"
    assert (b / "Bob_npc.asset").is_file() and (b / "Own.cs.meta").is_file()


def test_reexport_of_owner_keeps_its_copy_and_drops_stale_duplicate(tmp_path: Path) -> None:
    assets = tmp_path / "Proj" / "Assets"
    a = assets / "Ann"
    b = assets / "Bob"
    copy_unity_export(_export(tmp_path, "Ann", "v1"), a, assets)
    (b / "Scripts").mkdir(parents=True)
    (b / "Scripts" / "NpcData.cs").write_text("stale", encoding="utf-8")
    (b / "Scripts" / "NpcData.cs.meta").write_text(script_meta(SCRIPT_GUID), encoding="utf-8")

    copy_unity_export(_export(tmp_path, "Bob", "v2"), b, assets)
    assert not (b / "Scripts" / "NpcData.cs").exists()
    assert len(list(assets.rglob("NpcData.cs"))) == 1
