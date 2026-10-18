"""Live A/B: sprite-8dir-3d-mv (Qwen views → TRELLIS multi-view) vs single-view R5 on the same picked concept.

Scratch user recipes derived from both recipes (concept/pick replaced by an image input) let both meshes start from
the exact same front image. Without --image the real sprite-8dir-3d-mv recipe runs first and its pick is reused.

Usage: python scripts/e2e_r5mv.py <data_dir> [prompt] [--image front.png [--views l.png,b.png,r.png] [--only sv|mv]]
"""
import asyncio
import copy
import json
import secrets
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import websockets
import yaml
from PIL import Image, ImageDraw

from e2e_r1 import spawn
from e2e_r5 import MAGENTA, on_magenta

CORE = Path(__file__).resolve().parents[1]
PROMPT = "cute chibi wizard with purple robe and wooden staff, full body"
AB_SINGLE = "ab-sprite-8dir-3d-from-image"


AB_MULTI = "ab-sprite-8dir-3d-mv-from-image"
AB_VIEWS = "ab-sprite-8dir-3d-mv-from-views"


def from_image_recipe(src: str, rid: str) -> dict[str, Any]:
    doc = copy.deepcopy(yaml.safe_load((CORE / f"studio_core/recipes/{src}.yaml").read_text(encoding="utf-8")))
    doc["id"] = rid
    doc["name"] = f"A/B: {src} from image"
    doc["inputs"] = {"image": {"type": "image", "label": "Front"}} | {
        k: v for k, v in doc["inputs"].items() if k != "prompt"}
    doc["steps"] = [s for s in doc["steps"] if s["id"] not in ("concept", "pick")]
    for s in doc["steps"]:
        img = (s.get("in") or {}).get("image")
        if img == "pick.images":
            s["in"]["image"] = "inputs.image"
        elif isinstance(img, list):
            s["in"]["image"] = ["inputs.image" if b == "pick.images" else b for b in img]
    return doc


async def run_recipe(c: httpx.AsyncClient, ws: Any, recipe: str, inputs: dict[str, Any]) -> tuple[str, str, float]:
    r = await c.post("/api/runs", json={"recipe_id": recipe, "title": f"ab {recipe}", "inputs": inputs})
    assert r.is_success, r.text
    run_id = r.json()["run_id"]
    t0 = time.perf_counter()
    statuses: dict[str, str] = {}
    retries = 1
    while True:
        ev = json.loads(await asyncio.wait_for(ws.recv(), 2400))
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
            body = {"picked": [d["candidates"][0]["hash"]]} if d["kind"] == "pick" else {"approved": True}
            print(f"gate {d['kind']}: {len(d['candidates'])} candidates -> {body}", flush=True)
            g = await c.post(f"/api/gates/{ev['step_id']}", json=body)
            assert g.is_success, g.text
        elif t == "run.updated" and ev["data"].get("status") in ("succeeded", "failed", "cancelled", "interrupted"):
            final = ev["data"]["status"]
            if final == "failed" and retries > 0:
                bad = [s for s in (await c.get(f"/api/runs/{run_id}")).json()["steps"]
                       if s["status"] == "failed" and s["kind"].startswith("comfy.")]
                if bad:
                    retries -= 1
                    # Same as e2e_r5: a fresh ComfyUI process is what recovered TRELLIS crashes live.
                    await c.post("/api/resources/comfyui/stop")
                    print(f"retrying {bad[0]['key']}", flush=True)
                    rr = await c.post(f"/api/steps/{bad[0]['id']}/retry")
                    assert rr.is_success, rr.text
                    continue
            elapsed = time.perf_counter() - t0
            print(f"run {recipe}: {final} {elapsed:.1f}s", flush=True)
            for s in (await c.get(f"/api/runs/{run_id}")).json()["steps"]:
                dur = (s["ended_at"] - s["started_at"]) if s.get("ended_at") and s.get("started_at") else None
                print(f"  {s['key']:9} {s['kind']:38} {s['status']:9} "
                      + (f"{dur:7.1f}s" if dur is not None else "      -"), flush=True)
            return run_id, final, elapsed


async def blob_img(c: httpx.AsyncClient, ref: dict[str, Any]) -> Image.Image:
    return on_magenta((await c.get(f"/api/blobs/{ref['hash']}")).content)


def fit(img: Image.Image, h: int) -> Image.Image:
    return img.resize((max(1, round(img.width * h / img.height)), h), Image.LANCZOS)


def stack(rows: list[tuple[str, Image.Image]]) -> Image.Image:
    w = max(im.width for _, im in rows)
    label_h = 22
    out = Image.new("RGBA", (w, sum(im.height + label_h for _, im in rows)), (32, 32, 32, 255))
    d = ImageDraw.Draw(out)
    y = 0
    for label, im in rows:
        d.text((6, y + 4), label, fill=(255, 255, 255, 255))
        out.paste(im, (0, y + label_h))
        y += im.height + label_h
    return out


async def steps_of(c: httpx.AsyncClient, run_id: str) -> dict[str, Any]:
    return {s["key"]: s for s in (await c.get(f"/api/runs/{run_id}")).json()["steps"]}


