"""Restyle a game project folder in place (recipe restyle-folder): scan PNGs with their Unity .meta / Godot .import
(sprite rects, 9-slice border, pivot), and write restyled PNGs back over the originals with a zip backup, a
provenance manifest and undo. Importer files are never written, so GUIDs/UIDs and every reference stay intact."""
from __future__ import annotations

import io
import json
import os
import re
import time
import uuid
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

import yaml
from blake3 import blake3
from PIL import Image

SCAN_TYPE = "asset-studio/restyle-scan"
REPORT_TYPE = "asset-studio/restyle"
APPLY_TYPE = "asset-studio/restyle-apply"
MAX_FILES = 2000
MAX_SIDE = 4096
MIN_SIDE = 4
WHOLE_SHEET = 1024
BLOCK_CELLS = 24
SKIP_DIRS = {"library", "temp", "logs", "obj", "build", "builds", "packages", "usersettings", ".git", ".godot",
             ".import", ".vs", ".idea", "node_modules"}
# Unity TextureImporterType values that are not colour art: normal map, cookie, lightmap, single channel,
# directional lightmap, shadowmask. Restyling them would corrupt data, not art.
SKIP_TEXTURE_TYPES = {1, 4, 6, 10, 11, 12}
SKIP_SUFFIXES = ("_n", "_normal", "_nrm", "_mask", "_height", "_rough", "_roughness", "_metal", "_metallic", "_ao",
                 "_emission", "_lightmap")
KINDS = ("sprite", "sheet", "ui", "tile", "texture")


class RestyleError(Exception):
    pass


@dataclass
class Rect:
    name: str
    x: int
    y: int
    w: int
    h: int
    pivot: tuple[float, float] | None = None
    border: tuple[int, int, int, int] = (0, 0, 0, 0)


@dataclass
class Item:
    rel: str
    width: int
    height: int
    kind: str = "sprite"
    importer: str = ""
    importer_hash: str = ""
    png_hash: str = ""
    guid: str = ""
    alpha: bool = True
    pivot: tuple[float, float] | None = None
    border: tuple[int, int, int, int] = (0, 0, 0, 0)
    ppu: float | None = None
    rects: list[Rect] = field(default_factory=list)
    texture_type: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def backup_dir(data_dir: Path) -> Path:
    return data_dir / "restyle_backups"


def file_hash(p: Path) -> str:
    h = blake3()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_link(p: Path) -> bool:
    if p.is_symlink():
        return True
    is_junction = getattr(p, "is_junction", None)
    return bool(is_junction and is_junction())


def safe_target(root: Path, rel: str) -> Path:
    """`rel` resolved inside `root`, refusing absolute paths, `..`, links and anything but an existing .png."""
    pp = PurePosixPath(rel.replace("\\", "/"))
    if pp.is_absolute() or not pp.parts or any(part in ("", ".", "..") for part in pp.parts) or ":" in rel:
        raise RestyleError(f"path escapes the folder: {rel}")
    if pp.suffix.lower() != ".png":
        raise RestyleError(f"only .png files are written: {rel}")
    root = root.resolve()
    p = root.joinpath(*pp.parts)
    cur = root
    for part in pp.parts:
        cur = cur / part
        if _is_link(cur):
            raise RestyleError(f"link in path: {rel}")
    real = p.resolve()
    if not real.is_relative_to(root) or not real.is_file():
        raise RestyleError(f"not a file inside the folder: {rel}")
    return real


def project_root(folder: Path) -> tuple[str, Path]:
    """('unity' | 'godot' | 'none', engine project root) for a folder anywhere inside a project."""
    folder = folder.resolve()
    for d in (folder, *folder.parents):
        if (d / "project.godot").is_file():
            return "godot", d
        if (d / "ProjectSettings").is_dir() and (d / "Assets").is_dir():
            return "unity", d
    return "none", folder


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _border(b: Any) -> tuple[int, int, int, int]:
    """Unity border {x: left, y: bottom, z: right, w: top} -> (left, top, right, bottom) in pixels."""
    if not isinstance(b, dict):
        return 0, 0, 0, 0
    return (round(_f(b.get("x"))), round(_f(b.get("w"))), round(_f(b.get("z"))), round(_f(b.get("y"))))


