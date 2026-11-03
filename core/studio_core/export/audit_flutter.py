"""Flutter project audit: pubspec `assets:` vs. the files on disk, missing 2.0x / 3.0x variants, heavy PNG / JPG and assets
no Dart file mentions; one-click fixes (declare, make a variant, convert to WebP) with a zip backup, a manifest and
undo. Every fix is a unit that lands whole or not at all; a scan cancel or a failing unit never leaves half a change."""
from __future__ import annotations

import io
import json
import os
import re
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Callable

import yaml
from PIL import Image

from studio_core.export import audit as au
from studio_core.export import mobile
from studio_core.export.restyle import _is_link, file_hash
from studio_core.scan_jobs import ScanCancelled

FIX_TYPES = ("flutter_declare", "flutter_variant", "flutter_webp")
IMAGE_EXTS = (".png", ".jpg", ".jpeg")
RASTER_EXTS = (*IMAGE_EXTS, ".webp")
VARIANT_DIR = re.compile(r"^\d+(?:\.\d+)?x$")
SCALES = (2.0, 3.0)
BIG_BYTES = 1 << 20
BIG_SIDE = mobile.BUDGETS["sprite"].max_size
WEBP_QUALITY = 90
IGNORED = {".ds_store", "thumbs.db", "desktop.ini"}
CODE_DIRS = ("lib", "test", "integration_test", "bin", "tool", "example")
FONT_EXTS = (".ttf", ".otf", ".ttc")
TOKEN = re.compile(r"[\w.@%+-]+(?:/[\w.@%+-]+)*")
REPORT_TYPE = au.REPORT_TYPE
ORDER = {t: i for i, t in enumerate(FIX_TYPES)}


def flutter_root(folder: Path) -> Path | None:
    for d in (folder, *folder.parents):
        f = d / "pubspec.yaml"
        if not f.is_file():
            continue
        try:
            doc = yaml.safe_load(f.read_text(encoding="utf-8", errors="replace"))
        except (OSError, yaml.YAMLError):
            continue
        if isinstance(doc, dict) and ("flutter" in doc or "flutter" in (doc.get("dependencies") or {})):
            return d
    return None


def _scale(name: str) -> float | None:
    return float(name[:-1]) if VARIANT_DIR.match(name) else None


def is_variant(rel: str) -> bool:
    return _scale(PurePosixPath(rel).parent.name) is not None


def _rel_dir(rel: str) -> str:
    parent = PurePosixPath(rel).parent.as_posix()
    return "" if parent == "." else parent


def _fmt_scale(s: float) -> str:
    return f"{s:.1f}x"


class _Disk:
    """Exact-case listing (Windows would otherwise accept `Assets/Logo.png` for `assets/logo.png`, Flutter on Linux
    / macOS CI would not)."""

    def __init__(self, proj: Path) -> None:
        self.proj = proj
        self._names: dict[str, set[str]] = {}

    def names(self, rel_dir: str) -> set[str]:
        if rel_dir not in self._names:
            try:
                self._names[rel_dir] = set(os.listdir(self.proj.joinpath(*PurePosixPath(rel_dir).parts)))
            except OSError:
                self._names[rel_dir] = set()
        return self._names[rel_dir]

    def kind(self, rel: str) -> str | None:
        pp = PurePosixPath(rel)
        parent = _rel_dir(rel)
        if not pp.parts or ".." in pp.parts or (parent and self.kind(parent) != "dir"):
            return None
        if pp.name not in self.names(parent):
            return None
        return "dir" if self.proj.joinpath(*pp.parts).is_dir() else "file"


def _entries(doc: dict[str, Any]) -> tuple[list[str], set[str]]:
    """(`flutter: assets:` entries, other declared asset files: fonts and shaders)."""
    fl = doc.get("flutter") if isinstance(doc.get("flutter"), dict) else {}
    entries: list[str] = []
    for e in fl.get("assets") or []:
        path = e.get("path") if isinstance(e, dict) else e
        if isinstance(path, str) and path.strip() and not path.startswith("packages/"):
            entries.append(path.strip())
    extra: set[str] = set()
    for fam in fl.get("fonts") or []:
        for f in (fam.get("fonts") or []) if isinstance(fam, dict) else []:
            if isinstance(f, dict) and isinstance(f.get("asset"), str):
                extra.add(f["asset"])
    for s in fl.get("shaders") or []:
        if isinstance(s, str):
            extra.add(s)
    return entries, extra


