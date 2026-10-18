"""Locations that differ between a source checkout and the PyInstaller onedir build."""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "AI Asset Studio"
PACKAGE_DIR = Path(__file__).resolve().parent


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def package_dir() -> Path:
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "studio_core"
    return PACKAGE_DIR


def default_data_dir() -> Path:
    if is_frozen():
        # The install dir (Program Files or LocalAppData\Programs) is replaced on update; user data must not live there.
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(appdata) / APP_NAME / "data"
    return PACKAGE_DIR.parents[1] / "data"


def recipes_dir() -> Path:
    return package_dir() / "recipes"


def blender_scripts_dir() -> Path:
    """Shipped as plain .py files even when frozen: Blender's own Python runs them."""
    return package_dir() / "adapters" / "blender"


def core_command(python: str | None = None) -> list[str]:
    """argv prefix that starts the core CLI (`… serve`, `… mcp`)."""
    if is_frozen():
        return [sys.executable]
    return [python or sys.executable, "-m", "studio_core"]


def core_cwd() -> Path:
    return Path(sys.executable).parent if is_frozen() else PACKAGE_DIR.parent