async def main(data_dir: Path, prompt: str, image: Path | None, only: str | None,
               views_in: list[Path] | None = None) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(16)
    proc, port = spawn(data_dir, token)
    h = {"Authorization": f"Bearer {token}"}
    common = {"elevation": 30, "res": 256, "mirror": False}
    tag = image.stem if image else "prompt"
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", headers=h, timeout=120) as c, \
                websockets.connect(f"ws://127.0.0.1:{port}/ws?t={token}", max_size=None) as ws:
            for src, rid in (("sprite-8dir-3d", AB_SINGLE), ("sprite-8dir-3d-mv", AB_MULTI)):
                r = await c.put(f"/api/recipes/{rid}", json={"doc": from_image_recipe(src, rid)})
                assert r.is_success, r.text
            if image is None:
                mv_id, mv_status, mv_t = await run_recipe(c, ws, "sprite-8dir-3d-mv", {"prompt": prompt} | common)
                mv_steps = await steps_of(c, mv_id)
                picked = mv_steps["pick"]["outputs"]["images"][0]
                sv_id, sv_status, sv_t = await run_recipe(c, ws, AB_SINGLE, {"image": picked["hash"]} | common)
            else:
                async def upload(p: Path) -> dict[str, Any]:
                    up = await c.post("/api/blobs", files={"file": (p.name, p.read_bytes(), "image/png")})
                    assert up.is_success, up.text
                    return up.json()

                picked = await upload(image)
                mv_inputs = {"image": picked["hash"]}
                mv_recipe = AB_MULTI
                if views_in:
                    # Fixed Qwen views (e.g. a good earlier set) so a bad Qwen sample can't decide the A/B.
                    refs = [await upload(p) for p in views_in]
                    doc = from_image_recipe("sprite-8dir-3d-mv", AB_VIEWS)
                    doc["inputs"] |= {n: {"type": "image", "label": n} for n in ("left", "back", "right")}
                    doc["steps"] = [s for s in doc["steps"] if s["id"] not in ("views", "check")]
                    next(s for s in doc["steps"] if s["id"] == "mesh")["in"] = {
                        "image": ["inputs.image", "inputs.left", "inputs.back", "inputs.right"]}
                    r = await c.put(f"/api/recipes/{AB_VIEWS}", json={"doc": doc})
                    assert r.is_success, r.text
                    mv_inputs |= {n: ref["hash"] for n, ref in zip(("left", "back", "right"), refs)}
                    mv_recipe = AB_VIEWS
                sv_id = mv_id = None
                sv_status = mv_status = "skipped"
                sv_t = mv_t = 0.0
                if only != "mv":
                    sv_id, sv_status, sv_t = await run_recipe(c, ws, AB_SINGLE, {"image": picked["hash"]} | common)
                # 4-view conditioning holds ~24 GB of fp32 NAF features in RAM; start it in a fresh ComfyUI.
                await c.post("/api/resources/comfyui/stop")
                if only != "sv":
                    mv_id, mv_status, mv_t = await run_recipe(c, ws, mv_recipe, mv_inputs | common)
                mv_steps = await steps_of(c, mv_id) if mv_id else {}
                if views_in:
                    mv_steps["views"] = {"outputs": {"images": refs}}
            sv_steps = await steps_of(c, sv_id) if sv_id else {}

            views = [picked] + ((mv_steps.get("views") or {}).get("outputs") or {}).get("images", [])
            view_imgs = [fit(await blob_img(c, v), 256) for v in views]
            strip = Image.new("RGBA", (sum(i.width for i in view_imgs), 256), MAGENTA)
            x = 0
            for im in view_imgs:
                strip.paste(im, (x, 0))
                x += im.width
            strip.save(data_dir / f"mv_views_{tag}.png")
            rows: list[tuple[str, Image.Image]] = [("input views: front | qwen left 90 | back 180 | right 270", strip)]
            for label, st, status, secs in (("single-view R5", sv_steps, sv_status, sv_t),
                                            ("multi-view R5-mv", mv_steps, mv_status, mv_t)):
                if status != "succeeded":
                    continue
                sheet = await blob_img(c, st["sheet"]["outputs"]["sheet"][0])
                sheet.save(data_dir / f"{'sv' if label.startswith('single') else 'mv'}_sheet_{tag}.png")
                mesh_s = st["mesh"]["ended_at"] - st["mesh"]["started_at"]
                rows.append((f"{label}: run {secs:.0f}s, mesh step {mesh_s:.0f}s (0,45,...,315)", sheet))
            out = data_dir / f"ab_r5_vs_r5mv_{tag}.png"
            stack(rows).save(out)
            print("saved", out, flush=True)
            await c.post("/api/shutdown")
    finally:
        try:
            proc.wait(120)
        except Exception:
            proc.kill()
    print("core exited rc", proc.returncode)


if __name__ == "__main__":
    args = sys.argv[1:]
    opts = {k: args[args.index(k) + 1] for k in ("--image", "--only", "--views") if k in args}
    rest = [a for i, a in enumerate(args) if a not in opts and (i == 0 or args[i - 1] not in opts)]
    asyncio.run(main(Path(rest[0]), rest[1] if len(rest) > 1 else PROMPT,
                     Path(opts["--image"]) if "--image" in opts else None, opts.get("--only"),
                     [Path(p) for p in opts["--views"].split(",")] if "--views" in opts else None))