def _load_pubspec(proj: Path) -> tuple[str, dict[str, Any]]:
    text = (proj / "pubspec.yaml").read_text(encoding="utf-8")
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise au.AuditError(f"pubspec.yaml không đọc được: {e}") from e
    return text, doc if isinstance(doc, dict) else {}


def _walk_assets(proj: Path) -> list[str]:
    out: list[str] = []
    root = proj / "assets"
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        d = Path(dirpath)
        dirnames[:] = sorted(n for n in dirnames if not n.startswith(".") and not _is_link(d / n))
        for n in sorted(filenames):
            if n.startswith(".") or n.lower() in IGNORED or _is_link(d / n):
                continue
            out.append((d / n).relative_to(proj).as_posix())
    return out


def _code_text(proj: Path) -> tuple[list[str], str]:
    """Dart files (rel paths) and the text of root *.yaml configs (launcher icons / native splash name image files)."""
    dart: list[str] = []
    for top in CODE_DIRS:
        for dirpath, dirnames, filenames in os.walk(proj / top, followlinks=False):
            dirnames[:] = [n for n in dirnames if not n.startswith(".") and n not in ("build", "node_modules")]
            dart += [(Path(dirpath) / n).relative_to(proj).as_posix() for n in filenames if n.endswith(".dart")]
    dart += [n for n in os.listdir(proj) if n.endswith(".dart") and (proj / n).is_file()]
    yamls = [n for n in os.listdir(proj) if n.endswith((".yaml", ".yml")) and n != "pubspec.lock"]
    return sorted(dart), "\n".join(_text(proj / n) for n in yamls if n != "pubspec.yaml")


def _text(p: Path) -> str:
    try:
        if p.stat().st_size > au.MAX_REF_BYTES:
            return ""
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _tail_names(text: str) -> set[str]:
    return {t.rsplit("/", 1)[-1] for t in TOKEN.findall(text)}


def _bare_unique(names: dict[str, int], name: str) -> bool:
    return names.get(name, 0) == 1


def _finding(code: str, severity: str, rel: str, msg: str, fix: dict[str, Any] | None = None, **extra: Any
             ) -> dict[str, Any]:
    out: dict[str, Any] = {"code": code, "severity": severity, "rel": rel, "msg": msg, **extra}
    if fix:
        out["fix"] = fix
    return out


def _probe(p: Path) -> tuple[int, int]:
    with Image.open(p) as im:
        return im.size


