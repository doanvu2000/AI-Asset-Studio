"""Distributable asset pack of a project: Unity UPM package and/or Godot addon, provenance.json, README, CHANGELOG,
LICENSE, plus a zip. Local disk only; nothing here talks to git or any remote."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import unicodedata
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from studio_core import __version__
from studio_core.contracts import BlobStore, RunRow, StepRow, VersionRow
from studio_core.export.sheet import stable_hex
from studio_core.store.bundle import BundleError, engine_ready, export_engine
from studio_core.store.export import safe_filename
from studio_core.store.model_licenses import model_license, worst


class PackError(ValueError):
    pass


LICENSES = ("cc0", "cc-by-4.0", "proprietary")
PACK_ENGINES = ("unity", "godot")
_SEMVER = re.compile(r"^(0|[1-9]\d{0,8})\.(0|[1-9]\d{0,8})\.(0|[1-9]\d{0,8})$")
_GUID_LINE = re.compile(r"^guid:\s*([0-9a-fA-F]{32})\s*$", re.M)
_HASH = re.compile(r"^[0-9a-f]{64}$")
_DEDUPE_EXT = {".cs", ".shader", ".hlsl", ".cginc"}
_TEXT_EXT = {".prefab", ".asset", ".anim", ".controller", ".mat", ".meta", ".overrideController", ".unity",
             ".physicsMaterial2D", ".spriteatlas", ".playable", ".mask", ".fontsettings", ".json", ".txt", ".tres",
             ".tscn"}
_PROMPT_KEYS = {"prompt", "negative", "negative_prompt", "positive", "text", "brief", "description", "style",
                "instructions", "subject"}
FIRST_VERSION = "1.0.0"


@dataclass
class PackAsset:
    name: str
    step: StepRow
    siblings: list[StepRow]
    run: RunRow | None
    versions: list[VersionRow]


@dataclass
class EngineOptions:
    ppu: float = 100.0
    pixel: bool | None = None
    collider: Callable[[PackAsset], Any] = lambda _a: None
    shaders: list[Any] | None = None
    normal2d: Any = None


@dataclass
class PackOptions:
    engines: list[str] = field(default_factory=lambda: ["unity"])
    license: str = "cc0"
    version: str | None = None
    org: str = "assetstudio"
    author: str = ""
    description: str = ""
    out_dir: Path | None = None
    zip: bool = True
    export: EngineOptions = field(default_factory=EngineOptions)


def ascii_fold(text: str) -> str:
    text = text.replace("đ", "d").replace("Đ", "D")
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def slugify(text: str, fallback: str = "pack") -> str:
    s = re.sub(r"[^a-z0-9]+", "-", ascii_fold(text).lower()).strip("-")
    return s[:60].strip("-") or fallback


def folder_name(text: str, fallback: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", ascii_fold(text)).strip("_")[:60].strip("_") or fallback
    return f"_{s}" if s[0].isdigit() else s


def _pascal(text: str) -> str:
    return "".join(p[:1].upper() + p[1:] for p in re.split(r"[^A-Za-z0-9]+", ascii_fold(text)) if p) or "Pack"


def package_name(org: str, slug: str) -> str:
    o = re.sub(r"[^a-z0-9-]+", "", slugify(org, "assetstudio"))
    return f"com.{o}.{slug}"


def parse_semver(v: str) -> tuple[int, int, int]:
    m = _SEMVER.match(v.strip())
    if not m:
        raise PackError(f"version must be semver MAJOR.MINOR.PATCH, got '{v}'")
    return int(m[1]), int(m[2]), int(m[3])


def next_version(history: list[dict[str, Any]]) -> str:
    if not history:
        return FIRST_VERSION
    last = max((parse_semver(h["version"]) for h in history if _SEMVER.match(str(h.get("version", "")))),
               default=None)
    if last is None:
        return FIRST_VERSION
    return f"{last[0]}.{last[1]}.{last[2] + 1}"


def history_path(data_dir: Path, project_id: str) -> Path:
    return data_dir / "packs" / "history" / f"{project_id}.json"


def load_history(data_dir: Path, project_id: str) -> list[dict[str, Any]]:
    try:
        doc = json.loads(history_path(data_dir, project_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    packs = doc.get("packs") if isinstance(doc, dict) else None
    return [p for p in packs if isinstance(p, dict)] if isinstance(packs, list) else []


def save_history(data_dir: Path, project_id: str, history: list[dict[str, Any]]) -> None:
    p = history_path(data_dir, project_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(json.dumps({"project_id": project_id, "packs": history}, indent=1, ensure_ascii=False),
                   encoding="utf-8")
    os.replace(tmp, p)


# ---- provenance ----

def _workflows(step: StepRow) -> list[str]:
    if step.kind.startswith("comfy."):
        return [step.kind[len("comfy."):]]
    wf = step.params.get("workflow")
    if isinstance(wf, str) and wf:
        return [wf]
    if step.kind.startswith("views.qwen"):
        return ["multi_angles_qwen2511"]
    if step.kind == "mesh.checked":
        return ["img2mesh_trellis2_mv_textured"]
    return []


def step_models(step: StepRow) -> list[str]:
    from studio_core.bootstrap.plan import workflow_models

    names: set[str] = set()
    for wf in _workflows(step):
        names |= workflow_models(wf)
    if step.kind.startswith("codex."):
        names.add("codex")
    elif step.kind.startswith("claude."):
        names.add("claude")
    elif step.kind.startswith("tts."):
        from studio_core.adapters.tts import EN_MODEL, VI_MODEL

        lang = str(step.params.get("lang") or step.params.get("language") or "").lower()
        names |= {VI_MODEL} if lang.startswith("vi") else {EN_MODEL} if lang.startswith("en") else {VI_MODEL, EN_MODEL}
    return sorted(names)


def lineage(step: StepRow, siblings: list[StepRow]) -> list[StepRow]:
    by_key = {s.key: s for s in siblings}
    by_key.setdefault(step.key, step)
    todo, seen, out = [step.key], set(), []
    while todo:
        k = todo.pop()
        if k in seen or k not in by_key:
            continue
        seen.add(k)
        out.append(by_key[k])
        todo.extend(by_key[k].deps)
    return sorted(out, key=lambda s: (s.order, s.key))


def _prompts(run: RunRow | None, steps: list[StepRow]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(text: Any, **where: str) -> None:
        if isinstance(text, str) and text.strip() and not _HASH.match(text) and text not in seen:
            seen.add(text)
            out.append({**where, "text": text[:4000]})

    for k, v in (run.inputs if run else {}).items():
        add(v, input=k)
    for s in steps:
        for k, v in s.params.items():
            if k in _PROMPT_KEYS or k.endswith(".text"):
                add(v, step=s.key, param=k)
    return out


def _seeds(steps: list[StepRow]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for s in steps:
        for k, v in ((s.meta or {}).get("seeds") or {}).items():
            out[f"{s.key}:{k}"] = v
        for k, v in s.params.items():
            if (k == "seed" or k.endswith(".seed")) and isinstance(v, int) and not isinstance(v, bool):
                out.setdefault(f"{s.key}:{k}", v)
    return out


def asset_provenance(a: PackAsset, folder: str) -> dict[str, Any]:
    steps = lineage(a.step, a.siblings)
    models = sorted({m for s in steps for m in step_models(s)})
    lic = [model_license(m) for m in models]
    hashes = sorted({v.blob.hash for v in a.versions} | {r.hash for refs in a.step.outputs.values() for r in refs})
    return {
        "name": a.name, "folder": folder, "run_id": a.step.run_id, "step_id": a.step.id, "step_kind": a.step.kind,
        "recipe_id": a.run.recipe_id if a.run else None, "recipe_hash": a.run.recipe_hash if a.run else None,
        "run_created_at": a.run.created_at if a.run else None,
        "created_at": max((v.created_at for v in a.versions), default=a.step.ended_at),
        "version_ids": [v.id for v in a.versions],
        "generators": [{"step": s.key, "kind": s.kind} for s in steps],
        "models": lic, "commercial": worst([m["commercial"] for m in lic]),
        "prompts": _prompts(a.run, steps), "seeds": _seeds(steps),
        "blobs": [{"hash": v.blob.hash, "mime": v.blob.mime, "name": v.blob.name} for v in a.versions],
        "output_hashes": hashes,
        "digest": hashlib.blake2b("\x1f".join([a.step.id, *hashes]).encode(), digest_size=12).hexdigest(),
    }


# ---- Unity package post-processing ----

def _meta_guid(meta: Path) -> str | None:
    try:
        m = _GUID_LINE.search(meta.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
    return m[1].lower() if m else None


def _replace_guid(root: Path, old: str, new: str) -> None:
    for p in root.rglob("*"):
        if p.is_file() and p.suffix in _TEXT_EXT:
            try:
                t = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if old in t:
                p.write_text(t.replace(old, new), encoding="utf-8", newline="")


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _rel(p: Path, root: Path) -> str:
    return p.relative_to(root).as_posix()


def _dedupe_shared(pkg: Path, report: dict[str, Any]) -> None:
    """Identical scripts/shaders shipped by several assets (fixed-GUID NpcData.cs, per-export shader copies) keep one
    copy; references to a dropped copy's GUID are pointed at the kept one."""
    kept: dict[tuple[str, str], Path] = {}
    for p in sorted(pkg.rglob("*"), key=lambda q: q.as_posix()):
        if not p.is_file() or p.suffix.lower() not in _DEDUPE_EXT:
            continue
        key = (p.name.lower(), _sha(p))
        first = kept.get(key)
        if first is None:
            kept[key] = p
            continue
        meta, first_meta = p.with_name(p.name + ".meta"), first.with_name(first.name + ".meta")
        g_old = _meta_guid(meta) if meta.is_file() else None
        if g_old and not first_meta.is_file():
            shutil.move(meta, first_meta)
        else:
            g_keep = _meta_guid(first_meta) if first_meta.is_file() else None
            if g_old and g_keep and g_old != g_keep:
                _replace_guid(pkg, g_old, g_keep)
            meta.unlink(missing_ok=True)
        p.unlink()
        report["deduped"].append(_rel(p, pkg))


