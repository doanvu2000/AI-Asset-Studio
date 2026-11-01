"""Animated tiles (recipe tile-animated). `op.tile_anim_prep` makes the tile tileable and, for the video kinds, lays it
out 3×3 for Wan 2.2; `op.tile_anim_candidates` shows each Wan clip for gate.pick; `op.tile_animate` builds the loop:
water / lava / grass_wind warp the tile with noise that is periodic in x, y and time, so every frame wraps at the tile
edge and frame N equals frame 0; torch / waterfall / custom take the picked clip, rebuild the tile edges from the
neighbouring generated cells, find the repeat and crossfade it. A blob-47 autotile input re-renders the whole set
per frame with the same motion, so the transitions move together with the fill."""
from __future__ import annotations

import asyncio
import io
import json
import math
from typing import Any

import numpy as np
from PIL import Image

from ..contracts import BlobRef, Outputs, StepContext, StepError, StepResult
from . import autotile as at
from .anim_video import (
    contact_strip,
    crossfade_seam,
    find_cycle,
    sample,
    similarity_matrix,
)
from .executors import OpExecutor, _images_in, _load, _put_png, _read_json
from .keyart import FPS, frame_count
from .rig import _put_json
from .tile import _lum, _rgb, _seam_ratio, box_blur, seamless, tile_report, wrap_resize

TILE_ANIM_FILE = "tile_anim.json"
TILE_ANIM_TYPE = "asset-studio/tile-animated"
CPU_KINDS = ("water", "lava", "grass_wind")
VIDEO_KINDS = ("torch", "waterfall", "custom")
KINDS = CPU_KINDS + VIDEO_KINDS
SCROLLS = {"none": (0, 0), "down": (0, 1), "up": (0, -1), "right": (1, 0), "left": (-1, 0)}
LAYERS = ("auto", "a", "b", "both")
# Square 3×3 canvases a multiple of 3 × 32, so Wan's 32 px grid and the cell grid agree (640² is the 480p budget).
CANVAS = {"480p": 672, "540p": 768, "720p": 960}
# tile_report's seam_score is 100·exp(-0.35·(ratio - 1.1)) and passes at 70: the same ratio bounds an animated edge.
SEAM_RATIO_MAX = round(1.1 + math.log(100 / 70) / 0.35, 3)
# The r7 live waterfall looped at seam_similarity 0.159 and visibly jumped: below this the clip has no real repeat.
LOOP_SIMILARITY_MIN = 0.3
# A flame mask weaker than this (blue flame, flat image) would leave the torch frozen, so the whole tile moves instead.
MASK_MIN = 0.05
# Wan clip kinds without a clean repeat fall back to the CPU motion that reads closest; torch also gets a flame mask.
FALLBACK = {"waterfall": ("water", "down"), "torch": ("lava", "none"), "custom": ("water", "none")}
MOTION_WORDS = {"water": ("water", "sea", "ocean", "river", "lake", "pond", "swamp"),
                "lava": ("lava", "magma", "fire"), "grass_wind": ("grass", "meadow", "field", "wheat", "bush")}
VIDEO_PROMPTS = {
    "torch": "a wall torch mounted on the stone wall tile: only the flames flicker and dance, small embers rise and "
             "fade, warm firelight pulses softly on the wall around it, the torch and the wall stay perfectly still",
    "waterfall": "water pours straight down continuously, white foam streaks and ripples stream downward",
    "custom": "",
}
VIDEO_TAIL = ("The image is a seamless repeating 3x3 tile pattern: all nine tiles move in exactly the same way. "
              "Locked-off static camera, no zoom, no pan, no camera shake, no cuts, the layout stays perfectly aligned, "
              "only the described motion happens, game texture.")


def video_prompt(kind: str, prompt: str) -> str:
    body = " ".join(p for p in (str(prompt or "").strip(), VIDEO_PROMPTS.get(kind, "")) if p)
    if not body:
        raise StepError("op_bad_param", "custom motion needs a prompt")
    return f"{body}. {VIDEO_TAIL}"


def _terms(seed: int, n: int, kmax: int, cycles: int, direction: tuple[float, float] | None = None) -> np.ndarray:
    """(n, 5) rows (kx, ky, m, amplitude, phase), integer kx, ky, m: sin(2π(kx·u + ky·v + m·t) + φ) wraps in u, v and t.
    With a direction the waves travel along it (a gust front for grass)."""
    rng = np.random.default_rng(seed)
    rows = []
    while len(rows) < n:
        kx, ky = int(rng.integers(-kmax, kmax + 1)), int(rng.integers(-kmax, kmax + 1))
        if kx == ky == 0:
            continue
        if direction is not None and kx * direction[0] + ky * direction[1] <= 0:
            continue
        m = int(rng.integers(1, cycles + 1)) * (1 if direction is None or rng.random() < 0.85 else -1)
        if direction is None and rng.random() < 0.5:
            m = -m
        rows.append((kx, ky, m, 1.0 / math.hypot(kx, ky), float(rng.uniform(0, 2 * math.pi))))
    return np.asarray(rows, np.float64)


