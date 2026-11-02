"""Android exporter: `res/` tree to paste into app/src/main (density WebP, .9.png, frames, launcher icons) + Compose helpers."""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw

from studio_core.export.sheet import Track, ident, layout
from studio_core.export.source import Files, Source

DENSITIES = (("mdpi", 1.0), ("hdpi", 1.5), ("xhdpi", 2.0), ("xxhdpi", 3.0), ("xxxhdpi", 4.0))
MASTER = 4.0
MANY_FRAMES = 60
APP_ICON_NAMES = ("app_icon", "ic_launcher")
ICON_BG = "ic_launcher_background"
SAFE_ZONE = 66 / 108
KOTLIN_FILE = "kotlin/AssetStudioAssets.kt"
PACKAGE = "com.example.assets"
ANDROID_NS = "http://schemas.android.com/apk/res/android"

# Java/Kotlin keywords and generated R members: R.drawable.<name> must compile in both languages.
RESERVED = frozenset(
    "abstract assert boolean break byte case catch char class const continue default do double else enum extends "
    "final finally float for goto if implements import instanceof int interface long native new null package private "
    "protected public return short static strictfp super switch synchronized this throw throws transient try void "
    "volatile while true false attr color dimen drawable id layout string style styleable "
    "as fun in is object typealias typeof val var when".split())





@dataclass
class Clip:
    name: str
    frames: list[str]
    fps: float
    loop: bool


def res_name(name: str, used: set[str], fallback: str = "img") -> str:
    base = re.sub(r"_+", "_", re.sub(r"[^a-z0-9_]", "_", ident(name, fallback).lower())).strip("_") or fallback
    if base[0].isdigit():
        base = f"{fallback}_{base}"
    if base in RESERVED:
        base = f"{base}_{fallback}"
    out, n = base, 2
    while out in used:
        out, n = f"{base}_{n}", n + 1
    used.add(out)
    return out


def _load(p: Path | bytes) -> Image.Image:
    with Image.open(io.BytesIO(p) if isinstance(p, bytes) else p) as im:
        return im.convert("RGBA")


def _has_alpha(im: Image.Image) -> bool:
    return im.getchannel("A").getextrema()[0] < 255


def _webp(im: Image.Image, lossless: bool) -> bytes:
    buf = io.BytesIO()
    if lossless:
        im.save(buf, "WEBP", lossless=True)
    else:
        im.convert("RGB").save(buf, "WEBP", quality=90)
    return buf.getvalue()


def _png(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _scaled(im: Image.Image, f: float, pixel: bool) -> Image.Image:
    if f == 1.0:
        return im
    size = (max(1, round(im.width * f)), max(1, round(im.height * f)))
    return im.resize(size, Image.NEAREST if pixel else Image.LANCZOS)


def _fit_border(lo: int, hi: int, size: int) -> tuple[int, int]:
    """Shrinks a border pair until at least one stretchable pixel is left."""
    lo, hi = max(0, lo), max(0, hi)
    while lo + hi > size - 1:
        if lo >= hi:
            lo -= 1
        else:
            hi -= 1
    return lo, hi


def nine_patch(im: Image.Image, border: tuple[int, int, int, int]) -> Image.Image:
    """Wraps `im` in the 1px .9.png frame: top/left = stretch area, bottom/right = content (padding) area."""
    w, h = im.size
    l, r = _fit_border(border[0], border[2], w)
    t, b = _fit_border(border[1], border[3], h)
    out = Image.new("RGBA", (w + 2, h + 2), (0, 0, 0, 0))
    out.paste(im, (1, 1))
    px, black = out.load(), (0, 0, 0, 255)
    for x in range(1 + l, 1 + w - r):
        px[x, 0] = px[x, h + 1] = black
    for y in range(1 + t, 1 + h - b):
        px[0, y] = px[w + 1, y] = black
    return out


def ui_files(name: str, im: Image.Image, border: tuple[int, int, int, int], pixel: bool) -> Files:
    sliced = any(border)
    lossless = pixel or _has_alpha(im)
    out: Files = []
    for d, s in DENSITIES:
        f = s / MASTER
        img = _scaled(im, f, pixel)
        if sliced:
            b = tuple(0 if v <= 0 else max(1, round(v * f)) for v in border)
            out.append((f"res/drawable-{d}/{name}.9.png", _png(nine_patch(img, b))))  # type: ignore[arg-type]
        else:
            out.append((f"res/drawable-{d}/{name}.webp", _webp(img, lossless)))
    return out


def _tracks(src: Source) -> tuple[list[Track], Image.Image]:
    assert src.sheet is not None and src.meta is not None
    lay = layout(src.meta, src.clip_hint, directional_rows=src.directional_rows)
    tracks = list(lay.tracks)
    if not tracks and lay.cells:
        tracks = [Track(ident(src.clip_hint, "anim"), "", 0.0, lay.cells, 8.0, True)]
    return tracks, _load(src.sheet)


def sheet_files(src: Source, used: set[str]) -> tuple[Files, list[Clip], list[str]]:
    tracks, sheet = _tracks(src)
    files: Files = []
    clips: list[Clip] = []
    warns: list[str] = []
    for tr in tracks:
        clip = res_name(f"{src.asset}_{tr.name}", used, "anim")
        names = []
        for i, c in enumerate(tr.cells):
            fname = res_name(f"{clip}_{i}", used, "frame")
            files.append((f"res/drawable-nodpi/{fname}.webp",
                          _webp(sheet.crop((c.x, c.y, c.x + c.w, c.y + c.h)), True)))
            names.append(fname)
        clips.append(Clip(clip, names, tr.fps if tr.fps > 0 else 8.0, tr.loop))
        if len(names) > MANY_FRAMES:
            warns.append(f"- CẢNH BÁO: {clip} có {len(names)} khung (> {MANY_FRAMES}); mỗi khung là một bitmap riêng, "
                         "nên dùng sheet gốc + Canvas (drawImage với srcOffset/srcSize) thay vì AssetStudioAnimation.")
    return files, clips, warns


def _hex(color: str) -> str:
    c = color.strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", c):
        raise ValueError(f"colour must be #RRGGBB, got {color!r}")
    return f"#{c.upper()}"


def _rgba(color: str) -> tuple[int, int, int, int]:
    c = _hex(color)[1:]
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), 255