def scan(folder: Path, proj: Path, progress: Any = None) -> dict[str, Any]:
    t0 = time.time()
    tick: Callable[..., Any] = progress or (lambda *_: None)
    text, doc = _load_pubspec(proj)
    entries, extra_declared = _entries(doc)
    disk = _Disk(proj)
    folder_rel = "" if folder == proj else folder.relative_to(proj).as_posix()

    def inside(rel: str) -> bool:
        return not folder_rel or rel == folder_rel or rel.startswith(folder_rel + "/")

    findings: list[dict[str, Any]] = []
    declared: set[str] = {e for e in extra_declared if disk.kind(e) == "file"}
    seen_entries: set[str] = set()
    for e in entries:
        key = e.rstrip("/")
        if key in seen_entries:
            continue
        seen_entries.add(key)
        kind = disk.kind(key)
        if kind == "dir":
            declared.update(f"{key}/{n}" for n in sorted(disk.names(key)) if disk.kind(f"{key}/{n}") == "file")
        elif kind == "file" and not e.endswith("/"):
            declared.add(key)
        elif inside(key):
            hint = " (dùng dấu / thay cho \\)" if "\\" in e else ""
            what = "Thư mục" if e.endswith("/") else "File"
            findings.append(_finding("asset_missing", "error", e,
                                     f"{what} khai báo trong pubspec nhưng không có trên đĩa{hint}; flutter build sẽ lỗi."))

    walked = _walk_assets(proj)
    bases = sorted({r for r in (*declared, *walked) if not is_variant(r) and PurePosixPath(r).name.lower() not in IGNORED})
    name_count: dict[str, int] = {}
    for r in bases:
        name_count[PurePosixPath(r).name] = name_count.get(PurePosixPath(r).name, 0) + 1

    images = 0
    image_bytes = 0
    big_bytes = 0
    total = len(bases)
    for n, base in enumerate(bases):
        tick(n, total, "files")
        if base not in declared and inside(base):
            findings.append(_finding("asset_undeclared", "warn", base,
                                     "Có trong assets/ nhưng pubspec chưa khai báo nên không vào bản build; "
                                     "thêm một dòng vào flutter: assets:.",
                                     {"type": "flutter_declare", "rel": base}))
        if not base.lower().endswith(RASTER_EXTS) or disk.kind(base) != "file":
            continue
        members = _group(proj, disk, base)
        if inside(base):
            images += len(members)
            image_bytes += sum(m["bytes"] for m in members.values())
            findings += _group_findings(proj, base, members, disk)
            big = _big(members)
            if big:
                big_bytes += sum(m["bytes"] for m in members.values())
    tick(total, total, "analyze")

    dart, yaml_text = _code_text(proj)
    corpus = set()
    for rel in dart:
        corpus |= _tail_names(_text(proj / rel))
    corpus |= _tail_names(yaml_text)
    corpus |= _tail_names(_pubspec_without_assets(text))
    unreferenced = []
    for rel in sorted(declared):
        name = PurePosixPath(rel).name
        if is_variant(rel) or not inside(rel) or rel in extra_declared or rel.lower().endswith(FONT_EXTS):
            continue
        if name not in corpus:
            unreferenced.append({"rel": rel, "kind": au._ref_kind(name) or "other",
                                 "bytes": (proj / rel).stat().st_size})
    unreferenced.sort(key=lambda r: -r["bytes"])

    counts: dict[str, int] = {}
    for f in findings:
        counts[f["code"]] = counts.get(f["code"], 0) + 1
    if unreferenced:
        counts["asset_unused"] = len(unreferenced)
    notes = ["Asset nạp bằng đường dẫn ghép chuỗi trong code (vd 'assets/img_$i.png') không dò được; "
             "xem lại trước khi xoá mục “Không ai dùng”."]
    if not dart:
        notes.append("Không thấy file .dart nào trong lib/, nên mọi asset đều bị tính là không ai dùng.")
    return {
        "type": REPORT_TYPE, "version": 1, "root": str(folder), "project": str(proj), "engine": "flutter",
        "at": time.time(), "seconds": round(time.time() - t0, 2), "tri_budget": au.DEFAULT_TRI_BUDGET,
        "summary": {
            "textures": images, "texture_bytes": image_bytes, "gpu_before": 0, "gpu_after": 0,
            "fixable": sum(1 for f in findings if f.get("fix")), "duplicate_groups": 0, "duplicate_bytes": 0,
            "duplicate_method": "off", "unreferenced": len(unreferenced),
            "unreferenced_bytes": sum(u["bytes"] for u in unreferenced), "meshes": 0, "meshes_over": 0, "audio": 0,
            "issues": counts,
            "flutter": {"assets": len(bases), "declared": len(declared), "big_bytes": big_bytes,
                        "errors": sum(1 for f in findings if f["severity"] == "error")},
        },
        "pubspec_hash": file_hash(proj / "pubspec.yaml"), "name_count": name_count,
        "findings": findings, "textures": [], "duplicates": [], "unreferenced": unreferenced, "meshes": [],
        "audio": [], "notes": notes,
    }


def _pubspec_without_assets(text: str) -> str:
    lines = text.replace("\r\n", "\n").split("\n")
    block = _assets_block(lines)
    return "\n".join(lines[:block[0]] + lines[block[1]:]) if block else text


