"""P0-c (+e): supervise ComfyUI via Job Object, t2i -> remove_bg over WS; optional Qwen 8-direction multi-angle."""
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image

from comfy_lib import ComfyClient, ComfyServer

OUT = Path(__file__).parent / "out" / "comfy"
CHARACTER = ("full body game character, stylized 3D render, young knight with blue cape, silver armor and a sword "
             "at his hip, standing in a neutral A-pose, front view facing the camera, centered, isolated on plain "
             "white background, game asset, soft even lighting")
QWEN_DIRS = {  # branch -> (prompt, output node, label)
    "221": ("Rotate the camera 45 degrees to the right.", "373", "r45"),
    "246": ("Rotate the camera 90 degrees to the right.", "374", "r90"),
    "271": ("Rotate the camera 135 degrees to the right.", "376", "r135"),
    "296": ("Rotate the camera 180 degrees to show the back view.", "379", "back"),
    "321": ("Rotate the camera 135 degrees to the left.", "380", "l135"),
    "346": ("Rotate the camera 90 degrees to the left.", "377", "l90"),
    "371": ("Rotate the camera 45 degrees to the left.", "381", "l45"),
    "423": ("Turn the camera to a high-angle view looking down at 45 degrees.", "424", "high45"),
}
QWEN_PROMPT_NODE = {"221": "151", "246": "228", "271": "253", "296": "278", "321": "303", "346": "328",
                    "371": "353", "423": "405"}


def vram():
    out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                         capture_output=True, text=True).stdout.strip()
    return int(out or 0)


def log(**kw):
    print(json.dumps(kw, ensure_ascii=False, default=str), flush=True)


async def main():
    with_qwen = "--qwen" in sys.argv
    OUT.mkdir(parents=True, exist_ok=True)
    log(step="vram_before", mib=vram())
    server = ComfyServer()
    boot_s, stats = await server.start()
    log(step="server_ready", seconds=round(boot_s, 1), version=stats["system"]["comfyui_version"],
        job_pids=server.job_pids(), vram_free=stats["devices"][0]["vram_free"] // 2**20)

    client = ComfyClient(server.base)
    await client.connect()
    try:
        try:
            await client.run("t2i_zimage_turbo", {"57:13.width": "not-a-number"})
            log(step="bad_submit", result="UNEXPECTED_OK")
        except RuntimeError as e:
            log(step="bad_submit", result="rejected", detail=str(e)[:200])

        nodes = []
        t2i = await client.run("t2i_zimage_turbo", {"57:27.text": CHARACTER, "9.filename_prefix": "studio/knight"},
                               on_event=lambda t, d: t == "executing" and nodes.append(d.get("node")))
        log(step="t2i", seconds=t2i["seconds"], files=t2i["files"], timeline=t2i["timeline"],
            nodes_seen=len(nodes), previews=client.previews)

        t2i_warm = await client.run("t2i_zimage_turbo", {"57:27.text": CHARACTER + ", variant",
                                                         "9.filename_prefix": "studio/knight"})
        log(step="t2i_warm", seconds=t2i_warm["seconds"])

        src = client.stage_input(t2i["files"][0], "knight.png")
        rm = await client.run("remove_bg_birefnet", {"17.image": src, "save_rgba.filename_prefix": "studio/knight_rgba"})
        im = Image.open(rm["files"][0])
        log(step="remove_bg", seconds=rm["seconds"], file=rm["files"][0], mode=im.mode, size=im.size,
            corner_alpha=im.getchannel("A").getpixel((0, 0)) if im.mode == "RGBA" else None)

        if with_qwen:
            values = {"25.image": src}
            for branch, (text, out_node, label) in QWEN_DIRS.items():
                values[f"427:{branch}:{QWEN_PROMPT_NODE[branch]}.prompt"] = text
                values[f"{out_node}.filename_prefix"] = f"studio/qwen_{label}"
            prog = {}
            q = await client.run("multi_angles_qwen2511", values,
                                 on_event=lambda t, d: t == "progress" and prog.__setitem__(d["node"], d["max"]))
            log(step="qwen_8dir", seconds=q["seconds"], files=q["files"], progress_nodes=len(prog))
            order = [label for _, _, label in QWEN_DIRS.values()]
            files = sorted(q["files"], key=lambda f: next(i for i, l in enumerate(order) if f"qwen_{l}_" in f.name))
            thumbs = [Image.open(t2i["files"][0]).convert("RGB").resize((256, 256))]
            thumbs += [Image.open(f).convert("RGB").resize((256, 256)) for f in files]
            sheet = Image.new("RGB", (256 * len(thumbs), 256), "white")
            for i, t in enumerate(thumbs):
                sheet.paste(t, (i * 256, 0))
            sheet.save(OUT / "qwen_contact.png")
    finally:
        await client.close()
        log(step="vram_loaded", mib=vram())
        t0 = time.perf_counter()
        pids = server.kill()
        await asyncio.sleep(2)
        alive = [p for p in pids if subprocess.run(["tasklist", "/FI", f"PID eq {p}", "/NH"], capture_output=True,
                                                   text=True).stdout.strip().startswith(("python", "Python"))]
        log(step="killed", pids=pids, alive_after=alive, kill_s=round(time.perf_counter() - t0, 2), vram_after=vram())


if __name__ == "__main__":
    asyncio.run(main())
