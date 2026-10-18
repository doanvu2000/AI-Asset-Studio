"""P0-d: resident headless Blender worker renders a TRELLIS GLB in 8 ortho directions."""
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image

BLENDER = r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
GLB = r"L:\AI\setup\comfyUI\output\3d\ComfyUI_00001.glb"
HERE = Path(__file__).parent
OUT = HERE / "out" / "blender"


def log(**kw):
    print(json.dumps(kw, ensure_ascii=False), flush=True)


class Worker:
    def __init__(self):
        t0 = time.perf_counter()
        self.proc = subprocess.Popen(
            [BLENDER, "-b", "--factory-startup", "-noaudio", "-P", str(HERE / "blender_worker.py"), "--", "0"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
            errors="replace")
        self.boot_log = []
        for line in self.proc.stdout:
            self.boot_log.append(line.rstrip())
            if line.startswith("WORKER_READY"):
                port = int(line.split()[1])
                break
        else:
            raise RuntimeError("worker died:\n" + "\n".join(self.boot_log[-30:]))
        self.sock = socket.create_connection(("127.0.0.1", port))
        self.f = self.sock.makefile("rwb")
        self.boot_s = time.perf_counter() - t0
        self.n = 0

    def call(self, cmd, **params):
        self.n += 1
        t0 = time.perf_counter()
        self.f.write((json.dumps({"id": self.n, "cmd": cmd, "params": params}) + "\n").encode())
        self.f.flush()
        resp = json.loads(self.f.readline())
        resp["rtt_s"] = round(time.perf_counter() - t0, 2)
        if not resp["ok"]:
            raise RuntimeError(resp["trace"])
        return resp

    def close(self):
        self.f.write(b'{"cmd":"quit"}\n')
        self.f.flush()
        self.proc.wait(30)


def contact(frames, path, bg=(255, 0, 255, 255)):
    ims = [Image.open(f["file"]).convert("RGBA").resize((192, 192)) for f in frames]
    sheet = Image.new("RGBA", (192 * len(ims), 192), bg)
    for i, im in enumerate(ims):
        sheet.alpha_composite(im, (i * 192, 0))
    sheet.save(path)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    w = Worker()
    log(step="boot", seconds=round(w.boot_s, 2), ping=w.call("ping")["result"], engines=w.call("engines")["result"])
    jobs = [
        ("e30", dict(elevation=30)),
        ("e60", dict(elevation=60)),
        ("e30_mirror", dict(elevation=30, mirror=True)),
    ]
    if "--cycles" in sys.argv:
        jobs.append(("e30_cycles", dict(elevation=30, engine="CYCLES", samples=32)))
    for name, extra in jobs:
        out_dir = OUT / name
        out_dir.mkdir(exist_ok=True)
        r = w.call("render_dirs", glb=GLB, out_dir=str(out_dir).replace("\\", "/"), res=512, **extra)
        res = r["result"]
        frames = res.pop("frames")
        im0 = Image.open(frames[0]["file"])
        log(step=name, rtt_s=r["rtt_s"], **res, frame_s=[f["s"] for f in frames], mode=im0.mode, size=im0.size,
            corner_alpha=im0.getchannel("A").getpixel((0, 0)))
        contact(frames, OUT / f"{name}_contact.png")
    w.close()


if __name__ == "__main__":
    main()
