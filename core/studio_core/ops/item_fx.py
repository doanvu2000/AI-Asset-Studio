"""Looping item / UI animations (recipes item-spin, item-spin-image, ui-juice). `op.spin_frames` turns a Blender
turntable (blender.render_dirs) into cells that keep the spin axis on the cell centre; `op.item_spin` fakes the spin
of a flat 2D item (horizontal cos squash, darker mirrored back, extruded rim); `op.ui_juice` loops shine sweep, pulse
glow, bob and seeded sparkles over an icon or button. All three emit the cells, a centre-pivot json for op.sheet
(flipbook), GIF + animated WebP previews, a contact strip and a QA report (seam, axis drift, edge fringe)."""
from __future__ import annotations

import asyncio
import io
import math
import random
from typing import Any

import numpy as np
from PIL import Image, ImageFilter

from ..contracts import Outputs, StepContext, StepError, StepResult
from . import image as im
from .anim_video import checker, contact_strip
from .executors import OpExecutor, _images_in, _load, _put_png, _read_json
from .rig import _put_json

ALPHA_ON = 8
JUICE_EFFECTS = ("shine", "glow", "bob", "sparkle")
BACKS = ("mirror", "dark", "flat")


def _even(v: float) -> int:
    return max(2, round(v / 2) * 2)


def to_pm(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("RGBA").convert("RGBa"), np.float32) / 255


def from_pm(a: np.ndarray) -> Image.Image:
    data = (np.clip(a, 0, 1) * 255 + 0.5).astype(np.uint8)
    return Image.frombytes("RGBa", (a.shape[1], a.shape[0]), data.tobytes()).convert("RGBA")


def resize_pm(img: Image.Image, size: tuple[int, int], box: tuple[float, float, float, float] | None = None
              ) -> Image.Image:
    """Premultiplied resampling: straight-alpha LANCZOS pulls the black of transparent pixels into the edge."""
    return img.convert("RGBA").convert("RGBa").resize(size, Image.Resampling.LANCZOS, box=box).convert("RGBA")


def cols_for(n: int) -> int:
    return {12: 4, 16: 4, 24: 6, 32: 8}.get(n) or max(1, math.ceil(math.sqrt(n)))


def _small(img: Image.Image) -> np.ndarray:
    side = 64
    w, h = img.size
    s = side / max(w, h)
    return to_pm(img.resize((max(1, round(w * s)), max(1, round(h * s))), Image.Resampling.BILINEAR))


def loop_seam(cells: list[Image.Image]) -> dict[str, Any]:
    """The wrap (last -> first) must be no bigger a jump than the steps inside the loop."""
    small = [_small(c) for c in cells]
    steps = [float(np.abs(small[i + 1] - small[i]).mean()) for i in range(len(small) - 1)]
    seam = float(np.abs(small[0] - small[-1]).mean())
    top = max(steps) if steps else 0.0
    return {"seam_diff": round(seam, 5), "step_diff_mean": round(float(np.mean(steps)) if steps else 0.0, 5),
            "step_diff_max": round(top, 5), "seam_ok": seam <= top * 1.001 + 1e-6}


def _centroid_x(img: Image.Image) -> float | None:
    a = np.asarray(img.convert("RGBA"))[..., 3].astype(np.float64)
    tot = a.sum()
    if tot <= 0:
        return None
    return float((a.sum(0) * (np.arange(a.shape[1]) + 0.5)).sum() / tot)


