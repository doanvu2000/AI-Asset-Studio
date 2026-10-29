"""Runs a one-click setup in a background thread: downloads resume, files are checked by size + sha256."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from studio_core.bootstrap.plan import (comfy_ready, detect_gpu, download_path, free_bytes, manifest, model_path,
                                        model_present, pack_models, workflows_dir)
from studio_core.config import Settings, save_overrides

CHUNK = 1 << 20
GIB = 1024 ** 3
RETRIES = 30
MODEL_THREADS = 2
USER_AGENT = "AI-Asset-Studio-setup"
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


class Cancelled(Exception):
    pass


class SetupError(Exception):
    pass


@dataclass
class Item:
    id: str
    label: str
    state: str = "pending"
    detail: str = ""
    done: int = 0
    total: int = 0


@dataclass
class SetupRequest:
    tools_dir: Path
    models_dir: Path
    packs: list[str]
    blender: bool = True
    claude: bool = True


@dataclass
class SetupJob:
    settings: Settings
    req: SetupRequest
    items: list[Item] = field(default_factory=list)
    state: str = "running"
    error: str | None = None
    started: float = field(default_factory=time.time)
    finished: float | None = None
    _cancel: threading.Event = field(default_factory=threading.Event)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _procs: list[subprocess.Popen] = field(default_factory=list)
    _rate: list[tuple[float, int]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.comfy_dir = self.comfy_python = self.blender_exe = self.claude_exe = None
        self.models = [m for m in pack_models(self.req.packs) if not model_present(self.req.models_dir, m)]
        self.pack_files: dict[str, list[dict[str, Any]]] = {}
        taken: set[str] = set()
        self.items = [Item("comfyui", "ComfyUI + Python + PyTorch")]
        for p in manifest()["packs"]:
            if not (p["required"] or p["id"] in self.req.packs):
                continue
            names = {m["name"] for m in pack_models([p["id"]])} - taken
            files = [m for m in self.models if m["name"] in names]
            taken |= names
            if files:
                self.pack_files[p["id"]] = files
                self.items.append(Item(f"pack:{p['id']}", f"Gói {p['label']} ({len(files)} file)",
                                       total=sum(m["size"] for m in files)))
        self._active: dict[str, dict[str, list[int]]] = {}
        if self.req.blender:
            self.items.append(Item("blender", f"Blender {manifest()['blender']['version']}",
                                   total=manifest()["blender"]["size"]))
        if self.req.claude:
            self.items.append(Item("claude", "Claude Code"))
        self.items.append(Item("finish", "Lưu đường dẫn"))

    def item(self, id: str) -> Item:
        return next(i for i in self.items if i.id == id)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            done = sum(i.done for i in self.items)
            total = sum(i.total for i in self.items)
            now = time.time()
            window = [(t, b) for t, b in self._rate if now - t <= 10]
            speed = (window[-1][1] - window[0][1]) / max(1e-3, window[-1][0] - window[0][0]) if len(window) > 1 else 0
            return {"state": self.state, "error": self.error, "started": self.started, "finished": self.finished,
                    "items": [asdict(i) for i in self.items], "bytes_done": done, "bytes_total": total,
                    "speed": round(speed), "tools_dir": str(self.req.tools_dir), "models_dir": str(self.req.models_dir)}

    def cancel(self) -> None:
        self._cancel.set()
        for p in list(self._procs):
            if p.poll() is None:
                p.kill()

    def start(self) -> None:
        threading.Thread(target=self._run, name="studio-setup", daemon=True).start()

    def _progress(self, item: Item, delta: int) -> None:
        if self._cancel.is_set():
            raise Cancelled
        with self._lock:
            item.done += delta
            now = time.time()
            if not self._rate or now - self._rate[-1][0] >= 0.5:
                self._rate.append((now, sum(i.done for i in self.items)))
                self._rate[:] = self._rate[-40:]

    def _set(self, item: Item, state: str | None = None, detail: str | None = None) -> None:
        with self._lock:
            if state:
                item.state = state
            if detail is not None:
                item.detail = detail[-200:]

    def _run(self) -> None:
        self.req.tools_dir.mkdir(parents=True, exist_ok=True)
        self.req.models_dir.mkdir(parents=True, exist_ok=True)
        steps: list[tuple[str, Callable[[Item], None]]] = [
            ("comfyui", self._comfyui), *((f"pack:{p}", self._models) for p in self.pack_files),
            ("blender", self._blender), ("claude", self._claude), ("finish", self._finish)]
        failed = []
        try:
            self._check_space()
            for id, fn in steps:
                if not any(i.id == id for i in self.items):
                    continue
                item = self.item(id)
                self._set(item, "running")
                try:
                    fn(item)
                    if item.state == "running":
                        self._set(item, "done")
                except Cancelled:
                    raise
                except Exception as e:
                    self._set(item, "error", str(e) or type(e).__name__)
                    failed.append(item.label)
            self.state = "error" if failed else "done"
            self.error = f"Lỗi ở: {', '.join(failed)}" if failed else None
        except Cancelled:
            self.state = "cancelled"
            for i in self.items:
                if i.state in ("pending", "running"):
                    self._set(i, "cancelled")
        except Exception as e:
            self.state, self.error = "error", str(e)
        self.finished = time.time()

    def _check_space(self) -> None:
        need_models = sum(m["size"] for m in self.models)
        need_tools = manifest()["comfyui"]["disk_gb"] * 1024 ** 3 if not comfy_ready(self.settings) else 0
        if self.req.blender:
            need_tools += manifest()["blender"]["size"] * 3
        same = _drive(self.req.tools_dir) == _drive(self.req.models_dir)
        checks = [(self.req.models_dir, need_models + (need_tools if same else 0))]
        if not same:
            checks.append((self.req.tools_dir, need_tools))
        for path, need in checks:
            free = free_bytes(path)
            if free is not None and free < need:
                raise SetupError(f"Ổ {_drive(path)} còn {free / 1024 ** 3:.0f} GB, cần {need / 1024 ** 3:.0f} GB.")

    def _comfyui(self, item: Item) -> None:
        if comfy_ready(self.settings):
            self._set(item, "skipped", f"Đã có ở {self.settings.comfy_dir}")
            self.comfy_dir, self.comfy_python = self.settings.comfy_dir, self.settings.comfy_python
            return
        if problem := detect_gpu().problem():
            raise SetupError(problem)
        cfg = manifest()["comfyui"]
        tools = self.req.tools_dir
        uv = self._uv(item)
        comfy = tools / "ComfyUI"
        if not (comfy / "main.py").is_file():
            self._set(item, detail=f"Tải ComfyUI {cfg['version']}")
            archive = tools / "downloads" / f"ComfyUI-{cfg['version']}.zip"
            self._fetch(cfg["url"], archive, None)
            self._set(item, detail="Giải nén ComfyUI")
            _extract_single_root(archive, comfy)
        python = comfy / ".venv" / "Scripts" / "python.exe"
        env = {**os.environ, "UV_PYTHON_INSTALL_DIR": str(tools / "uv" / "python"),
               "UV_CACHE_DIR": str(tools / "uv" / "cache"), "UV_LINK_MODE": "copy"}
        if not python.is_file():
            self._exec(item, [uv, "venv", "--python", cfg["python"], "--python-preference", "only-managed",
                              str(comfy / ".venv")], env, "Cài Python")
        self._exec(item, [uv, "pip", "install", "--python", str(python), *cfg["torch"], "--index-url",
                          cfg["torch_index"]], env, "Cài PyTorch (≈ 3 GB)")
        self._exec(item, [uv, "pip", "install", "--python", str(python), "-r", str(comfy / "requirements.txt")], env,
                   "Cài thư viện ComfyUI")
        self._exec(item, [uv, "cache", "clean"], env, "Dọn cache")
        self.comfy_dir, self.comfy_python = comfy, python

    def _uv(self, item: Item) -> str:
        cfg = manifest()["uv"]
        exe = self.req.tools_dir / "uv" / "uv.exe"
        if not exe.is_file():
            self._set(item, detail="Tải uv")
            archive = self.req.tools_dir / "downloads" / f"uv-{cfg['version']}.zip"
            self._fetch(cfg["url"], archive, None, cfg["size"], cfg["sha256"])
            with zipfile.ZipFile(archive) as z:
                z.extractall(exe.parent)
            if not exe.is_file():
                found = next(exe.parent.rglob("uv.exe"), None)
                if not found:
                    raise SetupError("Không thấy uv.exe trong gói tải về")
                return str(found)
        return str(exe)

    def _models(self, item: Item) -> None:
        files = self.pack_files[item.id.split(":", 1)[1]]
        active = self._active.setdefault(item.id, {})

        def one(m: dict[str, Any]) -> None:
            with self._lock:
                active[m["name"]] = [0, m["size"]]

            def tick(n: int) -> None:
                self._progress(item, n)
                with self._lock:
                    active[m["name"]][0] += n
                    item.detail = "  ·  ".join(f"{k} {v[0] / GIB:.1f}/{v[1] / GIB:.1f} GB" for k, v in active.items())

            try:
                dest = download_path(self.req.models_dir, m)
                download(m["url"], dest, tick, m["size"], m["sha256"])
                if m.get("archive"):
                    extract_archive(dest, model_path(self.req.models_dir, m))
            finally:
                with self._lock:
                    active.pop(m["name"], None)
        with ThreadPoolExecutor(MODEL_THREADS) as pool:
            for f in [pool.submit(one, m) for m in files]:
                f.result()
        self._set(item, detail="Đủ model")

    def _blender(self, item: Item) -> None:
        cfg = manifest()["blender"]
        exe = self.req.tools_dir / cfg["exe"]
        if self.settings.blender_exe.is_file() or exe.is_file():
            self.blender_exe = self.settings.blender_exe if self.settings.blender_exe.is_file() else exe
            self._set(item, "skipped", f"Đã có ở {self.blender_exe}")
            item.total = 0
            return
        archive = self.req.tools_dir / "downloads" / cfg["url"].rsplit("/", 1)[1]
        self._fetch(cfg["url"], archive, item, cfg["size"], cfg["sha256"])
        self._set(item, detail="Giải nén Blender")
        with zipfile.ZipFile(archive) as z:
            z.extractall(self.req.tools_dir)
        archive.unlink(missing_ok=True)
        self.blender_exe = exe

    def _claude(self, item: Item) -> None:
        if self.settings.claude_exe.is_file():
            self.claude_exe = self.settings.claude_exe
            self._set(item, "skipped", "Đã có")
            return
        script = f"irm {manifest()['claude']['installer']} | iex"
        self._exec(item, ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                   dict(os.environ), "Cài Claude Code")
        exe = Path.home() / ".local" / "bin" / "claude.exe"
        if not exe.is_file():
            raise SetupError("Cài xong nhưng không thấy claude.exe")
        self.claude_exe = exe
        self._set(item, detail="Bấm “Đăng nhập Claude” để dùng các bước Claude")

    def _finish(self, item: Item) -> None:
        updates = {"comfy_models_dir": str(self.req.models_dir), "comfy_workflows_dir": str(workflows_dir())}
        for key in ("comfy_dir", "comfy_python", "blender_exe", "claude_exe"):
            if getattr(self, key):
                updates[key] = str(getattr(self, key))
        save_overrides(self.settings, updates)
        shutil.rmtree(self.req.tools_dir / "downloads", ignore_errors=True)

    def _exec(self, item: Item, argv: list[str], env: dict[str, str], what: str) -> None:
        self._set(item, detail=what)
        p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             env=env, creationflags=_NO_WINDOW, text=True, encoding="utf-8", errors="replace")
        self._procs.append(p)
        tail: list[str] = []
        try:
            for line in p.stdout:  # type: ignore[union-attr]
                if line.strip():
                    tail = (tail + [line.strip()])[-20:]
                    self._set(item, detail=f"{what}: {line.strip()}")
            p.wait()
        finally:
            self._procs.remove(p)
        if self._cancel.is_set():
            raise Cancelled
        if p.returncode != 0:
            raise SetupError(f"{what} lỗi (mã {p.returncode}): {' | '.join(tail[-3:])}")
        self._set(item, detail=what)

    def _fetch(self, url: str, dest: Path, item: Item | None, size: int | None = None,
               sha256: str | None = None) -> None:
        download(url, dest, (lambda n: self._progress(item, n)) if item else self._check_cancel, size, sha256)

    def _check_cancel(self, _: int = 0) -> None:
        if self._cancel.is_set():
            raise Cancelled


def download(url: str, dest: Path, progress: Callable[[int], None], size: int | None = None,
             sha256: str | None = None) -> None:
    """Resumable: a `.part` file keeps what arrived; progress also counts bytes already on disk once."""
    if dest.is_file() and (size is None or dest.stat().st_size == size):
        progress(dest.stat().st_size)
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.is_file() else 0
    if size and have > size:
        part.unlink()
        have = 0
    progress(have)
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                       **({"Range": f"bytes={have}-"} if have else {})})
            with urllib.request.urlopen(req, timeout=60) as r:
                if have and r.status != 206:
                    progress(-have)
                    have = 0
                with open(part, "ab" if have else "wb") as f:
                    while chunk := r.read(CHUNK):
                        f.write(chunk)
                        have += len(chunk)
                        progress(len(chunk))
            break
        except urllib.error.HTTPError as e:
            if e.code == 416 and size and have == size:
                break
            if e.code in (401, 403, 404) or attempt == RETRIES - 1:
                raise SetupError(f"Tải {dest.name} lỗi HTTP {e.code}") from e
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            if attempt == RETRIES - 1:
                raise SetupError(f"Tải {dest.name} lỗi: {e}") from e
        time.sleep(min(30, 2 ** attempt))
    if size and have != size:
        raise SetupError(f"{dest.name}: tải được {have} / {size} byte")
    if sha256 and file_sha256(part) != sha256:
        part.unlink()
        progress(-have)
        raise SetupError(f"{dest.name}: sai mã kiểm tra, đã xoá để tải lại")
    os.replace(part, dest)


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(8 * CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _drive(path: Path) -> str:
    return path.resolve().drive or path.resolve().anchor


def extract_archive(archive: Path, target: Path) -> None:
    """A .tar.bz2 / .tar.gz / .zip whose single top folder becomes `target`; the archive is removed afterwards."""
    tmp = target.with_name(target.name + ".unpack")
    shutil.rmtree(tmp, ignore_errors=True)
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as z:
            z.extractall(tmp)
    else:
        with tarfile.open(archive) as t:
            t.extractall(tmp, filter="data")
    roots = list(tmp.iterdir())
    src = roots[0] if len(roots) == 1 and roots[0].is_dir() else tmp
    if target.exists():
        shutil.rmtree(target)
    shutil.move(str(src), str(target))
    shutil.rmtree(tmp, ignore_errors=True)
    archive.unlink(missing_ok=True)


def _extract_single_root(archive: Path, target: Path) -> None:
    tmp = target.with_name(target.name + ".unzip")
    shutil.rmtree(tmp, ignore_errors=True)
    with zipfile.ZipFile(archive) as z:
        z.extractall(tmp)
    roots = list(tmp.iterdir())
    src = roots[0] if len(roots) == 1 and roots[0].is_dir() else tmp
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    shutil.move(str(src), str(target))
    shutil.rmtree(tmp, ignore_errors=True)
