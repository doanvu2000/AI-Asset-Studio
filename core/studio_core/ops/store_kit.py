"""`op.store_prep` frames a character/asset image for the Klein icon and key-art edits; `op.store_kit` turns a master
icon + key art (+ the cut-out subject and a title layout) into the whole store kit: platform icon sets, Steam
capsules, a zip with the folder tree, a report with the checks and a contact sheet for the approve gate."""
from __future__ import annotations

import asyncio
import io
import json
import zipfile
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from ..contracts import BlobRef, Outputs, StepContext, StepResult
from . import store_capsules as sc
from . import store_icons as si
from .executors import OpExecutor, _images_in, _load, _put_png, _read_json

KIT_TYPE = "asset_studio.store_kit"
TARGETS = ("ios", "android", "steam", "web", "windows")
ZIP_ROOT = "store_kit"
# Fixed member timestamps keep the zip byte-identical across reruns (step cache, blob dedupe).
ZIP_TIME = (2020, 1, 1, 0, 0, 0)


def parse_targets(raw: Any) -> list[str]:
    items = raw if isinstance(raw, list) else str(raw or "").replace(";", ",").split(",")
    picked = [t.strip().lower() for t in items if str(t).strip()]
    return [t for t in TARGETS if t in picked] or list(TARGETS)


def zip_bytes(files: si.Files) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path, data in files:
            info = zipfile.ZipInfo(f"{ZIP_ROOT}/{path}", ZIP_TIME)
            info.compress_type = zipfile.ZIP_STORED if path.endswith((".png", ".jpg")) else zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    return buf.getvalue()


