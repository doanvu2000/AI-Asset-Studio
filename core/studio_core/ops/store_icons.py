"""App/store icon exports from one master icon: iOS AppIcon.appiconset, Android adaptive + legacy mipmaps + Play,
Steam community/client icons, web favicons and a Windows .ico, plus the platform safe-zone checks."""
from __future__ import annotations

import io
import json
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .upscale import bleed_rgb

Files = list[tuple[str, bytes]]

MASTER = 1024
# Xcode's full AppIcon set as (idiom, points, scale); the 1024 ios-marketing entry is the App Store icon.
IOS_ICONS: tuple[tuple[str, float, int], ...] = (
    ("iphone", 20, 2), ("iphone", 20, 3), ("iphone", 29, 2), ("iphone", 29, 3), ("iphone", 40, 2),
    ("iphone", 40, 3), ("iphone", 60, 2), ("iphone", 60, 3),
    ("ipad", 20, 1), ("ipad", 20, 2), ("ipad", 29, 1), ("ipad", 29, 2), ("ipad", 40, 1), ("ipad", 40, 2),
    ("ipad", 76, 1), ("ipad", 76, 2), ("ipad", 83.5, 2),
    ("ios-marketing", 1024, 1),
)
ANDROID_DENSITIES = {"mdpi": 1.0, "hdpi": 1.5, "xhdpi": 2.0, "xxhdpi": 3.0, "xxxhdpi": 4.0}
# Adaptive layers are 108dp; launchers show the inner 72dp and only the 66dp circle survives every mask shape.
ADAPTIVE_DP, VISIBLE_DP, SAFE_DP, LEGACY_DP = 108, 72, 66, 48
ADAPTIVE_PX = ADAPTIVE_DP * 4
SAFE_RATIO = SAFE_DP / ADAPTIVE_DP
PLAY_SIZE = 512
STEAM_COMMUNITY = 184
STEAM_CLIENT_ICO = (16, 32)
WEB_ICO = (16, 32, 48)
WEB_PNG = {"favicon-16x16.png": 16, "favicon-32x32.png": 32, "apple-touch-icon.png": 180,
           "android-chrome-192x192.png": 192, "android-chrome-512x512.png": 512}
WINDOWS_ICO = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)
SAFE_TOLERANCE = 0.005

ADAPTIVE_XML = """<?xml version="1.0" encoding="utf-8"?>
<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">
    <background android:drawable="@mipmap/ic_launcher_background"/>
    <foreground android:drawable="@mipmap/ic_launcher_foreground"/>
    <monochrome android:drawable="@mipmap/ic_launcher_monochrome"/>
</adaptive-icon>
"""


def png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", compress_level=6)
    return buf.getvalue()


def jpg_bytes(img: Image.Image, quality: int = 92) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def square_master(img: Image.Image, size: int = MASTER) -> Image.Image:
    rgba = img.convert("RGBA")
    s = min(rgba.size)
    x0, y0 = (rgba.width - s) // 2, (rgba.height - s) // 2
    sq = rgba.crop((x0, y0, x0 + s, y0 + s))
    return sq if s == size else sq.resize((size, size), Image.Resampling.LANCZOS)


def border_color(img: Image.Image, ring: int = 6) -> tuple[int, int, int]:
    a = np.asarray(img.convert("RGBA"))
    edge = np.concatenate([a[:ring].reshape(-1, 4), a[-ring:].reshape(-1, 4), a[:, :ring].reshape(-1, 4),
                           a[:, -ring:].reshape(-1, 4)])
    seen = edge[edge[:, 3] > 128]
    if not len(seen):
        return (24, 24, 32)
    return tuple(int(v) for v in np.median(seen[:, :3], 0))  # type: ignore[return-value]


