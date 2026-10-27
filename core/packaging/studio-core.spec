# PyInstaller onedir build of the core. Run from core/: uv run pyinstaller packaging/studio-core.spec --noconfirm
# Output: core/dist/studio-core/studio-core.exe (+ _internal/), copied by electron-builder to resources/core.
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

CORE = Path(SPECPATH).parent
PKG = CORE / "studio_core"
ICON = CORE.parent / "app" / "resources" / "icon.ico"

# worker.py / cleanup_math.py / skin_math.py run inside Blender's own Python, so they must exist as real .py files.
datas = [
    (str(PKG / "recipes" / "*.yaml"), "studio_core/recipes"),
    (str(PKG / "workflows" / "*.json"), "studio_core/workflows"),
    (str(PKG / "bootstrap" / "manifest.json"), "studio_core/bootstrap"),
    (str(PKG / "adapters" / "blender" / "worker.py"), "studio_core/adapters/blender"),
    (str(PKG / "adapters" / "blender" / "cleanup_math.py"), "studio_core/adapters/blender"),
    (str(PKG / "adapters" / "blender" / "skin_math.py"), "studio_core/adapters/blender"),
    # Op cache keys hash these sources (ops/executors.py: ops_source_hash).
    (str(PKG / "ops" / "*.py"), "studio_core/ops"),
]

# app.py loads plugins and the engine via importlib, which static analysis cannot follow.
hiddenimports = [
    *collect_submodules("studio_core", filter=lambda name: not name.endswith(".blender.worker")),
    *collect_submodules("uvicorn"),
    *collect_submodules("mcp", filter=lambda name: not name.startswith("mcp.cli")),
    *collect_submodules("claude_agent_sdk"),
    "win32api",
    "win32con",
    "win32event",
    "win32job",
    "win32process",
    "pywintypes",
]

excludes = [
    "torch", "torchvision", "torchaudio", "tensorflow", "jax", "scipy", "pandas", "matplotlib", "sklearn",
    "cv2", "IPython", "jupyter", "notebook", "tkinter", "_tkinter", "pytest", "_pytest", "PyInstaller",
    "bpy", "mathutils", "bmesh", "addon_utils", "mcp.cli", "typer",
]

a = Analysis(
    [str(CORE / "packaging" / "entry.py")],
    pathex=[str(CORE)],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)

# claude_agent_sdk ships a ~260 MB claude.exe; the adapter falls back to `claude` on PATH instead.
a.datas = [d for d in a.datas if "claude_agent_sdk" not in d[0] or "_bundled" not in d[0]]
a.binaries = [b for b in a.binaries if "claude_agent_sdk" not in b[0] or "_bundled" not in b[0]]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="studio-core",
    console=True,
    icon=str(ICON) if ICON.is_file() else None,
    upx=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="studio-core",
    upx=False,
)