def _pivot(p: Any) -> tuple[float, float] | None:
    return (round(_f(p.get("x"), 0.5), 4), round(_f(p.get("y"), 0.5), 4)) if isinstance(p, dict) else None


def parse_unity_meta(text: str, height: int) -> dict[str, Any]:
    """guid, texture type, sprite mode, pivot, border, ppu and sprite rects (converted to top-left origin)."""
    try:
        doc = yaml.load(text, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader)) or {}
    except yaml.YAMLError as e:
        raise RestyleError(f"bad .meta: {e}") from e
    ti = doc.get("TextureImporter") or {}
    rects = []
    for s in ((ti.get("spriteSheet") or {}).get("sprites") or []):
        r = s.get("rect") or {}
        x, y, w, h = (round(_f(r.get(k))) for k in ("x", "y", "width", "height"))
        if w <= 0 or h <= 0:
            continue
        rects.append(Rect(str(s.get("name") or f"sprite_{len(rects)}"), x, height - (y + h), w, h,
                          _pivot(s.get("pivot")), _border(s.get("border"))))
    tt = ti.get("textureType")
    return {"guid": str(doc.get("guid") or ""), "texture_type": int(tt) if isinstance(tt, int) else None,
            "sprite_mode": int(ti.get("spriteMode") or 0), "pivot": _pivot(ti.get("spritePivot")),
            "border": _border(ti.get("spriteBorder")), "ppu": _f(ti.get("spritePixelsToUnits"), 100.0) or None,
            "rects": rects}


_SECTION = re.compile(r"^\[(\w+)(.*)\]\s*$")
_KV = re.compile(r'^([\w/]+)\s*=\s*(.*)$')
_ATTR = re.compile(r'(\w+)="([^"]*)"')


def parse_godot_import(text: str) -> dict[str, Any]:
    sections: dict[str, dict[str, str]] = {}
    cur: dict[str, str] = {}
    for line in text.splitlines():
        m = _SECTION.match(line.strip())
        if m:
            cur = sections.setdefault(m.group(1), {})
            continue
        kv = _KV.match(line.strip())
        if kv:
            cur[kv.group(1)] = kv.group(2).strip().strip('"')
    remap = sections.get("remap", {})
    return {"importer": remap.get("importer", ""), "uid": remap.get("uid", ""), "type": remap.get("type", ""),
            "normal_map": sections.get("params", {}).get("compress/normal_map", "0") == "1"}


def _blocks(text: str) -> list[tuple[str, dict[str, str], list[str]]]:
    out: list[tuple[str, dict[str, str], list[str]]] = []
    for line in text.splitlines():
        m = _SECTION.match(line.strip())
        if m:
            out.append((m.group(1), dict(_ATTR.findall(m.group(2))), []))
        elif out:
            out[-1][2].append(line.strip())
    return out


