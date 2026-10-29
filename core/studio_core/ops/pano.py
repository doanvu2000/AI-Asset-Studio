"""360° skybox from an equirectangular panorama (skybox-360). `op.pano_fix` closes the left/right wrap with the same
min-error quilt as op.seamless (one axis only), pinches each pole row to one colour so the cube's top and bottom do not
swirl, and optionally rotates the view; `op.pano_cubemap` resamples the sphere into six cube faces plus a
cosine-convolved irradiance map and the dominant light direction, written to skybox.json for the engine exporters.

Directions use x = right, y = up, z = forward; the panorama centre (u = 0.5) is forward and u grows to the right."""
from __future__ import annotations

import asyncio
import json
import math
from typing import Any

import numpy as np
from PIL import Image

from ..contracts import BlobRef, Outputs, StepContext, StepError, StepResult
from .executors import OpExecutor, _load, _put_png, _read_json
from .tile import _quilt_x, _rgb, _seam_ratio, box_blur, wrap_resize

SKYBOX_FILE = "skybox.json"
SKYBOX_TYPE = "asset-studio/skybox"
PANO_INFO_FILE = "pano.json"
FACES = ("front", "right", "back", "left", "up", "down")
UPSCALE_WIDTHS = (0, 4096, 8192)
# ESRGAN sees the wrap-padded strip, so its edge handling never touches the real seam columns.
UPSCALE_PAD = 32
IRR_W, IRR_H = 64, 32


def _u8(a: np.ndarray) -> Image.Image:
    return Image.fromarray(np.clip(np.round(a * 255.0), 0, 255).astype(np.uint8), "RGB")