def _field(terms: np.ndarray, u: np.ndarray, v: np.ndarray, t: float) -> np.ndarray:
    out = np.zeros(u.shape, np.float64)
    for kx, ky, m, a, ph in terms:
        out += a * np.sin(2 * math.pi * (kx * u + ky * v + m * t) + ph)
    return out / max(float(np.abs(terms[:, 3]).sum()), 1e-6) * 2.0


def warp(a: np.ndarray, dx: np.ndarray, dy: np.ndarray, nearest: bool = False) -> np.ndarray:
    """Samples `a` at (x + dx, y + dy) with wrap-around: a tileable source stays tileable."""
    h, w = a.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    x, y = xx + dx, yy + dy
    if nearest:
        return a[np.floor(y + 0.5).astype(int) % h, np.floor(x + 0.5).astype(int) % w]
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    fx, fy = (x - x0)[..., None], (y - y0)[..., None]
    x0, y0 = x0 % w, y0 % h
    x1, y1 = (x0 + 1) % w, (y0 + 1) % h
    top = a[y0, x0] * (1 - fx) + a[y0, x1] * fx
    bot = a[y1, x0] * (1 - fx) + a[y1, x1] * fx
    return top * (1 - fy) + bot * fy


def cpu_frames(img: np.ndarray, kind: str, n: int, strength: float = 1.0, seed: int = 0, cycles: int = 1,
               scroll: str = "none", nearest: bool = False) -> list[np.ndarray]:
    """n float RGB frames of one exact loop. Every term has integer frequencies in u, v and t, and the scroll moves one
    whole tile per loop, so frame n would equal frame 0 and every frame wraps like the source."""
    if kind not in CPU_KINDS:
        raise StepError("op_bad_param", f"kind must be one of {list(CPU_KINDS)} for the CPU path, got {kind!r}")
    if scroll not in SCROLLS:
        raise StepError("op_bad_param", f"scroll must be one of {list(SCROLLS)}, got {scroll!r}")
    h, w = img.shape[:2]
    side = float(min(h, w))
    v, u = np.mgrid[0:h, 0:w].astype(np.float64)
    u, v = (u + 0.5) / w, (v + 0.5) / h
    lum = _lum(img)
    lum_n = (lum - lum.min()) / max(float(np.ptp(lum)), 1e-6)
    sx, sy = SCROLLS[scroll]
    if kind == "water":
        flow = [_terms(seed + i, 6, 2, cycles) for i in range(2)]
        caustic = _terms(seed + 7, 7, 3, cycles)
        amp = 0.03 * side * strength
    elif kind == "lava":
        flow = [_terms(seed + i, 4, 1, cycles) for i in range(2)]
        caustic = _terms(seed + 7, 5, 2, cycles)
        amp = 0.045 * side * strength
    else:
        flow = [_terms(seed, 6, 3, cycles, direction=(1.0, 0.25)), _terms(seed + 1, 3, 2, cycles)]
        caustic = _terms(seed + 7, 4, 2, cycles, direction=(1.0, 0.25))
        amp = 0.035 * side * strength
    bright = img[lum >= np.percentile(lum, 90)].mean(0) if lum.size else np.ones(3)
    out = []
    for i in range(n):
        t = i / n
        f1, f2 = _field(flow[0], u, v, t), _field(flow[1], u, v, t)
        if kind == "grass_wind":
            # Blade tips catch the light and sway further than the dark roots between them.
            sway = 0.35 + 0.65 * lum_n
            dx, dy = amp * f1 * sway, amp * 0.25 * f2 * sway
        else:
            dx, dy = amp * f1, amp * f2
        dx, dy = dx - sx * w * t, dy - sy * h * t
        frame = warp(img, dx, dy, nearest)
        c = _field(caustic, u, v, t)
        if kind == "water":
            lines = np.clip(1 - np.abs(c) * 2.5, 0, 1) ** 3
            frame = frame + (0.22 * strength * lines)[..., None] * (bright - frame) * 0.9
        elif kind == "lava":
            hot = np.clip((_lum(frame) - 0.35) / 0.5, 0, 1)
            glow = 1 + 0.35 * strength * hot * (0.5 + 0.5 * c)
            frame = frame * glow[..., None] + (0.08 * strength * hot * np.clip(c, 0, 1))[..., None] * \
                np.array([1.0, 0.45, 0.05])
        else:
            frame = frame * (1 + 0.1 * strength * np.clip(c, -1, 1) * (0.3 + 0.7 * lum_n))[..., None]
        out.append(np.clip(frame, 0, 1).astype(np.float32))
    return out