def _nums(s: str) -> list[float]:
    inner = s[s.find("(") + 1:s.rfind(")")] if "(" in s else s
    return [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", inner)]


def godot_regions(project: Path, files: Iterable[Path]) -> dict[str, list[Rect]]:
    """res:// texture path -> AtlasTexture regions and NinePatchRect margins found in .tres/.tscn files."""
    found: dict[str, list[Rect]] = {}
    for f in files:
        try:
            if f.stat().st_size > 4 << 20:
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        ext: dict[str, str] = {}
        for tag, attrs, body in _blocks(text):
            if tag == "ext_resource" and attrs.get("path", "").lower().endswith(".png"):
                ext[attrs.get("id", "")] = attrs["path"]
                continue
            kv = {m.group(1): m.group(2) for m in (_KV.match(ln) for ln in body) if m}
            ref = re.search(r'ExtResource\(\s*"?([^")]+)"?\s*\)', kv.get("atlas", "") or kv.get("texture", ""))
            path = ext.get(ref.group(1)) if ref else None
            if not path:
                continue
            if "region" in kv:
                n = _nums(kv["region"])
                if len(n) == 4 and n[2] > 0 and n[3] > 0:
                    rects = found.setdefault(path, [])
                    rects.append(Rect(f"region_{len(rects)}", *(round(v) for v in n)))
            if any(k.startswith("patch_margin_") for k in kv):
                border = tuple(round(_f(kv.get(f"patch_margin_{s}"))) for s in ("left", "top", "right", "bottom"))
                found.setdefault(f"{path}#nine", []).append(Rect("nine", 0, 0, 0, 0, None, border))  # type: ignore[arg-type]
    return found


def _skip_name(stem: str) -> bool:
    s = stem.lower()
    return s.endswith(SKIP_SUFFIXES) or s.startswith("lightmap")


def _png_info(p: Path) -> tuple[int, int, bool]:
    with Image.open(p) as im:
        w, h = im.size
        alpha = im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)
        if alpha:
            a = im.convert("RGBA").getchannel("A")
            alpha = a.getextrema()[0] < 255
    return w, h, alpha


def _classify(it: Item, name: str) -> str:
    if len(it.rects) >= 2:
        return "sheet"
    if any(it.border) or (it.rects and any(it.rects[0].border)):
        return "ui"
    low = name.lower()
    if not it.alpha and ("tile" in low or "ground" in low or "floor" in low or "wall" in low):
        return "tile"
    return "sprite" if it.alpha else "texture"


def _walk(root: Path) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        d = Path(dirpath)
        dirnames[:] = sorted(n for n in dirnames if n.lower() not in SKIP_DIRS and not n.startswith(".")
                             and not n.endswith("~") and not _is_link(d / n))
        for n in sorted(filenames):
            yield d / n


def _match(rel: str, patterns: list[str]) -> bool:
    pp = PurePosixPath(rel.lower())
    return any(pp.match(p.lower()) or rel.lower().startswith(p.lower().rstrip("*").rstrip("/") + "/")
               for p in patterns)


def _patterns(raw: Any) -> list[str]:
    return [p.strip() for p in re.split(r"[,\n;]", str(raw or "")) if p.strip()]