def to_2to1(img: Image.Image) -> Image.Image:
    w = img.width - img.width % 2
    return wrap_resize(img.convert("RGB"), w, w // 2).convert("RGB") if img.size != (w, w // 2) else img.convert("RGB")


def seam_report(a: np.ndarray) -> dict[str, Any]:
    """op.tile_check's seam measure on the horizontal wrap only (the poles are not a wrap)."""
    ratios = {"x": _seam_ratio(a)["x"]}
    h, w = a.shape[:2]
    if h >= 64 and h % 4 == 0 and w % 4 == 0:
        ratios["x4"] = _seam_ratio(a.reshape(h // 4, 4, w // 4, 4, 3).mean((1, 3)))["x"]
    worst = max(ratios.values())
    return {"score": round(100.0 * math.exp(-0.35 * max(0.0, worst - 1.1)), 1),
            "ratio": {k: round(v, 3) for k, v in ratios.items()}}


def pole_spread(a: np.ndarray) -> dict[str, float]:
    """Colour spread along the first/last row; a clean pole is a single point, so ~0."""
    return {"top": round(float(a[0].std(0).mean()), 4), "bottom": round(float(a[-1].std(0).mean()), 4)}


def smooth_poles(a: np.ndarray, frac: float) -> np.ndarray:
    """Rows near a pole cover a tiny solid angle stretched across the whole width; blurring them along the wrap with a
    radius that grows to the full row turns the pinch into one smooth cap."""
    h, w = a.shape[:2]
    band = int(round(h * frac))
    if band < 2:
        return a
    out = a.copy()
    for y in range(band):
        t = 1.0 - y / band
        r = int(round(t * t * w / 2))
        if r < 1:
            continue
        blend = t * t * (3 - 2 * t)
        for row in (y, h - 1 - y):
            blurred = out[row].mean(0, keepdims=True) if r >= w // 2 else box_blur(out[row][None], r, (1,))[0]
            out[row] = out[row] * (1 - blend) + blurred * blend
    return out


def pano_fix(img: Image.Image, overlap: float = 0.03, pole: float = 0.05, yaw: float = 0.0,
             feather: int = 3) -> tuple[Image.Image, dict[str, Any]]:
    src = to_2to1(img)
    a = _rgb(src)
    h, w = a.shape[:2]
    if w < 256:
        raise ValueError(f"panorama too small ({img.width}x{img.height}), needs at least 256x128")
    before = {"seam": seam_report(a), "pole": pole_spread(a)}
    o = 0
    if overlap > 0:
        o = max(4, round(w * overlap))
        a = _rgb(wrap_resize(_u8(_quilt_x(a, overlap, feather, o)), w, h))
    a = smooth_poles(a, pole) if pole > 0 else a
    shift = int(round((yaw % 360) / 360.0 * w))
    if shift:
        a = np.roll(a, -shift, axis=1)
    info = {"width": w, "height": h, "overlap_px": o, "pole_rows": int(round(h * pole)), "yaw": yaw % 360,
            "before": before, "after": {"seam": seam_report(a), "pole": pole_spread(a)},
            "resized": img.size != (w, h)}
    return _u8(a), info


def seam_preview(before: Image.Image, after: Image.Image, width: int = 1024) -> Image.Image:
    """Both versions rolled half a turn so the wrap seam sits in the middle, before above after."""
    h = width // 2
    rows = []
    for im in (before, after):
        arr = np.asarray(to_2to1(im))
        arr = np.roll(arr, arr.shape[1] // 2, axis=1)
        rows.append(Image.fromarray(arr).resize((width, h), Image.Resampling.LANCZOS))
    out = Image.new("RGB", (width, h * 2 + 6), (40, 40, 46))
    out.paste(rows[0], (0, 0))
    out.paste(rows[1], (0, h + 6))
    return out


def face_dirs(face: str, n: int) -> np.ndarray:
    """(n, n, 3) unit view directions of a face's pixel centres, row 0 at the top as seen from inside the cube.
    Side faces stand upright; the up face's bottom edge and the down face's top edge touch the front face."""
    c = (np.arange(n, dtype=np.float64) + 0.5) / n * 2 - 1
    a, b = np.meshgrid(c, c)
    one = np.ones_like(a)
    d = {"front": (a, -b, one), "right": (one, -b, -a), "back": (-a, -b, -one), "left": (-one, -b, a),
         "up": (a, one, b), "down": (a, -one, -b)}[face]
    v = np.stack(d, -1)
    return v / np.linalg.norm(v, axis=-1, keepdims=True)


def dir_to_uv(d: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Panorama coordinates in [0, 1): u = 0.5 forward, 0.75 right; v = 0 at the zenith."""
    lon = np.arctan2(d[..., 0], d[..., 2])
    lat = np.arcsin(np.clip(d[..., 1], -1, 1))
    return (0.5 + lon / (2 * np.pi)) % 1.0, 0.5 - lat / np.pi


def uv_to_dir(u: np.ndarray, v: np.ndarray) -> np.ndarray:
    lon = (u - 0.5) * 2 * np.pi
    lat = (0.5 - v) * np.pi
    return np.stack([np.cos(lat) * np.sin(lon), np.sin(lat), np.cos(lat) * np.cos(lon)], -1)


def sample(eq: np.ndarray, d: np.ndarray) -> np.ndarray:
    """Bilinear lookup, wrapping across the left/right seam and clamping at the poles."""
    h, w = eq.shape[:2]
    u, v = dir_to_uv(d)
    x = u * w - 0.5
    y = np.clip(v * h - 0.5, 0, h - 1)
    x0 = np.floor(x).astype(np.int64)
    y0 = np.floor(y).astype(np.int64)
    fx = (x - x0)[..., None].astype(np.float32)
    fy = (y - y0)[..., None].astype(np.float32)
    x0 %= w
    x1 = (x0 + 1) % w
    y1 = np.minimum(y0 + 1, h - 1)
    top = eq[y0, x0] * (1 - fx) + eq[y0, x1] * fx
    bot = eq[y1, x0] * (1 - fx) + eq[y1, x1] * fx
    return top * (1 - fy) + bot * fy


def cube_faces(eq: np.ndarray, n: int) -> dict[str, np.ndarray]:
    # Supersampling 2x2 per face pixel: a face pixel near the poles spans many panorama texels.
    ss = 2 if n * 4 <= eq.shape[1] * 2 else 1
    out = {}
    for f in FACES:
        big = sample(eq, face_dirs(f, n * ss))
        out[f] = big.reshape(n, ss, n, ss, -1).mean((1, 3)) if ss > 1 else big
    return out


def cross_image(faces: dict[str, np.ndarray], cell: int = 256) -> Image.Image:
    """Horizontal cross: up above front, down below, then left · front · right · back."""
    slots = {"up": (1, 0), "left": (0, 1), "front": (1, 1), "right": (2, 1), "back": (3, 1), "down": (1, 2)}
    out = Image.new("RGBA", (cell * 4, cell * 3), (0, 0, 0, 0))
    for f, (cx, cy) in slots.items():
        out.paste(_u8(faces[f]).resize((cell, cell), Image.Resampling.LANCZOS).convert("RGBA"), (cx * cell, cy * cell))
    return out


def face_size(width: int, size: int = 0) -> int:
    if size > 0:
        return int(size)
    n = 1 << max(8, int(round(math.log2(max(1, width / 4)))))
    return min(n, 4096)


def irradiance(eq: np.ndarray, w: int = IRR_W, h: int = IRR_H) -> np.ndarray:
    """Lambert-convolved light per direction (what a white diffuse surface facing it would show), on a small
    equirect grid: every output texel integrates the whole sphere weighted by cos(angle) × texel solid angle."""
    src = np.asarray(Image.fromarray(np.clip(np.round(eq * 255), 0, 255).astype(np.uint8), "RGB")
                     .resize((w, h), Image.Resampling.BOX), np.float32) / 255.0
    lin = src ** 2.2
    vv, uu = np.mgrid[0:h, 0:w]
    dirs = uv_to_dir((uu + 0.5) / w, (vv + 0.5) / h).reshape(-1, 3)
    lat = (0.5 - (vv.reshape(-1) + 0.5) / h) * np.pi
    solid = np.cos(lat) * (2 * np.pi / w) * (np.pi / h)
    cosw = np.clip(dirs @ dirs.T, 0, None) * solid[None, :]
    irr = (cosw @ lin.reshape(-1, 3)) / np.pi
    return np.clip(irr.reshape(h, w, 3), 0, 1) ** (1 / 2.2)


def dominant_light(eq: np.ndarray) -> dict[str, Any]:
    """Brightest blurred spot of the upper hemisphere (sun, moon, bright sky patch) and the mean sky colour."""
    small = np.asarray(Image.fromarray(np.clip(np.round(eq * 255), 0, 255).astype(np.uint8), "RGB")
                       .resize((128, 64), Image.Resampling.BOX), np.float32) / 255.0
    lum = small[..., 0] * 0.2126 + small[..., 1] * 0.7152 + small[..., 2] * 0.0722
    soft = box_blur(lum, 2, (0, 1), (False, True))
    upper = soft[:32]
    y, x = np.unravel_index(int(np.argmax(upper)), upper.shape)
    d = uv_to_dir(np.array((x + 0.5) / 128), np.array((y + 0.5) / 64))
    elev = float(np.degrees(np.arcsin(d[1])))
    peak = float(upper[y, x])
    # A flat overcast sky has no light worth aiming a directional light at.
    contrast = peak / max(float(lum.mean()), 1e-3)
    color = small[max(0, y - 2):y + 3, max(0, x - 2):x + 3].reshape(-1, 3).mean(0)
    color = color / max(float(color.max()), 1e-3)
    mean = small.reshape(-1, 3).mean(0)
    return {"dir": [round(float(c), 4) for c in d], "elevation": round(elev, 1),
            "azimuth": round(float(np.degrees(np.arctan2(d[0], d[2]))), 1),
            "color": "#" + "".join(f"{int(round(c * 255)):02x}" for c in color),
            "strength": round(float(np.clip((contrast - 1.0) / 2.0, 0.2, 1.0)), 2),
            "distinct": bool(contrast > 1.6 and elev > 2.0),
            "ambient": "#" + "".join(f"{int(round(c * 255)):02x}" for c in mean)}


def _float_param(params: dict[str, Any], key: str, default: float, lo: float, hi: float) -> float:
    try:
        v = float(params.get(key, default))
    except (TypeError, ValueError) as e:
        raise StepError("op_bad_params", f"{key}: {e}") from e
    if not lo <= v <= hi:
        raise StepError("op_bad_params", f"{key} must be in [{lo}, {hi}]")
    return v


def _upscale_width(params: dict[str, Any]) -> int:
    try:
        target = int(params.get("upscale") or 0)
    except (TypeError, ValueError):
        raise StepError("op_bad_params", f"upscale must be one of {UPSCALE_WIDTHS}") from None
    if target not in UPSCALE_WIDTHS:
        raise StepError("op_bad_params", f"upscale must be one of {UPSCALE_WIDTHS}")
    return target


async def _put_json(ctx: StepContext, doc: Any, name: str) -> BlobRef:
    return await ctx.blobs.put_bytes(json.dumps(doc, indent=1, ensure_ascii=False).encode("utf-8"), name=name,
                                     mime="application/json")


def _asset(params: dict[str, Any]) -> str:
    from .enemy_variants import slug

    return slug(str(params.get("asset") or "sky")) or "sky"


class PanoFix(OpExecutor):
    kind = "op.pano_fix"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        refs = inputs.get("images") or []
        if not refs:
            raise StepError("op_no_input", "input 'images' is empty")
        overlap = _float_param(params, "overlap", 0.03, 0, 0.2)
        pole = _float_param(params, "pole", 0.05, 0, 0.2)
        yaw = _float_param(params, "yaw", 0, -360, 360)
        target = _upscale_width(params)
        asset = _asset(params)
        src = await _load(ctx, refs[0])
        ctx.progress(0, 3, label="seam")
        try:
            fixed, info = await asyncio.to_thread(pano_fix, src, overlap, pole, yaw)
        except ValueError as e:
            raise StepError("op_bad_input", str(e)) from e
        ctx.progress(1, 3, label="preview")
        out = await _put_png(ctx, fixed, f"{asset}_pano.png")
        preview = await _put_png(ctx, await asyncio.to_thread(seam_preview, src, fixed), f"{asset}_seam_check.png")
        upscale_in: list[BlobRef] = []
        if target > fixed.width:
            arr = np.asarray(fixed)
            padded = np.concatenate([arr[:, -UPSCALE_PAD:], arr, arr[:, :UPSCALE_PAD]], 1)
            upscale_in.append(await _put_png(ctx, Image.fromarray(padded, "RGB"), f"{asset}_pano_wrap.png"))
        info = {**info, "upscale": {"width": target, "pad": UPSCALE_PAD} if upscale_in else None}
        ctx.progress(3, 3)
        return StepResult(outputs={"images": [out], "preview": [preview], "upscale_in": upscale_in,
                                   "info": [await _put_json(ctx, info, PANO_INFO_FILE)]},
                          meta={"seam_before": info["before"]["seam"]["score"],
                                "seam_after": info["after"]["seam"]["score"], "overlap_px": info["overlap_px"]})


class PanoCubemap(OpExecutor):
    kind = "op.pano_cubemap"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        refs = inputs.get("images") or []
        if not refs:
            raise StepError("op_no_input", "input 'images' is empty")
        asset = _asset(params)
        try:
            size = int(params.get("face_size") or 0)
        except (TypeError, ValueError):
            raise StepError("op_bad_params", "face_size must be an integer") from None
        if size and not 64 <= size <= 4096:
            raise StepError("op_bad_params", "face_size must be 0 (auto) or 64..4096")
        info = await _read_json(ctx, inputs["info"][0]) if inputs.get("info") else {}
        pano = to_2to1(await _load(ctx, refs[0]))
        up = inputs.get("upscaled") or []
        upscale = (info or {}).get("upscale") or {}
        if up and upscale:
            big = await _load(ctx, up[0])
            pano = await asyncio.to_thread(_unwrap_upscaled, big, pano.width, int(upscale["pad"]),
                                           int(upscale["width"]))
        ctx.progress(1, 5, label="faces")
        eq = _rgb(pano)
        n = face_size(pano.width, size)
        faces = await asyncio.to_thread(cube_faces, eq, n)
        ctx.progress(2, 5, label="irradiance")
        irr = await asyncio.to_thread(irradiance, eq)
        light = await asyncio.to_thread(dominant_light, eq)
        seam = await asyncio.to_thread(seam_report, eq)
        ctx.progress(3, 5, label="save")
        pano_ref = await _put_png(ctx, pano, f"{asset}_pano.png")
        face_refs = {f: await _put_png(ctx, _u8(faces[f]), f"{asset}_{f}.png") for f in FACES}
        irr_img = _u8(irr).resize((IRR_W * 4, IRR_H * 4), Image.Resampling.BICUBIC)
        irr_ref = await _put_png(ctx, irr_img, f"{asset}_irradiance.png")
        cross = await _put_png(ctx, await asyncio.to_thread(cross_image, faces), f"{asset}_cross.png")
        ctx.progress(4, 5, label="skybox.json")
        doc = {"type": SKYBOX_TYPE, "asset": asset, "convention": "x right, y up, z forward; pano centre = forward",
               "pano": {"file": pano_ref.name, "hash": pano_ref.hash, "width": pano.width, "height": pano.height},
               "faces": {f: {"file": r.name, "hash": r.hash} for f, r in face_refs.items()}, "face_size": n,
               "irradiance": {"file": irr_ref.name, "hash": irr_ref.hash}, "light": light, "seam": seam,
               "fix": {k: info.get(k) for k in ("overlap_px", "pole_rows", "yaw", "before", "after")} if info else None}
        sky_ref = await _put_json(ctx, doc, SKYBOX_FILE)
        ctx.progress(5, 5)
        return StepResult(outputs={"images": [pano_ref], "faces": list(face_refs.values()), "irradiance": [irr_ref],
                                   "preview": [cross], "skybox": [sky_ref]},
                          meta={"face_size": n, "width": pano.width, "seam_score": seam["score"],
                                "sun_elevation": light["elevation"]})


def _unwrap_upscaled(big: Image.Image, width: int, pad: int, target: int) -> Image.Image:
    scale = big.width / (width + 2 * pad)
    p = int(round(pad * scale))
    core = big.convert("RGB").crop((p, 0, big.width - p, big.height))
    return wrap_resize(core, target, target // 2).convert("RGB")


PANO_OPS = (PanoFix, PanoCubemap)