def _hoist_scripts(pkg: Path, runtime: Path, report: dict[str, Any]) -> None:
    """C# in a package compiles only under an asmdef, so every script moves to Runtime/Scripts or Editor/."""
    for p in sorted(runtime.rglob("*.cs"), key=lambda q: q.as_posix()):
        editor = "Editor" in p.relative_to(runtime).parts[:-1]
        dst_dir = pkg / "Editor" if editor else runtime / "Scripts"
        dst = dst_dir / p.name
        if dst == p:
            continue
        n = 2
        while dst.exists():
            dst, n = dst_dir / f"{p.stem}_{n}{p.suffix}", n + 1
            report["warnings"].append(f"script name clash: {p.name} kept as {dst.name} (check duplicate classes)")
        dst_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(p, dst)
        meta = p.with_name(p.name + ".meta")
        if meta.is_file():
            shutil.move(meta, dst.with_name(dst.name + ".meta"))
    for d in sorted((q for q in runtime.rglob("*") if q.is_dir()), key=lambda q: -len(q.parts)):
        if not any(d.iterdir()):
            d.rmdir()


def _fix_collisions(pkg: Path, runtime: Path, report: dict[str, Any]) -> None:
    owners: dict[str, Path] = {}
    for meta in sorted(pkg.rglob("*.meta"), key=lambda q: q.as_posix()):
        g = _meta_guid(meta)
        if g is None:
            continue
        if g not in owners:
            owners[g] = meta
            continue
        new = stable_hex("pack-remap", _rel(meta, pkg))
        rel = meta.relative_to(runtime).parts if meta.is_relative_to(runtime) else ()
        # References to a colliding asset live inside its own Runtime/<asset>/ folder.
        if len(rel) > 1:
            _replace_guid(runtime / rel[0], g, new)
        else:
            meta.write_text(meta.read_text(encoding="utf-8").replace(g, new), encoding="utf-8", newline="")
        owners[new] = meta
        report["remapped"].append(_rel(meta, pkg))


