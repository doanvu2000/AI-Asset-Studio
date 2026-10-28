"""Live E2E for a recipe through its gates (picks keep every candidate); saves one step's outputs.

Usage: python scripts/e2e_text.py <data_dir> <recipe> '<inputs json>' [step to save, default post]
An input value "@<path>" is uploaded and replaced by its blob hash.
"""
import asyncio
import json
import secrets
import sys
import time
from pathlib import Path

import httpx
import websockets

from e2e_r1 import spawn


async def main(data_dir: Path, recipe: str, inputs: dict, save: str = "post") -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(16)
    proc, port = spawn(data_dir, token)
    h = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", headers=h, timeout=120) as c, \
                websockets.connect(f"ws://127.0.0.1:{port}/ws?t={token}", max_size=None) as ws:
            for k, v in list(inputs.items()):
                if isinstance(v, str) and v.startswith("@"):
                    f = Path(v[1:])
                    up = await c.post("/api/blobs", files={"file": (f.name, f.read_bytes(), "image/png")})
                    assert up.is_success, up.text
                    inputs[k] = up.json()["hash"]
            r = await c.post("/api/runs", json={"recipe_id": recipe, "title": f"e2e {recipe}", "inputs": inputs})
            assert r.is_success, r.text
            run_id = r.json()["run_id"]
            t0 = time.perf_counter()
            statuses: dict[str, str] = {}
            final = None
            while final is None:
                ev = json.loads(await asyncio.wait_for(ws.recv(), 2400))
                if ev.get("run_id") not in (None, run_id):
                    continue
                if ev["type"] == "step.updated":
                    s = ev["data"]
                    if statuses.get(s["key"]) != s["status"]:
                        statuses[s["key"]] = s["status"]
                        print(f"{time.perf_counter() - t0:7.1f}s {s['key']:8} {s['status']}"
                              + (f" err={s['error']}" if s.get("error") else ""), flush=True)
                elif ev["type"] == "gate.awaiting":
                    d = ev["data"]
                    body = ({"picked": [x["hash"] for x in d["candidates"]]} if d.get("kind") == "pick"
                            else {"approved": True})
                    g = await c.post(f"/api/gates/{ev['step_id']}", json=body)
                    assert g.is_success, g.text
                elif ev["type"] == "run.updated" and ev["data"].get("status") in ("succeeded", "failed",
                                                                                   "cancelled", "interrupted"):
                    final = ev["data"]["status"]
            print("run:", final, f"{time.perf_counter() - t0:.1f}s", flush=True)
            steps = {s["key"]: s for s in (await c.get(f"/api/runs/{run_id}")).json()["steps"]}
            for s in steps.values():
                dur = (s["ended_at"] - s["started_at"]) if s.get("ended_at") and s.get("started_at") else None
                print(f"  {s['key']:8} {s['kind']:26} {s['status']:9}" + (f" {dur:6.1f}s" if dur else "")
                      + (f" {s['error']}" if s.get("error") else ""))
            if final == "succeeded":
                step = steps[save]
                for ref in (r for refs in step["outputs"].values() for r in refs):
                    (data_dir / ref["name"]).write_bytes((await c.get(f"/api/blobs/{ref['hash']}")).content)
                print(f"{save} meta:", json.dumps({k: v for k, v in step["meta"].items() if k != "spec"})[:900])
                print("saved to", data_dir)
            await c.post("/api/shutdown")
    finally:
        try:
            proc.wait(90)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]), sys.argv[2], json.loads(sys.argv[3]), *sys.argv[4:5]))
