"""Folder exports of a whole step: every output under its real file name, sprite sheets repacked for an engine preset."""
from __future__ import annotations

import io
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

from studio_core.contracts import BlobRef, BlobStore, StepRow
from studio_core.export import godot, rig3d, unity
from studio_core.export.sheet import ident
from studio_core.export.source import Source
from studio_core.ops import image as im
from studio_core.ops.presets import ExportPreset, annotate_sheet_meta, apply_preset
from studio_core.store.export import safe_component, safe_filename


class BundleError(ValueError):
    pass


ENGINES = {"unity": unity.export, "godot": godot.export}


def sheet_meta(step: StepRow, blobs: BlobStore) -> dict[str, Any] | None:
    """The op.sheet JSON when this step can be repacked (8 directional frames with known angles)."""
    meta = _read_meta(step, blobs)
    if meta is None:
        return None
    frames, order = meta.get("frames"), meta.get("order")
    if not isinstance(frames, list) or not isinstance(order, list) or len(frames) != 8 or len(order) != 8:
        return None
    if meta.get("clips") or any(f is None for f in frames):
        return None
    return meta


def _read_meta(step: StepRow, blobs: BlobStore, key: str = "meta") -> dict[str, Any] | None:
    refs = step.outputs.get(key) or []
    if step.kind != "op.sheet" or not refs:
        return None
    try:
        meta = json.loads(blobs.path(refs[0].hash).read_bytes())
    except (OSError, ValueError):
        return None
    return meta if isinstance(meta, dict) and meta.get("frame_w") and meta.get("frame_h") else None


_PAIR_KEYS = ("frame_w", "frame_h", "cols", "rows", "count", "padding", "width", "height", "order")


