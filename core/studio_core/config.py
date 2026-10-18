import json
import os
from pathlib import Path

from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]


def _default_codex_exe() -> str:
    appdata = os.environ.get("APPDATA", "")
    return str(Path(appdata) / "npm/node_modules/@openai/codex/node_modules/@openai/codex-win32-x64/vendor"
               / "x86_64-pc-windows-msvc/bin/codex.exe")


class Settings(BaseModel):
    data_dir: Path = ROOT / "data"
    recipes_dir: Path = Path(__file__).parent / "recipes"

    comfy_dir: Path = Path(r"L:\AI\setup\comfyUI")
    comfy_python: Path = Path(r"L:\AI\setup\comfyUI\.venv\Scripts\python.exe")
    comfy_models_dir: Path = Path(r"F:\work\AI\comfyUI_models")
    comfy_workflows_dir: Path = Path(r"L:\AI\setup\workflows")
    comfy_port: int = 8188
    comfy_autostart: bool = False
    comfy_boot_timeout_s: float = 120

    blender_exe: Path = Path(r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe")
    codex_exe: Path = Field(default_factory=lambda: Path(_default_codex_exe()))
    codex_home: Path = Path.home() / ".codex"

    lane_limits: dict[str, int] = {"gpu": 1, "blender": 1, "cloud": 3, "cpu": 4}
    allowed_origins: list[str] = ["app://studio", "http://localhost:5173", "http://127.0.0.1:5173"]

    @property
    def db_path(self) -> Path:
        return self.data_dir / "studio.db"

    @property
    def blobs_dir(self) -> Path:
        return self.data_dir / "blobs"

    @property
    def thumbs_dir(self) -> Path:
        return self.data_dir / "thumbs"

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"


def load_settings(data_dir: Path | None = None) -> Settings:
    """Defaults < data_dir/settings.json < STUDIO_* env (data dir only)."""
    base = Settings()
    data = Path(data_dir or os.environ.get("STUDIO_DATA_DIR") or base.data_dir)
    overrides = {}
    f = data / "settings.json"
    if f.exists():
        overrides = json.loads(f.read_text(encoding="utf-8"))
    s = Settings(**{**overrides, "data_dir": data})
    for d in (s.data_dir, s.blobs_dir, s.thumbs_dir, s.jobs_dir):
        d.mkdir(parents=True, exist_ok=True)
    return s