def flame_mask(img: np.ndarray) -> np.ndarray:
    """Soft mask of the bright warm pixels (a flame, glowing embers): the wall around a torch must not move."""
    lum = _lum(img)
    warm = np.clip((img[..., 0] - img[..., 2]) * 3, 0, 1)
    hot = np.clip((lum - np.percentile(lum, 80)) / 0.15, 0, 1) * warm
    return box_blur(box_blur(hot, 2), 2)


def fallback_frames(base: np.ndarray, kind: str, n: int, strength: float, seed: int, cycles: int, pixel: bool
                    ) -> tuple[list[np.ndarray], dict[str, Any]]:
    motion, scroll = FALLBACK[kind]
    frames = cpu_frames(base, motion, n, strength, seed, cycles, scroll, pixel)
    used: dict[str, Any] = {"motion": motion, "scroll": scroll}
    if kind == "torch":
        mask = flame_mask(base)
        if float(mask.max()) < MASK_MIN:
            used["static_mask"] = True
        else:
            frames = [(base * (1 - mask[..., None]) + f * mask[..., None]).astype(np.float32) for f in frames]
    return frames, used


def seam_blend(frame: np.ndarray, band: float = 0.25) -> np.ndarray:
    """One tile from a 3×3 video frame whose edges are rebuilt from the neighbouring cells: the left band fades into
    the right cell's left band, the top band into the bottom cell's, so the wrap pairs the centre cell's right edge
    with the generated pixels that really follow it. The result wraps like neighbouring video pixels."""
    h, w = frame.shape[:2]
    c = min(h, w) // 3
    xs, ys = (w - 3 * c) // 2, (h - 3 * c) // 2
    cell = lambda r, k: frame[ys + r * c:ys + (r + 1) * c, xs + k * c:xs + (k + 1) * c]
    centre, right, below, corner = cell(1, 1), cell(1, 2), cell(2, 1), cell(2, 2)
    ramp = _ramp(c, max(2.0, band * c))
    wr, wb = ramp[None, :, None], ramp[:, None, None]
    return ((1 - wr) * (1 - wb) * centre + wr * (1 - wb) * right + (1 - wr) * wb * below + wr * wb * corner
            ).astype(np.float32)


def _ramp(n: int, band: float) -> np.ndarray:
    """1 at index 0 easing (smoothstep) to 0 at `band`."""
    r = np.clip(1 - np.arange(n) / max(band, 1e-6), 0, 1)
    return r * r * (3 - 2 * r)


def _edge_ring(h: int, w: int, band: float) -> np.ndarray:
    rx, ry = _ramp(w, max(1.0, band * w)), _ramp(h, max(1.0, band * h))
    return np.maximum(np.maximum(rx, rx[::-1])[None, :], np.maximum(ry, ry[::-1])[:, None])


def match_edge_tone(frames: list[np.ndarray], band: float = 0.2) -> list[np.ndarray]:
    """Pulls each frame's border ring to the clip's mean colour statistics (per channel mean / spread), fading to
    untouched inside: Wan relights the neighbouring cells differently frame to frame, so the rebuilt edges flicker in
    tone while the centre motion is what was asked for. Alpha, if any, is left alone."""
    if len(frames) < 2:
        return [f.copy() for f in frames]
    h, w = frames[0].shape[:2]
    ring = _edge_ring(h, w, band)
    mask = ring > 0.5
    stats = [(f[..., :3][mask].mean(0), f[..., :3][mask].std(0) + 1e-4) for f in frames]
    mu_ref, sd_ref = np.mean([s[0] for s in stats], 0), np.mean([s[1] for s in stats], 0)
    out = []
    for f, (mu, sd) in zip(frames, stats):
        g = f.copy()
        target = (f[..., :3] - mu) * np.clip(sd_ref / sd, 0.5, 2.0) + mu_ref
        g[..., :3] = np.clip(f[..., :3] + ring[..., None] * (target - f[..., :3]), 0, 1)
        out.append(g.astype(np.float32))
    return out


def close_wrap(frame: np.ndarray, band: float = 0.15, smooth: float = 1 / 16) -> np.ndarray:
    """Closes the wrap of one tile: the colour step between the first and last column (then row), smoothed along the
    edge so texture detail is not copied, is split between the two sides and faded out over `band` of the tile.
    An edge that already steps no more than the interior lines is left alone: it may be a real mortar line."""
    a = frame.astype(np.float32).copy()
    ch = min(a.shape[2], 3)
    if min(a.shape[:2]) < 2:
        return a
    for ax in (1, 0):
        n, m = a.shape[ax], a.shape[1 - ax]
        # Eased in from ratio 0.8 to full at 1.4, so neighbouring frames near the cut-off are treated alike.
        weight = float(np.clip((_seam_ratio(a[..., :ch])["x" if ax == 1 else "y"] - 0.8) / 0.6, 0, 1))
        if weight == 0:
            continue
        step = np.take(a[..., :ch], 0, axis=ax) - np.take(a[..., :ch], n - 1, axis=ax)
        step = box_blur(step, max(1, round(m * smooth)), axes=(0,))
        ramp = _ramp(n, max(2.0, band * n))
        shape = [1, 1, 1]
        shape[ax] = n
        delta = np.expand_dims(step, ax) * 0.5 * weight * (ramp[::-1] - ramp).reshape(shape)
        a[..., :ch] = np.clip(a[..., :ch] + delta, 0, 1)
    return a