def axis_error(cells: list[Image.Image], pivot_x: float) -> float:
    """A half turn mirrors an orthographic silhouette about the spin axis, so frames i and i + n/2 put their alpha
    centroids symmetric around it; returns the worst distance of that midpoint from the pivot column (px)."""
    n = len(cells)
    if n < 2 or n % 2:
        return 0.0
    worst = 0.0
    for i in range(n // 2):
        a, b = _centroid_x(cells[i]), _centroid_x(cells[i + n // 2])
        if a is not None and b is not None:
            worst = max(worst, abs((a + b) / 2 - pivot_x))
    return round(worst, 3)


def core_drift(cells: list[Image.Image], expected: list[tuple[float, float]]) -> float:
    """Max distance between the opaque core's centroid and where the animation put it (bob offset), relative to
    frame 0: the item must not wander inside its cell."""
    pts = []
    for c in cells:
        a = np.asarray(c.convert("RGBA"))[..., 3] >= 250
        ys, xs = np.nonzero(a)
        pts.append((float(xs.mean()) + 0.5, float(ys.mean()) + 0.5) if len(xs) else None)
    if pts[0] is None:
        return 0.0
    worst = 0.0
    for p, e in zip(pts, expected):
        if p is None:
            continue
        dx = p[0] - pts[0][0] - (e[0] - expected[0][0])
        dy = p[1] - pts[0][1] - (e[1] - expected[0][1])
        worst = max(worst, math.hypot(dx, dy))
    return round(worst, 3)


def edge_luma(img: Image.Image) -> float | None:
    """Mean luma of semi-transparent edge pixels over that of the opaque ones: a dark (black) fringe drops it."""
    a = np.asarray(img.convert("RGBA"), np.float32)
    luma = a[..., :3] @ np.array([0.299, 0.587, 0.114], np.float32)
    semi = (a[..., 3] > 24) & (a[..., 3] < 200)
    solid = a[..., 3] >= 250
    if semi.sum() < 8 or solid.sum() < 8:
        return None
    return float(luma[semi].mean() / max(1.0, float(luma[solid].mean())))


def fringe_ratio(cells: list[Image.Image], sources: list[Image.Image]) -> float | None:
    out = [v for v in (edge_luma(c) for c in cells) if v is not None]
    src = [v for v in (edge_luma(s) for s in sources) if v is not None]
    if not out or not src:
        return None
    return round(float(np.mean(out)) / max(1e-6, float(np.mean(src))), 3)


def _preview_frame(c: Image.Image, side: int, bg: Image.Image | None) -> Image.Image:
    s = side / max(c.size)
    f = resize_pm(c, (max(1, round(c.width * s)), max(1, round(c.height * s)))) if s != 1 else c.convert("RGBA")
    out = bg.copy() if bg is not None else Image.new("RGBA", (side, side), (0, 0, 0, 0))
    out.alpha_composite(f, ((side - f.width) // 2, (side - f.height) // 2))
    return out


def previews(cells: list[Image.Image], fps: float, side: int = 256) -> tuple[bytes, bytes]:
    """GIF on a checker (GIF has no soft alpha) and a lossless animated WebP that keeps it, both looping forever."""
    ms = max(20, round(1000 / max(fps, 1)))
    bg = checker((side, side))
    gif_frames = [_preview_frame(c, side, bg).convert("RGB").convert("P", palette=Image.Palette.ADAPTIVE, colors=255)
                  for c in cells]
    gif = io.BytesIO()
    gif_frames[0].save(gif, "GIF", save_all=True, append_images=gif_frames[1:], duration=ms, loop=0, disposal=2)
    webp_frames = [_preview_frame(c, side, None) for c in cells]
    webp = io.BytesIO()
    webp_frames[0].save(webp, "WEBP", save_all=True, append_images=webp_frames[1:], duration=ms, loop=0,
                        lossless=True, method=4)
    return gif.getvalue(), webp.getvalue()


def _shift(img: Image.Image, dx: float, dy: float, size: tuple[int, int]) -> np.ndarray:
    """Premultiplied subpixel paste of `img` with its top-left at (dx, dy) on a transparent canvas of `size`."""
    pm = img.convert("RGBA").convert("RGBa")
    moved = pm.transform(size, Image.Transform.AFFINE, (1, 0, -dx, 0, 1, -dy), Image.Resampling.BILINEAR)
    return np.asarray(moved, np.float32) / 255


def _over(top: np.ndarray, bottom: np.ndarray) -> np.ndarray:
    return top + bottom * (1 - top[..., 3:4])


def _rim_color(face: Image.Image) -> np.ndarray:
    a = np.asarray(face.convert("RGBA"), np.float32)
    solid = a[..., 3] > 128
    inner = np.asarray(Image.fromarray((solid * 255).astype(np.uint8)).filter(ImageFilter.MinFilter(5))) > 0
    ring = solid & ~inner
    pick = ring if ring.sum() >= 4 else solid
    if not pick.any():
        return np.array([0.5, 0.5, 0.5], np.float32)
    return a[..., :3][pick].mean(0) / 255


def _shade(img: Image.Image, k: float, desat: float = 0.0) -> Image.Image:
    a = np.asarray(img.convert("RGBA"), np.float32)
    rgb = a[..., :3]
    if desat:
        g = rgb @ np.array([0.299, 0.587, 0.114], np.float32)
        rgb = rgb * (1 - desat) + g[..., None] * desat
    a[..., :3] = np.clip(rgb * k, 0, 255)
    return Image.fromarray(a.astype(np.uint8), "RGBA")


def spin_2d(src: Image.Image, frames: int, size: int, thickness: int = 6, back: str = "mirror",
            back_shade: float = 0.62, pad: float = 0.08) -> tuple[list[Image.Image], tuple[int, int]]:
    """Flat item spinning about the vertical axis through its centre: the face squashes by |cos|, past 90 degrees
    the (mirrored, darker) back shows, and a rim `thickness` px deep fills the gap between both faces by |sin|."""
    if back not in BACKS:
        raise ValueError(f"back must be one of {list(BACKS)}, got {back!r}")
    if frames < 4:
        raise ValueError("a spin needs at least 4 frames")
    size = _even(size)
    face = im.trim(src)
    if face.getbbox() is None:
        raise ValueError("the image is empty")
    inner = size * (1 - 2 * pad)
    s = min((inner - thickness) / face.width, inner / face.height)
    fw, fh = max(2, round(face.width * s)), max(2, round(face.height * s))
    front = resize_pm(face, (fw, fh))
    rim = _rim_color(front)
    if back == "mirror":
        rear = _shade(front.transpose(Image.Transpose.FLIP_LEFT_RIGHT), back_shade)
    elif back == "dark":
        rear = _shade(front.transpose(Image.Transpose.FLIP_LEFT_RIGHT), back_shade * 0.8, desat=0.6)
    else:
        flat = Image.new("RGBA", front.size, tuple(int(v * 255 * back_shade) for v in rim) + (255,))
        flat.putalpha(front.getchannel("A"))
        rear = flat
    cells = []
    cx, top = size / 2, (size - fh) / 2
    for i in range(frames):
        th = 2 * math.pi * i / frames
        c, sn = math.cos(th), math.sin(th)
        w = max(1.0, abs(c) * fw)
        shown = front if c >= 0 else rear
        lit = 0.82 + 0.18 * abs(c)
        squashed = _shade(resize_pm(shown, (max(1, math.ceil(w)), fh)), lit)
        dx_face = thickness / 2 * sn * (1 if c >= 0 else -1)
        canvas = np.zeros((size, size, 4), np.float32)
        if thickness > 0 and abs(sn) > 1e-3:
            sil = np.asarray(squashed.getchannel("A"), np.float32) / 255
            edge = np.zeros((fh, squashed.width, 4), np.float32)
            edge[..., :3] = rim * (0.55 + 0.25 * abs(sn))
            edge[..., 3] = 1.0
            edge *= sil[..., None]
            edge_img = from_pm(edge)
            n = max(1, math.ceil(abs(2 * dx_face)))
            for k in range(n + 1):
                o = -dx_face + 2 * dx_face * k / n
                canvas = _over(canvas, _shift(edge_img, cx - squashed.width / 2 + o, top, (size, size)))
        canvas = _over(_shift(squashed, cx - squashed.width / 2 + dx_face, top, (size, size)), canvas)
        cells.append(from_pm(canvas))
    return cells, (size // 2, size // 2)


def spin_cells(renders: list[Image.Image], axis_x: float, size: int, pad: float = 0.06
               ) -> tuple[list[Image.Image], tuple[int, int]]:
    """One square crop for every turntable frame, centred horizontally on the spin axis column, so the item turns in
    place; premultiplied downscale to `size`."""
    size = _even(size)
    union = None
    for r in renders:
        bb = r.getchannel("A").point(lambda v: 255 if v > ALPHA_ON else 0).getbbox()
        if bb is None:
            continue
        union = bb if union is None else (min(union[0], bb[0]), min(union[1], bb[1]), max(union[2], bb[2]),
                                          max(union[3], bb[3]))
    if union is None:
        raise ValueError("every render is empty")
    half_w = max(axis_x - union[0], union[2] - axis_x)
    side = max(2 * half_w, union[3] - union[1]) * (1 + 2 * pad)
    cy = (union[1] + union[3]) / 2
    box = (axis_x - side / 2, cy - side / 2, axis_x + side / 2, cy + side / 2)
    m = math.ceil(side)
    cells = []
    for r in renders:
        big = Image.new("RGBA", (r.width + 2 * m, r.height + 2 * m), (0, 0, 0, 0))
        big.paste(r.convert("RGBA"), (m, m))
        cells.append(resize_pm(big, (size, size), (box[0] + m, box[1] + m, box[2] + m, box[3] + m)))
    return cells, (size // 2, size // 2)


def _star(px: int) -> np.ndarray:
    """Four-point twinkle star alpha, `px` square: thin cross arms plus a soft core."""
    px = max(3, px)
    y, x = np.mgrid[-1:1:complex(px), -1:1:complex(px)]
    ax, ay = np.abs(x), np.abs(y)
    arms = np.exp(-(ax * 14 + ay * 2.2)) + np.exp(-(ay * 14 + ax * 2.2))
    core = np.exp(-(x * x + y * y) * 18)
    return np.clip(arms + core, 0, 1).astype(np.float32)


def parse_hex(v: Any, default: str = "#fff4c2") -> np.ndarray:
    s = str(v or default).strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    try:
        return np.array([int(s[i:i + 2], 16) / 255 for i in (0, 2, 4)], np.float32)
    except ValueError:
        raise ValueError(f"bad colour {v!r}, want #rrggbb") from None


def parse_effects(v: Any) -> list[str]:
    raw = v if isinstance(v, list) else str(v or "").replace(";", ",").split(",")
    out = [str(e).strip().lower() for e in raw if str(e).strip()]
    bad = [e for e in out if e not in JUICE_EFFECTS]
    if bad:
        raise ValueError(f"unknown effect(s) {bad}; choose from {list(JUICE_EFFECTS)}")
    if not out:
        raise ValueError("pick at least one effect")
    return list(dict.fromkeys(out))


def ui_juice(src: Image.Image, frames: int, size: int, effects: list[str], color: np.ndarray, seed: int = 7,
             strength: float = 1.0, bob: float = 0.04, sparkles: int = 4
             ) -> tuple[list[Image.Image], tuple[int, int], list[tuple[float, float]]]:
    """Every effect is a function of the loop phase i/frames, so frame `frames` would equal frame 0 (seamless).
    Returns the cells, the centre pivot and the base layer's top-left per frame (for the drift check)."""
    if frames < 4:
        raise ValueError("a loop needs at least 4 frames")
    base = im.trim(src)
    if base.getbbox() is None:
        raise ValueError("the image is empty")
    glow_m = 0.10 if "glow" in effects else 0.02
    bob_m = bob if "bob" in effects else 0.0
    spark_m = 0.04 if "sparkle" in effects else 0.0
    margin_f = glow_m + bob_m + spark_m
    avail = size * (1 - 2 * margin_f)
    s = avail / max(base.size)
    bw, bh = max(2, round(base.width * s)), max(2, round(base.height * s))
    item = resize_pm(base, (bw, bh))
    margin = size * margin_f
    W, H = _even(bw + 2 * margin), _even(bh + 2 * margin)
    x0, y0 = (W - bw) / 2, (H - bh) / 2
    amp = bob_m * size
    alpha = np.asarray(item.getchannel("A"), np.float32) / 255

    glow = None
    if "glow" in effects:
        r = max(2.0, glow_m * size * 0.45)
        pad = math.ceil(r * 3)
        a_img = Image.new("L", (bw + 2 * pad, bh + 2 * pad), 0)
        # Solid body only: faint halos / drop shadows already in the art would otherwise spread the glow into a slab.
        a_img.paste(item.getchannel("A").point(lambda v: 255 if v > 128 else 0), (pad, pad))
        g = np.asarray(a_img.filter(ImageFilter.GaussianBlur(r)), np.float32) / 255
        glow = (np.clip(g * 1.1, 0, 1), pad)

    rng = random.Random(seed)
    stars = []
    if "sparkle" in effects:
        ys, xs = np.nonzero(alpha > 0.5)
        if len(xs):
            for k in range(max(1, sparkles)):
                j = rng.randrange(len(xs))
                stars.append({"x": float(xs[j]), "y": float(ys[j]), "phase": (k + rng.random() * 0.6) / max(1, sparkles),
                              "life": 0.28 + rng.random() * 0.14, "size": size * (0.07 + rng.random() * 0.05)})
    star_cache: dict[int, np.ndarray] = {}

    ang = math.radians(25)
    d = np.array([math.cos(ang), math.sin(ang)], np.float32)
    yy, xx = np.mgrid[0:bh, 0:bw].astype(np.float32)
    proj = xx * d[0] + yy * d[1]
    lo, hi = float(proj.min()), float(proj.max())
    band = max(3.0, 0.07 * (hi - lo))
    sweep = 0.45

    cells, tops = [], []
    for i in range(frames):
        t = i / frames
        ph = 2 * math.pi * t
        oy = -amp * math.sin(ph) if amp else 0.0
        px, py = x0, y0 + oy
        tops.append((px, py))
        item_pm = to_pm(item)
        if "shine" in effects and t < sweep:
            pos = lo - band + (t / sweep) * (hi - lo + 2 * band)
            k = np.exp(-((proj - pos) / band) ** 2) * min(1.0, 0.7 * strength)
            lit = item_pm[..., :3] + color[None, None, :] * item_pm[..., 3:4] * k[..., None]
            item_pm[..., :3] = np.minimum(lit, item_pm[..., 3:4])
        canvas = np.zeros((H, W, 4), np.float32)
        if glow is not None:
            g, pad = glow
            pulse = (0.3 + 0.7 * (0.5 - 0.5 * math.cos(ph))) * min(1.0, 0.8 * strength)
            ga = np.zeros((g.shape[0], g.shape[1], 4), np.float32)
            ga[..., 3] = g * pulse
            ga[..., :3] = color * ga[..., 3:4]
            canvas = _over(_shift(from_pm(ga), px - pad, py - pad, (W, H)), canvas)
        canvas = _over(_shift(from_pm(item_pm), px, py, (W, H)), canvas)
        for st in stars:
            u = ((t - st["phase"]) % 1.0) / st["life"]
            if u >= 1:
                continue
            spx = max(3, round(st["size"] * math.sin(math.pi * u)))
            if spx < 3:
                continue
            ker = star_cache.get(spx)
            if ker is None:
                ker = star_cache[spx] = _star(spx)
            layer = np.zeros((spx, spx, 4), np.float32)
            layer[..., 3] = ker * min(1.0, strength)
            layer[..., :3] = (0.6 + 0.4 * color) * layer[..., 3:4]
            canvas = _over(_shift(from_pm(layer), px + st["x"] - spx / 2, py + st["y"] - spx / 2, (W, H)), canvas)
        cells.append(from_pm(canvas))
    return cells, (W // 2, H // 2), tops


async def _finish(ctx: StepContext, cells: list[Image.Image], pivot: tuple[int, int], fps: float, clip: str,
                  report: dict[str, Any], sources: list[Image.Image]) -> Outputs:
    report |= loop_seam(cells)
    report["fringe_ratio"] = fringe_ratio(cells, sources)
    report |= {"frames": len(cells), "fps": fps, "cols": cols_for(len(cells)), "cell": list(cells[0].size),
               "pivot": list(pivot)}
    if not report["seam_ok"]:
        ctx.log(f"loop seam {report['seam_diff']} exceeds the largest step {report['step_diff_max']}", "warning")
    if report.get("fringe_ratio") is not None and report["fringe_ratio"] < 0.85:
        ctx.log(f"edges darkened (fringe ratio {report['fringe_ratio']})", "warning")
    gif, webp = await asyncio.to_thread(previews, cells, fps)
    strip = await asyncio.to_thread(contact_strip, cells, 8, 160)
    w, h = cells[0].size
    out: Outputs = {
        "images": [await _put_png(ctx, c, f"{clip}_{i:02d}.png") for i, c in enumerate(cells)],
        "pivot": [await _put_json(ctx, {"pivot": list(pivot), "width": w, "height": h}, f"{clip}_pivot.json")],
        "json": [await _put_json(ctx, report, f"{clip}.json")],
        "gif": [await ctx.blobs.put_bytes(gif, name=f"{clip}_loop.gif", mime="image/gif")],
        "webp": [await ctx.blobs.put_bytes(webp, name=f"{clip}_loop.webp", mime="image/webp")],
        "qa": [await _put_png(ctx, strip, f"{clip}_qa.png")],
    }
    return out


def _frames_param(params: dict[str, Any], default: int) -> int:
    n = int(params.get("frames") or default)
    if not 4 <= n <= 64:
        raise StepError("op_bad_param", f"frames must be 4-64, got {n}")
    return n


class SpinFrames(OpExecutor):
    kind = "op.spin_frames"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        refs = _images_in(inputs)
        if len(refs) < 4:
            raise StepError("op_bad_input", f"a turntable needs at least 4 renders, got {len(refs)}")
        renders = [await _load(ctx, r) for r in refs]
        axis = renders[0].width / 2
        if inputs.get("json"):
            info = await _read_json(ctx, inputs["json"][0])
            if isinstance(info, dict) and isinstance(info.get("pivot"), list) and info["pivot"]:
                axis = float(info["pivot"][0]) * renders[0].width / float(info.get("width") or renders[0].width)
        size = int(params.get("size") or 128)
        fps = float(params.get("fps") or 12)
        clip = str(params.get("clip") or "spin")
        try:
            cells, pivot = await asyncio.to_thread(spin_cells, renders, axis, size, float(params.get("pad", 0.06)))
        except ValueError as e:
            raise StepError("op_bad_input", str(e)) from e
        report: dict[str, Any] = {"mode": "mesh", "axis_error_px": axis_error(cells, pivot[0])}
        out = await _finish(ctx, cells, pivot, fps, clip, report, renders)
        return StepResult(outputs=out, meta=report)


class ItemSpin(OpExecutor):
    kind = "op.item_spin"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        refs = _images_in(inputs)
        src = await _load(ctx, refs[0])
        n = _frames_param(params, 16)
        size = int(params.get("size") or 128)
        fps = float(params.get("fps") or 12)
        clip = str(params.get("clip") or "spin")
        try:
            cells, pivot = await asyncio.to_thread(
                spin_2d, src, n, size, int(params.get("thickness", 6)), str(params.get("back") or "mirror"),
                float(params.get("back_shade", 0.62)))
        except ValueError as e:
            raise StepError("op_bad_param", str(e)) from e
        report: dict[str, Any] = {"mode": "image", "axis_error_px": axis_error(cells, pivot[0])}
        out = await _finish(ctx, cells, pivot, fps, clip, report, [im.trim(src)])
        return StepResult(outputs=out, meta=report)


class UiJuice(OpExecutor):
    kind = "op.ui_juice"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        refs = _images_in(inputs)
        src = await _load(ctx, refs[0])
        n = _frames_param(params, 24)
        size = int(params.get("size") or 256)
        fps = float(params.get("fps") or 12)
        clip = str(params.get("clip") or "juice")
        try:
            effects = parse_effects(params.get("effects") or "shine,glow")
            color = parse_hex(params.get("color"))
            cells, pivot, tops = await asyncio.to_thread(
                ui_juice, src, n, size, effects, color, int(params.get("seed", 7)),
                float(params.get("strength", 1.0)), float(params.get("bob", 0.04)), int(params.get("sparkles", 4)))
        except ValueError as e:
            raise StepError("op_bad_param", str(e)) from e
        report: dict[str, Any] = {"mode": "juice", "effects": effects, "drift_px": core_drift(cells, tops)}
        out = await _finish(ctx, cells, pivot, fps, clip, report, [im.trim(src)])
        return StepResult(outputs=out, meta=report)


ITEM_FX_OPS: tuple[type[OpExecutor], ...] = (SpinFrames, ItemSpin, UiJuice)
