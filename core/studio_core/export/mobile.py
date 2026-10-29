"""Mobile texture budget on engine export: per-asset-type presets (max size, ASTC / ETC2, mipmaps) written into Unity
Android + iPhone platform overrides and Godot .import files, plus an estimated GPU-memory table before/after."""
from __future__ import annotations

import io
import json
import math
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from PIL import Image

from studio_core.export.source import Files

TEXTURE_EXTS = (".png", ".jpg", ".jpeg", ".tga", ".webp")


@dataclass(frozen=True)
class Budget:
    kind: str
    label: str
    max_size: int
    # ASTC block edge for Unity; None = uncompressed RGBA32.
    astc: int | None
    # Godot compress/mode: 0 lossless, 2 VRAM compressed. high_quality picks ASTC 4x4 over ETC2 on mobile.
    godot_mode: int
    godot_hq: bool
    # None = keep what the exporter chose.
    mipmaps: bool | None


# Block sizes: UI/fonts and normals show 6x6 artefacts on gradients and edges; tiles 5x5 keeps seams clean;
# pixel art stays uncompressed (any block codec smears hard 1-px edges).
BUDGETS: dict[str, Budget] = {
    "sprite": Budget("sprite", "Sprite", 2048, 6, 2, True, False),
    "ui": Budget("ui", "UI / chữ", 2048, 4, 2, True, False),
    "texture": Budget("texture", "Texture 3D / lặp", 1024, 6, 2, False, True),
    "tileset": Budget("tileset", "Tileset", 2048, 5, 2, True, False),
    "normal": Budget("normal", "Normal map", 1024, 5, 2, True, None),
    "pixel": Budget("pixel", "Pixel art", 2048, None, 0, False, False),
}
# UnityEditor.TextureImporterFormat values.
UNITY_FORMAT = {4: 48, 5: 49, 6: 50, 8: 51, 10: 52, 12: 53}
UNITY_RGBA32 = 4
MIP_FACTOR = 4 / 3


def _has(rel: str, *words: str) -> bool:
    low = rel.lower()
    return any(w in low for w in words)


def classify(rel: str, meta: str | None, pixel: bool) -> str | None:
    """None = leave alone (colour-grading LUTs are data and must stay exact)."""
    stem = PurePosixPath(rel).stem.lower()
    parts = [p.lower() for p in PurePosixPath(rel).parts[:-1]]
    if stem.endswith("_lut") or "lut" in parts:
        return None
    if (meta and "textureType: 1\n" in meta) or stem.endswith(("_normal", "_n")):
        return "normal"
    if (meta and "filterMode: 0\n" in meta) or (meta is None and pixel):
        return "pixel"
    if _has(rel, "autotile", "wang", "tileset"):
        return "tileset"
    if "ui" in parts or "fonts" in parts or (meta and "spriteBorder: {x: 0, y: 0, z: 0, w: 0}" not in meta
                                              and "spriteBorder:" in meta):
        return "ui"
    if "parallax" in parts or "spine" in parts or "layers" in parts:
        return "sprite"
    if any(p in parts for p in ("textures", "pbr", "models")) or ".fbm" in rel.lower() or (
            meta and ("wrapU: 0\n" in meta or "textureType: 0\n" in meta)):
        return "texture"
    return "sprite"


def _image_info(data: bytes | Any) -> tuple[int, int, bool]:
    with Image.open(io.BytesIO(data) if isinstance(data, bytes) else data) as im:
        alpha = "A" in im.getbands() or "transparency" in im.info
        return im.width, im.height, alpha


def fit(size: tuple[int, int], max_size: int) -> tuple[int, int]:
    """Engines shrink the longest side to the limit and keep the aspect ratio."""
    w, h = size
    m = max(w, h)
    if m <= max_size:
        return w, h
    s = max_size / m
    return max(1, round(w * s)), max(1, round(h * s))


def astc_bytes(w: int, h: int, block: int) -> int:
    return math.ceil(w / block) * math.ceil(h / block) * 16


def _mips(n: float, on: bool) -> int:
    return int(n * MIP_FACTOR) if on else int(n)


