"""Live E2E: spawn core, run sprite-8dir-3d (or prop-3d) through its gates, print step timings, save a preview.

Usage: python scripts/e2e_r5.py <data_dir> [sprite-8dir-3d|prop-3d|sprite-8dir-glb] [prompt | glb path]
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

DEFAULT_PROMPTS = {"sprite-8dir-3d": "cute chibi knight with blue cape, full body",
                   "prop-3d": "wooden treasure chest with iron bands"}
MAGENTA = (255, 0, 255, 255)


def on_magenta(png: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(png)).convert("RGBA")
    bg = Image.new("RGBA", img.size, MAGENTA)
    bg.alpha_composite(img)
    return bg


async def save_previews(c: httpx.AsyncClient, run_id: str, recipe: str, data_dir: Path) -> None:
    steps = {s["key"]: s for s in (await c.get(f"/api/runs/{run_id}")).json()["steps"]}
    if recipe.startswith("sprite-"):
        sheet = steps["sheet"]["outputs"]["sheet"][0]
        png = (await c.get(f"/api/blobs/{sheet['hash']}")).content
        on_magenta(png).save(data_dir / "r5_sheet_magenta.png")
        print("sheet meta:", json.dumps({k: v for k, v in steps["sheet"]["meta"].items()
                                         if k not in ("frames", "spec", "ready_at")}))
        print("saved", data_dir / "r5_sheet_magenta.png")
    else:
        frames = steps["turntable"]["outputs"]["images"]
        imgs = [on_magenta((await c.get(f"/api/blobs/{f['hash']}")).content) for f in frames]
        w, h = imgs[0].size
        strip = Image.new("RGBA", (w * 8, h * 2), MAGENTA)
        for i, im in enumerate(imgs[:16]):
            strip.paste(im, ((i % 8) * w, (i // 8) * h))
        strip.save(data_dir / "r4_turntable_magenta.png")
        print("saved", data_dir / "r4_turntable_magenta.png")
    for key in ("cleanup", "validate", "gameready", "render", "turntable"):
        if key in steps:
            meta = {k: v for k, v in steps[key]["meta"].items() if k not in ("spec", "params", "angles", "ready_at")}
            print(f"{key} meta:", json.dumps(meta)[:600])


async def main(data_dir: Path, recipe: str, prompt: str) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(16)
    proc, port = spawn(data_dir, token)
    h = {"Authorization": f"Bearer {token}"}
    inputs: dict = {"prompt": prompt}
    if recipe.startswith("sprite-"):
        inputs |= {"elevation": 30, "res": 256, "mirror": False}
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", headers=h, timeout=120) as c, \
                websockets.connect(f"ws://127.0.0.1:{port}/ws?t={token}", max_size=None) as ws:
            if recipe == "sprite-8dir-glb":
                glb = Path(inputs.pop("prompt"))
                up = await c.post("/api/blobs", files={"file": (glb.name, glb.read_bytes(), "model/gltf-binary")})
                assert up.is_success, up.text
                inputs["mesh"] = up.json()["hash"]
            r = await c.post("/api/runs", json={"recipe_id": recipe, "title": f"e2e {recipe}", "inputs": inputs})
            assert r.is_success, r.text
            run_id = r.json()["run_id"]
            t0 = time.perf_counter()
            statuses: dict[str, str] = {}
            final = None
            retries = 1
            while final is None:
                ev = json.loads(await asyncio.wait_for(ws.recv(), 1500))
                if ev.get("run_id") not in (None, run_id):
                    continue
                t = ev["type"]
                if t == "step.updated":
                    s = ev["data"]
                    if statuses.get(s["key"]) != s["status"]:
                        statuses[s["key"]] = s["status"]
                        print(f"{time.perf_counter() - t0:7.1f}s {s['key']:9} {s['status']}"
                              + (f" err={s['error']}" if s.get("error") else ""), flush=True)
                elif t == "gate.awaiting":
                    d = ev["data"]
                    if d["kind"] == "pick":
                        body = {"picked": [d["candidates"][0]["hash"]]}
                    else:
                        body = {"approved": True}
                    print(f"gate {d['kind']}: {len(d['candidates'])} candidates -> {body}", flush=True)
                    g = await c.post(f"/api/gates/{ev['step_id']}", json=body)
                    assert g.is_success, g.text
                elif t == "run.updated" and ev["data"].get("status") in ("succeeded", "failed", "cancelled",
                                                                         "interrupted"):
                    final = ev["data"]["status"]
                    if final == "failed" and retries > 0:
                        bad = [s for s in (await c.get(f"/api/runs/{run_id}")).json()["steps"]
                               if s["status"] == "failed" and ((s.get("error") or {}).get("retryable")
                                                               or s["kind"].startswith("comfy."))]
                        if bad:
                            retries -= 1
                            if bad[0]["kind"].startswith("comfy."):
                                # TRELLIS right after Z-Image in one ComfyUI process crashed 2/2 live runs; a fresh
                                # process (nothing else loaded) succeeded, so restart instead of retrying in place.
                                await c.post("/api/resources/comfyui/stop")
                            print(f"retrying {bad[0]['key']} (attempt {bad[0]['attempt'] + 1})", flush=True)
                            rr = await c.post(f"/api/steps/{bad[0]['id']}/retry")
                            assert rr.is_success, rr.text
                            final = None
            print("run:", final, f"{time.perf_counter() - t0:.1f}s", flush=True)
            steps = (await c.get(f"/api/runs/{run_id}")).json()["steps"]
            for s in steps:
                dur = (s["ended_at"] - s["started_at"]) if s.get("ended_at") and s.get("started_at") else None
                print(f"  {s['key']:9} {s['kind']:34} {s['status']:9} "
                      + (f"{dur:7.1f}s" if dur is not None else "      -"))
            if final == "succeeded":
                await save_previews(c, run_id, recipe, data_dir)
            await c.post("/api/shutdown")
    finally:
        try:
            proc.wait(90)
        except Exception:
            proc.kill()
    print("core exited rc", proc.returncode)


if __name__ == "__main__":
    rec = sys.argv[2] if len(sys.argv) > 2 else "sprite-8dir-3d"
    asyncio.run(main(Path(sys.argv[1]), rec, sys.argv[3] if len(sys.argv) > 3 else DEFAULT_PROMPTS[rec]))