def tile_box(img: Image.Image, ring: int = 12, tol: float = 30) -> tuple[int, int, int, int] | None:
    """Image models often draw an icon *tile* (rounded square) on a plain backdrop despite the prompt; the stores
    add their own mask, so that tile is cropped out (inset past its rounded corners). None when full-bleed."""
    a = np.asarray(img.convert("RGB"), np.float32)
    n = a.shape[0]
    # Backdrops are often soft gradients: model them per row from the side edges and per column from top/bottom.
    left, right = np.median(a[:, :ring], 1), np.median(a[:, -ring:], 1)
    top, bottom = np.median(a[:ring], 0), np.median(a[-ring:], 0)
    if min(float(np.abs(left - right).max(1).mean()), float(np.abs(top - bottom).max(1).mean())) > tol / 2:
        return None
    t = np.linspace(0, 1, n, dtype=np.float32)[:, None]
    by_row = left[:, None] * (1 - t[None, :, :]) + right[:, None] * t[None, :, :]
    by_col = top[None, :] * (1 - t[:, None, :]) + bottom[None, :] * t[:, None, :]
    diff = np.minimum(np.abs(a - by_row).max(2), np.abs(a - by_col).max(2)) > tol
    rows, cols = np.flatnonzero(diff.mean(1) > 0.5), np.flatnonzero(diff.mean(0) > 0.5)
    if not len(rows) or not len(cols):
        return None
    x0, x1, y0, y1 = int(cols[0]), int(cols[-1]) + 1, int(rows[0]), int(rows[-1]) + 1
    side = min(x1 - x0, y1 - y0)
    if not 0.5 * n <= side <= 0.97 * n or abs((x1 - x0) - (y1 - y0)) > 0.06 * n:
        return None
    inset = round(side * 0.07)
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    half = side // 2 - inset
    return cx - half, cy - half, cx + half, cy + half


def transparent_share(img: Image.Image) -> float:
    if img.mode != "RGBA":
        return 0.0
    return float((np.asarray(img)[..., 3] < 255).mean())


def flatten(img: Image.Image, bg: tuple[int, int, int]) -> Image.Image:
    base = Image.new("RGBA", img.size, (*bg, 255))
    base.alpha_composite(img.convert("RGBA"))
    return base.convert("RGB")


def resized(img: Image.Image, px: int) -> Image.Image:
    out = img.resize((px, px), Image.Resampling.LANCZOS, reducing_gap=3.0)
    # Big downscale ratios wash small icons out; a light unsharp brings the edges back.
    return out.filter(ImageFilter.UnsharpMask(radius=0.6, percent=60, threshold=2)) if px <= 64 else out


def shape_mask(size: int, shape: str) -> Image.Image:
    """Launcher/OS masks for previews and legacy icons, drawn 4x and reduced for clean edges."""
    big = size * 4
    if shape == "squircle":
        y, x = (np.mgrid[0:big, 0:big] + 0.5) / big * 2 - 1
        m = (np.abs(x) ** 4 + np.abs(y) ** 4 <= 1).astype(np.uint8) * 255
        return Image.fromarray(m, "L").resize((size, size), Image.Resampling.BOX)
    mask = Image.new("L", (big, big), 0)
    d = ImageDraw.Draw(mask)
    if shape == "circle":
        d.ellipse((0, 0, big - 1, big - 1), fill=255)
    else:
        radius = {"ios": 0.2237, "rounded": 0.16}.get(shape, 0.16)
        d.rounded_rectangle((0, 0, big - 1, big - 1), radius=round(radius * big), fill=255)
    return mask.resize((size, size), Image.Resampling.BOX)


def masked(img: Image.Image, shape: str) -> Image.Image:
    out = img.convert("RGBA")
    out.putalpha(Image.fromarray(np.minimum(np.asarray(out.getchannel("A")),
                                             np.asarray(shape_mask(out.width, shape))), "L"))
    return out


def ios_contents() -> dict[str, Any]:
    images = [{"filename": ios_name(pt, sc), "idiom": idiom, "scale": f"{sc}x", "size": f"{_pt(pt)}x{_pt(pt)}"}
              for idiom, pt, sc in IOS_ICONS]
    return {"images": images, "info": {"author": "xcode", "version": 1}}


def _pt(pt: float) -> str:
    return f"{pt:g}"


def ios_name(pt: float, scale: int) -> str:
    return f"Icon-{_pt(pt)}@{scale}x.png"


