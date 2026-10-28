"""Localized image variants (op.localize_render): one sprite per language under Localized/, plus the text-free base,
with a README note on swapping them per locale (Unity Localization asset tables, Godot resource remaps)."""
from __future__ import annotations

import json
import re
from pathlib import Path

from studio_core.contracts import BlobStore, StepRow
from studio_core.export.source import Files, Source


def _lang(code: str) -> str:
    return re.sub(r"[^A-Za-z0-9-]", "", code) or "xx"


def localized_set(step: StepRow, blobs: BlobStore) -> tuple[list[tuple[str, Path]], str]:
    """op.localize_render's localize.json, also when a gate passed it through under another port name."""
    from studio_core.ops.localize import LOCALIZE_FILE, LOCALIZE_TYPE

    for refs in step.outputs.values():
        for ref in refs:
            if (ref.name or "") != LOCALIZE_FILE:
                continue
            try:
                doc = json.loads(blobs.path(ref.hash).read_bytes())
                if doc.get("type") != LOCALIZE_TYPE:
                    return [], ""
                out, seen = [], set()
                for e in doc["languages"]:
                    lang, path = _lang(str(e["lang"])), blobs.path(str(e["hash"]))
                    if lang.lower() in seen or not path.is_file():
                        continue
                    seen.add(lang.lower())
                    out.append((lang, path))
                base = blobs.path(str((doc.get("clean") or {}).get("hash") or ""))
                if out and base.is_file():
                    out.append(("notext", base))
                return out, str(doc.get("source_lang") or "")
            except (OSError, ValueError, KeyError, TypeError):
                return [], ""
    return [], ""


def _size(p: Path) -> tuple[int, int]:
    from PIL import Image

    with Image.open(p) as im:
        return im.size


def _langs(src: Source) -> str:
    return ", ".join(n for n, _ in src.localized if n != "notext")


def unity_files(src: Source) -> tuple[Files, list[str]]:
    from studio_core.export.unity import guid, texture_meta

    files: Files = []
    for name, p in src.localized:
        rel = f"Localized/{src.asset}_{name}.png"
        files += [(rel, p), (f"{rel}.meta", texture_meta(src.asset, guid(src.uid, src.asset, rel), None, _size(p),
                                                         src.ppu, src.pixel is True).encode())]
    note = (f"- Localized/: sprite {src.asset}_<ngôn ngữ>.png cho từng ngôn ngữ ({_langs(src)}); "
            f"{src.asset}_notext.png là bản đã xoá chữ (tự đặt TextMeshPro lên nếu muốn chữ động). Đổi theo ngôn ngữ: "
            "package Localization → Asset Table Collection, mỗi locale gán sprite tương ứng → LocalizeSpriteEvent "
            "trên Image/SpriteRenderer; hoặc tự chọn sprite theo Application.systemLanguage.")
    return files, [note]


def godot_files(src: Source) -> tuple[Files, list[str]]:
    files: Files = [(f"Localized/{src.asset}_{name}.png", p) for name, p in src.localized]
    base = f"res://Localized/{src.asset}_{src.localized_source_lang or 'en'}.png"
    remaps = ", ".join(f'"res://Localized/{src.asset}_{n}.png:{n.replace("-", "_")}"'
                       for n, _ in src.localized if n != "notext")
    note = (f"- Localized/: ảnh {src.asset}_<ngôn ngữ>.png ({_langs(src)}); {src.asset}_notext.png là bản đã xoá chữ. "
            "Đổi theo locale bằng Remaps: Project Settings → Localization → Remaps → Add ảnh gốc (dùng trong scene), "
            "rồi thêm từng bản dịch với locale tương ứng. Tương đương trong project.godot:\n"
            f"  [internationalization]\n  locale/translation_remaps={{\"{base}\": PackedStringArray({remaps})}}\n"
            "  (đổi khoá thành đường dẫn ảnh gốc thực tế; Godot đổi ảnh khi TranslationServer.set_locale()).")
    return files, [note]
