import json
import os
import shutil
from pathlib import Path

from pydantic import BaseModel, Field

from studio_core.paths import default_data_dir, recipes_dir

ROOT = Path(__file__).resolve().parents[2]
TOOL_KEYS = ("comfy_dir", "comfy_python", "comfy_models_dir", "comfy_workflows_dir", "blender_exe", "codex_exe",
             "claude_exe")


def _default_codex_exe() -> str:
    appdata = os.environ.get("APPDATA", "")
    return str(Path(appdata) / "npm/node_modules/@openai/codex/node_modules/@openai/codex-win32-x64/vendor"
               / "x86_64-pc-windows-msvc/bin/codex.exe")


def _default_claude_exe() -> str:
    """Native claude.exe only: the SDK refuses npm's claude.CMD shim (cmd.exe argument injection)."""
    candidates = [Path(os.environ.get("APPDATA", "")) / "npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe",
                  Path.home() / ".local/bin/claude.exe"]
    found = shutil.which("claude.exe")
    if found:
        candidates.append(Path(found))
    return str(next((c for c in candidates if c.is_file()), candidates[0]))


class Settings(BaseModel):
    data_dir: Path = Field(default_factory=default_data_dir)
    recipes_dir: Path = recipes_dir()

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
    claude_exe: Path = Field(default_factory=lambda: Path(_default_claude_exe()))

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

    @property
    def user_recipes_dir(self) -> Path:
        return self.data_dir / "recipes"

    @property
    def presets_path(self) -> Path:
        return self.data_dir / "presets.json"

    @property
    def settings_path(self) -> Path:
        return self.data_dir / "settings.json"


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


def save_overrides(settings: Settings, updates: dict[str, str]) -> dict[str, object]:
    """Merges `updates` into data_dir/settings.json (other keys kept) and applies them to the live object."""
    f = settings.settings_path
    current = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    current.update(updates)
    checked = Settings(**{**current, "data_dir": settings.data_dir})
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, f)
    for k in updates:
        setattr(settings, k, getattr(checked, k))
    return current