def _on_canvas(im: Image.Image, size: int, scale: float) -> Image.Image:
    box = max(1, round(size * scale))
    ratio = min(box / im.width, box / im.height)
    sized = im.resize((max(1, round(im.width * ratio)), max(1, round(im.height * ratio))), Image.LANCZOS)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(sized, ((size - sized.width) // 2, (size - sized.height) // 2), sized)
    return out


def _cover(im: Image.Image, size: int) -> Image.Image:
    ratio = max(size / im.width, size / im.height)
    big = im.resize((max(size, round(im.width * ratio)), max(size, round(im.height * ratio))), Image.LANCZOS)
    x, y = (big.width - size) // 2, (big.height - size) // 2
    return big.crop((x, y, x + size, y + size))


def _legacy(fg: Image.Image, bg: Image.Image | tuple[int, int, int, int], size: int, round_shape: bool) -> Image.Image:
    base = Image.new("RGBA", (size, size), bg) if isinstance(bg, tuple) else _cover(bg, size)
    base.alpha_composite(_on_canvas(fg, size, 0.8))
    big = size * 4
    mask = Image.new("L", (big, big), 0)
    d = ImageDraw.Draw(mask)
    if round_shape:
        d.ellipse((0, 0, big - 1, big - 1), fill=255)
    else:
        d.rounded_rectangle((0, 0, big - 1, big - 1), big // 6, fill=255)
    base.putalpha(mask.resize((size, size), Image.LANCZOS))
    return base


def _adaptive_xml(background: str) -> str:
    return (f'<?xml version="1.0" encoding="utf-8"?>\n<adaptive-icon xmlns:android="{ANDROID_NS}">\n'
            f'    <background android:drawable="{background}" />\n'
            '    <foreground android:drawable="@mipmap/ic_launcher_foreground" />\n'
            '    <monochrome android:drawable="@mipmap/ic_launcher_monochrome" />\n</adaptive-icon>\n')


def app_icon_files(image: Path | bytes, background: str | Path = "#FFFFFF") -> Files:
    """Launcher icons: legacy square/round, adaptive foreground (safe zone) + background + monochrome."""
    fg = _load(image)
    bg_img = None if isinstance(background, str) else _load(background)
    bg_fill = (255, 255, 255, 255) if bg_img is not None else _rgba(str(background))
    out: Files = []
    for d, s in DENSITIES:
        legacy, adaptive = round(48 * s), round(108 * s)
        for n, rnd in (("ic_launcher", False), ("ic_launcher_round", True)):
            out.append((f"res/mipmap-{d}/{n}.webp", _webp(_legacy(fg, bg_img or bg_fill, legacy, rnd), True)))
        canvas = _on_canvas(fg, adaptive, SAFE_ZONE)
        out.append((f"res/mipmap-{d}/ic_launcher_foreground.webp", _webp(canvas, True)))
        mono = Image.new("RGBA", canvas.size, (255, 255, 255, 0))
        mono.putalpha(canvas.getchannel("A"))
        out.append((f"res/mipmap-{d}/ic_launcher_monochrome.webp", _webp(mono, True)))
        if bg_img is not None:
            out.append((f"res/mipmap-{d}/{ICON_BG}.webp", _webp(_cover(bg_img, adaptive), False)))
    if bg_img is None:
        color = ('<?xml version="1.0" encoding="utf-8"?>\n<resources>\n'
                 f'    <color name="{ICON_BG}">{_hex(str(background))}</color>\n</resources>\n')
        out.append((f"res/values/{ICON_BG}.xml", color.encode()))
    xml = _adaptive_xml(f"@mipmap/{ICON_BG}" if bg_img is not None else f"@color/{ICON_BG}").encode()
    out += [("res/mipmap-anydpi-v26/ic_launcher.xml", xml), ("res/mipmap-anydpi-v26/ic_launcher_round.xml", xml)]
    return out


KOTLIN_HEAD = """package @PKG@

import android.graphics.Rect
import androidx.annotation.DrawableRes
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.graphics.drawscope.drawIntoCanvas
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.res.painterResource
import androidx.core.content.ContextCompat
import kotlinx.coroutines.delay
import @PKG@.R

class AssetStudioClip(val frames: List<Int>, val fps: Float, val loop: Boolean)

@Composable
fun AssetStudioAnimation(
    frames: List<Int>,
    fps: Float,
    modifier: Modifier = Modifier,
    loop: Boolean = true,
    playing: Boolean = true,
    contentDescription: String? = null,
    contentScale: ContentScale = ContentScale.Fit,
) {
    if (frames.isEmpty()) return
    var index by remember(frames) { mutableIntStateOf(0) }
    LaunchedEffect(frames, fps, loop, playing) {
        if (!playing) return@LaunchedEffect
        val frameMs = (1000f / fps.coerceAtLeast(1f)).toLong()
        while (true) {
            delay(frameMs)
            if (index >= frames.lastIndex) {
                if (!loop) break
                index = 0
            } else {
                index++
            }
        }
    }
    Image(
        painter = painterResource(frames[index.coerceAtMost(frames.lastIndex)]),
        contentDescription = contentDescription,
        modifier = modifier,
        contentScale = contentScale,
    )
}

@Composable
fun AssetStudioAnimation(
    clip: AssetStudioClip,
    modifier: Modifier = Modifier,
    playing: Boolean = true,
    contentDescription: String? = null,
    contentScale: ContentScale = ContentScale.Fit,
) = AssetStudioAnimation(clip.frames, clip.fps, modifier, clip.loop, playing, contentDescription, contentScale)

fun Modifier.ninePatchBackground(@DrawableRes id: Int): Modifier = composed {
    val context = LocalContext.current
    val drawable = remember(id) { ContextCompat.getDrawable(context, id)?.mutate() }
    drawBehind {
        drawable?.let { d ->
            d.setBounds(0, 0, size.width.toInt(), size.height.toInt())
            drawIntoCanvas { d.draw(it.nativeCanvas) }
        }
    }
}

fun Modifier.ninePatchPadding(@DrawableRes id: Int): Modifier = composed {
    val context = LocalContext.current
    val density = LocalDensity.current
    val pad = remember(id) { Rect().also { ContextCompat.getDrawable(context, id)?.getPadding(it) } }
    with(density) { Modifier.padding(pad.left.toDp(), pad.top.toDp(), pad.right.toDp(), pad.bottom.toDp()) }
}
"""


def kotlin_source(pkg: str, images: list[str], patches: list[str], clips: list[Clip]) -> str:
    out = [KOTLIN_HEAD.replace("@PKG@", pkg), "object AssetStudioAssets {"]
    for title, names in (("Images", images), ("NinePatches", patches)):
        if names:
            out += [f"    object {title} {{",
                    *[f"        @DrawableRes val {n}: Int = R.drawable.{n}" for n in names], "    }", ""]
    if clips:
        out.append("    object Clips {")
        for c in clips:
            frames = ", ".join(f"R.drawable.{f}" for f in c.frames)
            out.append(f"        val {c.name} = AssetStudioClip(listOf({frames}), {c.fps:g}f, {str(c.loop).lower()})")
        out += ["    }", ""]
    return "\n".join(out).rstrip() + "\n}\n"


README_HEAD = """{asset} — xuất từ AI Asset Studio cho Android (Kotlin + Jetpack Compose).
Chép thư mục res/ vào app/src/main/ (gộp với res/ có sẵn) và kotlin/AssetStudioAssets.kt vào
app/src/main/java/{pkgpath}/ (package {pkg}; dòng import {pkg}.R phải trùng namespace của app, sửa nếu khác).
Tên tài nguyên chỉ gồm [a-z0-9_]; tên trùng/không hợp lệ đã được đổi (xem danh sách bên dưới).

"""
README_UI = """- drawable-mdpi..xxxhdpi/<tên>.webp: ảnh UI/icon 5 mật độ (tỉ 1 : 1.5 : 2 : 3 : 4, bản gốc = xxxhdpi).
  Compose: Image(painterResource(AssetStudioAssets.Images.<tên>), contentDescription = null)."""
README_SLICED = """- <tên>.9.png: 9-slice chuẩn Android (viền 1px đen: trên/trái = vùng giãn, dưới/phải = vùng nội dung), viền
  đã scale theo mật độ. painterResource không giữ vùng giãn của .9.png nên dùng helper trong AssetStudioAssets.kt:
    Box(Modifier.ninePatchBackground(AssetStudioAssets.NinePatches.<tên>).ninePatchPadding(AssetStudioAssets.NinePatches.<tên>))"""
README_ANIM = """- drawable-nodpi/<clip>_<i>.webp: từng khung hoạt ảnh (nodpi = giữ nguyên pixel, không scale theo mật độ).
  Không xuất animation-list XML: AnimationDrawable cần View, còn Compose chạy khung bằng LaunchedEffect.
  Compose: AssetStudioAnimation(AssetStudioAssets.Clips.<clip>, Modifier.size(96.dp))."""
README_ICON = """- mipmap-*/ic_launcher(.webp|_round.webp|_foreground.webp|_monochrome.webp) + mipmap-anydpi-v26/ic_launcher(.xml|_round.xml)
  + nền ic_launcher_background (trắng #FFFFFF, đổi trong values/ic_launcher_background.xml): đặt
  android:icon="@mipmap/ic_launcher" và android:roundIcon="@mipmap/ic_launcher_round" trong AndroidManifest. Nội dung
  foreground nằm trong safe zone 66dp/108dp; monochrome (alpha -> trắng) cho themed icon Android 13+."""


def export(src: Source, package: str = PACKAGE) -> Files:
    a = src.asset
    used: set[str] = set()
    files: Files = []
    notes: list[str] = []
    names: list[str] = []
    images: list[str] = []
    patches: list[str] = []
    clips: list[Clip] = []
    pixel = src.is_pixel()
    if src.sheet is not None and src.meta is not None:
        sf, clips, warns = sheet_files(src, used)
        files += sf
        names += [f"anim {c.name} ({len(c.frames)} khung, {c.fps:g} fps, {'lặp' if c.loop else 'một lần'})" for c in clips]
        notes += [README_ANIM, *warns]
    icon = False
    for n, p, border, state in src.ui:
        label = n if state in ("", "normal") or n.endswith(f"_{state}") else f"{n}_{state}"
        name = res_name(label, used, "ui")
        files += ui_files(name, _load(p), tuple(border), pixel)  # type: ignore[arg-type]
        sliced = any(border)
        (patches if sliced else images).append(name)
        names.append(f"{'9-slice' if sliced else 'image'} {name}")
        if name in APP_ICON_NAMES and not icon:
            files += app_icon_files(p)
            icon = True
    if src.ui:
        notes = [README_UI, *([README_SLICED] if patches else []), *notes, *([README_ICON] if icon else [])]
    if not files:
        return []
    files.append((KOTLIN_FILE, kotlin_source(package, images, patches, clips).encode()))
    listing = "\n".join(f"  * {n}" for n in names)
    head = README_HEAD.format(asset=a, pkg=package, pkgpath=package.replace(".", "/"))
    files.append(("README_ANDROID.md", (head + "\n".join(notes) + f"\n\nTài nguyên:\n{listing}\n").encode()))
    return files