def ios_files(master_rgb: Image.Image, root: str = "ios/AppIcon.appiconset") -> Files:
    """App Store Connect rejects an icon with any alpha channel, so every file is plain RGB."""
    files: Files = []
    cache: dict[int, bytes] = {}
    for name, px in dict((ios_name(pt, sc), round(pt * sc)) for _, pt, sc in IOS_ICONS).items():
        if px not in cache:
            cache[px] = png_bytes(resized(master_rgb, px).convert("RGB"))
        files.append((f"{root}/{name}", cache[px]))
    files.append((f"{root}/Contents.json", json.dumps(ios_contents(), indent=2).encode()))
    return files


def _fit_subject(subject: Image.Image, size: int, radius: float) -> tuple[Image.Image, dict[str, Any]]:
    """Subject scaled about its bbox centre so its farthest visible pixel lands on `radius` (px)."""
    a = np.asarray(subject.getchannel("A"))
    ys, xs = np.nonzero(a > 32)
    if not len(xs):
        return Image.new("RGBA", (size, size), (0, 0, 0, 0)), {"empty": True}
    x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    r_max = float(np.sqrt((xs + 0.5 - cx) ** 2 + (ys + 0.5 - cy) ** 2).max())
    scale = radius / max(r_max, 1.0)
    crop = subject.crop((int(x0), int(y0), int(x1), int(y1)))
    w, h = max(1, round(crop.width * scale)), max(1, round(crop.height * scale))
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.alpha_composite(crop.resize((w, h), Image.Resampling.LANCZOS),
                           (round(size / 2 - (cx - x0) * scale), round(size / 2 - (cy - y0) * scale)))
    return canvas, {"scale": round(scale, 4)}


