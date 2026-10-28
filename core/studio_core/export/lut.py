"""Time/season LUT pack export (op.fit_lut): variant images under Seasons/, LUT strips set up as Unity URP Color
Lookup textures or Godot Texture3D + Environment, .cube files for HDRP / grading tools."""
from __future__ import annotations

import io
from pathlib import Path

from studio_core.export.source import Files, Source

GODOT_LUT_IMPORT = """[remap]

importer="3d_texture"
type="CompressedTexture3D"

[params]

compress/mode=0
compress/high_quality=false
compress/lossy_quality=0.7
compress/hdr_compression=1
compress/channel_pack=0
mipmaps/generate=false
mipmaps/limit=-1
slices/horizontal={n}
slices/vertical=1
"""


def _image_size(p: Path) -> tuple[int, int]:
    from PIL import Image

    with Image.open(p) as im:
        return im.size


def unity_strip(p: Path) -> bytes:
    """Unity samples the strip with v = green from the bottom row up; the op writes it top-down (UE / Godot)."""
    from PIL import Image

    with Image.open(p) as im:
        flipped = im.convert("RGB").transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    buf = io.BytesIO()
    flipped.save(buf, "PNG")
    return buf.getvalue()


def unity_lut_meta(asset: str, tex_guid: str, size: tuple[int, int]) -> str:
    """Color Lookup needs the strip linear (sRGB off), without mipmaps or compression, clamped."""
    from studio_core.export.unity import texture_meta

    meta = texture_meta(asset, tex_guid, None, size, 100.0, False)
    for a, b in (("sRGBTexture: 1", "sRGBTexture: 0"), ("spriteMode: 1", "spriteMode: 0"),
                 ("textureType: 8", "textureType: 0"), ("alphaIsTransparency: 1", "alphaIsTransparency: 0"),
                 ("enableMipMap: 1", "enableMipMap: 0")):
        meta = meta.replace(a, b)
    return meta


def unity_files(src: Source) -> tuple[Files, list[str]]:
    from studio_core.export.unity import guid, texture_meta

    files: Files = []
    for name, img, strip, cube in src.luts:
        rel = f"Seasons/{src.asset}_{name}.png"
        files += [(rel, img), (f"{rel}.meta", texture_meta(src.asset, guid(src.uid, src.asset, rel), None,
                                                           _image_size(img), src.ppu, src.pixel is True,
                                                           repeat=src.lut_seamless).encode())]
        lut = f"Seasons/LUT/{src.asset}_{name}_lut.png"
        files += [(lut, unity_strip(strip)),
                  (f"{lut}.meta", unity_lut_meta(src.asset, guid(src.uid, src.asset, lut), _image_size(strip)).encode()),
                  (f"Seasons/LUT/{src.asset}_{name}.cube", cube)]
    names = ", ".join(n for n, *_ in src.luts)
    note = (f"- Seasons/: ảnh từng biến thể {src.asset}_<biến thể>.png ({names})"
            f"{', Wrap Mode = Repeat (liền mạch)' if src.lut_seamless else ''}. Seasons/LUT/<…>_lut.png: LUT strip "
            "1024×32 đã đặt sẵn import (Texture Type Default, sRGB tắt, không mipmap, Clamp, không nén). URP: "
            "GameObject → Volume → Global Volume → Add Override → Post-processing → Color Lookup → Texture = strip, "
            "Contribution = 1; bật Post Processing trên Camera; URP Asset → Post-processing → LUT size 32. Đổi giờ/mùa "
            "= đổi texture, hoặc 2 Volume trộn theo Weight. File .cube cho HDRP (Color Lookup nhận Texture3D .cube) "
            "hoặc phần mềm chỉnh màu.")
    return files, [note]


def godot_environment(lut_file: str) -> str:
    # Background Canvas: the pack targets 2D art, where adjustments only run on the canvas background mode.
    return (f'[gd_resource type="Environment" load_steps=2 format=3]\n\n'
            f'[ext_resource type="Texture3D" path="{lut_file}" id="1_lut"]\n\n'
            "[resource]\nbackground_mode = 3\nadjustment_enabled = true\n"
            'adjustment_color_correction = ExtResource("1_lut")\n')


def godot_files(src: Source) -> tuple[Files, list[str]]:
    files: Files = []
    for name, img, strip, _ in src.luts:
        lut = f"{src.asset}_{name}_lut.png"
        w, h = _image_size(strip)
        files += [(f"Seasons/{src.asset}_{name}.png", img), (f"Seasons/{lut}", strip),
                  (f"Seasons/{lut}.import", GODOT_LUT_IMPORT.format(n=max(1, w // max(1, h))).encode()),
                  (f"Seasons/{src.asset}_{name}_environment.tres", godot_environment(lut).encode())]
    note = (f"- Seasons/: ảnh từng biến thể {src.asset}_<biến thể>.png; <…>_lut.png là LUT strip 1024×32 kèm .import "
            "sẵn kiểu Texture3D (32 lát ngang × 1 dọc, không mipmap) — nếu Godot vẫn nhập thành Texture2D: chọn file → "
            "Import → Import As: Texture3D, Horizontal 32, Vertical 1 → Reimport. <…>_environment.tres: Environment "
            "bật Adjustments + Color Correction = LUT, Background Mode = Canvas (2D); gán vào WorldEnvironment (3D: "
            "đổi Background Mode sang Sky/Clear Color). Đổi giờ/mùa = đổi environment hoặc adjustment_color_correction.")
    return files, [note]
