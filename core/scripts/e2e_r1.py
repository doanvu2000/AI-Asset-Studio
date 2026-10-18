"""End-to-end smoke: spawn core, run concept-2d through gate, restart core, verify history survives."""
import asyncio
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

import httpx
import websockets

CORE = Path(__file__).resolve().parents[1]
PY = CORE / ".venv" / "Scripts" / "python.exe"


def spawn(data_dir: Path, token: str) -> tuple[subprocess.Popen, int]:
    proc = subprocess.Popen([str(PY), "-m", "studio_core", "serve", "--port", "0", "--data-dir", str(data_dir)],
                            cwd=CORE, env={**os.environ, "STUDIO_TOKEN": token}, stdout=subprocess.PIPE,
                            stderr=open(data_dir / "core.stderr.log", "ab"), text=True)
    t0 = time.perf_counter()
    for line in proc.stdout:
        if line.startswith("STUDIO_CORE_READY"):
            port = json.loads(line.split(" ", 1)[1])["port"]
            print(f"core ready in {time.perf_counter() - t0:.2f}s port={port}", flush=True)
            return proc, port
    raise RuntimeError("core died before READY")


async def main(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(16)
    proc, port = spawn(data_dir, token)
    base = f"http://127.0.0.1:{port}"
    h = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(base_url=base, headers=h, timeout=60) as c, \
            websockets.connect(f"ws://127.0.0.1:{port}/ws?t={token}", max_size=None) as ws:
        print("recipes:", [r["id"] for r in (await c.get("/api/recipes")).json()])
        r = await c.post("/api/runs", json={"recipe_id": "concept-2d", "title": "e2e potion",
                                            "inputs": {"prompt": "red health potion game icon", "count": 2,
                                                       "size": 256}})
        run_id = r.json()["run_id"]
        t0 = time.perf_counter()
        statuses: dict[str, str] = {}
        while True:
            ev = json.loads(await asyncio.wait_for(ws.recv(), 600))
            if ev.get("run_id") not in (None, run_id):
                continue
            t = ev["type"]
            if t == "step.updated":
                s = ev["data"]
                if statuses.get(s["key"]) != s["status"]:
                    statuses[s["key"]] = s["status"]
                    print(f"{time.perf_counter() - t0:6.1f}s {s['key']:5} {s['status']}"
                          + (f" err={s['error']}" if s.get("error") else ""), flush=True)
            elif t == "gate.awaiting":
                cands = ev["data"]["candidates"]
                print(f"gate: {len(cands)} candidates → pick first", flush=True)
                g = await c.post(f"/api/gates/{ev['step_id']}", json={"picked": [cands[0]["hash"]]})
                assert g.status_code == 200, g.text
            elif t == "run.updated" and ev["data"].get("status") in ("succeeded", "failed", "cancelled"):
                print("run:", ev["data"]["status"], f"{time.perf_counter() - t0:.1f}s", flush=True)
                break
        vs = (await c.get("/api/versions", params={"run_id": run_id})).json()
        print("versions:", [(v["status"], v["blob"]["width"], v["blob"]["height"]) for v in vs])
        th = await c.get(f"/api/blobs/{vs[0]['blob']['hash']}/thumb", params={"size": 128})
        print("thumb:", th.status_code, th.headers.get("content-type"), len(th.content))
        await c.post("/api/shutdown")
    proc.wait(60)
    print("core exited rc", proc.returncode)

    proc, port = spawn(data_dir, token)
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", headers=h, timeout=30) as c:
        runs = (await c.get("/api/runs")).json()
        print("after restart runs:", [(x["id"] == run_id, x["status"]) for x in runs])
        await c.post("/api/shutdown")
    proc.wait(60)


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1])))