def _desc(frames: list[np.ndarray], side: int = 48) -> np.ndarray:
    out = np.zeros((len(frames), 2, side, side), np.float32)
    for i, f in enumerate(frames):
        small = _rgb(wrap_resize(_to_img(f), side, side, Image.Resampling.BOX))
        lum = _lum(small)
        out[i, 0] = lum
        # Second channel: the local contrast, which tracks flames and foam better than the mean brightness.
        out[i, 1] = np.abs(lum - np.roll(lum, 1, 0)) + np.abs(lum - np.roll(lum, 1, 1))
    return out


def video_loop(frames: list[np.ndarray], n_out: int, crossfade: float = 0.25) -> tuple[list[np.ndarray], dict[str, Any]]:
    """The most repeating stretch of the seam-blended clip, its wrap crossfaded over `crossfade` of the period, then
    resampled to n_out frames."""
    n = len(frames)
    if n < 6:
        raise StepError("op_bad_input", f"need at least 6 video frames, got {n}")
    sim = similarity_matrix(_desc(frames))
    start, period, seam = find_cycle(sim, max(4, n // 4))
    rgba = [np.concatenate([np.clip(f * 255 + 0.5, 0, 255), np.full((*f.shape[:2], 1), 255.0)], -1).astype(np.uint8)
            for f in frames]
    k = max(1, round(crossfade * period))
    seg, blended = crossfade_seam(rgba, start, period, k)
    idx = sample(period, n_out, True)
    out = [seg[i][..., :3].astype(np.float32) / 255 for i in idx]
    return out, {"source_frames": n, "start": start, "period": period, "seam_similarity": seam,
                 "crossfade_frames": blended, "indices": [start + i for i in idx]}


def loop_ratio(frames: list[np.ndarray]) -> float:
    """Step from the last frame back to the first over the 95th percentile of the steps inside the loop (≤ 1 = the
    wrap is no bigger than an ordinary frame step)."""
    if len(frames) < 2:
        return 0.0
    steps = [float(np.abs(frames[i + 1] - frames[i]).mean()) for i in range(len(frames) - 1)]
    wrap = float(np.abs(frames[0] - frames[-1]).mean())
    return round(wrap / max(float(np.percentile(steps, 95)), 1e-5), 3)


def frame_check(frames: list[np.ndarray]) -> dict[str, Any]:
    worst = {"x": 0.0, "y": 0.0}
    for f in frames:
        for k, v in _seam_ratio(f).items():
            worst[k] = max(worst[k], round(v, 3))
    temporal = loop_ratio(frames)
    return {"seam_ratio": worst, "loop_ratio": temporal, "max_ratio": SEAM_RATIO_MAX,
            "passed": max(*worst.values(), temporal) <= SEAM_RATIO_MAX}


def _to_img(a: np.ndarray) -> Image.Image:
    return Image.fromarray(np.clip(a * 255 + 0.5, 0, 255).astype(np.uint8), "RGB")


def palette_of(img: np.ndarray, limit: int = 64) -> np.ndarray | None:
    cols = np.unique(np.clip(img * 255 + 0.5, 0, 255).astype(np.uint8).reshape(-1, 3), axis=0)
    return cols.astype(np.float32) / 255 if len(cols) <= limit else None


def snap(frame: np.ndarray, pal: np.ndarray) -> np.ndarray:
    """Nearest palette colour per pixel: pixel-art frames keep the source colours instead of gaining blends."""
    d = ((frame[..., None, :] - pal[None, None]) ** 2).sum(-1)
    return pal[np.argmin(d, -1)]


def is_pixel(img: Image.Image, flag: Any) -> bool:
    v = str(flag if flag is not None else "auto").strip().lower()
    if v in ("true", "1", "yes", "on"):
        return True
    if v in ("false", "0", "no", "off"):
        return False
    return min(img.size) <= 32 or palette_of(_rgb(img.convert("RGB")), 32) is not None


def ensure_tileable(img: Image.Image) -> tuple[Image.Image, bool]:
    img = img.convert("RGB")
    if min(img.size) < 32 or tile_report(img)["seam_score"] >= 70:
        return img, False
    return seamless(img), True


def tile_canvas(tile: Image.Image, side: int) -> Image.Image:
    c = side // 3
    cell = wrap_resize(tile.convert("RGB"), c, c).convert("RGB")
    out = Image.new("RGB", (side, side))
    for r in range(3):
        for k in range(3):
            out.paste(cell, (k * c, r * c))
    return out


def autotile_parts(doc: dict[str, Any], atlas: Image.Image) -> tuple[np.ndarray, np.ndarray]:
    """Terrain A and B textures back from a blob-47 atlas: the all-neighbours tile is pure A, the fill tile pure B."""
    t = int(doc["tile_size"])
    tiles = doc["tiles"]
    full = next((e for e in tiles if e.get("mask") == 255), None)
    fill = next((e for e in tiles if e.get("mask") is None), None)
    if full is None or fill is None:
        raise StepError("op_bad_input", "autotile.json has no full A tile or no B fill tile")
    a = _rgb(atlas.convert("RGB"))
    crop = lambda e: a[e["row"] * t:(e["row"] + 1) * t, e["col"] * t:(e["col"] + 1) * t]
    return crop(full).copy(), crop(fill).copy()


def auto_layer(doc: dict[str, Any], kind: str) -> str:
    """The terrain whose name says it moves (water, lava…); otherwise B for liquids, A for grass."""
    names = [str(n).lower() for n in (doc.get("terrains") or ["a", "b"])]
    words = MOTION_WORDS.get(kind, ())
    for tag, name in zip(("a", "b"), names):
        if any(w in name for w in words):
            return tag
    return "a" if kind == "grass_wind" else "b"


def autotile_frames(doc: dict[str, Any], a_frames: list[np.ndarray], b_frames: list[np.ndarray]
                    ) -> list[list[np.ndarray]]:
    """Each frame's 48 tiles rendered with the source set's own options and field: the shapes stay put, the
    textures move, and neighbouring tiles still agree on every shared edge."""
    o = at.parse({**(doc.get("options") or {}), "size": int(doc["tile_size"])})
    f = at._field(o)
    pal = np.asarray([[int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)] for h in doc.get("palette") or []], np.float32)
    out = []
    for a, b in zip(a_frames, b_frames):
        tiles = [at.render_tile(e["mask"], a, b, o, f).astype(np.float32) for e in at.layout()]
        out.append([snap(t, pal) for t in tiles] if len(pal) else tiles)
    return out


def strip(frames: list[np.ndarray]) -> np.ndarray:
    return np.concatenate(frames, 1)


def stack_atlases(frames: list[list[np.ndarray]]) -> np.ndarray:
    return np.concatenate([at.atlas(tiles) for tiles in frames], 0)


def tiled_view(frame: np.ndarray, n: int = 3, side: int = 384) -> Image.Image:
    img = _to_img(np.tile(frame, (n, n, 1)))
    k = max(1, side // img.width)
    return img.resize((img.width * k, img.height * k), Image.Resampling.NEAREST) if k > 1 else img


def gif_bytes(views: list[Image.Image], fps: float) -> bytes:
    ms = max(20, round(1000 / max(fps, 1)))
    frames = [v.convert("P", palette=Image.Palette.ADAPTIVE, colors=255) for v in views]
    buf = io.BytesIO()
    frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:], duration=ms, loop=0, disposal=2)
    return buf.getvalue()


