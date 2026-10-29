"""Open an engine export in the real engine (Godot / Unity batchmode), report import errors and render a screenshot."""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from studio_core.paths import package_dir

PREVIEW_ENGINES = ("godot", "unity")
SIZE = 640
GODOT_IMPORT_TIMEOUT_S = 300.0
GODOT_RENDER_TIMEOUT_S = 120.0
UNITY_TIMEOUT_S = 900.0
MAX_ISSUES = 60
LOG_TAIL_LINES = 80
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_PROGRESS = re.compile(r"^\[\s*\d+% \]|^\[ DONE \]")


def scripts_dir() -> Path:
    return package_dir() / "export" / "engine_scripts"


@dataclass
class ProcResult:
    code: int | None
    output: str
    stopped: str | None = None


@dataclass
class PreviewResult:
    ok: bool
    png: Path | None = None
    target: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    note: str = ""
    log_tail: str = ""
    stopped: str | None = None


class PreviewCancelled(Exception):
    pass


def availability(settings: Any) -> dict[str, dict[str, Any]]:
    godot, unity, project = (Path(getattr(settings, k, "") or "") for k in ("godot_exe", "unity_exe", "unity_project"))

    def shown(p: Path) -> str:
        return "" if str(p) in ("", ".") else str(p)

    out: dict[str, dict[str, Any]] = {
        "godot": {"available": godot.is_file(), "path": shown(godot), "reason": None if godot.is_file() else
                  "Chưa có Godot (cài đặt › Công cụ › godot_exe)"},
    }
    reason = None
    if not unity.is_file():
        reason = "Chưa có Unity.exe (cài đặt › Công cụ › unity_exe)"
    elif not shown(project) or not (project / "Assets").is_dir() or not (project / "ProjectSettings").is_dir():
        reason = "Thư mục project Unity không hợp lệ (cần Assets/ và ProjectSettings/)"
    out["unity"] = {"available": reason is None, "path": shown(unity), "project": shown(project), "reason": reason}
    return out


async def run_proc(args: list[str], cwd: Path | None, env: dict[str, str] | None, timeout_s: float,
                   cancel: asyncio.Event) -> ProcResult:
    """Runs one engine process inside a kill-on-close job so cancel/timeout also ends the GUI child of Godot's
    console wrapper and Unity's helper processes."""
    from studio_core.adapters._winproc import spawn_in_job

    proc, job = await spawn_in_job(args, cwd=cwd, env=env, stdout=asyncio.subprocess.PIPE,
                                   stderr=asyncio.subprocess.STDOUT, stdin=asyncio.subprocess.DEVNULL)
    reader = asyncio.create_task(proc.stdout.read())  # type: ignore[union-attr]
    waiter = asyncio.create_task(proc.wait())
    stopper = asyncio.create_task(cancel.wait())
    stopped = None
    try:
        done, _ = await asyncio.wait({waiter, stopper}, timeout=timeout_s, return_when=asyncio.FIRST_COMPLETED)
        if waiter not in done:
            stopped = "cancelled" if stopper in done else "timeout"
            job.terminate()
            await proc.wait()
    finally:
        stopper.cancel()
        job.close()
    out = await reader
    return ProcResult(proc.returncode, out.decode("utf-8", "replace"), stopped)


