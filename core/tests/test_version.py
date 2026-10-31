import json
import tomllib
from pathlib import Path

from studio_core import __version__

ROOT = Path(__file__).resolve().parents[2]


def test_core_version_matches_app():
    app = json.loads((ROOT / "app" / "package.json").read_text(encoding="utf-8"))
    assert __version__ == app["version"]


def test_pyproject_version_is_dynamic():
    proj = tomllib.loads((ROOT / "core" / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert "version" not in proj and "version" in proj["dynamic"]