def _group(proj: Path, disk: _Disk, base: str) -> dict[float, dict[str, Any]]:
    """scale -> {rel, bytes, w, h} for the main file (1.0) and the sibling `2.0x/`, `3.0x/` ... copies."""
    d, name = _rel_dir(base), PurePosixPath(base).name
    found: dict[float, str] = {1.0: base}
    for sub in sorted(disk.names(d)):
        s = _scale(sub)
        sub_rel = f"{d}/{sub}" if d else sub
        if s is not None and disk.kind(sub_rel) == "dir" and name in disk.names(sub_rel):
            found[s] = f"{sub_rel}/{name}"
    out: dict[float, dict[str, Any]] = {}
    for s, rel in found.items():
        p = proj.joinpath(*PurePosixPath(rel).parts)
        try:
            w, h = _probe(p)
        except (OSError, ValueError, Image.DecompressionBombError):
            continue
        out[s] = {"rel": rel, "bytes": p.stat().st_size, "w": w, "h": h}
    return out


def _big(members: dict[float, dict[str, Any]]) -> bool:
    return any(m["bytes"] > BIG_BYTES or max(m["w"], m["h"]) > BIG_SIDE for m in members.values())


def _group_findings(proj: Path, base: str, members: dict[float, dict[str, Any]], disk: _Disk
                    ) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if 1.0 not in members:
        return out
    present = {s for s in SCALES if s in members}
    top = max(members)
    for s in SCALES:
        if s in members or not present:
            continue
        miss = _fmt_scale(s)
        have = ", ".join(_fmt_scale(x) for x in sorted(present))
        if s < top:
            src = members[top]
            out.append(_finding("variant_missing", "warn", base,
                                f"Có {have} nhưng thiếu {miss}; Flutter sẽ lấy bản nhỏ hơn rồi phóng to trên màn "
                                f"mật độ cao. Sinh từ {_fmt_scale(top)} (thu nhỏ Lanczos).",
                                {"type": "flutter_variant", "rel": base, "kind": miss},
                                src=src["rel"], src_scale=top, src_hash=file_hash(proj / src["rel"]),
                                bytes=src["bytes"]))
        else:
            out.append(_finding("variant_missing", "warn", base,
                                f"Có {have} nhưng thiếu {miss}; bản lớn nhất hiện có chỉ là {_fmt_scale(top)} nên "
                                "không tự sinh (không phóng to ảnh) — xuất lại từ file thiết kế."))
    if _big(members) and base.lower().endswith(IMAGE_EXTS):
        total = sum(m["bytes"] for m in members.values())
        side = max(max(m["w"], m["h"]) for m in members.values())
        stem = PurePosixPath(base).stem
        clash = any(f"{stem}.webp" in disk.names(_rel_dir(m["rel"])) for m in members.values())
        msg = f"{_mb(total)}, cạnh dài nhất {side}px (ngân sách {_mb(BIG_BYTES)} / {BIG_SIDE}px); nên chuyển WebP."
        fix = None if clash else {"type": "flutter_webp", "rel": base}
        out.append(_finding("image_big", "warn", base, msg if not clash else f"{msg} Đã có {stem}.webp cùng thư mục.",
                            fix, bytes=total,
                            members=[{"rel": m["rel"], "hash": file_hash(proj / m["rel"])} for m in members.values()]))
    return out


def _mb(n: float) -> str:
    return f"{n / 1048576:.2f} MB"


# --- pubspec text edits -------------------------------------------------------------------------------------------

def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _skippable(line: str) -> bool:
    s = line.strip()
    return not s or s.startswith("#")


def _flutter_block(lines: list[str]) -> tuple[int, int, int] | None:
    start = next((i for i, ln in enumerate(lines) if re.match(r"^flutter:\s*(#.*)?$", ln)), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines)) if not _skippable(lines[i]) and _indent(lines[i]) == 0),
               len(lines))
    child = next((_indent(lines[i]) for i in range(start + 1, end) if not _skippable(lines[i])), 2)
    return start, end, child


def _assets_block(lines: list[str]) -> tuple[int, int, int] | None:
    """(`assets:` line, line after its last item, key indent) inside the top-level flutter: block."""
    fb = _flutter_block(lines)
    if fb is None:
        return None
    start, end, child = fb
    for i in range(start + 1, end):
        m = re.match(r"^(\s*)assets:\s*(#.*)?$", lines[i])
        if m and len(m.group(1)) == child:
            last = i
            for j in range(i + 1, end):
                if _skippable(lines[j]):
                    continue
                if _indent(lines[j]) > child or (_indent(lines[j]) == child and lines[j].strip().startswith("- ")):
                    last = j
                else:
                    break
            return i, last + 1, child
    return None