_GUID_REF = re.compile(r"(guid: )([0-9a-f]{32})")


def _scope_guids(pkg: Path, package: str) -> None:
    """Export GUIDs derive from (step, asset), so the same asset exported into Assets/ and installed from the pack
    (and the fixed-GUID NpcData.cs) collide; Unity then rebinds one copy's references to the other. Re-key every
    GUID the package owns by package name — stable across pack versions, untouched GUIDs (Unity/package scripts)."""
    owned = {g for m in pkg.rglob("*.meta") if (g := _meta_guid(m))}
    remap = {g: stable_hex("pack-guid", package, g) for g in owned}
    for p in pkg.rglob("*"):
        if not (p.is_file() and p.suffix in _TEXT_EXT):
            continue
        try:
            t = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        new = _GUID_REF.sub(lambda m: m[1] + remap.get(m[2], m[2]), t)
        if new != t:
            p.write_text(new, encoding="utf-8", newline="")


def _meta_text(p: Path, g: str) -> str:
    head = f"fileFormatVersion: 2\nguid: {g}\n"
    tail = "  userData: \n  assetBundleName: \n  assetBundleVariant: \n"
    if p.is_dir():
        return head + "folderAsset: yes\nDefaultImporter:\n  externalObjects: {}\n" + tail
    ext = p.suffix.lower()
    if ext == ".cs":
        return (head + "MonoImporter:\n  externalObjects: {}\n  serializedVersion: 2\n  defaultReferences: []\n"
                "  executionOrder: 0\n  icon: {instanceID: 0}\n" + tail)
    if ext == ".asmdef":
        return head + "AssemblyDefinitionImporter:\n  externalObjects: {}\n" + tail
    if ext in (".md", ".json", ".txt"):
        return head + "TextScriptImporter:\n  externalObjects: {}\n" + tail
    if ext in (".png", ".jpg", ".jpeg", ".webp", ".tga"):
        from studio_core.export.unity import _image_size, texture_meta

        try:
            return texture_meta(p.stem, g, None, _image_size(p), 100.0, False)
        except OSError:
            return head
    return head


