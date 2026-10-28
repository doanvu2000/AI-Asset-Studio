"""Live E2E: spine-skin-variant on a skeleton + atlas (+ pages next to it), picking every Klein edit.

Usage: python scripts/e2e_skin_variant.py <data_dir> <skeleton.json> <file.atlas> "<instruction>\n<instruction>" [engine]
An engine (unity|godot) also exports the approve step to it.
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studio_core.spine_atlas import atlas_page_files  # noqa: E402


async def upload(c: httpx.AsyncClient, f: Path, mime: str) -> str:
    r = await c.post("/api/blobs", files={"file": (f.name, f.read_bytes(), mime)})
    assert r.is_success, r.text
    return r.json()["hash"]


async def main(data_dir: Path, skeleton: Path, atlas: Path, instructions: str, engine: str = "") -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(16)
    proc, port = spawn(data_dir, token)
    h = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", headers=h, timeout=120) as c, \
                websockets.connect(f"ws://127.0.0.1:{port}/ws?t={token}", max_size=None) as ws:
            sk = await upload(c, skeleton, "application/json")
            at = [await upload(c, atlas, "text/plain")] + [await upload(c, p, "image/png")
                                                           for p in atlas_page_files(atlas)]
            r = await c.post("/api/runs", json={"recipe_id": "spine-skin-variant", "title": "e2e skin variant",
                                                "inputs": {"skeleton": sk, "atlas": at,
                                                           "instructions": instructions}})
            assert r.is_success, r.text
            run_id = r.json()["run_id"]
            t0 = time.perf_counter()
            statuses: dict[str, str] = {}
            final = None
            while final is None:
                ev = json.loads(await asyncio.wait_for(ws.recv(), 1500))
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
                    body = ({"picked": [x["hash"] for x in d["candidates"]]} if d["kind"] == "pick"
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
                print(f"  {s['key']:8} {s['kind']:30} {s['status']:9}" + (f" {dur:6.1f}s" if dur else ""))
            if final == "succeeded":
                for ref in steps["variant"]["outputs"]["previews"]:
                    (data_dir / ref["name"]).write_bytes((await c.get(f"/api/blobs/{ref['hash']}")).content)
                    print("saved", data_dir / ref["name"])
                print("variant meta:", json.dumps(steps["variant"]["meta"])[:800])
                if engine:
                    ex = await c.post(f"/api/steps/{steps['approve']['id']}/export", json={"engine": engine})
                    print(f"export {engine}:", ex.status_code, json.dumps(ex.json())[:1200])
            await c.post("/api/shutdown")
    finally:
        try:
            proc.wait(90)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4].replace("\\n", "\n"),
                     sys.argv[5] if len(sys.argv) > 5 else ""))