def _quote(entry: str) -> str:
    return '"' + entry.replace("\\", "\\\\").replace('"', '\\"') + '"' if re.search(r"[\s#:'\"\[\]{},&*!|>%@`]", entry) \
        else entry


def declare_entry(text: str, entry: str) -> str:
    body, crlf = au._lf(text)
    lines = body.split("\n")
    block = _assets_block(lines)
    if block is None:
        fb = _flutter_block(lines)
        if fb is None:
            raise au.AuditError("pubspec.yaml chưa có mục flutter:")
        doc = yaml.safe_load(body) or {}
        if isinstance(doc.get("flutter"), dict) and doc["flutter"].get("assets") is not None:
            raise au.AuditError("assets: viết một dòng ([...]); sửa tay trong pubspec.yaml")
        pad = " " * fb[2]
        lines[fb[0] + 1:fb[0] + 1] = [f"{pad}assets:", f"{pad}  - {_quote(entry)}"]
    else:
        start, end, child = block
        item = next((_indent(ln) for ln in lines[start + 1:end] if ln.lstrip().startswith("- ")), child + 2)
        lines.insert(end, f"{' ' * item}- {_quote(entry)}")
    out = "\n".join(lines)
    return out.replace("\n", "\r\n") if crlf else out


def rename_entry(text: str, old: str, new: str) -> str:
    body, crlf = au._lf(text)
    lines = body.split("\n")
    block = _assets_block(lines)
    if block is None:
        return text
    pat = re.compile(r"^(\s*(?:-\s+)?(?:path:\s*)?)([\"']?)" + re.escape(old) + r"\2(\s*(?:#.*)?)$")
    for i in range(block[0] + 1, block[1]):
        m = pat.match(lines[i])
        if m:
            lines[i] = f"{m.group(1)}{m.group(2)}{new}{m.group(2)}{m.group(3)}"
    out = "\n".join(lines)
    return out.replace("\n", "\r\n") if crlf else out


def rewrite_refs(text: str, old: str, new: str, bare: bool) -> str:
    """Dart string literals naming `old` (full path; the bare file name too when no other asset shares it)."""
    out = re.sub(r"(?<![\w/.-])" + re.escape(old) + r"(?![\w])", new, text)
    if bare:
        o, n = PurePosixPath(old).name, PurePosixPath(new).name
        out = re.sub(r"(?<=[/'\"])" + re.escape(o) + r"(?=['\"])", n, out)
    return out


# --- image work ---------------------------------------------------------------------------------------------------

def _webp_bytes(img: Image.Image) -> bytes:
    """Hard (really used) alpha stays lossless; opaque pixels go lossy at WEBP_QUALITY."""
    buf = io.BytesIO()
    if "A" in img.getbands() and img.getchannel("A").getextrema()[0] < 255:
        img.save(buf, "WEBP", lossless=True, quality=75, method=4)
    else:
        img.convert("RGB").save(buf, "WEBP", quality=WEBP_QUALITY, method=4)
    return buf.getvalue()


def encode_webp(p: Path) -> bytes:
    with Image.open(p) as im:
        if getattr(im, "n_frames", 1) > 1:
            raise au.AuditError("ảnh động (APNG), không chuyển WebP")
        im.load()
        has_alpha = "A" in im.getbands() or "transparency" in im.info
        return _webp_bytes(im.convert("RGBA" if has_alpha else "RGB"))


def make_variant(src: Path, src_scale: float, target: float, ext: str) -> bytes:
    with Image.open(src) as im:
        im.load()
        img = im.convert("RGBA") if im.mode in ("P", "PA") else im
        k = target / src_scale
        out = img.resize((max(1, round(img.width * k)), max(1, round(img.height * k))), Image.Resampling.LANCZOS)
    if ext == ".webp":
        return _webp_bytes(out if out.mode in ("RGB", "RGBA") else out.convert("RGBA"))
    buf = io.BytesIO()
    if ext == ".png":
        out.save(buf, "PNG", optimize=True)
    else:
        out.convert("RGB").save(buf, "JPEG", quality=92, subsampling=0)
    return buf.getvalue()


# --- fixing -------------------------------------------------------------------------------------------------------