def _write_metas(pkg: Path, name: str) -> None:
    for p in sorted(pkg.rglob("*"), key=lambda q: q.as_posix()):
        rel = p.relative_to(pkg)
        if p.suffix == ".meta" or any(part.endswith("~") or part.startswith(".") for part in rel.parts):
            continue
        meta = p.with_name(p.name + ".meta")
        if not meta.exists():
            meta.write_text(_meta_text(p, stable_hex("pack-meta", name, rel.as_posix())), encoding="utf-8")


def _asmdef(name: str, editor: bool, refs: list[str]) -> str:
    doc: dict[str, Any] = {"name": name, "rootNamespace": "", "references": refs,
                           "includePlatforms": ["Editor"] if editor else [], "excludePlatforms": [],
                           "allowUnsafeCode": False, "overrideReferences": False, "precompiledReferences": [],
                           "autoReferenced": True, "defineConstraints": [], "versionDefines": [],
                           "noEngineReferences": False}
    return json.dumps(doc, indent=4) + "\n"


def finalize_unity(pkg: Path, package: str, asm: str) -> dict[str, Any]:
    report: dict[str, Any] = {"deduped": [], "remapped": [], "warnings": [], "asmdefs": []}
    runtime = pkg / "Runtime"
    _dedupe_shared(pkg, report)
    _hoist_scripts(pkg, runtime, report)
    has_runtime = any(runtime.rglob("*.cs"))
    if has_runtime:
        (runtime / f"{asm}.asmdef").write_text(_asmdef(asm, False, []), encoding="utf-8")
        report["asmdefs"].append(f"Runtime/{asm}.asmdef")
    if any((pkg / "Editor").rglob("*.cs")):
        (pkg / "Editor" / f"{asm}.Editor.asmdef").write_text(
            _asmdef(f"{asm}.Editor", True, [asm] if has_runtime else []), encoding="utf-8")
        report["asmdefs"].append(f"Editor/{asm}.Editor.asmdef")
    _fix_collisions(pkg, runtime, report)
    _scope_guids(pkg, package)
    _write_metas(pkg, package)
    report["guids"] = check_guids(pkg)
    return report


def check_guids(pkg: Path) -> int:
    seen: dict[str, str] = {}
    for meta in pkg.rglob("*.meta"):
        if any(part.endswith("~") for part in meta.relative_to(pkg).parts):
            continue
        g = _meta_guid(meta)
        if g is None:
            raise PackError(f"meta without a guid: {_rel(meta, pkg)}")
        if g in seen:
            raise PackError(f"duplicate GUID {g}: {seen[g]} and {_rel(meta, pkg)}")
        seen[g] = _rel(meta, pkg)
    return len(seen)


# ---- documents ----

_LICENSE_SPDX = {"cc0": "CC0-1.0", "cc-by-4.0": "CC-BY-4.0", "proprietary": "SEE LICENSE IN LICENSE.md"}


