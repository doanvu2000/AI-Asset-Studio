"""Live E2E for R6 spine-character: spawn core, run through both gates, print step timings, dump outputs.

Usage: python scripts/e2e_r6.py <data_dir> [prompt] [pick_index] [rig: claude|auto]
Outputs land in <data_dir>/r6_out/: Spine files, setup-pose render, animation frames, part/segment previews.
"""
import asyncio
import io
import json
import secrets
import sys
import time
from pathlib import Path

import httpx
import websockets
from PIL import Image

from e2e_r1 import spawn

DEFAULT_PROMPT = "young adventurer girl with red scarf and short cape, leather armor"
SAVE = {"export": ("skeleton", "atlas", "texture", "html", "preview", "frames", "report"),
        "layers": ("preview", "meta"), "parts": ("preview", "meta"), "bones": ("json",)}


async def dump(c: httpx.AsyncClient, run_id: str, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    steps = {s["key"]: s for s in (await c.get(f"/api/runs/{run_id}")).json()["steps"]}

    async def blob(ref: dict) -> bytes:
        return (await c.get(f"/api/blobs/{ref['hash']}")).content

    for key, names in SAVE.items():
        for n in names:
            for i, ref in enumerate(steps[key]["outputs"].get(n, [])):
                fname = f"{key}_{n}_{i}_{ref.get('name') or 'blob'}"
                (out / fname).write_bytes(await blob(ref))
    for key in ("segment", "fill"):
        refs = steps[key]["outputs"].get("images", [])
        if refs:
            imgs = [Image.open(io.BytesIO(await blob(r))).convert("RGB") for r in refs]
            h = 256
            tiles = [im.resize((max(1, im.width * h // im.height), h)) for im in imgs]
            strip = Image.new("RGB", (sum(t.width for t in tiles) + 4 * len(tiles), h), "white")
            x = 0
            for t in tiles:
                strip.paste(t, (x, 0))
                x += t.width + 4
            strip.save(out / f"{key}_strip.png")
    holes = steps["layers"]["outputs"].get("holes", [])
    for i, ref in enumerate(holes):
        (out / f"hole_{i}.png").write_bytes(await blob(ref))
    print("export meta:", json.dumps({k: v for k, v in steps["export"]["meta"].items()
                                      if k not in ("spec", "ready_at")})[:1500])
    print("layers meta:", json.dumps({k: v for k, v in steps["layers"]["meta"].items()
                                      if k not in ("spec", "ready_at")})[:1500])
    print("bones meta:", json.dumps({k: v for k, v in steps["bones"]["meta"].items()
                                     if k not in ("spec", "ready_at", "refs")})[:600])
    print("saved to", out)


async def main(data_dir: Path, prompt: str, pick: int, rig: str) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(16)
    proc, port = spawn(data_dir, token)
    h = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", headers=h, timeout=120) as c, \
                websockets.connect(f"ws://127.0.0.1:{port}/ws?t={token}", max_size=None) as ws:
            r = await c.post("/api/runs", json={"recipe_id": "spine-character", "title": "e2e spine",
                                                "inputs": {"prompt": prompt, "name": "hero", "rig": rig}})
            assert r.is_success, r.text
            run_id = r.json()["run_id"]
            t0 = time.perf_counter()
            statuses: dict[str, str] = {}
            final = None
            while final is None:
                ev = json.loads(await asyncio.wait_for(ws.recv(), 1500))
                if ev.get("run_id") not in (None, run_id):
                    continue
                t = ev["type"]
                if t == "step.updated":
                    s = ev["data"]
                    if statuses.get(s["key"]) != s["status"]:
                        statuses[s["key"]] = s["status"]
                        print(f"{time.perf_counter() - t0:7.1f}s {s['key']:8} {s['status']}"
                              + (f" err={s['error']}" if s.get("error") else ""), flush=True)
                elif t == "gate.awaiting":
                    d = ev["data"]
                    if d["kind"] == "pick":
                        out = data_dir / "r6_out"
                        out.mkdir(parents=True, exist_ok=True)
                        for i, cand in enumerate(d["candidates"]):
                            (out / f"concept_{i}.png").write_bytes((await c.get(f"/api/blobs/{cand['hash']}")).content)
                        body = {"picked": [d["candidates"][min(pick, len(d["candidates"]) - 1)]["hash"]]}
                    else:
                        body = {"approved": True}
                    print(f"gate {d['kind']}: {len(d['candidates'])} candidates -> {body}", flush=True)
                    g = await c.post(f"/api/gates/{ev['step_id']}", json=body)
                    assert g.is_success, g.text
                elif t == "run.updated" and ev["data"].get("status") in ("succeeded", "failed", "cancelled",
                                                                         "interrupted"):
                    final = ev["data"]["status"]
            print("run:", final, f"{time.perf_counter() - t0:.1f}s", flush=True)
            for s in (await c.get(f"/api/runs/{run_id}")).json()["steps"]:
                dur = (s["ended_at"] - s["started_at"]) if s.get("ended_at") and s.get("started_at") else None
                print(f"  {s['key']:8} {s['kind']:30} {s['status']:9} "
                      + (f"{dur:7.1f}s" if dur is not None else "      -"))
            if final == "succeeded":
                await dump(c, run_id, data_dir / "r6_out")
            await c.post("/api/shutdown")
    finally:
        try:
            proc.wait(90)
        except Exception:
            proc.kill()
    print("core exited rc", proc.returncode)


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else DEFAULT_PROMPT,
                     int(sys.argv[3]) if len(sys.argv) > 3 else 0, sys.argv[4] if len(sys.argv) > 4 else "claude"))