def _unity_override(target: str, max_size: int, fmt: int) -> str:
    from studio_core.export.unity import _platform

    return (_platform(target, max_size).replace("textureFormat: -1", f"textureFormat: {fmt}")
            .replace("textureCompression: 0", "textureCompression: 1").replace("overridden: 0", "overridden: 1"))


def patch_unity_meta(meta: str, b: Budget) -> str:
    lines = meta.split("\n")
    try:
        start = lines.index("  platformSettings:")
    except ValueError:
        return meta
    end = start + 1
    while end < len(lines) and (lines[end].startswith("  - ") or lines[end].startswith("    ")):
        end += 1
    kept, entry = [], []
    for ln in lines[start + 1:end] + ["  - "]:
        if ln.startswith("  - ") and entry:
            if not any(x.strip() in ("buildTarget: Android", "buildTarget: iPhone") for x in entry):
                kept += entry
            entry = []
        entry.append(ln)
    fmt = UNITY_FORMAT[b.astc] if b.astc else UNITY_RGBA32
    add = (_unity_override("Android", b.max_size, fmt) + _unity_override("iPhone", b.max_size, fmt)).rstrip("\n")
    out = "\n".join(lines[:start + 1] + kept + add.split("\n") + lines[end:])
    if b.mipmaps is not None:
        out = out.replace("    enableMipMap: 1\n", f"    enableMipMap: {int(b.mipmaps)}\n", 1).replace(
            "    enableMipMap: 0\n", f"    enableMipMap: {int(b.mipmaps)}\n", 1)
    return out.replace("    crunchedCompression: 1\n", "    crunchedCompression: 0\n")


def godot_import(b: Budget, normal: bool) -> bytes:
    """No uid/dest: Godot fills those in on first import and keeps these params."""
    mips = b.mipmaps if b.mipmaps is not None else False
    return ("[remap]\n\nimporter=\"texture\"\ntype=\"CompressedTexture2D\"\n\n[params]\n\n"
            f"compress/mode={b.godot_mode}\ncompress/high_quality={'true' if b.godot_hq else 'false'}\n"
            "compress/lossy_quality=0.7\ncompress/hdr_compression=1\n"
            f"compress/normal_map={1 if normal else 2}\ncompress/channel_pack=0\n"
            f"mipmaps/generate={'true' if mips else 'false'}\nmipmaps/limit=-1\nroughness/mode=0\n"
            "process/fix_alpha_border=true\nprocess/premult_alpha=false\nprocess/normal_map_invert_y=false\n"
            "process/hdr_as_srgb=false\nprocess/hdr_clamp_exposure=false\n"
            f"process/size_limit={b.max_size}\ndetect_3d/compress_to=0\n").encode()


def _godot_format(b: Budget, alpha: bool, normal: bool) -> tuple[str, float]:
    if b.godot_mode == 0:
        return "Lossless (RGBA8)", 4.0
    if b.godot_hq or normal:
        return "VRAM: ASTC 4×4 (mobile)", 1.0
    return ("VRAM: ETC2 RGBA8" if alpha else "VRAM: ETC2 RGB"), (1.0 if alpha else 0.5)


