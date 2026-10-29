import json
import os
import re
import shutil
import sys
from functools import cache
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

from studio_core.paths import default_data_dir, package_dir, recipes_dir

ROOT = Path(__file__).resolve().parents[2]
TOOL_KEYS = ("comfy_dir", "comfy_python", "comfy_models_dir", "comfy_workflows_dir", "blender_exe", "codex_exe",
             "claude_exe", "ffmpeg_exe", "godot_exe", "unity_exe", "unity_project")


def _default_codex_exe() -> str:
    appdata = os.environ.get("APPDATA", "")
    return str(Path(appdata) / "npm/node_modules/@openai/codex/node_modules/@openai/codex-win32-x64/vendor"
               / "x86_64-pc-windows-msvc/bin/codex.exe")


def _default_ffmpeg_exe() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    winget = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WinGet/Packages"
    hits = sorted(winget.glob("Gyan.FFmpeg*/ffmpeg-*/bin/ffmpeg.exe")) if winget.is_dir() else []
    return str(hits[-1]) if hits else "ffmpeg.exe"


def _default_claude_exe() -> str:
    """Native claude.exe only: the SDK refuses npm's claude.CMD shim (cmd.exe argument injection)."""
    candidates = [Path(os.environ.get("APPDATA", "")) / "npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe",
                  Path.home() / ".local/bin/claude.exe"]
    found = shutil.which("claude.exe")
    if found:
        candidates.append(Path(found))
    return str(next((c for c in candidates if c.is_file()), candidates[0]))


def fixed_drives() -> list[Path]:
    """Local fixed disks only: probing a disconnected network or empty card-reader drive can block for seconds."""
    if sys.platform != "win32":
        return []
    import ctypes

    k32 = ctypes.windll.kernel32
    mask = k32.GetLogicalDrives()
    roots = [f"{chr(65 + i)}:\\" for i in range(26) if mask >> i & 1]
    return [Path(r) for r in roots if k32.GetDriveTypeW(r) == 3]


def _version_key(p: Path) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", p.as_posix()))


def _newest(hits: list[Path]) -> str:
    files = [h for h in hits if h.is_file()]
    return str(max(files, key=_version_key)) if files else ""


@cache
def find_godot(roots: tuple[Path, ...]) -> str:
    env = os.environ
    dirs = [Path(d) for d in env.get("PATH", "").split(os.pathsep) if d]
    dirs += [Path(env.get("LOCALAPPDATA", "")) / "Programs/Godot", Path(env.get("ProgramFiles", "")) / "Godot",
             Path.home() / "scoop/apps/godot/current"]
    dirs += [r / sub for r in roots for sub in ("AI/tools/godot", "tools/godot", "Godot")]
    hits = [h for d in dirs if d.is_dir() for h in d.glob("Godot_v4*_console.exe")]
    winget = Path(env.get("LOCALAPPDATA", "")) / "Microsoft/WinGet/Packages"
    if winget.is_dir():
        hits += winget.glob("GodotEngine.GodotEngine*/Godot_v4*_console.exe")
    return _newest(hits)


@cache
def find_unity(roots: tuple[Path, ...]) -> str:
    editors = [Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Unity/Hub/Editor"]
    hub = Path(os.environ.get("APPDATA", "")) / "UnityHub/secondaryInstallPath.json"
    try:
        extra = json.loads(hub.read_text(encoding="utf-8"))
        if isinstance(extra, str) and extra:
            editors.append(Path(extra))
    except (OSError, ValueError):
        pass
    editors += [r / sub for r in roots for sub in ("Unity", "Program Files/Unity/Hub/Editor")]
    return _newest([h for d in editors if d.is_dir() for h in d.glob("*/Editor/Unity.exe")])


@cache
def find_unity_project(roots: tuple[Path, ...]) -> str:
    """A project the engine preview already used (it leaves Assets/AssetStudioPreview), up to two levels deep."""
    for r in roots:
        for pattern in ("*/Assets/AssetStudioPreview", "*/*/Assets/AssetStudioPreview"):
            try:
                hit = next((h.parents[1] for h in r.glob(pattern) if (h.parents[1] / "ProjectSettings").is_dir()),
                           None)
            except OSError:
                hit = None
            if hit:
                return str(hit)
    return ""


def _unset(p: Path) -> bool:
    return str(p) in ("", ".")


class Settings(BaseModel):
    data_dir: Path = Field(default_factory=default_data_dir)
    recipes_dir: Path = recipes_dir()

    comfy_dir: Path = Path(r"L:\AI\setup\comfyUI")
    comfy_python: Path = Path(r"L:\AI\setup\comfyUI\.venv\Scripts\python.exe")
    comfy_models_dir: Path = Path(r"F:\work\AI\comfyUI_models")
    comfy_workflows_dir: Path = package_dir() / "workflows"
    comfy_port: int = 8188
    comfy_autostart: bool = False
    comfy_boot_timeout_s: float = 120

    blender_exe: Path = Path(r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe")
    codex_exe: Path = Field(default_factory=lambda: Path(_default_codex_exe()))
    codex_home: Path = Path.home() / ".codex"
    claude_exe: Path = Field(default_factory=lambda: Path(_default_claude_exe()))
    ffmpeg_exe: Path = Field(default_factory=lambda: Path(_default_ffmpeg_exe()))
    # Empty = auto-detect (see _detect_engines); settings.json may list extra install roots in tool_search_roots.
    godot_exe: Path = Path("")
    unity_exe: Path = Path("")
    unity_project: Path = Path("")
    tool_search_roots: list[Path] = Field(default_factory=fixed_drives)

    lane_limits: dict[str, int] = {"gpu": 1, "blender": 1, "cloud": 3, "cpu": 4}
    allowed_origins: list[str] = ["app://studio", "http://localhost:5173", "http://127.0.0.1:5173"]

    @model_validator(mode="after")
    def _detect_engines(self) -> "Settings":
        roots = tuple(self.tool_search_roots)
        if _unset(self.godot_exe):
            self.godot_exe = Path(find_godot(roots))
        if _unset(self.unity_exe):
            self.unity_exe = Path(find_unity(roots))
        if _unset(self.unity_project):
            self.unity_project = Path(find_unity_project(roots))
        return self

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
    def app_exports_dir(self) -> Path:
        return self.data_dir / "exports" / "app"

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
