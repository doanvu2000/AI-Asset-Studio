"""Enemy variant export (op.enemy_variants): every variant is exported exactly like the source sheet (texture, clips,
controller/prefab or SpriteFrames/scene) under Variants/{asset}_{variant}/, plus an index of the set."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from studio_core.export.source import Files, Source


def variant_set(step: Any, blobs: Any) -> tuple[list[tuple[str, Path]], dict[str, Any] | None, Path | None]:
    """(slug, sheet path) per variant, the shared sheet meta and the source sheet, from enemy_variants.json (also
    when a gate passed it through under another port name)."""
    from studio_core.export.sheet import ident
    from studio_core.ops.enemy_variants import VARIANTS_FILE, VARIANTS_TYPE

    for refs in step.outputs.values():
        for ref in refs:
            if (ref.name or "") != VARIANTS_FILE:
                continue
            try:
                doc = json.loads(blobs.path(ref.hash).read_bytes())
                if doc.get("type") != VARIANTS_TYPE or not isinstance(doc.get("meta"), dict):
                    return [], None, None
                out, seen = [], set()
                for v in doc["variants"]:
                    name, path = ident(str(v["slug"]), "variant"), blobs.path(str(v["hash"]))
                    if name in seen or not path.is_file():
                        continue
                    seen.add(name)
                    out.append((name, path))
                src = blobs.path(str(doc["source"]["hash"]))
                return out, doc["meta"], src if src.is_file() else None
            except (OSError, ValueError, KeyError, TypeError):
                return [], None, None
    return [], None, None


def _variant_source(src: Source, name: str, path: Path) -> Source:
    return Source(asset=f"{src.asset}_{name}", uid=src.uid, sheet=path, meta=src.enemy_meta, clip_hint=src.clip_hint,
                  directional_rows=True, ppu=src.ppu, pixel=src.pixel, collider=src.collider)


def index_json(src: Source) -> bytes:
    doc = {"asset": src.asset, "source": f"{src.asset}.png",
           "variants": [{"variant": n, "asset": f"{src.asset}_{n}", "folder": f"Variants/{src.asset}_{n}"}
                        for n, _ in src.enemy_variants]}
    return json.dumps(doc, indent=1).encode()


def _files(src: Source, exporter: Callable[[Source], Files]) -> Files:
    files: Files = []
    for name, path in src.enemy_variants:
        sub = _variant_source(src, name, path)
        files += [(f"Variants/{sub.asset}/{rel}", data) for rel, data in exporter(sub) if rel != "README.txt"]
    files.append((f"{src.asset}_variants.json", index_json(src)))
    return files


def _note(src: Source, what: str) -> str:
    names = ", ".join(n for n, _ in src.enemy_variants)
    return (f"- Variants/{src.asset}_<biến thể>/ ({names}): mỗi biến thể xuất y như sheet gốc ({what}), cùng ô, pivot "
            f"và tên clip nên thay thẳng cho nhau; {src.asset}_variants.json liệt kê các biến thể.")


def unity_files(src: Source) -> tuple[Files, list[str]]:
    from studio_core.export import unity

    return _files(src, unity.export), [_note(src, "texture + Animations/*.anim + controller + prefab")]


def godot_files(src: Source) -> tuple[Files, list[str]]:
    from studio_core.export import godot

    return _files(src, godot.export), [_note(src, "SpriteFrames .tres + scene .tscn")]
