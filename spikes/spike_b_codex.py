"""P0-b: Codex headless image gen — output path, timing, size/alpha, --ephemeral, parallelism."""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

from PIL import Image

CODEX_EXE = os.environ.get("CODEX_EXE") or str(Path(os.environ["APPDATA"]) / "npm/node_modules/@openai/codex/node_modules/@openai/codex-win32-x64/vendor/x86_64-pc-windows-msvc/bin/codex.exe")
GEN_DIR = Path.home() / ".codex" / "generated_images"
OUT = Path(__file__).parent / "out" / "codex"
SUFFIX = (
    "\n\nIMPORTANT: Only call your image generation tool once to produce the image. Do not run shell commands, "
    "do not write code, do not try to save files. After the image is generated just reply DONE."
)


def describe(png: Path):
    im = Image.open(png)
    info = {"file": png.name, "size": im.size, "mode": im.mode, "bytes": png.stat().st_size}
    if im.mode == "RGBA":
        a = im.getchannel("A")
        w, h = im.size
        info["corners_alpha"] = [a.getpixel(p) for p in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))]
        info["transparent_ratio"] = round(sum(1 for v in a.getdata() if v == 0) / (w * h), 3)
    return info


async def gen(label, prompt, ephemeral=False):
    job = OUT / label
    job.mkdir(parents=True, exist_ok=True)
    args = [CODEX_EXE, "exec", "--json", "--skip-git-repo-check", "--sandbox", "read-only", "-C", str(job)]
    if ephemeral:
        args.append("--ephemeral")
    args.append(prompt + SUFFIX)
    t0 = time.perf_counter()
    proc = await asyncio.create_subprocess_exec(
        *args, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    thread_id, events, ext_items, last_msg = None, [], [], None
    async for raw in proc.stdout:
        line = raw.decode("utf-8", "replace").strip()
        if not line:
            continue
        ev = json.loads(line)
        events.append(ev["type"])
        if ev["type"] == "thread.started":
            thread_id = ev["thread_id"]
        item = ev.get("item") or {}
        if item.get("type") == "agent_message":
            last_msg = item.get("text")
        elif item.get("type") not in (None, "agent_message", "error", "reasoning"):
            ext_items.append({k: item.get(k) for k in ("type", "id", "status")})
    err = (await proc.stderr.read()).decode("utf-8", "replace")
    await proc.wait()
    dt = time.perf_counter() - t0
    images = sorted((GEN_DIR / thread_id).glob("*.png")) if thread_id else []
    res = {
        "label": label, "ephemeral": ephemeral, "rc": proc.returncode, "seconds": round(dt, 1),
        "thread_id": thread_id, "last_msg": last_msg, "event_types": sorted(set(events)), "items": ext_items,
        "images": [describe(p) for p in images], "stderr_tail": err.strip().splitlines()[-2:],
    }
    print(json.dumps(res, ensure_ascii=False), flush=True)
    return res


async def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "single"
    if mode == "single":
        await gen("potion", "Generate an image: game inventory icon of a red health potion in a corked glass flask, "
                  "stylized 2D, centered, plain white background.")
    elif mode == "alpha":
        await gen("potion_alpha", "Generate an image: game inventory icon of a blue mana potion, stylized 2D, centered. "
                  "Output a PNG with a TRUE alpha channel: transparent background, no checkerboard, no solid fill.",
                  ephemeral=True)
    elif mode == "parallel":
        items = ["golden key", "wooden shield", "green poison bottle"]
        t0 = time.perf_counter()
        await asyncio.gather(*(gen(f"par_{i}", f"Generate an image: game inventory icon of a {name}, stylized 2D, "
                                   "centered, plain white background.") for i, name in enumerate(items)))
        print(json.dumps({"parallel_wall_s": round(time.perf_counter() - t0, 1)}), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