def _safe(proj: Path, rel: str) -> Path:
    pp = PurePosixPath(rel.replace("\\", "/"))
    if pp.is_absolute() or not pp.parts or any(part in ("", ".", "..") for part in pp.parts) or ":" in rel:
        raise au.AuditError(f"path escapes the project: {rel}")
    root = proj.resolve()
    cur = root
    for part in pp.parts:
        cur = cur / part
        if _is_link(cur):
            raise au.AuditError(f"link in path: {rel}")
    if not cur.resolve().is_relative_to(root):
        raise au.AuditError(f"path escapes the project: {rel}")
    return root.joinpath(*pp.parts)


class _Tx:
    """File changes of one fix: undone in reverse if a later step fails."""

    def __init__(self, proj: Path) -> None:
        self.proj = proj.resolve()
        self.log: list[Callable[[], None]] = []

    def write(self, rel: str, data: bytes) -> None:
        p = _safe(self.proj, rel)
        old = p.read_bytes() if p.is_file() else None
        made = [d for d in reversed(p.parents) if d.is_relative_to(self.proj) and not d.exists()]
        p.parent.mkdir(parents=True, exist_ok=True)
        au._atomic_write(p, data)

        def back() -> None:
            if old is None:
                p.unlink(missing_ok=True)
                for d in reversed(made):
                    try:
                        d.rmdir()
                    except OSError:
                        break
            else:
                au._atomic_write(p, old)
        self.log.append(back)

    def delete(self, rel: str) -> None:
        p = _safe(self.proj, rel)
        old = p.read_bytes()
        p.unlink()
        pruned = p.parent if _scale(p.parent.name) is not None and not any(p.parent.iterdir()) else None
        if pruned:
            pruned.rmdir()

        def back() -> None:
            p.parent.mkdir(parents=True, exist_ok=True)
            au._atomic_write(p, old)
        self.log.append(back)

    def rollback(self) -> None:
        for undo in reversed(self.log):
            try:
                undo()
            except OSError:
                pass
        self.log.clear()


def _read_text(proj: Path, rel: str) -> str:
    return _safe(proj, rel).read_bytes().decode("utf-8")


def _find(report: dict[str, Any], a: dict[str, Any]) -> dict[str, Any] | None:
    for f in report.get("findings") or []:
        fx = f.get("fix") or {}
        if fx.get("type") == a["type"] and fx.get("rel") == a.get("rel") and fx.get("kind") == a.get("kind"):
            return f
    return None


def _hash_of(proj: Path, rel: str) -> str | None:
    p = _safe(proj, rel)
    return file_hash(p) if p.is_file() else None


def plan_fixes(report: dict[str, Any], actions: list[dict[str, Any]], progress: Any = None) -> list[dict[str, Any]]:
    proj = Path(report["project"])
    pub_ok = _hash_of(proj, "pubspec.yaml") == report.get("pubspec_hash")
    pub_text = _read_text(proj, "pubspec.yaml")
    dart = _code_text(proj)[0]
    names = report.get("name_count") or {}
    seen: set[tuple[str, str, str]] = set()
    units: list[dict[str, Any]] = []
    for a in actions:
        key = (str(a.get("type")), str(a.get("rel")), str(a.get("kind") or ""))
        if key in seen:
            continue
        seen.add(key)
        units.append({"rel": str(a.get("rel") or ""), "type": str(a.get("type")), "kind": a.get("kind"),
                      "action": "write", "fixes": [str(a.get("type"))], "_a": a})
    units.sort(key=lambda u: ORDER.get(u["type"], 9))
    for i, u in enumerate(units):
        if progress is not None:
            progress(i, len(units), "plan")
        try:
            f = _find(report, u["_a"]) if u["type"] in FIX_TYPES else None
            if f is None:
                raise au.AuditError("không có trong lần quét gần nhất")
            if u["type"] == "flutter_declare":
                if not pub_ok:
                    raise au.AuditError("pubspec.yaml đã đổi sau lần quét; quét lại")
                if _hash_of(proj, u["rel"]) is None:
                    raise au.AuditError("file không còn trên đĩa")
                if declare_entry(pub_text, u["rel"]) == pub_text:
                    raise au.AuditError("đã khai báo")
                u["touch"] = ["pubspec.yaml"]
            elif u["type"] == "flutter_variant":
                if _hash_of(proj, f["src"]) != f["src_hash"]:
                    raise au.AuditError("ảnh nguồn đã đổi sau lần quét; quét lại")
                u["target"] = _variant_path(u["rel"], u["kind"])
                if _hash_of(proj, u["target"]) is not None:
                    raise au.AuditError("biến thể đã có")
                u["src"], u["src_scale"] = f["src"], f["src_scale"]
                u["touch"] = [u["target"]]
            else:
                if not pub_ok:
                    raise au.AuditError("pubspec.yaml đã đổi sau lần quét; quét lại")
                _plan_webp(proj, u, f, dart, names)
        except (au.AuditError, OSError, UnicodeDecodeError, ValueError) as e:
            u.update(action="skip", why=str(e))
    return units


