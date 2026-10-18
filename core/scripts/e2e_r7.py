"""Live E2E for P4 (Claude + headless Blender only, no ComfyUI).

  python scripts/e2e_r7.py <data_dir> r7 ["idea"]      spawn core, run procedural-3d, auto-approve, save previews
  python scripts/e2e_r7.py <data_dir> brief ["idea"]   in-process engine: claude.brief -> `${steps.brief.text}` binding
"""
import asyncio
import io
import json
import secrets
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import websockets
from PIL import Image

from e2e_r1 import spawn

DEFAULT_IDEA = {"r7": "low-poly wooden treasure chest with iron bands and a gold lock",
                "brief": "a potion that heals the undead"}
BRIEF_RECIPE = """
id: brief-check
inputs:
  idea: {type: text}
steps:
  - {id: brief, kind: claude.brief, params: {idea: "${inputs.idea}", target: concept-2d, model: haiku}}
  - id: use
    kind: t.echo
    params: {"57:27.text": "${steps.brief.text}, isolated on plain white background", palette: "${steps.brief.json.palette}"}
"""


def grid(pngs: list[bytes], cols: int, bg: tuple[int, int, int, int] = (40, 40, 48, 255)) -> Image.Image:
    imgs = [Image.open(io.BytesIO(p)).convert("RGBA") for p in pngs]
    w, h = imgs[0].size
    rows = (len(imgs) + cols - 1) // cols
    out = Image.new("RGBA", (w * cols, h * rows), bg)
    for i, im in enumerate(imgs):
        tile = Image.new("RGBA", im.size, bg)
        tile.alpha_composite(im)
        out.paste(tile, ((i % cols) * w, (i // cols) * h))
    return out


async def run_r7(data_dir: Path, idea: str) -> None:
    token = secrets.token_hex(16)
    proc, port = spawn(data_dir, token)
    h = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", headers=h, timeout=120) as c, \
                websockets.connect(f"ws://127.0.0.1:{port}/ws?t={token}", max_size=None) as ws:
            r = await c.post("/api/runs", json={"recipe_id": "procedural-3d", "title": "e2e r7",
                                                "inputs": {"idea": idea}})
            assert r.is_success, r.text
            run_id = r.json()["run_id"]
            t0 = time.perf_counter()
            statuses: dict[str, str] = {}
            final = None
            while final is None:
                ev = json.loads(await asyncio.wait_for(ws.recv(), 1200))
                if ev.get("run_id") not in (None, run_id):
                    continue
                t = ev["type"]
                if t == "step.updated":
                    s = ev["data"]
                    if statuses.get(s["key"]) != s["status"]:
                        statuses[s["key"]] = s["status"]
                        print(f"{time.perf_counter() - t0:7.1f}s {s['key']:9} {s['status']}"
                              + (f" err={s['error']}" if s.get("error") else ""), flush=True)
                elif t == "step.log":
                    print(f"{time.perf_counter() - t0:7.1f}s   log: {ev['data']['msg'][:220]}", flush=True)
                elif t == "gate.awaiting":
                    print(f"gate {ev['data']['kind']}: auto-approve", flush=True)
                    g = await c.post(f"/api/gates/{ev['step_id']}", json={"approved": True})
                    assert g.is_success, g.text
                elif t == "run.updated" and ev["data"].get("status") in ("succeeded", "failed", "cancelled",
                                                                         "interrupted"):
                    final = ev["data"]["status"]
            print("run:", final, f"{time.perf_counter() - t0:.1f}s", flush=True)
            steps = {s["key"]: s for s in (await c.get(f"/api/runs/{run_id}")).json()["steps"]}
            for s in steps.values():
                dur = (s["ended_at"] - s["started_at"]) if s.get("ended_at") and s.get("started_at") else None
                print(f"  {s['key']:9} {s['kind']:22} {s['status']:9} " + (f"{dur:7.1f}s" if dur else "      -"))
            build = steps["build"]
            meta = {k: v for k, v in build["meta"].items() if k not in ("spec", "ready_at")}
            print("build meta:", json.dumps(meta, indent=1)[:3000])
            if build["outputs"].get("images"):
                pngs = [(await c.get(f"/api/blobs/{b['hash']}")).content for b in build["outputs"]["images"]]
                grid(pngs, len(pngs)).save(data_dir / "r7_review_views.png")
                code = (await c.get(f"/api/blobs/{build['outputs']['code'][0]['hash']}")).content
                (data_dir / "r7_best.py").write_bytes(code)
                rounds = (await c.get(f"/api/blobs/{build['outputs']['json'][0]['hash']}")).json()
                (data_dir / "r7_rounds.json").write_text(json.dumps(rounds, indent=1), encoding="utf-8")
            if steps["turntable"]["outputs"].get("images"):
                pngs = [(await c.get(f"/api/blobs/{b['hash']}")).content
                        for b in steps["turntable"]["outputs"]["images"]]
                grid(pngs, 4).save(data_dir / "r7_turntable.png")
            if steps["validate"].get("meta"):
                print("validate:", json.dumps({k: v for k, v in steps["validate"]["meta"].items()
                                               if k not in ("spec", "ready_at")})[:600])
            print("saved previews in", data_dir)
            await c.post("/api/shutdown")
    finally:
        try:
            proc.wait(90)
        except Exception:
            proc.kill()
    print("core exited rc", proc.returncode)


class Echo:
    kind = "t.echo"
    version = "1"

    def __init__(self) -> None:
        from studio_core.contracts import Lane
        self.lane = Lane.CPU
        self.params: dict[str, Any] = {}

    def cacheable(self, params: dict[str, Any]) -> bool:
        return False

    def cache_material(self, params: dict[str, Any]) -> str:
        return ""

    async def run(self, ctx: Any, params: dict[str, Any], inputs: Any) -> Any:
        from studio_core.contracts import StepResult
        self.params = params
        return StepResult(outputs={})


async def run_brief(data_dir: Path, idea: str) -> None:
    from studio_core.adapters import register as register_adapters
    from studio_core.config import load_settings
    from studio_core.contracts import ExecutorRegistry
    from studio_core.engine import create_engine
    from studio_core.store import Database, FsBlobStore, MemoryEventBus, SqliteRepo

    rdir = data_dir / "recipes"
    rdir.mkdir(parents=True, exist_ok=True)
    (rdir / "brief-check.yaml").write_text(BRIEF_RECIPE, encoding="utf-8")
    settings = load_settings(data_dir).model_copy(update={"recipes_dir": rdir})
    db = Database(settings.db_path)
    await db.open()
    repo = SqliteRepo(db)
    blobs = FsBlobStore(settings.blobs_dir, settings.thumbs_dir, repo)
    bus = MemoryEventBus()
    bus.bind_loop(asyncio.get_running_loop())
    registry = ExecutorRegistry()
    register_adapters(registry, settings, bus, blobs)
    echo = Echo()
    registry.register(echo)  # type: ignore[arg-type]
    engine = create_engine(settings=settings, repo=repo, blobs=blobs, bus=bus, registry=registry, resources=[])
    try:
        t0 = time.perf_counter()
        run_id = await engine.start_run("brief-check", {"idea": idea})
        await engine.wait_idle(run_id)
        print("run:", (await repo.get_run(run_id)).status.value, f"{time.perf_counter() - t0:.1f}s")
        steps = {s.key: s for s in await repo.get_steps(run_id)}
        b = steps["brief"]
        if b.error:
            print("brief error:", b.error)
        else:
            print("brief meta:", json.dumps({k: v for k, v in b.meta.items() if k not in ("spec", "ready_at")}))
            print("brief json:", blobs.path(b.outputs["json"][0].hash).read_text(encoding="utf-8"))
            print("bound params:", json.dumps(echo.params, ensure_ascii=False, indent=1))
    finally:
        await engine.shutdown()
        await db.close()


if __name__ == "__main__":
    dd = Path(sys.argv[1])
    dd.mkdir(parents=True, exist_ok=True)
    mode = sys.argv[2] if len(sys.argv) > 2 else "r7"
    text = sys.argv[3] if len(sys.argv) > 3 else DEFAULT_IDEA[mode]
    asyncio.run(run_r7(dd, text) if mode == "r7" else run_brief(dd, text))