def qa_image(frames: list[np.ndarray], thumb: int = 64) -> Image.Image:
    """Every frame in a row, then the first and last frame tiled 3×3 side by side: a seam shows as a cross, a bad
    loop as two different tilings."""
    n = len(frames)
    row = Image.new("RGB", (n * (thumb + 4) + 4, thumb + 8), (30, 30, 33))
    for i, f in enumerate(frames):
        row.paste(_to_img(f).resize((thumb, thumb), Image.Resampling.NEAREST), (4 + i * (thumb + 4), 4))
    a, b = tiled_view(frames[0], 3, 288), tiled_view(frames[-1], 3, 288)
    out = Image.new("RGB", (max(row.width, a.width + b.width + 12), row.height + max(a.height, b.height) + 8),
                    (30, 30, 33))
    out.paste(row, (0, 0))
    out.paste(a, (4, row.height + 4))
    out.paste(b, (a.width + 8, row.height + 4))
    return out


async def _put_bytes(ctx: StepContext, data: bytes, name: str, mime: str) -> BlobRef:
    return await ctx.blobs.put_bytes(data, name=name, mime=mime)


async def _autotile_doc(ctx: StepContext, inputs: Outputs) -> dict[str, Any] | None:
    for ref in inputs.get("autotile") or []:
        doc = await _read_json(ctx, ref)
        if isinstance(doc, dict) and doc.get("type") == at.AUTOTILE_TYPE:
            return doc
        raise StepError("op_bad_input", "the autotile input is not an autotile.json from autotile-terrain")
    return None


