"""Prototype ComfyUI adapter for P0 spikes (server supervision via Job Object + WS client)."""
import asyncio
import ctypes
import json
import random
import subprocess
import time
import uuid
from pathlib import Path

import httpx
import websockets
import win32api
import win32con
import win32job

COMFY_DIR = Path(r"L:\AI\setup\comfyUI")
COMFY_PY = COMFY_DIR / ".venv" / "Scripts" / "python.exe"
MODELS_DIR = r"F:\work\AI\comfyUI_models"
WF_DIR = Path(r"L:\AI\setup\workflows")
CREATE_SUSPENDED = 0x4


class ComfyServer:
    def __init__(self, port=8188, log_path=None):
        self.port = port
        self.base = f"http://127.0.0.1:{port}"
        self.log_path = Path(log_path or Path(__file__).parent / "out" / "comfy_server.log")
        self.proc = None
        self.job = None

    async def start(self, timeout=180):
        self.job = win32job.CreateJobObject(None, "")
        info = win32job.QueryInformationJobObject(self.job, win32job.JobObjectExtendedLimitInformation)
        info["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(self.job, win32job.JobObjectExtendedLimitInformation, info)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        log = open(self.log_path, "wb")
        args = [str(COMFY_PY), "main.py", "--listen", "127.0.0.1", "--port", str(self.port),
                "--disable-auto-launch", "--disable-pinned-memory", "--models-directory", MODELS_DIR,
                "--preview-method", "auto"]
        # Suspended until inside the job so the uv launcher's real python child cannot escape it.
        self.proc = subprocess.Popen(args, cwd=COMFY_DIR, stdout=log, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, creationflags=CREATE_SUSPENDED)
        handle = win32api.OpenProcess(win32con.PROCESS_ALL_ACCESS, False, self.proc.pid)
        win32job.AssignProcessToJobObject(self.job, handle)
        ctypes.windll.ntdll.NtResumeProcess(int(handle))
        t0 = time.perf_counter()
        async with httpx.AsyncClient(timeout=2) as c:
            while time.perf_counter() - t0 < timeout:
                if self.proc.poll() is not None:
                    raise RuntimeError(f"comfy exited rc={self.proc.returncode}, see {self.log_path}")
                try:
                    r = await c.get(self.base + "/system_stats")
                    if r.status_code == 200:
                        stats = r.json()
                        argv = stats["system"]["argv"]
                        assert "--disable-pinned-memory" in argv, argv
                        return time.perf_counter() - t0, stats
                except httpx.TransportError:
                    pass
                await asyncio.sleep(0.5)
        raise TimeoutError("comfy did not become ready")

    def job_pids(self):
        return list(win32job.QueryInformationJobObject(self.job, win32job.JobObjectBasicProcessIdList))

    def kill(self):
        pids = self.job_pids()
        win32job.TerminateJobObject(self.job, 1)
        self.proc.wait(10)
        return pids


class ComfyClient:
    def __init__(self, base="http://127.0.0.1:8188"):
        self.base = base
        self.client_id = str(uuid.uuid4())
        self.http = httpx.AsyncClient(base_url=base, timeout=30)
        self.ws = None
        self.waiters = {}
        self.reader = None
        self.previews = 0

    async def connect(self):
        self.ws = await websockets.connect(self.base.replace("http", "ws") + f"/ws?clientId={self.client_id}",
                                           max_size=None)
        self.reader = asyncio.create_task(self._read())

    async def _read(self):
        async for frame in self.ws:
            if isinstance(frame, bytes):
                self.previews += 1
                continue
            msg = json.loads(frame)
            pid = (msg.get("data") or {}).get("prompt_id")
            q = self.waiters.get(pid)
            if q:
                q.put_nowait(msg)

    def build(self, workflow, values):
        index = json.loads((WF_DIR / "index.json").read_text(encoding="utf-8"))[workflow]
        prompt = json.loads((WF_DIR / index["file"]).read_text(encoding="utf-8"))
        for key in index["seeds"]:
            values.setdefault(key, random.randint(0, 2**53))
        for key, v in values.items():
            node, name = key.rsplit(".", 1)
            prompt[node]["inputs"][name] = v
        return prompt, index

    async def run(self, workflow, values, timeout=1800, drop_outputs=(), on_event=None):
        prompt, index = self.build(workflow, dict(values))
        for node in drop_outputs:
            prompt.pop(node, None)
        pid = str(uuid.uuid4())
        q = self.waiters[pid] = asyncio.Queue()
        t0 = time.perf_counter()
        r = await self.http.post("/prompt", json={"prompt": prompt, "client_id": self.client_id, "prompt_id": pid})
        body = r.json()
        if r.status_code != 200 or body.get("node_errors"):
            raise RuntimeError(f"submit failed {r.status_code}: {json.dumps(body)[:800]}")
        timeline = []
        try:
            while True:
                msg = await asyncio.wait_for(q.get(), timeout - (time.perf_counter() - t0))
                t, d = msg["type"], msg["data"]
                if on_event:
                    on_event(t, d)
                if t in ("execution_start", "execution_cached", "execution_success"):
                    timeline.append((t, round(time.perf_counter() - t0, 2)))
                if t == "execution_success":
                    break
                if t in ("execution_error", "execution_interrupted"):
                    raise RuntimeError(f"{t}: {d.get('exception_type')} {d.get('exception_message')}")
        finally:
            self.waiters.pop(pid, None)
        hist = (await self.http.get(f"/history/{pid}")).json()[pid]
        files = [COMFY_DIR / "output" / f["subfolder"] / f["filename"]
                 for node, out in hist["outputs"].items() for kind in ("images", "3d")
                 for f in out.get(kind, []) if f.get("type") == "output"]
        return {"prompt_id": pid, "seconds": round(time.perf_counter() - t0, 2), "files": files, "timeline": timeline}

    def stage_input(self, src: Path, name=None):
        dst = COMFY_DIR / "input" / "studio" / (name or src.name)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
        return f"studio/{dst.name}"

    async def free(self):
        await self.http.post("/free", json={"unload_models": True, "free_memory": True})

    async def close(self):
        if self.ws:
            await self.ws.close()
        await self.http.aclose()