def _variant_path(base: str, kind: str) -> str:
    d, name = _rel_dir(base), PurePosixPath(base).name
    if not VARIANT_DIR.match(str(kind)):
        raise au.AuditError(f"bad variant {kind}")
    return f"{d}/{kind}/{name}" if d else f"{kind}/{name}"


def _plan_webp(proj: Path, u: dict[str, Any], f: dict[str, Any], dart: list[str], names: dict[str, int]) -> None:
    members = f["members"]
    for m in members:
        if _hash_of(proj, m["rel"]) != m["hash"]:
            raise au.AuditError("ảnh đã đổi sau lần quét; quét lại")
    new_of = {m["rel"]: str(PurePosixPath(m["rel"]).with_suffix(".webp")) for m in members}
    if any(_hash_of(proj, n) is not None for n in new_of.values()):
        raise au.AuditError("đã có file .webp cùng tên")
    before = sum(_safe(proj, m["rel"]).stat().st_size for m in members)
    after = sum(len(encode_webp(_safe(proj, m["rel"]))) for m in members)
    if after >= before:
        raise au.AuditError("WebP không nhẹ hơn bản gốc")
    base = u["rel"]
    bare = _bare_unique(names, PurePosixPath(base).name)
    edits = [r for r in dart if rewrite_refs(_read_text(proj, r), base, new_of[base], bare) != _read_text(proj, r)]
    u.update(members=[m["rel"] for m in members], new_of=new_of, dart=edits, bare=bare, bytes_before=before,
             bytes_after=after,
             touch=["pubspec.yaml", *edits, *new_of.keys(), *new_of.values()])


def _run_unit(proj: Path, u: dict[str, Any], expected: dict[str, str | None]) -> None:
    for rel in u.get("touch", []):
        if expected.get(rel, _hash_of(proj, rel)) != _hash_of(proj, rel):
            raise au.AuditError(f"{rel} đã đổi trong lúc sửa")
    tx = _Tx(proj)
    try:
        if u["type"] == "flutter_declare":
            tx.write("pubspec.yaml", declare_entry(_read_text(proj, "pubspec.yaml"), u["rel"]).encode("utf-8"))
        elif u["type"] == "flutter_variant":
            ext = PurePosixPath(u["rel"]).suffix.lower()
            tx.write(u["target"], make_variant(_safe(proj, u["src"]), u["src_scale"], _scale(u["kind"]) or 1.0, ext))
        else:
            new_of = u["new_of"]
            data = {m: encode_webp(_safe(proj, m)) for m in u["members"]}
            for m, blob in data.items():
                tx.write(new_of[m], blob)
            for rel in u["dart"]:
                tx.write(rel, rewrite_refs(_read_text(proj, rel), u["rel"], new_of[u["rel"]], u["bare"]).encode("utf-8"))
            tx.write("pubspec.yaml", rename_entry(_read_text(proj, "pubspec.yaml"), u["rel"], new_of[u["rel"]])
                     .encode("utf-8"))
            for m in u["members"]:
                tx.delete(m)
    except BaseException:
        tx.rollback()
        raise