def prep_frames(img: Image.Image, icon: int = si.MASTER, wide: tuple[int, int] = (1344, 768)
                ) -> tuple[Image.Image, Image.Image]:
    """Subject trimmed and centred on white: a square for the icon edit, a wide canvas for the key-art edit."""
    rgba = img.convert("RGBA")
    bbox = rgba.getchannel("A").point(lambda v: 255 if v > 16 else 0).getbbox() or (0, 0, *rgba.size)
    sub = rgba.crop(bbox)

    def place(size: tuple[int, int], frac: float) -> Image.Image:
        s = min(size[0] * frac / sub.width, size[1] * frac / sub.height)
        im = sub.resize((max(1, round(sub.width * s)), max(1, round(sub.height * s))), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", size, (255, 255, 255, 255))
        canvas.alpha_composite(im, ((size[0] - im.width) // 2, (size[1] - im.height) // 2))
        return canvas.convert("RGB")

    return place((icon, icon), 0.82), place(wide, 0.86)


class StorePrep(OpExecutor):
    kind = "op.store_prep"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        src = await _load(ctx, _images_in(inputs)[0])
        square, wide = await asyncio.to_thread(prep_frames, src)
        return StepResult(outputs={"square": [await _put_png(ctx, square, "icon_ref.png")],
                                   "wide": [await _put_png(ctx, wide, "key_art_ref.png")]})


def _checker(size: tuple[int, int], cell: int = 10) -> Image.Image:
    a = (np.indices((size[1], size[0])) // cell).sum(0) % 2
    v = np.where(a, 92, 70).astype(np.uint8)
    return Image.fromarray(np.dstack([v, v, v, np.full_like(v, 255)]), "RGBA")


def _thumb(img: Image.Image, h: int, max_w: int) -> Image.Image:
    s = min(h / img.height, max_w / img.width)
    rs = Image.Resampling.NEAREST if s >= 2 else Image.Resampling.LANCZOS
    im = img.convert("RGBA").resize((max(1, round(img.width * s)), max(1, round(img.height * s))), rs)
    if im.getchannel("A").getextrema()[0] < 255:
        bg = _checker(im.size)
        bg.alpha_composite(im)
        return bg
    return im


def contact_sheet(sections: list[tuple[str, list[tuple[str, Image.Image, int, bool]]]], width: int = 1800
                  ) -> Image.Image:
    """Rows of (label, image, row height, ok) tiles under section titles; failed checks get an orange label."""
    gap, label_h, head_h = 18, 22, 34
    ui = next((f for f in ("segoe_bold", "arial_bold", "dejavu_bold") if f in sc.available_fonts()), None)
    font, head = sc.load_font(ui, 14), sc.load_font(ui, 22)
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    placed: list[tuple[int, int, Image.Image, str, bool]] = []
    heads: list[tuple[int, str]] = []
    y = gap
    for title, tiles in sections:
        heads.append((y, title))
        y += head_h
        x, row_h = gap, 0
        for label, img, h, ok in tiles:
            t = _thumb(img, h, width - 2 * gap)
            slot = max(t.width, round(probe.textlength(label, font=font)))
            if x + slot > width - gap and x > gap:
                y += row_h + label_h + gap
                x, row_h = gap, 0
            placed.append((x, y, t, label, ok))
            x += slot + gap
            row_h = max(row_h, t.height)
        y += row_h + label_h + gap * 2
    canvas = Image.new("RGBA", (width, y), (28, 28, 34, 255))
    d = ImageDraw.Draw(canvas)
    for hy, title in heads:
        d.text((gap, hy), title, fill=(240, 240, 245, 255), font=head)
    for x, ty, t, label, ok in placed:
        canvas.alpha_composite(t, (x, ty))
        d.text((x, ty + t.height + 3), label, fill=(170, 220, 170, 255) if ok else (255, 160, 60, 255), font=font)
    return canvas.convert("RGB")


def build_kit(icon: Image.Image, subject: Image.Image | None, art: Image.Image | None, layout_raw: Any,
              title: str, targets: list[str]) -> dict[str, Any]:
    master = si.square_master(icon)
    checks: dict[str, Any] = {}
    flagged: list[str] = []
    tile = si.tile_box(master)
    if tile:
        master = master.crop(tile).resize((si.MASTER, si.MASTER), Image.Resampling.LANCZOS)
        flagged.append("icon: the model drew an icon tile on a backdrop; cropped to the tile")
    if subject is not None:
        subject = si.square_master(subject, si.MASTER)
        if tile:
            subject = subject.crop(tile).resize((si.MASTER, si.MASTER), Image.Resampling.LANCZOS)
        cover = float((np.asarray(subject.getchannel("A")) > 128).mean())
        if cover > 0.75:
            flagged.append(f"Android: the cut-out covers {cover:.0%} of the icon (not a separable subject), "
                           "adaptive icon uses the whole art")
            subject = None
    checks["icon"] = {"tile_cropped": list(tile) if tile else None, "subject": subject is not None}
    alpha_share = si.transparent_share(master)
    bg = si.border_color(master)
    master_rgb = si.flatten(master, bg)
    files: si.Files = [("master/icon_1024.png", si.png_bytes(master_rgb))]
    named: list[tuple[str, Image.Image]] = [("app_icon_1024.png", master_rgb)]
    icon_tiles: list[tuple[str, Image.Image, int, bool]] = [("master 1024", master_rgb, 200, True)]

    if "ios" in targets:
        files += si.ios_files(master_rgb)
        checks["ios"] = {"ok": True, "files": len(si.IOS_ICONS), "alpha": False,
                         "flattened_transparency": round(alpha_share, 4)}
        if alpha_share > 0.001:
            flagged.append(f"iOS: master icon had {alpha_share:.1%} transparent pixels, flattened onto rgb{bg}")
        icon_tiles.append(("iOS mask", si.masked(si.resized(master_rgb, 180), "ios"), 140, True))
    if "android" in targets:
        afiles, ainfo, aimgs = si.android_files(master_rgb, subject)
        files += afiles
        sz = ainfo["safe_zone"]
        checks["android"] = {**ainfo, "ok": bool(sz["ok"])}
        if not ainfo["subject"]:
            flagged.append("Android: no cut-out subject; the whole art sits in the background layer, round masks "
                           "may clip its corners and there is no monochrome layer")
        elif not sz["ok"]:
            flagged.append(f"Android: {sz['outside_safe']:.1%} of the foreground is outside the 66dp safe circle")
        named.append(("play_store_512.png", aimgs["play"]))
        comp = aimgs["composite"]
        safe = comp.copy()
        dd = ImageDraw.Draw(safe)
        n, r = safe.width, si.SAFE_RATIO * safe.width / 2
        dd.ellipse((n / 2 - r, n / 2 - r, n / 2 + r, n / 2 + r), outline=(0, 255, 255, 255), width=3)
        v = (n - si.VISIBLE_DP / si.ADAPTIVE_DP * n) / 2
        dd.rectangle((v, v, n - v, n - v), outline=(255, 255, 0, 200), width=2)
        icon_tiles += [("adaptive 108dp + safe 66dp", safe, 170, bool(sz["ok"]))]
        vis = si.visible_crop(comp)
        icon_tiles += [(f"android {shape}", si.masked(vis, shape), 120, True)
                       for shape in ("circle", "squircle", "rounded")]
        if ainfo["subject"]:
            themed = Image.new("RGBA", vis.size, (32, 48, 40, 255))
            mono = si.visible_crop(aimgs["monochrome"])
            tint = Image.new("RGBA", mono.size, (190, 235, 205, 255))
            tint.putalpha(mono.getchannel("A"))
            themed.alpha_composite(tint)
            icon_tiles.append(("themed (monochrome)", si.masked(themed, "circle"), 120, True))
        icon_tiles.append(("Play 512", aimgs["play"], 140, True))
    if "web" in targets:
        files += si.web_files(master_rgb, title or "Game")
        icon_tiles += [("favicon 16 (x4)", si.resized(master_rgb, 16), 64, True),
                       ("favicon 32 (x2)", si.resized(master_rgb, 32), 64, True)]
        checks["web"] = {"ok": True, "ico": list(si.WEB_ICO), "png": si.WEB_PNG}
    if "windows" in targets:
        files += si.windows_files(master_rgb)
        checks["windows"] = {"ok": True, "ico": list(si.WINDOWS_ICO)}

    steam_tiles: list[tuple[str, Image.Image, int, bool]] = []
    layout = None
    if "steam" in targets:
        files += si.steam_icon_files(master_rgb)
        icon_tiles.append(("Steam community 184", si.resized(master_rgb, si.STEAM_COMMUNITY), 100, True))
        if art is None:
            flagged.append("Steam: no key art, capsules skipped")
            checks["steam"] = {"ok": False, "capsules": []}
        else:
            art_rgb = art.convert("RGB")
            focus = None
            if isinstance(layout_raw, dict):
                focus = sc.normalize_focus(layout_raw.get("focus"))
            layout = sc.normalize_layout(layout_raw, title, focus or sc.detect_focus(art_rgb))
            caps = []
            for name, w, h, with_title in sc.STEAM_CAPSULES:
                img, info = sc.capsule(art_rgb, layout, name, w, h, with_title and bool(title.strip()))
                # The small capsule is meant to be mostly logo, so its title may sit over the art's focus.
                covered = info.get("text_over_focus", 0) > 0.3 and info.get("anchor") != "center"
                ok = info["focus_kept"] >= 0.6 and not covered
                if info["focus_kept"] < 0.6:
                    flagged.append(f"Steam {name}: crop keeps only {info['focus_kept']:.0%} of the focus box")
                if covered:
                    flagged.append(f"Steam {name}: title covers the focus ({info['text_over_focus']:.0%} of it)")
                info["ok"] = ok
                caps.append(info)
                fname = f"{name}_{w}x{h}.png"
                files.append((f"steam/{fname}", si.png_bytes(img)))
                named.append((f"steam_{fname}", img))
                steam_tiles.append((f"{name} {w}x{h}", img, min(h, 260 if w < 3000 else 200), ok))
            if title.strip():
                logo = sc.library_logo(layout)
                files.append(("steam/library_logo.png", si.png_bytes(logo)))
                named.append(("steam_library_logo.png", logo))
                steam_tiles.append((f"library_logo {logo.width}x{logo.height}", logo, min(logo.height, 160), True))
            files.append(("master/key_art.jpg", si.jpg_bytes(art_rgb)))
            checks["steam"] = {"ok": all(c["ok"] for c in caps), "capsules": caps}

    report = {"type": KIT_TYPE, "title": title, "targets": targets, "layout": layout, "checks": checks,
              "flagged": flagged, "files": sorted(p for p, _ in files)}
    files.append(("report.json", json.dumps(report, indent=1, ensure_ascii=False).encode("utf-8")))
    sections = [("App icon", icon_tiles)] + ([("Steam", steam_tiles)] if steam_tiles else [])
    return {"files": files, "named": named, "report": report, "sheet": contact_sheet(sections)}


class StoreKit(OpExecutor):
    kind = "op.store_kit"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        icon = await _load(ctx, _images_in(inputs, "icon")[0])
        subject = await _load(ctx, inputs["subject"][0]) if inputs.get("subject") else None
        art = await _load(ctx, inputs["art"][0]) if inputs.get("art") else None
        layout = await _read_json(ctx, inputs["layout"][0]) if inputs.get("layout") else None
        title = " ".join(str(params.get("title") or "").split())
        targets = parse_targets(params.get("targets"))
        ctx.progress(0, 2, "store kit")
        kit = await asyncio.to_thread(build_kit, icon, subject, art, layout, title, targets)
        ctx.progress(1, 2, "store kit")
        images: list[BlobRef] = [await _put_png(ctx, img, name) for name, img in kit["named"]]
        zipped = await asyncio.to_thread(zip_bytes, kit["files"])
        zip_ref = await ctx.blobs.put_bytes(zipped, name=f"{ZIP_ROOT}.zip", mime="application/zip")
        report_ref = await ctx.blobs.put_bytes(json.dumps(kit["report"], indent=1, ensure_ascii=False).encode(),
                                               name="store_kit.json", mime="application/json")
        sheet_ref = await _put_png(ctx, kit["sheet"], "store_kit_preview.png")
        ctx.progress(2, 2, "store kit")
        meta: dict[str, Any] = {"files": len(kit["files"]), "zip_bytes": len(zipped), "targets": targets,
                                "font": (kit["report"]["layout"] or {}).get("font")}
        if kit["report"]["flagged"]:
            meta["flagged"] = kit["report"]["flagged"]
        return StepResult(outputs={"images": images, "zip": [zip_ref], "preview": [sheet_ref],
                                   "report": [report_ref]}, meta=meta)


STORE_OPS = (StorePrep, StoreKit)
