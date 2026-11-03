"""Machine probe (GPU, RAM, CPU, disk) and the green / yellow / red fit of recipes and flows against it."""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from studio_core.bootstrap import plan
from studio_core.config import Settings

MB = 1024 ** 2
CACHE_S = 30
# Cards report a bit under their nominal size (16 GB -> ~15.9, 12 GB -> ~11.9, 64 GB RAM -> ~63.8).
SLACK = 0.95
ORDER = {"green": 0, "yellow": 1, "red": 2}
NO_GPU = "Không thấy GPU NVIDIA — chức năng này cần card NVIDIA."


@dataclass
class GpuInfo:
    name: str
    driver: float | None
    vram_total_mb: int
    vram_free_mb: int | None
    ok: bool
    problem: str | None = None


@dataclass
class Hardware:
    gpu: GpuInfo | None
    ram_total_mb: int
    ram_free_mb: int
    cpu_threads: int
    disk_free_mb: int | None
    tier: str

    def to_json(self) -> dict[str, Any]:
        g = self.gpu
        gpu = None if g is None else {
            "name": g.name, "vram_total_mb": g.vram_total_mb, "vram_free_mb": g.vram_free_mb,
            "driver": f"{g.driver:g}" if g.driver is not None else None, "ok": g.ok}
        return {"gpu": gpu, "ram_total_mb": self.ram_total_mb, "ram_free_mb": self.ram_free_mb,
                "cpu_threads": self.cpu_threads, "disk_free_mb": self.disk_free_mb, "tier": self.tier}

    @property
    def vram_gb(self) -> float:
        return self.gpu.vram_total_mb / 1024 if self.gpu else 0.0

    @property
    def ram_gb(self) -> float:
        return self.ram_total_mb / 1024


def _query_gpu() -> list[list[str]]:
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total,memory.free",
                            "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=15, creationflags=flags, check=False)
    except (OSError, subprocess.SubprocessError):
        return []
    if r.returncode != 0:
        return []
    return [[p.strip() for p in line.split(",")] for line in r.stdout.splitlines() if line.strip()]


def detect_gpu_info() -> GpuInfo | None:
    """The card with the most VRAM; only NVIDIA cards are probed because ComfyUI needs CUDA."""
    best: GpuInfo | None = None
    for parts in _query_gpu():
        if len(parts) < 4:
            continue
        try:
            total = int(float(parts[2]))
        except ValueError:
            continue
        try:
            driver: float | None = float(".".join(parts[1].split(".")[:2]))
        except ValueError:
            driver = None
        try:
            free: int | None = int(float(parts[3]))
        except ValueError:
            free = None
        problem = plan.Gpu(parts[0], driver, total / 1024).problem()
        if best is None or total > best.vram_total_mb:
            best = GpuInfo(parts[0], driver, total, free, problem is None, problem)
    return best


class _MemStatus(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def _ram_windows() -> tuple[int, int] | None:
    st = _MemStatus()
    st.dwLength = ctypes.sizeof(_MemStatus)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):  # type: ignore[attr-defined]
        return None
    return st.ullTotalPhys // MB, st.ullAvailPhys // MB


def parse_meminfo(text: str) -> tuple[int, int] | None:
    kb: dict[str, int] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        try:
            kb[key] = int(rest.split()[0])
        except (ValueError, IndexError):
            continue
    if "MemTotal" not in kb:
        return None
    return kb["MemTotal"] // 1024, kb.get("MemAvailable", kb.get("MemFree", 0)) // 1024


def read_ram() -> tuple[int, int]:
    got: tuple[int, int] | None = None
    if sys.platform == "win32":
        try:
            got = _ram_windows()
        except (OSError, AttributeError):
            got = None
    if got is None:
        try:
            got = parse_meminfo(Path("/proc/meminfo").read_text(encoding="utf-8"))
        except OSError:
            got = None
    return got or (0, 0)


def tier_of(gpu: GpuInfo | None, ram_total_mb: int) -> str:
    vram = gpu.vram_total_mb / 1024 if gpu and gpu.ok else 0.0
    if vram >= 15.5 and ram_total_mb / 1024 >= 30:
        return "high"
    return "mid" if vram >= 7.5 else "low"


_cache: tuple[float, Hardware] | None = None


