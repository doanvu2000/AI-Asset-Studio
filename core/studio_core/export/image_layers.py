"""Image layers export (op.layers_export): full-canvas layer sprites under Layers/, a Unity prefab of stacked
SpriteRenderers (sorting order = depth) and a Godot 4 scene of stacked Sprite2D nodes, back to front."""
from __future__ import annotations

import json
from pathlib import Path

from studio_core.contracts import BlobStore, StepRow
from studio_core.export.sheet import ident
from studio_core.export.source import Files, Source


def layers_set(step: StepRow, blobs: BlobStore) -> tuple[list[tuple[str, Path]], tuple[int, int]]:
    """op.layers_export's layers.json, also when a gate passed it through under another port name."""
    from studio_core.ops.image_layers import LAYERS_FILE, LAYERS_TYPE

    for refs in step.outputs.values():
        for ref in refs:
            if (ref.name or "") != LAYERS_FILE:
                continue
            try:
                doc = json.loads(blobs.path(ref.hash).read_bytes())
                if doc.get("type") != LAYERS_TYPE:  # op.layers (Spine rig) writes a layers.json too
                    continue
                out, seen = [], set()
                for la in doc["layers"]:
                    name, path = ident(str(la["name"]), "layer"), blobs.path(str(la["hash"]))
                    if name in seen or not path.is_file():
                        continue
                    seen.add(name)
                    out.append((name, path))
                w, h = (int(v) for v in doc["size"])
                return out, (w, h)
            except (OSError, ValueError, KeyError, TypeError):
                return [], (0, 0)
    return [], (0, 0)


def _files(src: Source) -> list[tuple[str, str, Path]]:
    return [(n, f"{src.asset}_{i + 1:02d}_{n}.png", p) for i, (n, p) in enumerate(src.image_layers)]


def index_json(src: Source) -> bytes:
    w, h = src.image_layers_size
    doc = {"asset": src.asset, "size": [w, h], "ppu": src.ppu, "order": "back_to_front",
           "layers": [{"name": n, "file": f, "sorting_order": i} for i, (n, f, _) in enumerate(_files(src))]}
    return json.dumps(doc, indent=1).encode()


def unity_prefab(src: Source, guids: dict[str, str]) -> str:
    from studio_core.export.damage import _go, _renderer, _transform
    from studio_core.export.unity import HEADER, file_id

    a, ppu = src.asset, src.ppu or 100.0
    w, h = src.image_layers_size
    root, rtr = file_id("layers", a, "go"), file_id("layers", a, "transform")
    layers = _files(src)
    ids = [tuple(file_id("layers", a, n, k) for k in ("go", "tr", "sr")) for n, *_ in layers]
    docs = [_go(root, [rtr], f"{a}_layers"), _transform(rtr, root, (0, 0), [i[1] for i in ids], 0)]
    for k, ((name, f, _), (go, tr, sr)) in enumerate(zip(layers, ids)):
        docs += [_go(go, [tr, sr], name), _transform(tr, go, (0, 0), [], rtr),
                 _renderer(sr, go, guids[f], (w / ppu, h / ppu), k)]
    return HEADER + "".join(docs)


def unity_files(src: Source) -> tuple[Files, list[str]]:
    from studio_core.export.unity import guid, prefab_meta, texture_meta

    files: Files = []
    guids: dict[str, str] = {}
    for _, f, p in _files(src):
        rel = f"Layers/{f}"
        guids[f] = guid(src.uid, src.asset, rel)
        files += [(rel, p), (f"{rel}.meta", texture_meta(src.asset, guids[f], None, src.image_layers_size, src.ppu,
                                                         src.pixel is True).encode())]
    prefab_rel = f"Layers/{src.asset}_layers.prefab"
    files += [(f"Layers/{src.asset}_layers.json", index_json(src)), (prefab_rel, unity_prefab(src, guids).encode()),
              (f"{prefab_rel}.meta", prefab_meta(guid(src.uid, src.asset, prefab_rel)).encode())]
    note = (f"- Layers/: {len(src.image_layers)} layer {src.asset}_<thứ tự>_<tên>.png, cùng cỡ khung gốc, pivot giữa. "
            f"Kéo {src.asset}_layers.prefab vào scene: mỗi layer là 1 SpriteRenderer chồng đúng chỗ, Sorting Order "
            "0 = sau cùng, tăng dần ra trước. Phần bị che đã được model vẽ bù nên có thể tách layer ra để anim.")
    return files, [note]


def godot_scene(src: Source) -> str:
    ext, nodes = [], [f'[node name="{src.asset}_layers" type="Node2D"]']
    for i, (name, f, _) in enumerate(_files(src)):
        ext.append(f'[ext_resource type="Texture2D" path="Layers/{f}" id="{i}_{name}"]')
        nodes += ["", f'[node name="{name}" type="Sprite2D" parent="."]', f'texture = ExtResource("{i}_{name}")',
                  f"z_index = {i}"]
        if src.pixel is True:
            nodes.append("texture_filter = 1")
    return "\n".join(["[gd_scene format=3]", "", *ext, "", *nodes]) + "\n"


def godot_files(src: Source) -> tuple[Files, list[str]]:
    files: Files = [(f"Layers/{f}", p) for _, f, p in _files(src)]
    files += [(f"Layers/{src.asset}_layers.json", index_json(src)),
              (f"{src.asset}_layers.tscn", godot_scene(src).encode())]
    note = (f"- {src.asset}_layers.tscn (Godot 4): Node2D chứa {len(src.image_layers)} Sprite2D chồng nhau, căn giữa "
            "tại (0, 0), z_index 0 = sau cùng. Ảnh trong Layers/, cùng cỡ khung gốc.")
    return files, [note]