def scan_folder(folder: str | Path, include: Any = "", exclude: Any = "", max_files: int = MAX_FILES) -> dict[str, Any]:
    root = Path(str(folder or "")).expanduser()
    if not str(folder or "").strip() or not root.is_absolute():
        raise RestyleError("folder must be an absolute path")
    if not root.is_dir():
        raise RestyleError(f"folder not found: {root}")
    if _is_link(root):
        raise RestyleError("folder is a link")
    root = root.resolve()
    engine, proj = project_root(root)
    inc, exc = _patterns(include), _patterns(exclude)
    pngs, scenes = [], []
    for p in _walk(root):
        low = p.name.lower()
        if low.endswith(".png") and not _is_link(p):
            pngs.append(p)
        elif engine == "godot" and low.endswith((".tres", ".tscn")):
            scenes.append(p)
    if engine == "godot" and proj != root:
        seen = set(scenes)
        scenes += [p for p in _walk(proj) if p.name.lower().endswith((".tres", ".tscn")) and p not in seen]
    regions = godot_regions(proj, scenes) if engine == "godot" else {}
    items: list[Item] = []
    skipped: list[dict[str, str]] = []
    for p in pngs:
        rel = p.relative_to(root).as_posix()
        if (inc and not _match(rel, inc)) or (exc and _match(rel, exc)):
            continue
        if len(items) >= max_files:
            skipped.append({"rel": rel, "why": f"over {max_files} files"})
            continue
        if _skip_name(p.stem):
            skipped.append({"rel": rel, "why": "data map (normal/mask/lightmap)"})
            continue
        try:
            w, h, alpha = _png_info(p)
        except (OSError, ValueError) as e:
            skipped.append({"rel": rel, "why": f"unreadable: {e}"})
            continue
        if min(w, h) < MIN_SIDE or max(w, h) > MAX_SIDE:
            skipped.append({"rel": rel, "why": f"size {w}x{h} out of range"})
            continue
        it = Item(rel=rel, width=w, height=h, alpha=alpha, png_hash=file_hash(p))
        meta, imp = p.with_name(p.name + ".meta"), p.with_name(p.name + ".import")
        try:
            if meta.is_file():
                info = parse_unity_meta(meta.read_text(encoding="utf-8", errors="replace"), h)
                it.importer, it.importer_hash, it.guid = meta.name, file_hash(meta), info["guid"]
                it.texture_type, it.pivot, it.border, it.ppu = info["texture_type"], info["pivot"], info["border"], \
                    info["ppu"]
                if info["sprite_mode"] == 2:
                    it.rects = info["rects"]
                if it.texture_type in SKIP_TEXTURE_TYPES:
                    skipped.append({"rel": rel, "why": f"texture type {it.texture_type} is not colour art"})
                    continue
            elif imp.is_file():
                info = parse_godot_import(imp.read_text(encoding="utf-8", errors="replace"))
                it.importer, it.importer_hash, it.guid = imp.name, file_hash(imp), info["uid"]
                if info["normal_map"]:
                    skipped.append({"rel": rel, "why": "normal map"})
                    continue
                res = "res://" + p.resolve().relative_to(proj).as_posix()
                it.rects = list(regions.get(res, []))
                nine = regions.get(f"{res}#nine")
                if nine:
                    it.border = nine[0].border
        except RestyleError as e:
            skipped.append({"rel": rel, "why": str(e)})
            continue
        it.rects = [r for r in it.rects if r.x >= 0 and r.y >= 0 and r.x + r.w <= w and r.y + r.h <= h]
        it.kind = _classify(it, p.stem)
        items.append(it)
    counts = {k: sum(1 for i in items if i.kind == k) for k in KINDS}
    return {"type": SCAN_TYPE, "version": 1, "root": str(root), "engine": engine, "project": str(proj),
            "items": [i.as_dict() for i in items], "skipped": skipped, "counts": counts,
            "units": sum(len(units_for(i.as_dict())) for i in items)}


def _rect_of(r: Any) -> tuple[int, int, int, int]:
    if isinstance(r, Rect):
        return r.x, r.y, r.w, r.h
    return int(r["x"]), int(r["y"]), int(r["w"]), int(r["h"])