def license_text(kind: str, author: str, year: int, title: str) -> str:
    who = author or "the author"
    if kind == "cc0":
        return (f"# CC0 1.0 Universal\n\n{title}\n\nTo the extent possible under law, {who} has waived all copyright "
                "and related or neighboring rights to this work. This work is published from the author's "
                "jurisdiction.\n\nFull legal code: https://creativecommons.org/publicdomain/zero/1.0/legalcode\n")
    if kind == "cc-by-4.0":
        return (f"# Creative Commons Attribution 4.0 International (CC BY 4.0)\n\nCopyright (c) {year} {who}\n\n"
                f"{title} is licensed under CC BY 4.0. You may share and adapt it for any purpose, even "
                f"commercially, as long as you give appropriate credit to {who}, provide a link to the license and "
                "indicate if changes were made.\n\n"
                "Full legal code: https://creativecommons.org/licenses/by/4.0/legalcode\n")
    return (f"# License\n\nCopyright (c) {year} {who}. All rights reserved.\n\n"
            "PLACEHOLDER — replace this file with your own terms before distributing. Until then no permission is "
            f"granted to use, copy, modify or distribute {title} beyond what applicable law allows.\n")


def _changes_md(version: str, date: str, ch: dict[str, list[str]]) -> str:
    out = [f"## [{version}] - {date}"]
    for title, key in (("Added", "added"), ("Changed", "updated"), ("Removed", "removed")):
        if ch.get(key):
            out += ["", f"### {title}", *[f"- {n}" for n in ch[key]]]
    if len(out) == 1:
        out += ["", "No asset changes."]
    return "\n".join(out)


def changelog(entries: list[dict[str, Any]]) -> str:
    """Newest first, one section per pack ever built for the project."""
    parts = ["# Changelog", "", "All notable changes to this asset pack. Format: https://keepachangelog.com/", ""]
    for e in entries:
        date = time.strftime("%Y-%m-%d", time.localtime(e.get("created_at") or 0))
        parts += [_changes_md(str(e["version"]), date, e.get("changes") or {}), ""]
    return "\n".join(parts)


def diff_assets(prev: dict[str, str], cur: dict[str, str]) -> dict[str, list[str]]:
    return {"added": sorted(set(cur) - set(prev)), "removed": sorted(set(prev) - set(cur)),
            "updated": sorted(k for k in set(cur) & set(prev) if cur[k] != prev[k])}


def readme(title: str, version: str, desc: str, pkg_name: str, addon: str, engines: list[str], lic: str,
           prov: list[dict[str, Any]], plugin: bool) -> str:
    lines = [f"# {title}", "", f"Version {version}. {desc}".strip(), ""]
    if "unity" in engines:
        lines += ["## Unity (6000.0+)", "",
                  f"Package `{pkg_name}`. Window > Package Manager > + > *Add package from disk…* and pick "
                  f"`unity/{pkg_name}/package.json`, or copy the `{pkg_name}` folder into your project's `Packages/`.",
                  ""]
    if "godot" in engines:
        lines += ["## Godot 4", "", f"Copy `godot/addons/{addon}/` into your project's `addons/` folder."
                  + (" Enable it under Project Settings > Plugins." if plugin else ""), ""]
    lines += ["## Assets", "", "| Asset | Recipe | Commercial use of models |", "|---|---|---|"]
    lines += [f"| {p['folder']} | {p.get('recipe_id') or '-'} | {p['commercial']} |" for p in prov]
    flagged = [p for p in prov if p["commercial"] != "yes"]
    lines += ["", "## License", "", f"Assets: {_LICENSE_SPDX[lic]} (see LICENSE.md)."]
    if flagged:
        lines += ["", "**Check before commercial use:** some assets were made with models whose license is "
                  "non-commercial, conditional or unverified (details per asset in provenance.json):", ""]
        for p in flagged:
            ms = ", ".join(f"{m['name']} ({m['license']})" for m in p["models"] if m["commercial"] != "yes")
            lines.append(f"- {p['folder']}: {p['commercial']} — {ms}")
    lines += ["", f"Generated by AI Asset Studio {__version__}. provenance.json lists the recipe, models, prompts "
              "and seeds behind every asset.", ""]
    return "\n".join(lines)


# ---- build ----

def _raw_copy(a: PackAsset, blobs: BlobStore, dst: Path) -> list[str]:
    dst.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    out = []
    for v in a.versions:
        name = safe_filename(None, v.blob)
        stem, ext = os.path.splitext(name)
        n = 2
        while name.lower() in seen:
            name, n = f"{stem}_{n}{ext}", n + 1
        seen.add(name.lower())
        shutil.copyfile(blobs.path(v.blob.hash), dst / name)
        out.append(name)
    return out