async def _autotile_atlas(ctx: StepContext, doc: dict[str, Any], inputs: Outputs) -> Image.Image:
    h = str((doc.get("atlas") or {}).get("hash") or "")
    ref = await ctx.blobs.get(h) if h else None
    img = await _load(ctx, ref if ref is not None else _images_in(inputs)[0])
    t = int(doc["tile_size"])
    if img.size != (at.COLS * t, at.ROWS * t):
        raise StepError("op_bad_input", f"the autotile atlas should be {at.COLS * t}×{at.ROWS * t}, got "
                                        f"{img.width}×{img.height}")
    return img


def _kind(params: dict[str, Any]) -> str:
    kind = str(params.get("kind") or "water").strip().lower()
    if kind not in KINDS:
        raise StepError("op_bad_param", f"kind must be one of {list(KINDS)}, got {kind!r}")
    return kind


def _flag(v: Any) -> bool:
    return v.strip().lower() not in ("false", "0", "no", "off", "") if isinstance(v, str) else bool(v)


class TileAnimPrep(OpExecutor):
    kind = "op.tile_anim_prep"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        kind = _kind(params)
        video = kind in VIDEO_KINDS
        res = str(params.get("resolution") or "480p").lower()
        if res not in CANVAS:
            raise StepError("op_bad_param", f"resolution must be one of {list(CANVAS)}, got {res!r}")
        k = min(max(int(params.get("candidates") or 1), 1), 4)
        layer = str(params.get("layer") or "auto").lower()
        if layer not in LAYERS:
            raise StepError("op_bad_param", f"layer must be one of {list(LAYERS)}, got {layer!r}")
        doc = await _autotile_doc(ctx, inputs)
        info: dict[str, Any] = {"kind": kind, "video": video, "mode": "blob47" if doc else "single"}
        if doc:
            a, b = autotile_parts(doc, await _autotile_atlas(ctx, doc, inputs))
            layer = auto_layer(doc, kind) if layer == "auto" else layer
            if video and layer == "both":
                raise StepError("op_bad_param", "a video kind animates one terrain: pick layer a or b")
            tile = _to_img(a if layer in ("a", "both") else b)
            fixed = False
            info |= {"layer": layer, "terrains": doc.get("terrains"), "tile_size": int(doc["tile_size"])}
        else:
            tile, fixed = await asyncio.to_thread(ensure_tileable, await _load(ctx, _images_in(inputs)[0]))
            info["tile_size"] = list(tile.size)
        info["made_seamless"] = fixed
        out: Outputs = {"image": [await _put_png(ctx, tile, "tile_source.png")], "starts": []}
        seconds = min(max(float(params.get("seconds") or 2), 1.0), 5.0)
        side = CANVAS[res]
        # The Wan step reads these fields even when it maps over no start image, so the CPU path writes them too.
        info |= {"prompt": "", "width": side, "height": side, "length": frame_count(seconds), "fps": FPS,
                 "candidates": k if video else 0, "resolution": res}
        if video:
            canvas = await asyncio.to_thread(tile_canvas, tile, side)
            start = await _put_png(ctx, canvas, "tile_start.png")
            out["starts"] = [start] * k
            info["prompt"] = video_prompt(kind, str(params.get("prompt") or ""))
        # Only a real choice between Wan clips needs a person; the CPU path feeds its own tile through the pick.
        info["pick"] = bool(video and k > 1 and _flag(params.get("review", True)))
        out["json"] = [await _put_json(ctx, info, "tile_anim_prep.json")]
        return StepResult(outputs=out, meta={key: v for key, v in info.items() if key != "prompt"})


class TileAnimCandidates(OpExecutor):
    kind = "op.tile_anim_candidates"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        refs = inputs.get("frames") or []
        info = await _read_json(ctx, _images_in(inputs, "json")[0])
        if not info.get("video"):
            tile = _images_in(inputs, "image")[0]
            doc = {"candidates": [{"preview": tile.hash, "frames": []}], "frames_per_clip": 0}
            return StepResult(outputs={"previews": [tile], "json": [await _put_json(ctx, doc, "tile_candidates.json")]},
                              meta={"candidates": 0})
        k = int(info.get("candidates") or 1)
        if not refs or len(refs) % k:
            raise StepError("op_bad_input", f"{len(refs)} video frames do not split into {k} clips")
        per = len(refs) // k
        groups = [refs[i * per:(i + 1) * per] for i in range(k)]
        previews: list[BlobRef] = []
        for i, g in enumerate(groups):
            idx = sorted({round(j * (per - 1) / 7) for j in range(8)})
            frames = [await _load(ctx, g[j]) for j in idx]
            sheet = await asyncio.to_thread(contact_strip, frames, len(frames))
            previews.append(await _put_png(ctx, sheet, f"tile_candidate_{i + 1}.png"))
            ctx.progress(i + 1, k, "preview")
        doc = {"candidates": [{"preview": p.hash, "frames": [r.hash for r in g]} for p, g in zip(previews, groups)],
               "frames_per_clip": per}
        return StepResult(outputs={"previews": previews, "json": [await _put_json(ctx, doc, "tile_candidates.json")]},
                          meta={"candidates": k, "frames_per_clip": per})


