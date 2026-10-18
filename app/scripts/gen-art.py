"""Generate UI art with headless Codex image gen, then optimize to WebP / icon sizes.

Usage (core venv has Pillow):
  python gen-art.py gen   <manifest.json> <raw_dir> [--only name1,name2] [--parallel 3]
  python gen-art.py check <raw_dir>
  python gen-art.py webp  <raw_dir> <out_dir> <size> [--only ...] [--quality 85]
  python gen-art.py icon  <raw_png> <resources_dir>

Manifest: [{"name": "empty-runs", "prompt": "...", "alpha": true}, ...]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter

CODEX = Path(os.environ["APPDATA"]) / (
    "npm/node_modules/@openai/codex/node_modules/@openai/codex-win32-x64"
    "/vendor/x86_64-pc-windows-msvc/bin/codex.exe"
)
IMAGES_ROOT = Path.home() / ".codex" / "generated_images"
SUFFIX = (
    "\n\nIMPORTANT: Only call your image generation tool once to produce the image. "
    "Do not run shell commands, do not write code, do not try to save files. "
    "After the image is generated just reply DONE."
)
ALPHA_SUFFIX = " Output a PNG with a TRUE alpha channel: transparent background, no checkerboard, no solid fill."


def run_codex(name: str, prompt: str, alpha: bool, raw_dir: Path) -> str:
    full = prompt + (ALPHA_SUFFIX if alpha else "") + SUFFIX
    started = time.time()
    with tempfile.TemporaryDirectory(prefix=f"codex-{name}-") as job_dir:
        proc = subprocess.run(
            [str(CODEX), "exec", "--json", "--skip-git-repo-check", "--sandbox", "read-only",
             "--ephemeral", "-C", job_dir, full],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", timeout=600,
        )
    thread_id = None
    for line in proc.stdout.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "thread.started":
            thread_id = ev.get("thread_id")
    if not thread_id:
        return f"{name}: FAIL no thread (rc={proc.returncode}) {proc.stderr[-300:]}"
    pngs = sorted((IMAGES_ROOT / thread_id).glob("*.png"), key=lambda p: p.stat().st_mtime)
    if not pngs:
        return f"{name}: FAIL no image in thread {thread_id}"
    dest = raw_dir / f"{name}.png"
    shutil.copyfile(pngs[-1], dest)
    return f"{name}: ok {time.time() - started:.0f}s {describe(dest)}"


def describe(path: Path) -> str:
    im = Image.open(path)
    w, h = im.size
    if im.mode != "RGBA":
        return f"{w}x{h} {im.mode} (no alpha)"
    a = im.getchannel("A")
    corners = [a.getpixel(p) for p in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))]
    clear = a.histogram()[0] / (w * h)
    return f"{w}x{h} RGBA corners={corners} transparent={clear:.0%}"


def cmd_gen(args: argparse.Namespace) -> None:
    jobs = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    if args.only:
        wanted = set(args.only.split(","))
        jobs = [j for j in jobs if j["name"] in wanted]
    raw_dir = Path(args.raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        futures = [pool.submit(run_codex, j["name"], j["prompt"], j.get("alpha", True), raw_dir) for j in jobs]
        for f in futures:
            print(f.result(), flush=True)


def cmd_check(args: argparse.Namespace) -> None:
    for p in sorted(Path(args.raw_dir).glob("*.png")):
        print(f"{p.stem}: {describe(p)}")


def despeckle(im: Image.Image, speck: int = 7, keep: int = 11) -> Image.Image:
    """Codex alpha leaves faint isolated specks around the subject; a morphological opening drops anything smaller
    than `speck` px while the wider `keep` dilation preserves the subject's anti-aliased edge."""
    if im.mode != "RGBA":
        return im
    a = im.getchannel("A")
    mask = a.point(lambda v: 255 if v > 24 else 0)
    core = mask.filter(ImageFilter.MinFilter(speck)).filter(ImageFilter.MaxFilter(speck))
    allowed = core.filter(ImageFilter.MaxFilter(keep))
    out = im.copy()
    out.putalpha(ImageChops.multiply(a, allowed))
    return out


def trim_to_square(im: Image.Image, pad_ratio: float) -> Image.Image:
    """Crop to alpha bounds, then re-center on a square canvas so every illustration has equal padding."""
    if im.mode != "RGBA":
        return im
    bbox = im.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
    if not bbox:
        return im
    cropped = im.crop(bbox)
    side = int(max(cropped.size) * (1 + 2 * pad_ratio))
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(cropped, ((side - cropped.width) // 2, (side - cropped.height) // 2))
    return canvas


def cmd_webp(args: argparse.Namespace) -> None:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    wanted = set(args.only.split(",")) if args.only else None
    for p in sorted(Path(args.raw_dir).glob("*.png")):
        if wanted and p.stem not in wanted:
            continue
        im = trim_to_square(despeckle(Image.open(p).convert("RGBA")), args.pad)
        im = im.resize((args.size, args.size), Image.LANCZOS)
        dest = out_dir / f"{p.stem}.webp"
        quality = args.quality
        while True:
            im.save(dest, "WEBP", quality=quality, method=6, alpha_quality=90)
            if dest.stat().st_size <= 80_000 or quality <= 60:
                break
            quality -= 5
        print(f"{dest.name}: {dest.stat().st_size // 1024}KB q={quality}")


def cmd_icon(args: argparse.Namespace) -> None:
    res = Path(args.resources_dir)
    clean = despeckle(Image.open(args.raw_png).convert("RGBA"))
    # Big Sur grid: ~10% margin around the tile at 1024; Windows shell icons read better nearly full-bleed.
    big = trim_to_square(clean, 0.1).resize((1024, 1024), Image.LANCZOS)
    big.save(res / "icon.png", optimize=True)
    tight = trim_to_square(clean, 0.02)
    tight.resize((256, 256), Image.LANCZOS).save(
        res / "icon.ico", sizes=[(s, s) for s in (16, 20, 24, 32, 40, 48, 64, 128, 256)]
    )
    tight.resize((32, 32), Image.LANCZOS).save(res / "tray.png", optimize=True)
    for f in ("icon.png", "icon.ico", "tray.png"):
        print(f"{f}: {(res / f).stat().st_size // 1024}KB")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gen")
    g.add_argument("manifest")
    g.add_argument("raw_dir")
    g.add_argument("--only")
    g.add_argument("--parallel", type=int, default=3)
    c = sub.add_parser("check")
    c.add_argument("raw_dir")
    w = sub.add_parser("webp")
    w.add_argument("raw_dir")
    w.add_argument("out_dir")
    w.add_argument("size", type=int)
    w.add_argument("--only")
    w.add_argument("--quality", type=int, default=85)
    w.add_argument("--pad", type=float, default=0.06)
    i = sub.add_parser("icon")
    i.add_argument("raw_png")
    i.add_argument("resources_dir")
    args = ap.parse_args()
    {"gen": cmd_gen, "check": cmd_check, "webp": cmd_webp, "icon": cmd_icon}[args.cmd](args)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
