from __future__ import annotations

from pathlib import Path

from studio_core.export.godot_copy import copy_godot_export
from studio_core.export.npc import GODOT_SCRIPT, GODOT_SCRIPT_FILE


def _export(root: Path, name: str, code: str = GODOT_SCRIPT, uid: str = "") -> Path:
    d = root / "exports" / name
    (d / "NPC").mkdir(parents=True)
    (d / GODOT_SCRIPT_FILE).write_text(code, encoding="utf-8")
    (d / "NPC" / "face.png").write_bytes(b"png")
    u = f' uid="{uid}"' if uid else ""
    (d / f"{name}_npc.tres").write_text(
        '[gd_resource type="Resource" script_class="NpcData" load_steps=3 format=3]\n\n'
        f'[ext_resource type="Script"{u} path="{GODOT_SCRIPT_FILE}" id="1_script"]\n'
        '[ext_resource type="Texture2D" path="NPC/face.png" id="p0"]\n\n'
        '[resource]\nscript = ExtResource("1_script")\n', encoding="utf-8")
    (d / "Scenes").mkdir()
    (d / "Scenes" / "talk.tscn").write_text(
        f'[gd_scene format=3]\n\n[ext_resource type="Script" path="../{GODOT_SCRIPT_FILE}" id="1"]\n'
        '[ext_resource type="Resource" path="../x_npc.tres" id="2"]\n', encoding="utf-8")
    return d


def _project(tmp: Path) -> Path:
    root = tmp / "Gáme Proj"
    root.mkdir()
    (root / "project.godot").write_text("config_version=5\n", encoding="utf-8")
    return root


def test_first_export_copies_everything(tmp_path: Path) -> None:
    root = _project(tmp_path)
    a = root / "asset_studio" / "Ann"
    assert copy_godot_export(_export(tmp_path, "Ann"), a, root) == []
    assert (a / GODOT_SCRIPT_FILE).is_file()
    assert f'path="{GODOT_SCRIPT_FILE}"' in (a / "Ann_npc.tres").read_text(encoding="utf-8")


def test_second_export_reuses_script_and_repoints_resources(tmp_path: Path) -> None:
    root = _project(tmp_path)
    a = root / "asset_studio" / "Ann"
    b = root / "asset_studio" / "Bob"
    copy_godot_export(_export(tmp_path, "Ann", "class_name NpcData\n# v1\n"), a, root)

    reused = copy_godot_export(_export(tmp_path, "Bob", "class_name NpcData\n# v2\n", uid="uid://bob"), b, root)
    shared = f"res://asset_studio/Ann/{GODOT_SCRIPT_FILE}"
    assert reused == [shared]
    assert not (b / GODOT_SCRIPT_FILE).exists()
    assert (a / GODOT_SCRIPT_FILE).read_text(encoding="utf-8").endswith("# v2\n")
    tres = (b / "Bob_npc.tres").read_text(encoding="utf-8")
    assert f'[ext_resource type="Script" path="{shared}" id="1_script"]' in tres
    assert 'path="NPC/face.png"' in tres and "uid=" not in tres
    tscn = (b / "Scenes" / "talk.tscn").read_text(encoding="utf-8")
    assert f'path="{shared}" id="1"' in tscn and 'path="../x_npc.tres"' in tscn
    assert len(list(root.rglob(GODOT_SCRIPT_FILE))) == 1


def test_reexport_of_owner_keeps_its_copy_and_drops_stale_duplicate(tmp_path: Path) -> None:
    root = _project(tmp_path)
    a = root / "Ann"
    b = root / "Bob"
    copy_godot_export(_export(tmp_path, "Ann"), a, root)
    b.mkdir()
    (b / GODOT_SCRIPT_FILE).write_text("stale", encoding="utf-8")
    (b / f"{GODOT_SCRIPT_FILE}.uid").write_text("uid://stale", encoding="utf-8")

    copy_godot_export(_export(tmp_path, "Bob"), b, root)
    assert not (b / GODOT_SCRIPT_FILE).exists() and not (b / f"{GODOT_SCRIPT_FILE}.uid").exists()
    assert len(list(root.rglob(GODOT_SCRIPT_FILE))) == 1

    copy_godot_export(_export(tmp_path / "again", "Ann"), a, root)
    assert (a / GODOT_SCRIPT_FILE).is_file()


def test_same_file_other_class_or_ignored_folder_is_not_reused(tmp_path: Path) -> None:
    root = _project(tmp_path)
    (root / "mine").mkdir()
    (root / "mine" / GODOT_SCRIPT_FILE).write_text("class_name MyNpc\n", encoding="utf-8")
    (root / ".godot" / "x").mkdir(parents=True)
    (root / ".godot" / "x" / GODOT_SCRIPT_FILE).write_text(GODOT_SCRIPT, encoding="utf-8")
    (root / "raw").mkdir()
    (root / "raw" / ".gdignore").write_text("", encoding="utf-8")
    (root / "raw" / GODOT_SCRIPT_FILE).write_text(GODOT_SCRIPT, encoding="utf-8")

    b = root / "Bob"
    assert copy_godot_export(_export(tmp_path, "Bob"), b, root) == []
    assert (b / GODOT_SCRIPT_FILE).is_file()
    assert (root / "mine" / GODOT_SCRIPT_FILE).read_text(encoding="utf-8") == "class_name MyNpc\n"