def probe(models_dir: Path | None = None, *, force: bool = False) -> Hardware:
    """Disk is read on every call (it differs per models dir); the slow probes are cached for CACHE_S seconds."""
    global _cache
    now = time.monotonic()
    if _cache is None or force or now - _cache[0] > CACHE_S:
        gpu = detect_gpu_info()
        total, free = read_ram()
        _cache = (now, Hardware(gpu, total, free, os.cpu_count() or 1, None, tier_of(gpu, total)))
    hw = _cache[1]
    disk = plan.free_bytes(models_dir) if models_dir else None
    return Hardware(hw.gpu, hw.ram_total_mb, hw.ram_free_mb, hw.cpu_threads,
                    disk // MB if disk is not None else None, hw.tier)


@dataclass
class PackState:
    installed: bool
    size_mb: int
    missing_mb: int


@dataclass
class Env:
    hw: Hardware
    packs: dict[str, PackState]
    comfy: bool
    blender: bool
    claude: bool


def pack_states(settings: Settings, models_dir: Path) -> dict[str, PackState]:
    base = plan.pack_models([])
    out: dict[str, PackState] = {}
    for p in plan.manifest()["packs"]:
        ms = base if p["required"] else [m for m in plan.pack_models([p["id"]]) if m not in base]
        missing = [m for m in ms if not plan.model_present(models_dir, m)]
        nodes = [n for n in p.get("custom_nodes") or [] if not plan.custom_node_present(settings.comfy_dir, n)]
        c_size, c_missing = plan.component_sizes(p, models_dir)
        out[p["id"]] = PackState(not missing and not nodes and not c_missing,
                                 (sum(m["size"] for m in ms) + c_size) // MB,
                                 (sum(m["size"] for m in missing) + c_missing) // MB)
    return out


def environment(settings: Settings, hw: Hardware | None = None) -> Env:
    models_dir = plan.default_dirs(settings)[1]
    return Env(hw or probe(models_dir), pack_states(settings, models_dir), plan.comfy_ready(settings),
               settings.blender_exe.is_file(), settings.claude_exe.is_file())


@dataclass
class Needs:
    packs: set[str] = field(default_factory=set)
    lanes: set[str] = field(default_factory=set)


def _hw_of(pack_id: str) -> dict[str, float]:
    for p in plan.manifest()["packs"]:
        if p["id"] == pack_id:
            return p.get("hw") or {}
    return {}


def _gb(v: float) -> int | float:
    return int(v) if float(v).is_integer() else round(v, 1)


class _Verdict:
    def __init__(self) -> None:
        self.level = "green"
        self.reasons: list[str] = []

    def add(self, level: str, reason: str) -> None:
        if reason not in self.reasons:
            self.reasons.append(reason)
        if ORDER[level] > ORDER[self.level]:
            self.level = level


def _gpu_packs(needs: Needs) -> list[str]:
    """A GPU step that names no workflow still runs on the base card requirements."""
    packs = sorted(needs.packs)
    if "gpu" in needs.lanes and not any(_hw_of(p).get("min_vram_gb") for p in packs):
        packs.append("base")
    return packs


def _check_pack(v: _Verdict, pack_id: str, hw: Hardware) -> tuple[float, float]:
    spec = _hw_of(pack_id)
    min_v, rec_v, min_r = spec.get("min_vram_gb", 0), spec.get("rec_vram_gb", 0), spec.get("min_ram_gb", 0)
    if not rec_v:
        return 0, 0
    if hw.gpu is None:
        v.add("red", NO_GPU)
        return rec_v, min_r
    if not hw.gpu.ok:
        v.add("red", hw.gpu.problem or "GPU NVIDIA chưa dùng được.")
        return rec_v, min_r
    ram_ok = hw.ram_gb >= min_r * SLACK
    have_v, have_r = _gb(round(hw.vram_gb)), _gb(round(hw.ram_gb))
    if hw.vram_gb < rec_v * SLACK:
        what = f"Cần ~{_gb(rec_v)} GB VRAM, máy có {have_v} GB"
        if not ram_ok and hw.vram_gb < min_v * SLACK:
            v.add("red", f"{what} và RAM {have_r} GB không đủ offload (cần ~{_gb(min_r)} GB).")
        elif not ram_ok:
            v.add("red", f"{what}; cần ~{_gb(min_r)} GB RAM để offload, máy có {have_r} GB.")
        else:
            v.add("yellow", f"{what} — sẽ chạy chậm (offload sang RAM).")
    elif not ram_ok:
        v.add("yellow", f"Cần ~{_gb(min_r)} GB RAM, máy có {have_r} GB.")
    return rec_v, min_r


def fit_needs(needs: Needs, env: Env) -> dict[str, Any]:
    v = _Verdict()
    vram = ram = 0.0
    for pid in _gpu_packs(needs):
        a, b = _check_pack(v, pid, env.hw)
        vram, ram = max(vram, a), max(ram, b)
    if "gpu" in needs.lanes and env.hw.gpu is None:
        v.add("red", NO_GPU)
    elif "gpu" in needs.lanes and env.hw.gpu and not env.hw.gpu.ok:
        v.add("red", env.hw.gpu.problem or "GPU NVIDIA chưa dùng được.")

    labels = {p["id"]: p["label"] for p in plan.manifest()["packs"]}
    missing = sorted(p for p in needs.packs if p in env.packs and not env.packs[p].installed)
    if missing:
        mb = sum(env.packs[p].missing_mb for p in missing)
        names = ", ".join(labels.get(p, p) for p in missing)
        v.add("yellow", f"Chưa cài gói {names} (~{_gb(round(mb / 1024, 1))} GB) — cài trong Thiết lập.")
        if env.hw.disk_free_mb is not None and mb > env.hw.disk_free_mb:
            v.add("red", f"Ổ chứa model còn {_gb(round(env.hw.disk_free_mb / 1024, 1))} GB, "
                         f"không đủ ~{_gb(round(mb / 1024, 1))} GB để cài.")
    if any(_hw_of(p).get("rec_vram_gb") for p in needs.packs) and not env.comfy:
        v.add("yellow", "Chưa cài ComfyUI — cài trong Thiết lập.")
    if "blender" in needs.lanes and not env.blender:
        v.add("yellow", "Chưa cấu hình Blender (đường dẫn blender.exe).")
    if "cloud" in needs.lanes and not env.claude:
        v.add("yellow", "Chưa cài / đăng nhập Claude Code.")
    return {"level": v.level, "reasons": v.reasons,
            "needs": {"vram_gb": _gb(vram), "ram_gb": _gb(ram), "lanes": sorted(needs.lanes),
                      "packs": sorted(needs.packs)},
            "missing_packs": missing}


def merge_fits(fits: list[dict[str, Any]]) -> dict[str, Any]:
    level = "green"
    reasons: list[str] = []
    lanes: set[str] = set()
    packs: set[str] = set()
    missing: set[str] = set()
    vram = ram = 0
    for f in fits:
        if ORDER[f["level"]] > ORDER[level]:
            level = f["level"]
        reasons += [r for r in f["reasons"] if r not in reasons]
        n = f["needs"]
        vram, ram = max(vram, n["vram_gb"]), max(ram, n["ram_gb"])
        lanes |= set(n["lanes"])
        packs |= set(n["packs"])
        missing |= set(f["missing_packs"])
    return {"level": level, "reasons": reasons,
            "needs": {"vram_gb": vram, "ram_gb": ram, "lanes": sorted(lanes), "packs": sorted(packs)},
            "missing_packs": sorted(missing)}


_needs_cache: dict[tuple[str, str], Needs] = {}


def recipe_needs(recipe: Any, text: str, lane_of: Callable[[str], str | None]) -> Needs:
    key = (recipe.id, recipe.hash)
    if key not in _needs_cache:
        lanes = {lane for s in recipe.steps if (lane := lane_of(s.kind)) and lane not in ("none", "cpu")}
        _needs_cache[key] = Needs(plan.recipe_packs(text), lanes)
    return _needs_cache[key]


def fit_all(env: Env, recipes: dict[str, Needs], flows: dict[str, list[str]]) -> dict[str, Any]:
    rec = {rid: fit_needs(n, env) for rid, n in recipes.items()}
    fl = {fid: merge_fits([rec[r] for r in nodes if r in rec]) for fid, nodes in flows.items()}
    return {"hardware": env.hw.to_json(), "recipes": rec, "flows": fl, "recommend": recommend(env, rec)}


def recommend(env: Env, rec: dict[str, Any]) -> dict[str, Any]:
    hw = env.hw
    packs = []
    for p in plan.manifest()["packs"]:
        st = env.packs.get(p["id"], PackState(False, 0, 0))
        v = _Verdict()
        _check_pack(v, p["id"], hw)
        level, reason = v.level, v.reasons[0] if v.reasons else "Hợp với máy này."
        if not st.installed and hw.disk_free_mb is not None and st.missing_mb > hw.disk_free_mb:
            level, reason = "red", f"Ổ chứa model không đủ ~{_gb(round(st.missing_mb / 1024, 1))} GB để cài."
        packs.append({"id": p["id"], "label": p["label"], "level": level, "reason": reason,
                      "installed": st.installed, "size_mb": st.size_mb})
    notes: list[str] = []
    if hw.gpu is None:
        notes.append("Không thấy GPU NVIDIA: chỉ chạy được các chức năng CPU (tìm ảnh giống, giọng đọc).")
    elif not hw.gpu.ok:
        notes.append(hw.gpu.problem or "GPU NVIDIA chưa dùng được.")
    elif hw.vram_gb < 8:
        notes.append(f"VRAM {_gb(round(hw.vram_gb))} GB thấp: model lớn sẽ offload sang RAM và chạy chậm.")
    if hw.gpu and hw.gpu.ok and hw.ram_gb < 32:
        notes.append(f"RAM {_gb(round(hw.ram_gb))} GB: các gói cần ~64 GB RAM (VFX, tách layer, skybox) sẽ không chạy.")
    if hw.disk_free_mb is not None and hw.disk_free_mb < 50 * 1024:
        notes.append(f"Ổ chứa model chỉ còn {_gb(round(hw.disk_free_mb / 1024))} GB, nên chọn ít gói.")
    n_red = sum(1 for r in rec.values() if r["level"] == "red")
    if n_red:
        notes.append(f"{n_red} chức năng chưa phù hợp với máy này (đèn đỏ).")
    return {"packs": packs, "lane_limits": {"cpu": max(1, min(8, hw.cpu_threads // 4))}, "notes": notes}
