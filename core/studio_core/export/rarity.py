"""Item rarity export (op.rarity_set): one sprite per tier under Rarity/, a JSON index for Unity, a Resource .tres
with a tier -> Texture2D dictionary for Godot."""
from __future__ import annotations

import json
from pathlib import Path

from studio_core.export.source import Files, Source


def _image_size(p: Path) -> tuple[int, int]:
    from PIL import Image

    with Image.open(p) as im:
        return im.size


def _names(src: Source) -> tuple[list[tuple[str, str, Path]], list[tuple[str, Path]]]:
    tiers = [(t, f"{src.asset}_{t}.png", p) for t, p in src.rarity]
    last = src.rarity[-1][0] if src.rarity else "legendary"
    shim = [(f"{src.asset}_{last}_shimmer_{k}.png", p) for k, p in enumerate(src.rarity_shimmer)]
    return tiers, shim


def index_json(src: Source) -> bytes:
    tiers, shim = _names(src)
    doc = {"item": src.asset,
           "tiers": [{"tier": t, "rank": i, "color": src.rarity_colors.get(t, ""), "sprite": n}
                     for i, (t, n, _) in enumerate(tiers)],
           "shimmer": [n for n, _ in shim]}
    return json.dumps(doc, indent=1).encode()


def unity_files(src: Source) -> tuple[Files, list[str]]:
    from studio_core.export.unity import guid, texture_meta

    tiers, shim = _names(src)
    files: Files = []
    for name, p in [(n, p) for _, n, p in tiers] + shim:
        rel = f"Rarity/{name}"
        files += [(rel, p), (f"{rel}.meta", texture_meta(src.asset, guid(src.uid, src.asset, rel), None,
                                                         _image_size(p), src.ppu, src.pixel is True).encode())]
    files.append((f"Rarity/{src.asset}_rarity.json", index_json(src)))
    note = (f"- Rarity/: mỗi bậc một sprite {src.asset}_<bậc>.png (Sprite Mode Single); {src.asset}_rarity.json "
            "liệt kê bậc theo thứ tự thấp → cao kèm màu và tên sprite")
    if shim:
        note += f", cùng {len(shim)} frame shimmer của bậc cao nhất (phát lặp bằng Animator hoặc đổi sprite theo thời gian)"
    return files, [note + "."]


def godot_resource(src: Source) -> str:
    """Script-free Resource: metadata/tiers maps tier -> Texture2D in rank order; read with get_meta("tiers")."""
    tiers, shim = _names(src)
    ext = [f'[ext_resource type="Texture2D" path="{n}" id="{i + 1}_{t}"]' for i, (t, n, _) in enumerate(tiers)]
    ext += [f'[ext_resource type="Texture2D" path="{n}" id="s{k}"]' for k, (n, _) in enumerate(shim)]
    tex = ", ".join(f'"{t}": ExtResource("{i + 1}_{t}")' for i, (t, _, _) in enumerate(tiers))
    order = ", ".join(f'"{t}"' for t, _, _ in tiers)
    colors = ", ".join(f'"{t}": Color("{src.rarity_colors[t]}")' for t, _, _ in tiers if src.rarity_colors.get(t))
    props = [f"metadata/tiers = {{{tex}}}", f"metadata/order = PackedStringArray({order})",
             f"metadata/colors = {{{colors}}}"]
    if shim:
        props.append("metadata/shimmer = [" + ", ".join(f'ExtResource("s{k}")' for k in range(len(shim))) + "]")
    return (f'[gd_resource type="Resource" load_steps={len(ext) + 1} format=3]\n\n' + "\n".join(ext)
            + "\n\n[resource]\n" + "\n".join(props) + "\n")


def godot_files(src: Source) -> tuple[Files, list[str]]:
    tiers, shim = _names(src)
    files: Files = [(f"Rarity/{n}", p) for _, n, p in tiers] + [(f"Rarity/{n}", p) for n, p in shim]
    files.append((f"Rarity/{src.asset}_rarity.tres", godot_resource(src).encode()))
    note = (f"- Rarity/{src.asset}_rarity.tres: Resource, load() rồi get_meta(\"tiers\")[\"<bậc>\"] lấy Texture2D; "
            "get_meta(\"order\") là thứ tự thấp → cao, get_meta(\"colors\") màu từng bậc")
    if shim:
        note += ", get_meta(\"shimmer\") các frame shimmer của bậc cao nhất"
    return files, [note + "."]