class TileAnimate(OpExecutor):
    kind = "op.tile_animate"
    version = "3"

    async def _clips(self, ctx: StepContext, inputs: Outputs) -> list[tuple[int, list[str]]]:
        """(candidate number, frame hashes) with the picked clip first, then the other candidates as retries."""
        picked = _images_in(inputs, "picked")[0]
        doc = await _read_json(ctx, _images_in(inputs, "candidates")[0])
        cands = doc.get("candidates") if isinstance(doc, dict) else None
        if not any(c.get("preview") == picked.hash for c in cands or []):
            raise StepError("op_bad_input", "the picked image is not one of the candidate previews")
        order = sorted(enumerate(cands), key=lambda e: e[1].get("preview") != picked.hash)
        return [(i + 1, list(c.get("frames") or [])) for i, c in order]

    async def _video(self, ctx: StepContext, inputs: Outputs, n: int, crossfade: float
                     ) -> tuple[list[np.ndarray] | None, dict[str, Any]]:
        attempts: list[dict[str, Any]] = []
        clips = await self._clips(ctx, inputs)
        for done, (number, hashes) in enumerate(clips):
            if not hashes:
                attempts.append({"candidate": number, "seam_similarity": None, "error": "no frames"})
                ctx.log(f"candidate {number}: no frames", "warning")
                continue
            clip = []
            for i, h in enumerate(hashes):
                ref = await ctx.blobs.get(h)
                if ref is None:
                    raise StepError("op_bad_input", f"frame blob {h[:12]} is missing")
                clip.append(seam_blend(_rgb(await _load(ctx, ref))))
                ctx.progress(0.05 + 0.4 * (done + (i + 1) / len(hashes)) / len(clips), 1.0, "load")
            frames, vrep = await asyncio.to_thread(video_loop, clip, n, crossfade)
            attempts.append({"candidate": number, "seam_similarity": vrep["seam_similarity"]})
            if vrep["seam_similarity"] >= LOOP_SIMILARITY_MIN:
                return frames, vrep | {"attempts": attempts}
            ctx.log(f"candidate {number}: seam similarity {vrep['seam_similarity']} < {LOOP_SIMILARITY_MIN}", "warning")
        return None, {"attempts": attempts}

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        kind = _kind(params)
        n = int(params.get("frames") or 8)
        if not 4 <= n <= 16:
            raise StepError("op_bad_param", f"frames must be 4-16, got {n}")
        fps = float(params.get("fps") or 8)
        strength = float(params.get("strength", 1.0))
        seed = int(params.get("seed") or 0)
        cycles = min(max(int(params.get("cycles") or 1), 1), 4)
        scroll = str(params.get("scroll") or "none").lower()
        size = int(params.get("size") or 0)
        info = await _read_json(ctx, _images_in(inputs, "json")[0])
        if info.get("kind") != kind:
            raise StepError("op_bad_input", f"prep was made for {info.get('kind')!r}, not {kind!r}")
        doc = await _autotile_doc(ctx, inputs)
        source = await _load(ctx, _images_in(inputs, "image")[0])
        pixel = bool(doc.get("pixel")) if doc else is_pixel(source, params.get("pixel"))
        ctx.progress(0.05, 1.0, "frames")
        if doc is None and size and source.size != (size, size):
            source = wrap_resize(source.convert("RGB"), size, size,
                                 Image.Resampling.BOX if pixel else Image.Resampling.LANCZOS).convert("RGB")
        base = _rgb(source.convert("RGB"))
        report: dict[str, Any] = {"kind": kind, "frames": n, "fps": fps, "pixel": pixel, "mode": info.get("mode")}
        if kind in CPU_KINDS:
            frames = await asyncio.to_thread(cpu_frames, base, kind, n, strength, seed, cycles, scroll, pixel)
            report |= {"strength": strength, "cycles": cycles, "scroll": scroll, "seed": seed}
        else:
            frames, vrep = await self._video(ctx, inputs, n, float(params.get("crossfade", 0.25)))
            if frames is None:
                frames, used = await asyncio.to_thread(fallback_frames, base, kind, n, strength, seed, cycles, pixel)
                tried = [a for a in vrep["attempts"] if a["seam_similarity"] is not None]
                why = (f"seam similarity below {LOOP_SIMILARITY_MIN} in {len(tried)} candidate(s)" if tried
                       else "every candidate came back with no frames")
                note = f"no Wan clip looped ({why}); used the CPU {used['motion']} motion instead"
                if used.get("static_mask"):
                    note += "; no flame found in the tile, so the whole tile moves"
                ctx.log(note, "warning")
                report["fallback"] = {"note": note, **used, "attempts": vrep["attempts"]}
            else:
                th, tw = base.shape[:2]
                frames = [_rgb(wrap_resize(_to_img(f), tw, th, Image.Resampling.LANCZOS).convert("RGB"))
                          for f in frames]
                raw_seam = frame_check(frames)["seam_ratio"]
                if _flag(params.get("edge_fix", True)):
                    frames = [close_wrap(f) for f in match_edge_tone(frames)]
                report["video"] = vrep | {"seam_before_edge_fix": raw_seam}
        if pixel and doc is None and (pal := palette_of(base)) is not None:
            frames = [snap(f, pal) for f in frames]
        ctx.progress(0.6, 1.0, "check")
        if doc is not None:
            layer = str(info.get("layer") or "a")
            a, b = autotile_parts(doc, await _autotile_atlas(ctx, doc, inputs))
            if layer == "both":
                if kind in CPU_KINDS:
                    b_frames = await asyncio.to_thread(cpu_frames, b, kind, n, strength, seed + 101, cycles, scroll,
                                                       pixel)
                else:
                    b_frames = (await asyncio.to_thread(fallback_frames, b, kind, n, strength, seed + 101, cycles,
                                                        pixel))[0]
                a_frames = frames
            else:
                a_frames = frames if layer == "a" else [a] * n
                b_frames = frames if layer == "b" else [b] * n
            sets = await asyncio.to_thread(autotile_frames, doc, a_frames, b_frames)
            sheet = stack_atlases(sets)
            t = int(doc["tile_size"])
            cells = at.random_cells(10, 7, int((doc.get("options") or {}).get("seed") or 0) + 1)
            views = [Image.fromarray(at._u8(at.tile_map(cells, s)), "RGB") for s in sets]
            check = frame_check(a_frames if layer != "b" else b_frames)
            check["loop_ratio"] = loop_ratio([at.atlas(s) for s in sets])
            check["autotile_seam"] = at.seam_check(cells, sets[0])["seam_ratio"]
            check["passed"] = max(*check["seam_ratio"].values(), check["loop_ratio"]) <= SEAM_RATIO_MAX
            layout = {"cols": at.COLS, "rows": at.ROWS * n, "frame_offset": [0, at.ROWS]}
            report |= {"layer": layer, "terrains": doc.get("terrains")}
            qa = qa_image(a_frames if layer != "b" else b_frames)
        else:
            t = base.shape[1]
            sheet = strip(frames)
            check = frame_check(frames)
            layout = {"cols": n, "rows": 1, "frame_offset": [1, 0]}
            views = [tiled_view(f) for f in frames]
            qa = qa_image(frames)
        report["check"] = check
        if not check["passed"]:
            ctx.log(f"seam/loop ratio above {SEAM_RATIO_MAX}: {check}", "warning")
        ctx.progress(0.8, 1.0, "save")
        name = f"{kind}_tile"
        sheet_ref = await _put_png(ctx, _to_img(sheet), f"{name}_sheet.png")
        frame_refs = [await _put_png(ctx, _to_img(f), f"{name}_{i:02d}.png") for i, f in enumerate(frames)]
        tile_doc: dict[str, Any] = {
            "type": TILE_ANIM_TYPE, "mode": "blob47" if doc else "single", "kind": kind, "frames": n, "fps": fps,
            "tile_size": t, "pixel": pixel, "sheet": {"file": sheet_ref.name, "hash": sheet_ref.hash},
            "layout": layout, "check": check,
            "rule": "frame k of the tile at atlas cell (c, r) sits at (c, r) + k · frame_offset",
        }
        if doc:
            tile_doc["autotile"] = {key: doc.get(key) for key in ("terrains", "grid", "tiles", "bit_order",
                                                                    "unity_neighbor_order", "rule")}
            tile_doc["autotile"]["layer"] = report["layer"]
        js = await ctx.blobs.put_bytes(json.dumps(tile_doc, indent=1).encode(), name=TILE_ANIM_FILE,
                                       mime="application/json")
        gif = await asyncio.to_thread(gif_bytes, views, fps)
        out: Outputs = {
            "sheet": [sheet_ref], "images": frame_refs, "tile_anim": [js],
            "gif": [await _put_bytes(ctx, gif, f"{name}_loop.gif", "image/gif")],
            "qa": [await _put_png(ctx, qa, f"{name}_qa.png")],
            "json": [await _put_json(ctx, report, "tile_anim_report.json")],
        }
        ctx.progress(1.0, 1.0, "done")
        return StepResult(outputs=out, meta={"frames": n, "fps": fps, "tile": t, "mode": tile_doc["mode"],
                                             "check": check})


TILE_ANIM_OPS: tuple[type[OpExecutor], ...] = (TileAnimPrep, TileAnimCandidates, TileAnimate)
