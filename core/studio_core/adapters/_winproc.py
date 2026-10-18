"""Windows process-tree supervision via Job Objects (kill the whole tree, including uv launcher children)."""
from __future__ import annotations

import asyncio
import ctypes
import subprocess
import sys
from pathlib import Path
from typing import Any

import win32api
import win32con
import win32job

CREATE_SUSPENDED = 0x00000004
CREATE_NO_WINDOW = 0x08000000


class JobObject:
    def __init__(self) -> None:
        self.handle = win32job.CreateJobObject(None, "")
        info = win32job.QueryInformationJobObject(self.handle, win32job.JobObjectExtendedLimitInformation)
        info["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(self.handle, win32job.JobObjectExtendedLimitInformation, info)
        self.closed = False

    def assign_and_resume(self, pid: int) -> None:
        h = win32api.OpenProcess(win32con.PROCESS_ALL_ACCESS, False, pid)
        try:
            win32job.AssignProcessToJobObject(self.handle, h)
            ctypes.windll.ntdll.NtResumeProcess(int(h))
        finally:
            win32api.CloseHandle(h)

    def pids(self) -> list[int]:
        if self.closed:
            return []
        try:
            return list(win32job.QueryInformationJobObject(self.handle, win32job.JobObjectBasicProcessIdList))
        except Exception:
            return []

    def terminate(self, code: int = 1) -> None:
        if self.closed:
            return
        try:
            win32job.TerminateJobObject(self.handle, code)
        except Exception:
            pass

    def close(self) -> None:
        if not self.closed:
            self.terminate()
            self.handle.Close()
            self.closed = True


async def spawn_in_job(args: list[str], cwd: Path | str | None = None, **kwargs: Any
                       ) -> tuple[asyncio.subprocess.Process, JobObject]:
    """Spawn suspended, put into a fresh kill-on-close job, then resume.

    Starting suspended matters: a uv venv python.exe is a launcher that immediately spawns the real interpreter;
    if it ran before AssignProcessToJobObject the child could escape the job and keep VRAM after kill.
    """
    job = JobObject()
    flags = kwargs.pop("creationflags", 0) | CREATE_SUSPENDED | CREATE_NO_WINDOW
    try:
        proc = await asyncio.create_subprocess_exec(*args, cwd=str(cwd) if cwd else None, creationflags=flags,
                                                    **kwargs)
    except BaseException:
        job.close()
        raise
    try:
        job.assign_and_resume(proc.pid)
    except BaseException:
        job.close()
        raise
    return proc, job


def pid_alive(pid: int) -> bool:
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True,
                         creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0).stdout
    return str(pid) in out
