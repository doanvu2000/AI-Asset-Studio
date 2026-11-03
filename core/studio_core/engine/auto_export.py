"""Exports an approved version's step into the project's game folder, in the background, and reports on the bus."""
from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Any

from studio_core.contracts import StepStatus, VersionRow, VersionStatus
from studio_core.store.bundle import BundleError, export_engine

log = logging.getLogger("studio.auto_export")

EVENT = "project.auto_export"


class AutoExporter:
    def __init__(self, app_state: Any) -> None:
        self._st = app_state
        self._task: asyncio.Task[None] | None = None
        self._jobs: set[asyncio.Task[None]] = set()
        self._locks: dict[str, asyncio.Lock] = {}

    def start(self, bus: Any) -> None:
        self._task = asyncio.create_task(self._watch(bus), name="auto-export-watch")

    async def stop(self) -> None:
        tasks = [t for t in (self._task, *self._jobs) if t is not None]
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None
        self._jobs.clear()

    async def _watch(self, bus: Any) -> None:
        async for ev in bus.subscribe():
            if ev.type != "version.updated" or ev.data.get("status") != VersionStatus.APPROVED.value:
                continue
            try:
                v = VersionRow.model_validate(ev.data)
            except ValueError:
                continue
            job = asyncio.create_task(self._run(v), name=f"auto-export-{v.id[:8]}")
            self._jobs.add(job)
            job.add_done_callback(self._jobs.discard)

    async def _run(self, v: VersionRow) -> None:
        repo = self._st.repo
        try:
            project = await repo.get_project(v.project_id)
            if project is None or not project.auto_export:
                return
            # One export at a time per target folder: two approvals of the same step must not interleave their copies.
            lock = self._locks.setdefault(project.auto_export_dir or project.id, asyncio.Lock())
            async with lock:
                project = await repo.get_project(v.project_id)
                if project is None or not project.auto_export:
                    return
                data = await self._export(v, project.engine_target, project.auto_export_dir)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("auto export failed")
            data = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        self._st.bus.publish(EVENT, {"project_id": v.project_id, "version_id": v.id, "asset_name": v.asset_name,
                                     **data}, run_id=v.run_id, step_id=v.step_id)

    async def _export(self, v: VersionRow, engine: str, dest_dir: str) -> dict[str, Any]:
        base = {"engine": engine, "dest_dir": dest_dir}
        if engine == "none":
            return {**base, "ok": False, "error": "Chưa chọn engine đích cho project"}
        if not dest_dir.strip():
            return {**base, "ok": False, "error": "Chưa chọn thư mục project game"}
        dest = Path(dest_dir)
        if not dest.is_absolute() or not await asyncio.to_thread(dest.is_dir):
            return {**base, "ok": False, "error": f"Thư mục không tồn tại: {dest_dir}"}
        repo = self._st.repo
        step = await repo.get_step(v.step_id)
        if step is None or step.status not in (StepStatus.SUCCEEDED, StepStatus.CACHED) or not step.outputs:
            return {**base, "ok": False, "error": "Bước tạo ra phiên bản này chưa có kết quả để xuất"}
        settings = self._st.settings
        root = getattr(settings, "app_exports_dir", None) or Path(settings.data_dir) / "exports" / "app"
        siblings = await repo.get_steps(step.run_id)
        try:
            out = await asyncio.to_thread(export_engine, step, siblings, self._st.blobs, Path(root), v.asset_name or None, engine)
            target = await asyncio.to_thread(_place, Path(out["dir"]), dest, engine)
        except BundleError as e:
            return {**base, "ok": False, "error": str(e), "code": e.code}
        except ValueError as e:
            return {**base, "ok": False, "error": str(e), "code": "invalid_target"}
        except OSError as e:
            return {**base, "ok": False, "error": f"Không ghi được vào {dest_dir}: {e.strerror or e}"}
        return {**base, "ok": True, "path": str(target), "files": len(out["files"])}


def _place(src: Path, dest: Path, engine: str) -> Path:
    """Copies the export folder to dest/<name>; reruns overwrite the same files, so approving again never duplicates."""
    target = dest / src.name
    if engine in ("unity", "godot"):
        from studio_core.export.restyle import project_root

        kind, proj = project_root(dest)
        if kind != engine or (engine == "unity" and not dest.resolve().is_relative_to(proj / "Assets")):
            where = "<project>/Assets" if engine == "unity" else "một project Godot (có project.godot)"
            raise ValueError(f"Thư mục phải nằm trong {where}")
        if engine == "unity":
            from studio_core.export.unity import copy_unity_export

            copy_unity_export(src, target, proj / "Assets")
        else:
            from studio_core.export.godot_copy import copy_godot_export

            copy_godot_export(src, target, proj)
        return target
    shutil.copytree(src, target, dirs_exist_ok=True)
    return target