def apply(report: dict[str, Any], actions: list[dict[str, Any]], backups: Path, dry_run: bool,
          source: dict[str, Any] | None, progress: Any, units: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    proj = Path(report["project"])
    progress = progress or (lambda *_: None)
    doc: dict[str, Any] = {"type": au.FIX_TYPE, "version": 1, "dry_run": dry_run, "project": str(proj),
                           "engine": "flutter", "at": time.time(), "source": source or {}, "files": [],
                           "written": 0, "skipped": 0, "gpu_before": 0, "gpu_after": 0}
    try:
        if units is None:
            units = plan_fixes(report, actions, progress)
    except ScanCancelled:
        doc.update(cancelled=True)
        return doc
    todo = [u for u in units if u["action"] == "write"]
    keep = ("rel", "action", "why", "fixes", "bytes_before", "bytes_after")
    doc["files"] = [{k: u[k] for k in keep if k in u} for u in units]
    doc.update(skipped=len(units) - len(todo), bytes_before=sum(u.get("bytes_before", 0) for u in todo),
               bytes_after=sum(u.get("bytes_after", 0) for u in todo))
    if dry_run or not todo:
        return doc
    touched = sorted({r for u in todo for r in u["touch"]})
    orig = {r: _hash_of(proj, r) for r in touched}
    backups.mkdir(parents=True, exist_ok=True)
    bid = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    zpath = backups / f"{bid}.zip"
    saved = [r for r in touched if orig[r] is not None]
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for r in saved:
            z.write(_safe(proj, r), r)
    with zipfile.ZipFile(zpath) as z:
        if z.testzip() is not None or len(z.namelist()) != len(saved):
            raise au.AuditError("backup zip failed verification; nothing written")
    doc.update(backup_id=bid, backup=str(zpath))
    expected = dict(orig)
    written = 0
    done: list[dict[str, Any]] = []
    files = [f for f in doc["files"] if f["action"] == "write"]
    try:
        for i, (u, f) in enumerate(zip(todo, files)):
            try:
                progress(i, len(todo), "files")
            except ScanCancelled:
                doc["cancelled"] = True
                for left in files[i:]:
                    left.update(action="skip", why="cancelled")
                break
            try:
                _run_unit(proj, u, expected)
            except (au.AuditError, OSError, UnicodeDecodeError, ValueError) as e:
                f.update(action="skip", why=str(e))
                continue
            for r in u["touch"]:
                expected[r] = _hash_of(proj, r)
            done.append(u)
            written += 1
    finally:
        doc["written"] = written
        doc["skipped"] = len(doc["files"]) - written
        doc["bytes_before"] = sum(u.get("bytes_before", 0) for u in done)
        doc["bytes_after"] = sum(u.get("bytes_after", 0) for u in done)
        changed = sorted({r for u in done for r in u["touch"]})
        doc["touched"] = [{"rel": r, "old": orig[r], "new": _hash_of(proj, r)} for r in changed
                          if orig[r] != _hash_of(proj, r)]
        if not written:
            zpath.unlink(missing_ok=True)
            doc.pop("backup_id", None)
            doc.pop("backup", None)
        else:
            (backups / f"{bid}.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
    return doc


def undo(backups: Path, doc: dict[str, Any], bid: str, force: bool) -> dict[str, Any]:
    """All or nothing: a Dart file edited by hand after the fix would otherwise be left pointing at a restored PNG."""
    proj = Path(doc["project"])
    touched = doc.get("touched") or []
    conflicts = [{"rel": t["rel"], "why": "changed after the fix"} for t in touched
                 if _hash_of(proj, t["rel"]) != t["new"]]
    if conflicts and not force:
        return {"backup_id": bid, "restored": [], "conflicts": conflicts}
    tx = _Tx(proj)
    restored: list[str] = []
    try:
        with zipfile.ZipFile(backups / f"{bid}.zip") as z:
            names = set(z.namelist())
            for t in touched:
                if t["old"] is not None and t["rel"] in names:
                    tx.write(t["rel"], z.read(t["rel"]))
                    restored.append(t["rel"])
        for t in touched:
            if t["old"] is None and _hash_of(proj, t["rel"]) is not None:
                tx.delete(t["rel"])
                restored.append(t["rel"])
    except BaseException:
        tx.rollback()
        raise
    doc.update(undone_at=time.time(), restored=restored, conflicts=conflicts)
    (backups / f"{bid}.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
    return {"backup_id": bid, "restored": restored, "conflicts": conflicts}