def units_for(item: dict[str, Any]) -> list[list[int]]:
    """Edit boxes [x0, y0, x1, y1] for one item: the whole image, or for big sheets blocks of whole sprite rows so
    the editor sees several frames together and never cuts a frame."""
    w, h = int(item["width"]), int(item["height"])
    rects = [_rect_of(r) for r in item.get("rects") or []]
    if item.get("kind") != "sheet" or not rects or (w <= WHOLE_SHEET and h <= WHOLE_SHEET):
        return [[0, 0, w, h]]
    rows: list[list[tuple[int, int, int, int]]] = []
    for r in sorted(rects, key=lambda r: (r[1], r[0])):
        if rows and abs(rows[-1][0][1] - r[1]) <= max(2, r[3] // 4):
            rows[-1].append(r)
        else:
            rows.append([r])
    boxes: list[list[int]] = []
    cur: list[tuple[int, int, int, int]] = []

    def flush() -> None:
        if cur:
            boxes.append([min(r[0] for r in cur), min(r[1] for r in cur), max(r[0] + r[2] for r in cur),
                          max(r[1] + r[3] for r in cur)])
            cur.clear()

    for row in rows:
        chunks: list[list[tuple[int, int, int, int]]] = [[]]
        for r in sorted(row):
            c = chunks[-1]
            if c and (r[0] + r[2] - c[0][0] > WHOLE_SHEET or len(c) >= BLOCK_CELLS):
                chunks.append([])
            chunks[-1].append(r)
        if len(chunks) > 1:
            flush()
            for c in chunks:
                cur.extend(c)
                flush()
            continue
        trial = cur + row
        span_h = max(r[1] + r[3] for r in trial) - min(r[1] for r in trial)
        span_w = max(r[0] + r[2] for r in trial) - min(r[0] for r in trial)
        if cur and (span_h > WHOLE_SHEET or span_w > WHOLE_SHEET or len(trial) > BLOCK_CELLS):
            flush()
        cur.extend(row)
    flush()
    return boxes


def sample_items(items: list[dict[str, Any]], n: int) -> list[int]:
    """Indices of `n` items spread over the kinds (smallest first in each), for the trial run."""
    if n <= 0 or n >= len(items):
        return list(range(len(items)))
    by_kind: dict[str, list[int]] = {}
    for i, it in enumerate(items):
        by_kind.setdefault(it["kind"], []).append(i)
    for idx in by_kind.values():
        idx.sort(key=lambda i: (len(units_for(items[i])), items[i]["width"] * items[i]["height"], items[i]["rel"]))
    picked: list[int] = []
    while len(picked) < n:
        for k in KINDS:
            if by_kind.get(k) and len(picked) < n:
                picked.append(by_kind[k].pop(0))
    return sorted(picked)


def mirror_root(scan: dict[str, Any]) -> Path:
    """Sibling of the engine project (or of the folder): same relative layout, outside anything the engine scans."""
    proj, root = Path(scan["project"]), Path(scan["root"])
    base = proj if scan.get("engine") in ("unity", "godot") else root
    return base.parent / f"{base.name}_restyled" / root.relative_to(base)


def _atomic_write(dst: Path, data: bytes) -> None:
    # Hidden + trailing "~": neither Unity nor Godot imports the temp file while it exists.
    tmp = dst.with_name(f".{dst.name}.{uuid.uuid4().hex[:8]}.restyle~")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, dst)
    finally:
        if tmp.exists():
            tmp.unlink()


def plan_writes(report: dict[str, Any], files: dict[str, bytes]) -> list[dict[str, Any]]:
    """What applying would do for every restyled file: write, or skip with the reason (changed since the scan,
    importer changed, size mismatch, unsafe path)."""
    root = Path(report["root"])
    out = []
    for it in report.get("items") or []:
        rel = it["rel"]
        rec: dict[str, Any] = {"rel": rel, "action": "write", "old": it.get("png_hash"), "new": it.get("new_hash"),
                               "importer": it.get("importer") or ""}
        try:
            dst = safe_target(root, rel)
            if rel not in files:
                raise RestyleError("no restyled image")
            if file_hash(dst) != it.get("png_hash"):
                raise RestyleError("file changed since the scan")
            imp = dst.with_name(dst.name + (".meta" if it.get("importer", "").endswith(".meta") else ".import"))
            if it.get("importer") and (not imp.is_file() or file_hash(imp) != it.get("importer_hash")):
                raise RestyleError("importer file changed since the scan")
            with Image.open(io.BytesIO(files[rel])) as im:
                if im.size != (it["width"], it["height"]):
                    raise RestyleError(f"size {im.size} != {(it['width'], it['height'])}")
        except (RestyleError, OSError) as e:
            rec.update(action="skip", why=str(e))
        out.append(rec)
    return out


def _importer_hashes(root: Path, recs: list[dict[str, Any]]) -> dict[str, str]:
    out = {}
    for r in recs:
        p = root / r["rel"]
        for suffix in (".meta", ".import"):
            imp = p.with_name(p.name + suffix)
            if imp.is_file():
                out[r["rel"] + suffix] = file_hash(imp)
    return out