def _same_grid(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return all(a.get(k) == b.get(k) for k in _PAIR_KEYS)


def _normal_keys(key: str) -> set[str]:
    return {key.replace("sheet", "nsheet"), f"n{key}", f"{key}_normal"} - {key}


def _same_lineage(a: StepRow, b: StepRow) -> bool:
    pa, pb = a.inputs.get("pivot") or [], b.inputs.get("pivot") or []
    return bool(pa) and [r.hash for r in pa] == [r.hash for r in pb]


def _normal_sheet(step: StepRow, meta: dict[str, Any], siblings: list[StepRow], blobs: BlobStore) -> Path | None:
    if step.outputs.get("normal_sheet"):
        return blobs.path(step.outputs["normal_sheet"][0].hash)
    cands = []
    for s in siblings:
        m = _read_meta(s, blobs)
        if s.id != step.id and s.outputs.get("sheet") and m and m.get("normal") and _same_grid(m, meta):
            cands.append(s)
    named = [s for s in cands if s.key in _normal_keys(step.key)]
    lineage = [s for s in cands if _same_lineage(s, step)]
    pick = named[:1] or (lineage if len(lineage) == 1 else [])
    return blobs.path(pick[0].outputs["sheet"][0].hash) if pick else None


def _from_animate_dirs(step: StepRow, siblings: list[StepRow]) -> bool:
    by_key = {s.key: s for s in siblings}
    todo, seen = list(step.deps), set()
    while todo:
        k = todo.pop()
        if k in seen or k not in by_key:
            continue
        seen.add(k)
        if by_key[k].kind == "blender.animate_dirs":
            return True
        todo.extend(by_key[k].deps)
    return False


def _unique(name: str, seen: set[str]) -> str:
    stem, ext = os.path.splitext(name)
    n = 2
    while name.lower() in seen:
        name, n = f"{stem}-{n}{ext}", n + 1
    seen.add(name.lower())
    return name


def _atlas_pages(atlas: Path) -> list[str]:
    """Spine atlas page names: the first line of the file and of every block after a blank line."""
    try:
        lines = atlas.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except OSError:
        return []
    pages, fresh = [], True
    for line in lines:
        if not line.strip():
            fresh = True
        elif fresh:
            pages.append(line.strip())
            fresh = False
    return pages


def _page_names(refs: list[BlobRef], atlas: Path) -> list[str]:
    pages = {p.lower(): p for p in _atlas_pages(atlas)}
    names, seen = [], set()
    for r in refs:
        safe = safe_filename(None, r)
        want = pages.get(safe.lower()) or (next(iter(pages.values())) if len(pages) == 1 == len(refs) else None)
        # safe_filename lowercases the extension; the atlas must find "Hero.PNG" byte for byte.
        ok = want is not None and safe_filename(want, r).lower() == want.lower()
        names.append(_unique(want if ok and want else safe, seen))
    return names


def _is_glb(ref: BlobRef) -> bool:
    return ref.mime == "model/gltf-binary" or (ref.name or "").lower().endswith(".glb")


TILE_REPORT = "tile_check.json"
_TILE_ROLES = (("images", "color"), ("sizes", "color"), ("nmap", "normal"), ("pixel", "pixel"), ("atlas", "atlas"))


def tile_report(step: StepRow, blobs: BlobStore) -> dict[str, Any] | None:
    """op.tile_check's report, also when a gate passed it through: marks the step's images as tileable textures."""
    for ref in step.outputs.get("report") or []:
        if (ref.name or "") != TILE_REPORT:
            continue
        try:
            rep = json.loads(blobs.path(ref.hash).read_bytes())
        except (OSError, ValueError):
            return None
        return rep if isinstance(rep, dict) and rep.get("tileable") else None
    return None


def _tile_textures(step: StepRow, blobs: BlobStore) -> list[tuple[str, Path, str]]:
    out: list[tuple[str, Path, str]] = []
    names: set[str] = set()
    hashes: set[str] = set()
    for port, role in _TILE_ROLES:
        for r in step.outputs.get(port) or []:
            if r.hash in hashes or not r.mime.startswith("image/"):
                continue
            hashes.add(r.hash)
            out.append((_unique(safe_filename(None, r), names), blobs.path(r.hash), role))
    return out


def engine_source(step: StepRow, siblings: list[StepRow], blobs: BlobStore, asset: str, ppu: float = 100.0,
                  pixel: bool | None = None) -> Source:
    src = Source(asset=asset, uid=step.id, clip_hint=step.key, ppu=ppu, pixel=pixel,
                 directional_rows=_from_animate_dirs(step, siblings))
    meta = _read_meta(step, blobs)
    if meta is not None and step.outputs.get("sheet") and not meta.get("normal"):
        src.sheet, src.meta = blobs.path(step.outputs["sheet"][0].hash), meta
        src.normal = _normal_sheet(step, meta, siblings, blobs)
    sk, atlas = step.outputs.get("skeleton"), step.outputs.get("atlas")
    if sk and atlas:
        src.spine_json = (safe_filename(None, sk[0]), blobs.path(sk[0].hash))
        src.spine_atlas = (safe_filename(None, atlas[0]), blobs.path(atlas[0].hash))
        pages = step.outputs.get("texture") or []
        src.spine_pages = list(zip(_page_names(pages, src.spine_atlas[1]), (blobs.path(r.hash) for r in pages)))
    if tile_report(step, blobs) is not None:
        src.textures = _tile_textures(step, blobs)
    hashes: set[str] = set()
    names: set[str] = set()
    for refs in step.outputs.values():
        for r in refs:
            if _is_glb(r) and r.hash not in hashes:
                hashes.add(r.hash)
                src.glb.append((_unique(safe_filename(None, r), names), blobs.path(r.hash)))
    src.fbx, src.rig = rig3d.collect(step.outputs, blobs.path, lambda n: _unique(n, names),
                                     lambda r: safe_filename(None, r))
    src.audio = _audio(step, blobs)
    src.ui = ui_elements(step, blobs)
    src.pbr = pbr_materials(step, blobs)
    src.rarity, src.rarity_colors, src.rarity_shimmer = rarity_set(step, blobs)
    from studio_core.export.damage import damage_set

    src.damage_states, src.damage_pieces, src.damage_canvas = damage_set(step, blobs)
    src.soundscape = soundscape_doc(step, blobs)
    from studio_core.export.npc import npc_set

    src.npc, src.npc_portraits = npc_set(step, blobs)
    src.luts, src.lut_seamless = lut_set(step, blobs)
    from studio_core.export.parallax import parallax_set

    src.parallax, src.parallax_size = parallax_set(step, blobs)
    from studio_core.export.localize import localized_set

    src.localized, src.localized_source_lang = localized_set(step, blobs)
    from studio_core.export.autotile import autotile_set

    src.autotile, src.autotile_atlas = autotile_set(step, blobs)
    from studio_core.export.bitmap_font import font_set

    src.font, src.font_atlas = font_set(step, blobs)
    from studio_core.export.paperdoll import paperdoll_set

    src.paperdoll, src.paperdoll_layers, body = paperdoll_set(step, blobs)
    if body is not None and src.sheet is None:
        src.sheet, src.meta = body
        src.directional_rows = True
    return src


def lut_set(step: StepRow, blobs: BlobStore) -> tuple[list[tuple[str, Path, Path, Path]], bool]:
    """op.fit_lut's lut.json, also when a gate passed it through under another port name."""
    from studio_core.ops.lut import LUT_FILE, LUT_TYPE

    for refs in step.outputs.values():
        for ref in refs:
            if (ref.name or "") != LUT_FILE:
                continue
            try:
                doc = json.loads(blobs.path(ref.hash).read_bytes())
                if doc.get("type") != LUT_TYPE:
                    return [], False
                out, seen = [], set()
                for v in doc["variants"]:
                    name = ident(str(v["name"]), "variant")
                    paths = (blobs.path(str(v["hash"])), blobs.path(str(v["strip"]["hash"])),
                             blobs.path(str(v["cube"]["hash"])))
                    if name in seen or not all(p.is_file() for p in paths):
                        continue
                    seen.add(name)
                    out.append((name, *paths))
                return out, bool(doc.get("seamless"))
            except (OSError, ValueError, KeyError, TypeError):
                return [], False
    return [], False


def pbr_materials(step: StepRow, blobs: BlobStore) -> list[tuple[str, dict[str, Path]]]:
    """op.pbr's pbr_material.json, also when a gate passed it through under another port name."""
    from studio_core.ops.pbr import MATERIAL_NAME, MATERIAL_TYPE, ROLES

    for refs in step.outputs.values():
        for ref in refs:
            if (ref.name or "") != MATERIAL_NAME:
                continue
            try:
                doc = json.loads(blobs.path(ref.hash).read_bytes())
                out, seen = [], set()
                for m in doc["materials"] if doc.get("type") == MATERIAL_TYPE else []:
                    name = ident(str(m["name"]), "material")
                    maps = {r: blobs.path(str(m["maps"][r]["hash"])) for r in ROLES if r in m["maps"]}
                    if name in seen or "albedo" not in maps or not all(p.is_file() for p in maps.values()):
                        continue
                    seen.add(name)
                    out.append((name, maps))
                return out
            except (OSError, ValueError, KeyError, TypeError):
                return []
    return []


def rarity_set(step: StepRow, blobs: BlobStore) -> tuple[list[tuple[str, Path]], dict[str, str], list[Path]]:
    """op.rarity_set's rarity.json, also when a gate passed it through under another port name."""
    from studio_core.ops.rarity import RARITY_FILE, RARITY_TYPE

    for refs in step.outputs.values():
        for ref in refs:
            if (ref.name or "") != RARITY_FILE:
                continue
            try:
                doc = json.loads(blobs.path(ref.hash).read_bytes())
                if doc.get("type") != RARITY_TYPE:
                    return [], {}, []
                tiers, colors, seen = [], {}, set()
                for t in doc["tiers"]:
                    name = ident(str(t["tier"]), "tier")
                    path = blobs.path(str(t["hash"]))
                    if name in seen or not path.is_file():
                        continue
                    seen.add(name)
                    tiers.append((name, path))
                    colors[name] = str(t.get("color") or "")
                shim = [blobs.path(str(f["hash"])) for f in (doc["tiers"][-1].get("shimmer") or [])]
                return tiers, colors, [p for p in shim if p.is_file()]
            except (OSError, ValueError, KeyError, TypeError, IndexError):
                return [], {}, []
    return [], {}, []


def soundscape_doc(step: StepRow, blobs: BlobStore) -> dict[str, Any] | None:
    from studio_core.ops.soundscape import SOUNDSCAPE_FILE, SOUNDSCAPE_TYPE

    for refs in step.outputs.values():
        for ref in refs:
            if (ref.name or "") != SOUNDSCAPE_FILE:
                continue
            try:
                doc = json.loads(blobs.path(ref.hash).read_bytes())
            except (OSError, ValueError):
                return None
            return doc if isinstance(doc, dict) and doc.get("type") == SOUNDSCAPE_TYPE else None
    return None


def ui_elements(step: StepRow, blobs: BlobStore) -> list[tuple[str, Path, tuple[int, int, int, int], str]]:
    """op.nine_slice's ui_slices.json, also when a gate passed it through: each element with its 9-slice border."""
    from studio_core.ops.ui import SLICES_FILE, SLICES_TYPE

    for ref in step.outputs.get("slices") or []:
        if (ref.name or "") != SLICES_FILE:
            continue
        try:
            doc = json.loads(blobs.path(ref.hash).read_bytes())
            out, seen = [], set()
            for e in doc["elements"] if doc.get("type") == SLICES_TYPE else []:
                name = ident(str(e["name"]), "element")
                if name in seen or not blobs.path(str(e["hash"])).is_file():
                    continue
                seen.add(name)
                b = [int(v) for v in e["border"]]
                out.append((name, blobs.path(str(e["hash"])), (b[0], b[1], b[2], b[3]), str(e.get("state") or "")))
            return out
        except (OSError, ValueError, KeyError, TypeError, IndexError):
            return []
    return []


AUDIO_EXT = (".ogg", ".wav", ".mp3", ".flac")


def _audio(step: StepRow, blobs: BlobStore) -> list[tuple[str, Path]]:
    # op.audio_post writes each clip as OGG and WAV; an engine needs one, and OGG streams smaller.
    best: dict[str, BlobRef] = {}
    for refs in step.outputs.values():
        for r in refs:
            name = safe_filename(None, r)
            ext = Path(name).suffix.lower()
            if not (r.mime.startswith("audio/") or ext in AUDIO_EXT):
                continue
            stem = Path(name).stem
            cur = best.get(stem)
            if cur is None or (ext == ".ogg" and Path(safe_filename(None, cur)).suffix.lower() != ".ogg"):
                best[stem] = r
    return [(safe_filename(None, r), blobs.path(r.hash)) for r in best.values()]


def engine_ready(step: StepRow, blobs: BlobStore) -> bool:
    src = engine_source(step, [], blobs, "x")
    return (src.sheet is not None or src.spine_json is not None or bool(src.glb) or bool(src.textures)
            or bool(src.audio) or bool(src.ui) or bool(src.pbr) or bool(src.fbx)
            or bool(src.rarity) or bool(src.luts) or bool(src.damage_states)
            or bool(src.parallax) or bool(src.localized) or bool(src.npc) or bool(src.autotile)
            or bool(src.font))


def export_engine(step: StepRow, siblings: list[StepRow], blobs: BlobStore, root: Path, name: str | None,
                  engine: str, ppu: float = 100.0, pixel: bool | None = None, collider: Any = None,
                  shaders: list[Any] | None = None, normal2d: Any = None) -> dict[str, Any]:
    exporter = ENGINES.get(engine)
    if exporter is None:
        raise BundleError(f"unknown engine '{engine}' (use {', '.join(ENGINES)})")
    base = name or str(step.params.get("asset") or "") or str(step.params.get("name") or "") or step.key
    folder = safe_component(base, step.key)
    src = engine_source(step, siblings, blobs, ident(folder, "asset"), ppu, pixel)
    src.collider = collider
    from studio_core.export.normal2d import apply as apply_normal2d, append_readme

    normal_note = apply_normal2d(src, normal2d)
    files = exporter(src)
    if normal_note:
        files = append_readme(files, normal_note)
    if not files:
        raise BundleError("nothing to export for a game engine (needs a sprite sheet, Spine export, GLB, tileable "
                          "texture, PBR material, sound, UI kit, rarity set, LUT pack or parallax layers)")
    if shaders:
        from studio_core.export.shaders import ShaderError, apply

        try:
            files = apply(files, engine, src, shaders)
        except ShaderError as e:
            raise BundleError(str(e)) from None
    dst = root / "bundles" / f"{step.id[:12]}-{engine}" / folder
    dst.parent.mkdir(parents=True, exist_ok=True)
    written = _write(dst, files)
    return {"dir": str(dst.resolve()), "name": folder, "preset": None, "engine": engine,
            "files": [str(p.resolve()) for p in written]}


def _png(img: Any) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", compress_level=6)
    return buf.getvalue()


def _sprite_files(meta: dict[str, Any], blobs: BlobStore, preset: ExportPreset, asset: str
                  ) -> list[tuple[str, bytes | Path]]:
    angles = [int(a) for a in meta["order"]]
    try:
        slots, named = apply_preset(preset, angles, asset)
    except ValueError as e:
        raise BundleError(str(e)) from None
    hashes = [meta["frames"][i] for i in slots]
    imgs = [im.open_image(blobs.path(h)) for h in hashes]
    pivot = meta.get("pivot")
    sheet, out = im.pack_sheet(imgs, preset.cols_for(len(imgs)), (int(meta["frame_w"]), int(meta["frame_h"])),
                               preset.padding, (int(pivot[0]), int(pivot[1])) if pivot else None,
                               [angles[i] for i in slots])
    out["frames"] = hashes
    annotate_sheet_meta(out, named, preset)
    files: list[tuple[str, bytes | Path]] = [
        (f"{asset}_sheet.png", _png(sheet)),
        (f"{asset}_sheet.json", json.dumps(out, indent=2, ensure_ascii=False).encode()),
    ]
    for rec, h in zip(named, hashes):
        name = safe_filename(f"{rec['name']}.png", BlobRef(hash=h, mime="image/png", size=0))
        files.append((f"frames/{name}", blobs.path(h)))
    return files


def _raw_files(step: StepRow, blobs: BlobStore) -> list[tuple[str, bytes | Path]]:
    # Spine's .atlas names its page PNG, so outputs keep the blob names the executor gave them.
    files: list[tuple[str, bytes | Path]] = []
    seen: set[str] = set()
    hashes: set[str] = set()
    for refs in step.outputs.values():
        for ref in refs:
            if ref.hash in hashes:
                continue
            hashes.add(ref.hash)
            name = safe_filename(None, ref)
            stem, ext = os.path.splitext(name)
            n = 2
            while name.lower() in seen:
                name, n = f"{stem}_{n}{ext}", n + 1
            seen.add(name.lower())
            files.append((name, blobs.path(ref.hash)))
    return files


def _write(dst: Path, files: list[tuple[str, bytes | Path]]) -> list[Path]:
    tmp = dst.with_name(f".{dst.name}.{uuid.uuid4().hex[:8]}.tmp")
    written: list[Path] = []
    try:
        for rel, src in files:
            p = tmp / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(src, Path):
                shutil.copyfile(src, p)
            else:
                p.write_bytes(src)
            written.append(dst / rel)
        tmp.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            # Windows cannot replace a non-empty folder; move the old one aside first.
            old = dst.with_name(f".{dst.name}.{uuid.uuid4().hex[:8]}.old")
            os.replace(dst, old)
            shutil.rmtree(old, ignore_errors=True)
        os.replace(tmp, dst)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
    return written


def export_bundle(step: StepRow, blobs: BlobStore, root: Path, name: str | None,
                  preset: ExportPreset | None) -> dict[str, Any]:
    if not step.outputs:
        raise BundleError("step has no outputs")
    meta = sheet_meta(step, blobs)
    if preset is not None and meta is None:
        raise BundleError("export presets only apply to 8-direction sprite sheets")
    asset_param = str(step.params.get("asset") or "")
    folder = safe_component(name or asset_param or step.key, step.key)
    if meta is not None and preset is not None:
        asset = safe_component(name or asset_param, "sprite")
        files = _sprite_files(meta, blobs, preset, asset)
    else:
        files = _raw_files(step, blobs)
    dst = root / "bundles" / f"{step.id[:12]}-{preset.id if preset else 'raw'}" / folder
    dst.parent.mkdir(parents=True, exist_ok=True)
    written = _write(dst, files)
    return {"dir": str(dst.resolve()), "name": folder, "preset": preset.id if preset else None,
            "files": [str(p.resolve()) for p in written]}