def apply(files: Files, engine: str, pixel: bool = False, uid: str = "", asset: str = "") -> tuple[Files, dict[str, Any]]:
    """Rewrites/adds import settings for every texture in an engine export; returns the files and the size report."""
    by_name = {rel: i for i, (rel, _) in enumerate(files)}
    out = list(files)
    rows: list[dict[str, Any]] = []
    for rel, data in files:
        if not rel.lower().endswith(TEXTURE_EXTS):
            continue
        try:
            w, h, alpha = _image_info(data)
        except (OSError, ValueError):
            continue
        if engine == "unity":
            mi = by_name.get(f"{rel}.meta")
            meta = out[mi][1] if mi is not None else None
            text = meta.decode() if isinstance(meta, bytes) else (meta.read_text("utf-8") if meta is not None else None)
            if text is not None and "TextureImporter:" not in text:
                continue
            kind = classify(rel, text, pixel)
            if kind is None:
                continue
            b = BUDGETS[kind]
            if text is None:
                from studio_core.export.unity import guid, texture_meta

                repeat = kind in ("texture", "normal") and not rel.lower().startswith("ui/")
                text = texture_meta(PurePosixPath(rel).stem, guid(uid, asset, rel), None, (w, h), 100.0,
                                    kind == "pixel", is_normal=kind == "normal", repeat=repeat)
            mips_before = "    enableMipMap: 1\n" in text
            patched = patch_unity_meta(text, b)
            mips_after = "    enableMipMap: 1\n" in patched
            if mi is None:
                by_name[f"{rel}.meta"] = len(out)
                out.append((f"{rel}.meta", patched.encode()))
            else:
                out[mi] = (out[mi][0], patched.encode())
            iw, ih = fit((w, h), b.max_size)
            after = astc_bytes(iw, ih, b.astc) if b.astc else iw * ih * 4
            fmt = f"ASTC {b.astc}×{b.astc}" if b.astc else "RGBA32"
            before = _mips(w * h * 4, mips_before)
            after = _mips(after, mips_after)
        else:
            if f"{rel}.import" in by_name:
                continue
            kind = classify(rel, None, pixel)
            if kind is None:
                continue
            b = BUDGETS[kind]
            out.append((f"{rel}.import", godot_import(b, kind == "normal")))
            iw, ih = fit((w, h), b.max_size)
            fmt, bpp = _godot_format(b, alpha, kind == "normal")
            before = w * h * 4
            after = _mips(iw * ih * bpp, bool(b.mipmaps))
        size = len(data) if isinstance(data, bytes) else data.stat().st_size
        rows.append({"file": rel, "kind": kind, "size": [w, h], "import_size": [iw, ih], "format": fmt,
                     "file_bytes": size, "gpu_before": int(before), "gpu_after": int(after)})
    totals = {k: sum(r[k] for r in rows) for k in ("file_bytes", "gpu_before", "gpu_after")}
    report = {"engine": engine, "rows": rows, "totals": {"files": len(rows), **totals}}
    if rows:
        out = _readme(out, readme_note(report))
        out.append(("mobile_budget.json", (json.dumps(report, indent=1, ensure_ascii=False) + "\n").encode()))
    return out, report


def _mb(n: float) -> str:
    return f"{n / 1048576:.2f} MB"


def readme_note(report: dict[str, Any]) -> str:
    t = report["totals"]
    if report["engine"] == "unity":
        head = ("- Ngân sách mobile: .meta có override Android + iOS (ASTC theo loại, crunch tắt, max size); bản desktop "
                "giữ nguyên.")
    else:
        head = ("- Ngân sách mobile: mỗi ảnh có .import (VRAM compressed / lossless cho pixel art, size_limit, mipmap). "
                "Bật Project Settings > Rendering > Textures > VRAM Compression > Import ETC2 ASTC để bản Android/iOS "
                "dùng ETC2/ASTC (desktop dùng S3TC/BPTC). Pixel art: Texture Filter = Nearest.")
    lines = [head, f"  RAM GPU ước tính {_mb(t['gpu_before'])} → {_mb(t['gpu_after'])}; tệp ảnh {_mb(t['file_bytes'])}.",
             "  Tệp | Loại | Kích thước | Nén | RAM GPU trước → sau"]
    for r in report["rows"]:
        size = "×".join(map(str, r["size"]))
        if r["import_size"] != r["size"]:
            size += " → " + "×".join(map(str, r["import_size"]))
        lines.append(f"  {r['file']} | {BUDGETS[r['kind']].label} | {size} | {r['format']} | "
                     f"{_mb(r['gpu_before'])} → {_mb(r['gpu_after'])}")
    return "\n".join(lines)


def _readme(files: Files, note: str) -> Files:
    for i, (n, d) in enumerate(files):
        if n == "README.txt" and isinstance(d, bytes):
            files[i] = (n, (d.decode().rstrip("\n") + "\n" + note + "\n").encode())
            return files
    return [*files, ("README.txt", (note + "\n").encode())]


def merge_reports(reports: list[dict[str, Any]], engine: str) -> dict[str, Any]:
    rows = [r for rep in reports if rep for r in rep.get("rows", [])]
    totals = {k: sum(r[k] for r in rows) for k in ("file_bytes", "gpu_before", "gpu_after")}
    return {"engine": engine, "rows": rows, "totals": {"files": len(rows), **totals}}