def adaptive_layers(master: Image.Image, subject: Image.Image | None
                    ) -> tuple[Image.Image, Image.Image, Image.Image, dict[str, Any]]:
    """(foreground, background, monochrome) at xxxhdpi (432 px). The foreground is the cut-out subject fitted inside
    the 66dp safe circle; the background is the master icon with the subject filled from its surroundings and
    blurred, so the art the subject stood on carries over without a ghost of it."""
    size = ADAPTIVE_PX
    if subject is None:
        # Whole art fills the 72dp viewport; the 18dp bleed around it is the same art blurred.
        rgb = master.convert("RGB")
        bg = rgb.filter(ImageFilter.GaussianBlur(master.width * 0.03)).resize((size, size), Image.Resampling.LANCZOS)
        inner = round(size * VISIBLE_DP / ADAPTIVE_DP)
        bg.paste(rgb.resize((inner, inner), Image.Resampling.LANCZOS), ((size - inner) // 2,) * 2)
        empty = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        return empty, bg.convert("RGBA"), empty, {"subject": False}
    sub = subject.convert("RGBA")
    if sub.size != master.size:
        sub = square_master(sub, master.width)
    fg, info = _fit_subject(sub, size, SAFE_RATIO * size / 2 * 0.98)
    hole = np.asarray(sub.getchannel("A").filter(ImageFilter.MaxFilter(9)))
    rgba = np.dstack([np.asarray(master.convert("RGB")), 255 - hole]).astype(np.uint8)
    filled = bleed_rgb(Image.fromarray(rgba, "RGBA"))
    bg = filled.filter(ImageFilter.GaussianBlur(master.width * 0.02)).resize((size, size), Image.Resampling.LANCZOS)
    a = fg.getchannel("A")
    mono = Image.merge("RGBA", (Image.new("L", a.size, 255),) * 3 + (a,))
    return fg, bg.convert("RGBA"), mono, {"subject": True, **info}


def safe_zone(fg: Image.Image) -> dict[str, Any]:
    """Share of the foreground's visible alpha outside the 66dp safe circle (any launcher mask may cut it)."""
    a = np.asarray(fg.getchannel("A"), np.float32) / 255
    total = float(a.sum())
    n = a.shape[0]
    y, x = (np.mgrid[0:n, 0:n] + 0.5) - n / 2
    r = np.sqrt(x * x + y * y) / n * ADAPTIVE_DP
    outside = float(a[r > SAFE_DP / 2].sum()) / total if total else 0.0
    beyond = float(a[r > VISIBLE_DP / 2].sum()) / total if total else 0.0
    return {"outside_safe": round(outside, 4), "outside_visible": round(beyond, 4),
            "ok": total > 0 and outside <= SAFE_TOLERANCE}


def adaptive_composite(fg: Image.Image, bg: Image.Image) -> Image.Image:
    out = bg.convert("RGBA").copy()
    out.alpha_composite(fg)
    return out


def visible_crop(img: Image.Image) -> Image.Image:
    n = img.width
    m = round(n * (ADAPTIVE_DP - VISIBLE_DP) / 2 / ADAPTIVE_DP)
    return img.crop((m, m, n - m, n - m))


def android_files(master: Image.Image, subject: Image.Image | None, root: str = "android"
                  ) -> tuple[Files, dict[str, Any], dict[str, Image.Image]]:
    fg, bg, mono, info = adaptive_layers(master, subject)
    comp = adaptive_composite(fg, bg)
    legacy = visible_crop(comp)
    files: Files = []
    for dens, k in ANDROID_DENSITIES.items():
        d = f"{root}/res/mipmap-{dens}"
        lp, ap = round(LEGACY_DP * k), round(ADAPTIVE_DP * k)
        layer = (lambda im: im if ap == ADAPTIVE_PX else im.resize((ap, ap), Image.Resampling.LANCZOS))
        if info["subject"]:
            files.append((f"{d}/ic_launcher_foreground.png", png_bytes(layer(fg))))
            files.append((f"{d}/ic_launcher_monochrome.png", png_bytes(layer(mono))))
        files.append((f"{d}/ic_launcher_background.png", png_bytes(layer(bg).convert("RGB"))))
        small = resized(legacy, lp)
        files.append((f"{d}/ic_launcher.png", png_bytes(masked(small, "rounded"))))
        files.append((f"{d}/ic_launcher_round.png", png_bytes(masked(small, "circle"))))
    xml = ADAPTIVE_XML
    if not info["subject"]:
        xml = "\n".join(ln for ln in xml.splitlines() if "foreground" not in ln and "monochrome" not in ln) + "\n"
    files.append((f"{root}/res/mipmap-anydpi-v26/ic_launcher.xml", xml.encode()))
    files.append((f"{root}/res/mipmap-anydpi-v26/ic_launcher_round.xml", xml.encode()))
    # Google Play: 512x512 32-bit PNG, full square; Play applies its own rounded mask.
    play = resized(master, PLAY_SIZE).convert("RGBA")
    files.append((f"{root}/play_store_512.png", png_bytes(play)))
    check = safe_zone(fg) if info["subject"] else {"ok": False, "outside_safe": None, "outside_visible": None}
    return files, {**info, "safe_zone": check}, {"foreground": fg, "background": bg, "monochrome": mono,
                                                 "composite": comp, "play": play}


def ico_bytes(master: Image.Image, sizes: tuple[int, ...]) -> bytes:
    src = master.convert("RGBA")
    frames = [resized(src, s).convert("RGBA") for s in sizes]
    buf = io.BytesIO()
    frames[-1].save(buf, "ICO", sizes=[(s, s) for s in sizes], append_images=frames[:-1])
    return buf.getvalue()


def web_files(master: Image.Image, name: str, root: str = "web") -> Files:
    files: Files = [(f"{root}/favicon.ico", ico_bytes(master, WEB_ICO))]
    for fname, px in WEB_PNG.items():
        img = resized(master, px)
        # iOS fills apple-touch-icon transparency with black.
        files.append((f"{root}/{fname}", png_bytes(img.convert("RGB") if fname.startswith("apple") else img)))
    manifest = {"name": name, "short_name": name[:12], "display": "standalone",
                "icons": [{"src": f"/android-chrome-{s}x{s}.png", "sizes": f"{s}x{s}", "type": "image/png"}
                          for s in (192, 512)]}
    files.append((f"{root}/site.webmanifest", json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8")))
    return files


def windows_files(master: Image.Image, root: str = "windows") -> Files:
    return [(f"{root}/app.ico", ico_bytes(master, WINDOWS_ICO))]


def steam_icon_files(master_rgb: Image.Image, root: str = "steam") -> Files:
    return [(f"{root}/community_icon_{STEAM_COMMUNITY}x{STEAM_COMMUNITY}.jpg",
             jpg_bytes(resized(master_rgb, STEAM_COMMUNITY))),
            (f"{root}/client_icon.ico", ico_bytes(master_rgb, STEAM_CLIENT_ICO))]