def apply_files(report: dict[str, Any], files: dict[str, bytes], backups: Path, mode: str = "apply",
                source: dict[str, Any] | None = None) -> dict[str, Any]:
    """mode 'dry_run' plans only; 'mirror' writes into the sibling folder; 'apply' backs up every file it will
    overwrite into one zip first, then overwrites the PNGs in place and records a manifest for undo."""
    if mode not in ("dry_run", "mirror", "apply"):
        raise RestyleError(f"unknown mode {mode}")
    root = Path(report["root"])
    plan = plan_writes(report, files)
    todo = [r for r in plan if r["action"] == "write"]
    doc: dict[str, Any] = {"type": APPLY_TYPE, "version": 1, "mode": mode, "root": str(root),
                           "engine": report.get("engine"), "at": time.time(), "source": source or {},
                           "files": plan, "written": 0, "skipped": len(plan) - len(todo)}
    if mode == "dry_run":
        return doc
    if mode == "mirror":
        dest = mirror_root(report)
        for r in todo:
            out = dest.joinpath(*PurePosixPath(r["rel"]).parts)
            out.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(out, files[r["rel"]])
        doc.update(written=len(todo), mirror=str(dest))
        return doc
    if not todo:
        return doc
    before = _importer_hashes(root, todo)
    backups.mkdir(parents=True, exist_ok=True)
    bid = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    zpath = backups / f"{bid}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for r in todo:
            z.write(safe_target(root, r["rel"]), r["rel"])
    with zipfile.ZipFile(zpath) as z:
        if z.testzip() is not None or len(z.namelist()) != len(todo):
            raise RestyleError("backup zip failed verification; nothing written")
    doc.update(backup_id=bid, backup=str(zpath))
    written = []
    try:
        for r in todo:
            _atomic_write(safe_target(root, r["rel"]), files[r["rel"]])
            written.append(r["rel"])
    finally:
        doc["written"] = len(written)
        doc["importers_unchanged"] = _importer_hashes(root, todo) == before
        (backups / f"{bid}.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
    return doc


def load_manifest(backups: Path, bid: str) -> dict[str, Any]:
    if not re.fullmatch(r"[\w-]{1,64}", bid or ""):
        raise RestyleError("bad backup id")
    p = backups / f"{bid}.json"
    if not p.is_file():
        raise RestyleError(f"unknown backup {bid}")
    return json.loads(p.read_text(encoding="utf-8"))


def undo(backups: Path, bid: str, force: bool = False) -> dict[str, Any]:
    """Restores the backed-up PNGs; a file edited after the restyle (hash != what was written) is left alone
    unless `force`."""
    doc = load_manifest(backups, bid)
    if doc.get("undone_at"):
        raise RestyleError("already undone")
    root = Path(doc["root"])
    restored, conflicts = [], []
    with zipfile.ZipFile(backups / f"{bid}.zip") as z:
        names = set(z.namelist())
        for r in doc.get("files") or []:
            if r.get("action") != "write" or r["rel"] not in names:
                continue
            try:
                dst = safe_target(root, r["rel"])
            except RestyleError as e:
                conflicts.append({"rel": r["rel"], "why": str(e)})
                continue
            if not force and file_hash(dst) != r.get("new"):
                conflicts.append({"rel": r["rel"], "why": "changed after the restyle"})
                continue
            _atomic_write(dst, z.read(r["rel"]))
            restored.append(r["rel"])
    doc.update(undone_at=time.time(), restored=restored, conflicts=conflicts)
    (backups / f"{bid}.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
    return {"backup_id": bid, "restored": restored, "conflicts": conflicts}


def list_backups(backups: Path) -> list[dict[str, Any]]:
    out = []
    for p in sorted(backups.glob("*.json"), reverse=True) if backups.is_dir() else []:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append({"backup_id": p.stem, "root": d.get("root"), "at": d.get("at"), "written": d.get("written"),
                    "undone_at": d.get("undone_at"), "source": d.get("source") or {}})
    return out