def _dedupe(lines: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for line in lines:
        seen.setdefault(line.strip()[:500], None)
    return list(seen)[:MAX_ISSUES]


def _tail(text: str, n: int = LOG_TAIL_LINES) -> str:
    lines = [ln for ln in _ANSI.sub("", text).splitlines() if ln.strip() and not _PROGRESS.match(ln.strip())]
    return "\n".join(lines[-n:])


def godot_issues(text: str) -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    for line in _ANSI.sub("", text).splitlines():
        s = line.strip()
        if s.startswith(("ERROR:", "SCRIPT ERROR:", "USER ERROR:", "PREVIEW_FAIL")):
            errors.append(s)
        elif s.startswith(("WARNING:", "USER WARNING:")):
            warnings.append(s)
    return _dedupe(errors), _dedupe(warnings)


def godot_target(folder: Path) -> Path | None:
    """The file to open: a scene first, then a model, a resource, a texture."""
    def pick(paths: list[Path]) -> Path | None:
        return min(paths, key=lambda p: (p.stem.endswith("_original"), len(p.relative_to(folder).parts),
                                         len(p.stem), p.name)) if paths else None

    for pattern in ("*.tscn", "*.glb", "*.gltf", "*.tres", "*.png"):
        hit = pick([p for p in folder.rglob(pattern) if p.is_file()])
        if hit:
            return hit
    return None


def _godot_project(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "project.godot").write_text(
        'config_version=5\n\n[application]\n\nconfig/name="AssetStudio Preview"\n\n[display]\n\n'
        f"window/size/viewport_width={SIZE}\nwindow/size/viewport_height={SIZE}\n\n[rendering]\n\n"
        'renderer/rendering_method="gl_compatibility"\n', encoding="utf-8")
    shutil.copy2(scripts_dir() / "preview.gd", root / "_asset_studio_preview.gd")


async def preview_godot(export_dir: Path, work: Path, exe: Path, cancel: asyncio.Event,
                        frames: int = 12) -> PreviewResult:
    project = work / "project"
    name = export_dir.name
    await asyncio.to_thread(_godot_project, project)
    await asyncio.to_thread(shutil.copytree, export_dir, project / name)
    imp = await run_proc([str(exe), "--headless", "--path", str(project), "--import"], project, None,
                         GODOT_IMPORT_TIMEOUT_S, cancel)
    log = imp.output
    if imp.stopped:
        return PreviewResult(False, errors=[f"Godot import {imp.stopped}"], log_tail=_tail(log), stopped=imp.stopped)
    target = godot_target(project / name)
    errors, warnings = godot_issues(log)
    if target is None:
        return PreviewResult(not errors, errors=errors, warnings=warnings, note="Không có gì để hiển thị",
                             log_tail=_tail(log))
    res = "res://" + target.relative_to(project).as_posix()
    png = work / "preview.png"
    # A real window is needed: --headless has only the dummy renderer, which cannot read back the viewport.
    run = await run_proc([str(exe), "--path", str(project), "--rendering-driver", "opengl3",
                          "--rendering-method", "gl_compatibility", "--audio-driver", "Dummy",
                          "--resolution", f"{SIZE}x{SIZE}", "--position", "-32000,-32000",
                          "--script", "res://_asset_studio_preview.gd", "--", res, str(png), str(frames)],
                         project, None, GODOT_RENDER_TIMEOUT_S, cancel)
    log += "\n" + run.output
    errors, warnings = godot_issues(log)
    ok = run.stopped is None and run.code == 0 and png.is_file()
    if run.stopped:
        errors.append(f"Godot render {run.stopped}")
    elif not ok and not errors:
        errors.append(f"Godot exited with code {run.code}")
    return PreviewResult(ok and not any(e.startswith("PREVIEW_FAIL") for e in errors), png if png.is_file() else None,
                         target.relative_to(project / name).as_posix(), errors, warnings, log_tail=_tail(log),
                         stopped=run.stopped)


UNITY_FOLDER = "AssetStudio"
UNITY_SCRIPT_DIR = "AssetStudioPreview/Editor"
_UNITY_FATAL = ("another Unity instance is running", "Scripts have compiler errors", "error CS",
                "Aborting batchmode due to failure", "executeMethod class")


def unity_log_issues(text: str, asset_dir: str) -> tuple[list[str], list[str]]:
    """Lines about our folder plus project-level failures; the rest of Unity's log is mostly noise."""
    errors, warnings = [], []
    needle = asset_dir.lower()
    for line in text.splitlines():
        s = line.strip()
        low = s.lower()
        if any(f.lower() in low for f in _UNITY_FATAL):
            errors.append(s)
        elif needle in low and ("error" in low or "fail" in low or "exception" in low):
            errors.append(s)
        elif needle in low and "warning" in low:
            warnings.append(s)
    return _dedupe(errors), _dedupe(warnings)


def project_locked(project: Path) -> bool:
    lock = project / "Temp" / "UnityLockfile"
    if not lock.exists():
        return False
    try:
        with lock.open("a"):
            return False
    except OSError:
        return True


def _unity_stage(export_dir: Path, project: Path) -> str:
    script_dir = project / "Assets" / UNITY_SCRIPT_DIR
    script_dir.mkdir(parents=True, exist_ok=True)
    src = (scripts_dir() / "AssetStudioPreview.cs").read_bytes()
    dst = script_dir / "AssetStudioPreview.cs"
    if not dst.is_file() or dst.read_bytes() != src:
        dst.write_bytes(src)
    target = project / "Assets" / UNITY_FOLDER / export_dir.name
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(export_dir, target)
    return f"Assets/{UNITY_FOLDER}/{export_dir.name}"


async def preview_unity(export_dir: Path, work: Path, exe: Path, project: Path, cancel: asyncio.Event
                        ) -> PreviewResult:
    if await asyncio.to_thread(project_locked, project):
        return PreviewResult(False, errors=[f"Project Unity đang mở trong Editor: {project} — đóng Unity rồi thử lại"])
    work.mkdir(parents=True, exist_ok=True)
    asset_dir = await asyncio.to_thread(_unity_stage, export_dir, project)
    png, report, log_file = work / "preview.png", work / "report.json", work / "unity.log"
    env = {**os.environ, "ASSET_STUDIO_PREVIEW_DIR": asset_dir, "ASSET_STUDIO_PREVIEW_OUT": str(png),
           "ASSET_STUDIO_PREVIEW_REPORT": str(report), "ASSET_STUDIO_PREVIEW_SIZE": str(SIZE)}
    # No -nographics: Camera.Render needs a graphics device. No -quit: the method calls EditorApplication.Exit.
    run = await run_proc([str(exe), "-batchmode", "-projectPath", str(project), "-executeMethod",
                          "AssetStudio.AssetStudioPreview.Run", "-logFile", str(log_file)], project, env,
                         UNITY_TIMEOUT_S, cancel)
    log = log_file.read_text(encoding="utf-8", errors="replace") if log_file.is_file() else run.output
    errors, warnings = unity_log_issues(log, asset_dir)
    rep: dict[str, Any] = {}
    if report.is_file():
        try:
            rep = json.loads(report.read_text(encoding="utf-8-sig"))
        except ValueError:
            rep = {}
    errors = _dedupe(errors + list(rep.get("errors") or []))
    warnings = _dedupe(warnings + list(rep.get("warnings") or []))
    if run.stopped:
        errors.append(f"Unity {run.stopped}")
    elif not rep:
        errors.append(f"Unity exited with code {run.code} without a report")
    target = str(rep.get("target") or "")
    if target.startswith(asset_dir + "/"):
        target = target[len(asset_dir) + 1:]
    return PreviewResult(bool(rep.get("ok")) and run.code == 0 and not run.stopped, png if png.is_file() else None,
                         target, errors, warnings, str(rep.get("note") or ""), _tail(log), run.stopped)


@dataclass
class PreviewJob:
    id: str
    step_id: str
    engine: str
    export_dir: str
    status: str = "queued"
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    target: str = ""
    image: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    note: str = ""
    log_tail: str = ""
    cancel: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    task: asyncio.Task[None] | None = field(default=None, repr=False)

    def view(self) -> dict[str, Any]:
        end = self.ended_at or time.time()
        return {"id": self.id, "step_id": self.step_id, "engine": self.engine, "export_dir": self.export_dir,
                "status": self.status, "started_at": self.started_at, "ended_at": self.ended_at,
                "seconds": round(end - self.started_at, 1), "target": self.target, "image": self.image,
                "errors": self.errors, "warnings": self.warnings, "note": self.note, "log_tail": self.log_tail}


class PreviewManager:
    """In-memory jobs; one process per engine at a time (a Unity project can only be opened once)."""

    KEEP = 30

    def __init__(self, settings: Any, blobs: Any) -> None:
        self.settings = settings
        self.blobs = blobs
        self.jobs: dict[str, PreviewJob] = {}
        self.locks = {e: asyncio.Lock() for e in PREVIEW_ENGINES}

    def get(self, job_id: str) -> PreviewJob | None:
        return self.jobs.get(job_id)

    def start(self, step_id: str, engine: str, export_dir: Path) -> PreviewJob:
        job = PreviewJob(uuid.uuid4().hex[:16], step_id, engine, str(export_dir))
        self.jobs[job.id] = job
        for old in list(self.jobs.values())[:-self.KEEP]:
            if old.status not in ("queued", "running"):
                self.jobs.pop(old.id, None)
        job.task = asyncio.create_task(self._run(job))
        return job

    def cancel(self, job: PreviewJob) -> None:
        job.cancel.set()

    async def _run(self, job: PreviewJob) -> None:
        work = Path(self.settings.data_dir) / "engine_preview" / job.id
        try:
            async with self.locks[job.engine]:
                if job.cancel.is_set():
                    raise PreviewCancelled
                job.status = "running"
                job.started_at = time.time()
                res = await self._engine(job, work)
            if res.stopped == "cancelled":
                raise PreviewCancelled
            if res.png is not None:
                ref = await self.blobs.put_file(res.png, name=f"{Path(job.export_dir).name}-{job.engine}-preview.png",
                                                mime="image/png")
                job.image = ref.model_dump(mode="json") if hasattr(ref, "model_dump") else dict(ref)
            job.target, job.errors, job.warnings = res.target, res.errors, res.warnings
            job.note, job.log_tail = res.note, res.log_tail
            job.status = "succeeded" if res.ok else "failed"
        except PreviewCancelled:
            job.status = "cancelled"
        except Exception as e:  # noqa: BLE001  (surface any engine/IO failure on the job instead of losing it)
            job.status = "failed"
            job.errors = [*job.errors, f"{type(e).__name__}: {e}"]
        finally:
            job.ended_at = time.time()
            await asyncio.to_thread(shutil.rmtree, work, True)

    async def _engine(self, job: PreviewJob, work: Path) -> PreviewResult:
        src = Path(job.export_dir)
        if job.engine == "godot":
            return await preview_godot(src, work, Path(self.settings.godot_exe), job.cancel)
        return await preview_unity(src, work, Path(self.settings.unity_exe), Path(self.settings.unity_project),
                                   job.cancel)