def _export_one(a: PackAsset, blobs: BlobStore, staging: Path, folder: str, engine: str, opts: EngineOptions,
                warnings: list[str]) -> Path | None:
    if not engine_ready(a.step, blobs):
        return None
    args = (a.step, a.siblings, blobs, staging, folder, engine, opts.ppu, opts.pixel, opts.collider(a))
    try:
        out = export_engine(*args, opts.shaders, opts.normal2d)
    except BundleError as e:
        if not opts.shaders:
            warnings.append(f"{folder} ({engine}): {e}; raw files packed instead")
            return None
        out = export_engine(*args, None, opts.normal2d)
    return Path(out["dir"])


def _zip(src: Path, dst: Path) -> None:
    tmp = dst.with_name(f".{dst.name}.{uuid.uuid4().hex[:8]}.tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in sorted(src.rglob("*"), key=lambda q: q.as_posix()):
            if p.is_file():
                z.write(p, f"{src.name}/{_rel(p, src)}")
    os.replace(tmp, dst)


def _replace_dir(tmp: Path, dst: Path) -> None:
    if dst.exists():
        old = dst.with_name(f".{dst.name}.{uuid.uuid4().hex[:8]}.old")
        os.replace(dst, old)
        shutil.rmtree(old, ignore_errors=True)
    os.replace(tmp, dst)


def build_pack(assets: list[PackAsset], blobs: BlobStore, opts: PackOptions, project_id: str, project_name: str,
               data_dir: Path) -> dict[str, Any]:
    if not assets:
        raise PackError("no approved assets to pack (approve versions first or check the include list)")
    engines = [e for e in PACK_ENGINES if e in opts.engines]
    if not engines:
        raise PackError(f"pick at least one engine ({', '.join(PACK_ENGINES)})")
    if opts.license not in LICENSES:
        raise PackError(f"license must be one of {', '.join(LICENSES)}")
    history = load_history(data_dir, project_id)
    version = opts.version.strip() if opts.version else next_version(history)
    parse_semver(version)
    slug = slugify(project_name, project_id[:12] or "pack")
    pkg_name = package_name(opts.org, slug)
    addon = slug.replace("-", "_")
    out_root = Path(opts.out_dir) if opts.out_dir else data_dir / "packs"
    if not out_root.is_absolute():
        raise PackError("out_dir must be an absolute path")
    out_root.mkdir(parents=True, exist_ok=True)
    final = out_root / f"{slug}-{version}"
    work = out_root / f".{final.name}.{uuid.uuid4().hex[:8]}.tmp"
    # Engine exports nest deeply; a short system temp root keeps them under Windows' 260-char path limit.
    staging = Path(tempfile.mkdtemp(prefix="pack-"))
    now = time.time()
    warnings: list[str] = []
    try:
        folders, used = [], set()
        for a in assets:
            f = folder_name(a.name, "asset")
            base, n = f, 2
            while f.lower() in used:
                f, n = f"{base}_{n}", n + 1
            used.add(f.lower())
            folders.append(f)
        prov = [asset_provenance(a, f) for a, f in zip(assets, folders)]
        files: dict[str, dict[str, list[str]]] = {f: {} for f in folders}
        result: dict[str, Any] = {}

        roots: dict[str, Path] = {}
        if "unity" in engines:
            roots["unity"] = work / "unity" / pkg_name / "Runtime"
        if "godot" in engines:
            roots["godot"] = work / "godot" / "addons" / addon
        for engine, base_dir in roots.items():
            for a, f in zip(assets, folders):
                dst = base_dir / f
                src = _export_one(a, blobs, staging / engine, f, engine, opts.export, warnings)
                if src is None:
                    _raw_copy(a, blobs, dst)
                else:
                    shutil.copytree(src, dst)
                files[f][engine] = sorted(_rel(p, dst) for p in dst.rglob("*") if p.is_file())
        shutil.rmtree(staging, ignore_errors=True)

        for p in prov:
            p["files"] = files[p["folder"]]
        flagged = {"non_commercial": [p["folder"] for p in prov if p["commercial"] == "no"],
                   "restricted": [p["folder"] for p in prov if p["commercial"] == "restricted"],
                   "unverified": [p["folder"] for p in prov if p["commercial"] == "unknown"]}
        digests = {p["folder"]: p["digest"] for p in prov}
        prev = history[-1]["assets"] if history and isinstance(history[-1].get("assets"), dict) else {}
        changes = diff_assets(prev, digests)
        entry = {"version": version, "created_at": now, "engines": engines, "license": opts.license,
                 "package": pkg_name, "assets": digests, "changes": changes,
                 "commercial": worst([p["commercial"] for p in prov])}
        others = [h for h in history if h.get("version") != version]
        log_entries = sorted([entry, *others], key=lambda h: parse_semver(str(h["version"])) if _SEMVER.match(
            str(h.get("version", ""))) else (0, 0, 0), reverse=True)

        title = project_name or slug
        provenance = {"format": "asset-studio-provenance/1", "generator": f"AI Asset Studio {__version__}",
                      "project": {"id": project_id, "name": project_name},
                      "pack": {"name": pkg_name, "version": version, "created_at": now,
                               "license": _LICENSE_SPDX[opts.license]},
                      "summary": {"commercial": entry["commercial"], **flagged}, "assets": prov}
        docs = {
            "README.md": readme(title, version, opts.description, pkg_name, addon, engines, opts.license, prov,
                                False),
            "CHANGELOG.md": changelog(log_entries),
            "LICENSE.md": license_text(opts.license, opts.author, time.localtime(now).tm_year, title),
            "provenance.json": json.dumps(provenance, indent=1, ensure_ascii=False) + "\n",
        }

        if "unity" in engines:
            pkg = work / "unity" / pkg_name
            pkg.mkdir(parents=True, exist_ok=True)
            manifest = {"name": pkg_name, "version": version, "displayName": title,
                        "description": opts.description or f"{title}: game assets made with AI Asset Studio.",
                        "unity": "6000.0", "license": _LICENSE_SPDX[opts.license],
                        "keywords": ["assets", "2d", "asset-studio"], "dependencies": {}}
            if opts.author:
                manifest["author"] = {"name": opts.author}
            (pkg / "package.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                                              encoding="utf-8")
            for n, text in docs.items():
                (pkg / n).write_text(text, encoding="utf-8")
            report = finalize_unity(pkg, pkg_name, _pascal(opts.org) + "." + _pascal(slug))
            warnings += report["warnings"]
            result["unity"] = {"dir": str(final / "unity" / pkg_name), "package": manifest, **report}
        if "godot" in engines:
            groot = work / "godot"
            addon_dir = groot / "addons" / addon
            plugin = any(addon_dir.rglob("*.gd"))
            if plugin:
                (addon_dir / "plugin.cfg").write_text(
                    f'[plugin]\n\nname="{title}"\ndescription="{(opts.description or title).replace(chr(34), "")}"\n'
                    f'author="{opts.author.replace(chr(34), "")}"\nversion="{version}"\nscript="plugin.gd"\n',
                    encoding="utf-8")
                (addon_dir / "plugin.gd").write_text("@tool\nextends EditorPlugin\n", encoding="utf-8")
            docs_g = {**docs, "README.md": readme(title, version, opts.description, pkg_name, addon, engines,
                                                  opts.license, prov, plugin)}
            for n, text in docs_g.items():
                (groot / n).write_text(text, encoding="utf-8")
            (addon_dir / "LICENSE.md").write_text(docs["LICENSE.md"], encoding="utf-8")
            (addon_dir / "provenance.json").write_text(docs["provenance.json"], encoding="utf-8")
            result["godot"] = {"dir": str(final / "godot"), "addon": f"addons/{addon}", "plugin": plugin}
        (work / "provenance.json").write_text(docs["provenance.json"], encoding="utf-8")
        (work / "README.md").write_text(docs["README.md"], encoding="utf-8")
        (work / "CHANGELOG.md").write_text(docs["CHANGELOG.md"], encoding="utf-8")
        _replace_dir(work, final)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        if work.exists():
            shutil.rmtree(work, ignore_errors=True)

    zip_path = out_root / f"{final.name}.zip"
    if opts.zip:
        _zip(final, zip_path)
    entry.update({"dir": str(final.resolve()), "zip": str(zip_path.resolve()) if opts.zip else None})
    save_history(data_dir, project_id, [*others, entry])
    return {**entry, "previous": history[-1]["version"] if history else None, "warnings": warnings,
            "provenance": provenance["summary"], **result,
            "items": [{"name": p["name"], "folder": p["folder"], "commercial": p["commercial"]} for p in prov]}
