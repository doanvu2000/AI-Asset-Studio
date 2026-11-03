"""Game project audit (Unity / Godot): textures vs. mobile budget and on-screen size, duplicate images, assets nothing
references, heavy meshes and audio import settings; one-click fixes that only rewrite importer files (.meta /
.import) with a zip backup, a manifest and undo. Images, scenes and GUIDs/UIDs are never written."""
from __future__ import annotations

import json
import os
import re
import time
import uuid
import zipfile
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

import numpy as np
import yaml
from PIL import Image

from studio_core.export import mobile
from studio_core.export.audit_media import MeshError, audio_seconds, mesh_tris
from studio_core.export.restyle import SKIP_DIRS, _is_link, file_hash, project_root
from studio_core.scan_jobs import ScanCancelled

REPORT_TYPE = "asset-studio/audit"
FIX_TYPE = "asset-studio/audit-fix"
TEXTURE_EXTS = (".png", ".jpg", ".jpeg", ".tga", ".psd", ".tif", ".tiff", ".bmp", ".webp", ".gif", ".exr", ".hdr")
MESH_EXTS = (".fbx", ".glb", ".gltf", ".obj")
AUDIO_EXTS = (".wav", ".ogg", ".mp3", ".aif", ".aiff", ".flac")
UNITY_REF_EXTS = (".unity", ".prefab", ".asset", ".mat", ".controller", ".overridecontroller", ".anim", ".playable",
                  ".spriteatlas", ".spriteatlasv2", ".mixer", ".guiskin", ".fontsettings", ".preset", ".lighting",
                  ".rendertexture", ".cubemap", ".flare", ".signal", ".physicmaterial", ".physicsmaterial2d",
                  ".terrainlayer", ".brush", ".shadergraph", ".shadersubgraph", ".vfx", ".uss", ".uxml", ".tss",
                  ".meta", ".asmdef", ".json")
GODOT_REF_EXTS = (".tscn", ".tres", ".gd", ".cs", ".gdshader", ".godot", ".cfg", ".json", ".escn", ".res")
# Folders whose assets are loaded by path/name at runtime or belong to tooling, so "nothing references it" means
# nothing there.
UNITY_DYNAMIC = {"resources", "streamingassets", "editor", "plugins", "gizmos", "editor default resources",
                 "addressableassetsdata", "textmesh pro", "settings"}
GODOT_DYNAMIC = {"addons", ".godot"}
MAX_FILES = 20000
MAX_HASH_SIDE = 8192
MAX_REF_BYTES = 32 << 20
DEFAULT_TRI_BUDGET = 15000
NEAR_PHASH = 4
NEAR_DHASH = 6
LONG_CLIP_S = 10.0
PCM_CLIP_S = 2.0
BIG_WAV = 1 << 20
# Display-size check: keep 2x the largest on-screen size (retina / camera zoom); below that the texture is wasted.
DISPLAY_HEADROOM = 2
# UnityEditor.TextureImporterFormat -> bytes per pixel on device (ASTC block sizes from export/mobile.py).
UNITY_BPP = {4: 4.0, 3: 3.0, 5: 4.0, 7: 2.0, 13: 2.0, 1: 1.0, 63: 1.0, 34: 0.5, 45: 0.5, 46: 0.5, 47: 1.0,
             48: 1.0, 49: 16 / 25, 50: 16 / 36, 51: 16 / 64, 52: 16 / 100, 53: 16 / 144}
# Unity 6 picks ASTC 6x6 for a compressed texture on Android without an override (Player > Texture compression).
UNITY_ANDROID_DEFAULT_BPP = 16 / 36
FIX_TYPES = ("mobile_preset", "max_size", "drop_alpha")
# TextureImporterType cookie, lightmap, single channel, directional lightmap, shadowmask: data, not art.
DATA_TEXTURE_TYPES = {4, 6, 10, 11, 12}
MAX_NEAR_DUP = 3000
# CLIP on CPU runs ~20 ms per image; past this many candidates the scan would stall, so pHash alone decides.
MAX_CLIP_DUP = 1500
CLIP_EMBED_CHUNK = 64
# Measured on a Unity UI kit: different panels of one kit sit at cosine 0.95-0.97, so CLIP alone needs a higher
# bar than the Library's "find similar" (0.95); pHash-close pairs only need CLIP not to disagree (recolours).
CLIP_CONFIRM = 0.9
CLIP_ONLY = 0.985
BINARY_CAPABLE = (".unity", ".prefab", ".asset", ".mat", ".controller", ".anim", ".playable", ".mixer",
                  ".spriteatlas", ".spriteatlasv2", ".terrainlayer", ".lighting")
HIGH_DEPTH_MODES = ("I;16", "I;16B", "I;16L", "I", "F")
DATA_STEM_WORDS = ("height", "splat", "_mask", "flowmap", "_lut", "lightmap", "shadowmask", "_sdf")


class AuditError(Exception):
    pass


def backup_dir(data_dir: Path) -> Path:
    return data_dir / "audit_backups"


def _walk(root: Path, skip: set[str]) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        d = Path(dirpath)
        dirnames[:] = sorted(n for n in dirnames if n.lower() not in skip and not n.startswith(".")
                             and not n.endswith("~") and not _is_link(d / n))
        for n in sorted(filenames):
            yield d / n


def _pot(n: int) -> bool:
    return n > 0 and n & (n - 1) == 0


def _next_pot(n: int) -> int:
    p = 32
    while p < n:
        p *= 2
    return p


def _fit(w: int, h: int, limit: int | None) -> tuple[int, int]:
    return mobile.fit((w, h), limit) if limit and limit > 0 else (w, h)


def _lf(text: str) -> tuple[str, bool]:
    return text.replace("\r\n", "\n"), "\r\n" in text


def _load_yaml(text: str) -> dict[str, Any]:
    try:
        doc = yaml.load(text, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
    except yaml.YAMLError:
        return {}
    return doc if isinstance(doc, dict) else {}


def _int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def unity_texture_settings(meta: dict[str, Any]) -> dict[str, Any]:
    ti = meta.get("TextureImporter") or {}
    plats = {str(p.get("buildTarget")): p for p in (ti.get("platformSettings") or []) if isinstance(p, dict)}
    default = plats.get("DefaultTexturePlatform") or {}
    android = plats.get("Android") or {}
    override = _int(android.get("overridden")) == 1
    alpha_key = "alphaUsage" if "alphaUsage" in ti else "alphaSource"
    return {
        "max": _int(default.get("maxTextureSize") or ti.get("maxTextureSize"), 2048),
        "compressed": _int(default.get("textureCompression"), 1) != 0,
        "override": override,
        "override_max": _int(android.get("maxTextureSize"), 0) if override else None,
        "override_format": _int(android.get("textureFormat"), -1) if override else None,
        "mips": _int((ti.get("mipmaps") or {}).get("enableMipMap"), 0) == 1,
        "texture_type": _int(ti.get("textureType"), 0),
        "shape": _int(ti.get("textureShape"), 1),
        "sprite_mode": _int(ti.get("spriteMode"), 0),
        "alpha_usage": _int(ti.get(alpha_key), 1),
        "filter": _int((ti.get("textureSettings") or {}).get("filterMode"), 1),
    }


def _unity_gpu(w: int, h: int, s: dict[str, Any]) -> float:
    if s["override"]:
        iw, ih = _fit(w, h, min(s["override_max"] or s["max"], s["max"]) if s["override_max"] else s["max"])
        bpp = UNITY_BPP.get(s["override_format"], UNITY_ANDROID_DEFAULT_BPP if s["compressed"] else 4.0)
    else:
        iw, ih = _fit(w, h, s["max"])
        bpp = UNITY_ANDROID_DEFAULT_BPP if s["compressed"] else 4.0
    return iw * ih * bpp * (mobile.MIP_FACTOR if s["mips"] else 1)


_KV = re.compile(r"^([\w/]+)\s*=\s*(.*)$")


def parse_import(text: str) -> dict[str, dict[str, str]]:
    sections: dict[str, dict[str, str]] = {}
    cur: dict[str, str] = {}
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            cur = sections.setdefault(s[1:-1], {})
            continue
        m = _KV.match(s)
        if m:
            cur[m.group(1)] = m.group(2).strip().strip('"')
    return sections


def godot_texture_settings(sections: dict[str, dict[str, str]]) -> dict[str, Any]:
    p = sections.get("params", {})
    remap = sections.get("remap", {})
    return {"importer": remap.get("importer", ""), "uid": remap.get("uid", ""),
            "mode": _int(p.get("compress/mode"), 0), "hq": p.get("compress/high_quality") == "true",
            "size_limit": _int(p.get("process/size_limit"), 0), "mips": p.get("mipmaps/generate") == "true",
            "normal": p.get("compress/normal_map") == "1"}


def _godot_gpu(w: int, h: int, s: dict[str, Any]) -> float:
    iw, ih = _fit(w, h, s["size_limit"])
    # Lossless and lossy (WebP) both upload RGBA8; VRAM / Basis are ~1 byte per pixel (ETC2 RGBA8, ASTC 4x4, BPTC).
    bpp = 4.0 if s["mode"] in (0, 1) else 1.0
    return iw * ih * bpp * (mobile.MIP_FACTOR if s["mips"] else 1)


def _image_probe(p: Path, want_hash: bool) -> dict[str, Any]:
    from studio_core.engine.similar import hashes

    with Image.open(p) as im:
        w, h = im.size
        info: dict[str, Any] = {"w": w, "h": h, "alpha_channel": False, "alpha_used": None, "phash": None,
                                "dhash": None}
        bands = im.getbands()
        has_alpha = "A" in bands or "transparency" in im.info
        info["alpha_channel"] = has_alpha
        info["data"] = im.mode in HIGH_DEPTH_MODES
        if max(w, h) > MAX_HASH_SIDE or im.format in ("PSD",):
            return info
        im.load()
        if info["data"]:
            arr = np.asarray(im, np.float64)
            span = max(float(arr.max() - arr.min()), 1.0)
            im = Image.fromarray(((arr - arr.min()) / span * 255).astype(np.uint8), "L")
        if has_alpha:
            info["alpha_used"] = im.convert("RGBA").getchannel("A").getextrema()[0] < 255
        if want_hash:
            info["phash"], info["dhash"] = hashes(im)
    return info


def _issue(code: str, severity: str, msg: str, fix: dict[str, Any] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"code": code, "severity": severity, "msg": msg}
    if fix:
        out["fix"] = fix
    return out


def _mb(n: float) -> str:
    return f"{n / 1048576:.2f} MB"


def _texture_kind(rel: str, meta_text: str | None, settings: dict[str, Any], engine: str) -> str | None:
    """Budget kind, or None for data textures a mobile preset must not touch (lightmaps, cookies, cubemaps,
    heightmaps, splat / mask maps...)."""
    stem = PurePosixPath(rel.lower()).stem
    if settings.get("data") or any(w in stem for w in DATA_STEM_WORDS):
        return None
    if engine == "unity":
        if settings.get("texture_type") in DATA_TEXTURE_TYPES or settings.get("shape", 1) != 1:
            return None
        pixel = settings.get("filter") == 0
        return mobile.classify(rel, meta_text, pixel)
    if settings.get("importer") not in ("texture", ""):
        return None
    low = rel.lower()
    if settings.get("normal") or PurePosixPath(low).stem.endswith(("_normal", "_n")):
        return "normal"
    return mobile.classify(rel, None, "pixel" in low)


def _budget(kind: str, max_size: int | None = None) -> mobile.Budget:
    b = replace(mobile.BUDGETS[kind], mipmaps=None)
    return replace(b, max_size=min(b.max_size, max_size)) if max_size else b


def _after_gpu(engine: str, w: int, h: int, s: dict[str, Any], b: mobile.Budget) -> float:
    if engine == "unity":
        limit = min(b.max_size, s["max"])
        iw, ih = _fit(w, h, limit)
        n = mobile.astc_bytes(iw, ih, b.astc) if b.astc else iw * ih * 4
        return n * (mobile.MIP_FACTOR if s["mips"] else 1)
    limit = min(b.max_size, s["size_limit"]) if s["size_limit"] else b.max_size
    iw, ih = _fit(w, h, limit)
    bpp = 4.0 if b.godot_mode == 0 else 1.0
    return iw * ih * bpp * (mobile.MIP_FACTOR if s["mips"] else 1)


def _unity_displays(files: list[Path]) -> dict[str, list[tuple[float, float] | None]]:
    """sprite texture guid -> on-screen sizes of uGUI Images showing it (None = stretched, size unknown)."""
    out: dict[str, list[tuple[float, float] | None]] = {}
    for f in files:
        if f.suffix.lower() not in (".prefab", ".unity"):
            continue
        try:
            if f.stat().st_size > MAX_REF_BYTES:
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rects: dict[str, tuple[float, float] | None] = {}
        for m in re.finditer(r"--- !u!224 &\d+[^\n]*\nRectTransform:\n(.*?)(?=\n--- |\Z)", text, re.S):
            body = m.group(1)
            go = re.search(r"m_GameObject: \{fileID: (-?\d+)\}", body)
            amin = re.search(r"m_AnchorMin: \{x: ([-\d.e]+), y: ([-\d.e]+)\}", body)
            amax = re.search(r"m_AnchorMax: \{x: ([-\d.e]+), y: ([-\d.e]+)\}", body)
            size = re.search(r"m_SizeDelta: \{x: ([-\d.e]+), y: ([-\d.e]+)\}", body)
            scale = re.search(r"m_LocalScale: \{x: ([-\d.e]+), y: ([-\d.e]+)", body)
            if not (go and amin and amax and size):
                continue
            if amin.groups() != amax.groups():
                rects[go.group(1)] = None
                continue
            sx, sy = (abs(float(v)) for v in scale.groups()) if scale else (1.0, 1.0)
            rects[go.group(1)] = (abs(float(size.group(1))) * sx, abs(float(size.group(2))) * sy)
        for m in re.finditer(r"--- !u!114 &\d+[^\n]*\nMonoBehaviour:\n(.*?)(?=\n--- |\Z)", text, re.S):
            body = m.group(1)
            sp = re.search(r"m_Sprite: \{fileID: -?\d+, guid: ([0-9a-f]{32})", body)
            go = re.search(r"m_GameObject: \{fileID: (-?\d+)\}", body)
            if sp and go and go.group(1) in rects:
                out.setdefault(sp.group(1), []).append(rects[go.group(1)])
    return out


def _godot_displays(proj: Path, files: list[Path]) -> dict[str, list[tuple[float, float] | None]]:
    """res:// texture -> Sprite2D scales (fractions of the texture size; region / frame sprites are left out)."""
    out: dict[str, list[tuple[float, float] | None]] = {}
    for f in files:
        if f.suffix.lower() != ".tscn":
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        ids = {}
        for line in re.findall(r"^\[ext_resource [^\n]*\]", text, re.M):
            attrs = dict(re.findall(r'(\w+)="([^"]*)"', line))
            if attrs.get("id") and attrs.get("path"):
                path = attrs["path"]
                ids[attrs["id"]] = path if path.startswith("res://") else (_res(proj, f, path) or path)
        for block in re.split(r"\n(?=\[node )", text):
            if not block.startswith("[node ") or 'type="Sprite2D"' not in block.split("\n", 1)[0]:
                continue
            tex = re.search(r'^texture = ExtResource\("([^"]+)"\)', block, re.M)
            if not tex or tex.group(1) not in ids or re.search(r"^(hframes|vframes|region_enabled) = ", block, re.M):
                continue
            sc = re.search(r"^scale = Vector2\(([-\d.e]+), ([-\d.e]+)\)", block, re.M)
            out.setdefault(ids[tex.group(1)], []).append(
                (abs(float(sc.group(1))), abs(float(sc.group(2)))) if sc else (1.0, 1.0))
    return out


_GUID = re.compile(rb"guid: ?([0-9a-f]{32})|guid=([0-9a-f]{32})")
_RES = re.compile(rb'res://[^"\'\s)\]]+')
_UID = re.compile(rb"uid://[0-9a-z]+")
_REL_PATH = re.compile(rb'(?<![\w/])path="([^"]+)"')


def _unity_refs(files: list[Path]) -> tuple[set[str], list[bytes]]:
    """Every GUID a project file mentions (other than a .meta's own guid), plus the bytes of binary-serialized
    assets, where GUIDs are stored raw and are matched with binary_guid()."""
    seen: set[str] = set()
    binary: list[bytes] = []
    for f in files:
        try:
            if f.stat().st_size > MAX_REF_BYTES:
                continue
            data = f.read_bytes()
        except OSError:
            continue
        low = f.name.lower()
        if low.endswith(BINARY_CAPABLE) and data[:5] != b"%YAML" and data[:1] not in (b"{", b"["):
            binary.append(data)
            continue
        own = None
        if low.endswith(".meta"):
            m = re.search(rb"^guid: ([0-9a-f]{32})", data, re.M)
            own = m.group(1).decode() if m else None
        for m in _GUID.finditer(data):
            g = (m.group(1) or m.group(2)).decode()
            if g != own:
                seen.add(g)
    return seen, binary


def binary_guid(g: str) -> bytes:
    """Unity prints each GUID byte low nibble first, so the raw bytes are the hex string with each pair swapped."""
    return bytes.fromhex("".join(g[i + 1] + g[i] for i in range(0, 32, 2)))


def _res(proj: Path, base: Path, ref: str) -> str | None:
    """A relative path written in `base` (ext_resource path, glTF image uri) as res://, if it stays in the project."""
    try:
        p = (base.parent / ref.replace("%20", " ")).resolve()
        return "res://" + p.relative_to(proj.resolve()).as_posix()
    except (ValueError, OSError):
        return None


def _godot_refs(proj: Path, files: list[Path]) -> tuple[set[str], set[str]]:
    paths: set[str] = set()
    uids: set[str] = set()
    for f in files:
        try:
            if f.stat().st_size > MAX_REF_BYTES:
                continue
            data = f.read_bytes()
        except OSError:
            continue
        paths.update(m.group(0).decode("utf-8", "replace") for m in _RES.finditer(data))
        own = _UID.search(data.split(b"\n", 1)[0]) if f.suffix.lower() in (".tscn", ".tres") else None
        uids.update(m.group(0).decode() for m in _UID.finditer(data) if not own or m.start() != own.start())
        if f.suffix.lower() in (".tscn", ".tres"):
            for m in _REL_PATH.finditer(data):
                ref = m.group(1).decode("utf-8", "replace")
                if not ref.startswith(("res://", "uid://", "user://")):
                    paths.add(_res(proj, f, ref) or "")
    return paths, uids


def _model_refs(proj: Path, models: list[Path]) -> set[str]:
    """res:// of the external images a .gltf/.glb points at (embedded or data: images need nothing)."""
    out: set[str] = set()
    for p in models:
        if p.suffix.lower() not in (".gltf", ".glb"):
            continue
        try:
            data = p.read_bytes() if p.stat().st_size <= MAX_REF_BYTES * 4 else b""
            if not data:
                continue
            if p.suffix.lower() == ".glb":
                n = int.from_bytes(data[12:16], "little")
                doc = json.loads(data[20:20 + n])
            else:
                doc = json.loads(data)
        except (OSError, ValueError):
            continue
        for img in doc.get("images") or []:
            uri = img.get("uri") if isinstance(img, dict) else None
            if isinstance(uri, str) and uri and not uri.startswith("data:"):
                out.add(_res(proj, p, uri) or "")
    return out


def _dynamic(rel: str, dyn: set[str]) -> bool:
    return any(part.lower() in dyn for part in PurePosixPath(rel).parts[:-1])


def _ref_kind(name: str) -> str | None:
    low = name.lower()
    if low.endswith(TEXTURE_EXTS):
        return "texture"
    if low.endswith(AUDIO_EXTS):
        return "audio"
    if low.endswith(MESH_EXTS + (".blend",)):
        return "model"
    if low.endswith(".mat"):
        return "material"
    if low.endswith((".prefab", ".tscn")):
        return "prefab"
    return None


def _clip_vectors(proj: Path, rels: list[str], embedder: Any) -> dict[str, np.ndarray] | None:
    if embedder is None or not rels or len(rels) > MAX_CLIP_DUP or not embedder.available():
        return None
    vecs: dict[str, np.ndarray] = {}
    for i in range(0, len(rels), CLIP_EMBED_CHUNK):
        chunk, imgs = [], []
        for r in rels[i:i + CLIP_EMBED_CHUNK]:
            try:
                with Image.open(proj / r) as im:
                    im.draft("RGB", (448, 448))
                    imgs.append(im.convert("RGBA") if im.mode in ("P", "PA", "LA") else im.copy())
                chunk.append(r)
            except (OSError, ValueError, Image.DecompressionBombError):
                continue
        if imgs:
            arr = embedder.embed_images(imgs)
            vecs.update(zip(chunk, arr))
    return vecs


def _dup_groups(tex: list[dict[str, Any]], probes: dict[str, dict[str, Any]], hashes: dict[str, str],
                proj: Path | None = None, embedder: Any = None) -> tuple[list[dict[str, Any]], str]:
    """Exact (same bytes) and near-duplicate groups. With the CLIP model a pHash-close pair must also agree in
    embedding, and an almost identical embedding counts on its own (crop / padding changes); else pHash + dHash."""
    from studio_core.engine.similar import hamming

    groups: list[dict[str, Any]] = []
    by_hash: dict[str, list[str]] = {}
    for t in tex:
        by_hash.setdefault(hashes[t["rel"]], []).append(t["rel"])
    size = {t["rel"]: t["bytes"] for t in tex}
    exact_member: set[str] = set()
    for rels in by_hash.values():
        if len(rels) > 1:
            groups.append({"kind": "exact", "files": rels, "wasted_bytes": sum(size[r] for r in rels[1:])})
            exact_member.update(rels[1:])
    cands = [t["rel"] for t in tex if t["rel"] not in exact_member and probes[t["rel"]].get("phash")][:MAX_NEAR_DUP]
    vecs = None
    if proj is not None and embedder is not None:
        try:
            vecs = _clip_vectors(proj, [r for r in cands if not probes[r].get("data")], embedder)
        except Exception:  # noqa: BLE001 - a broken model must not fail the whole audit
            vecs = None
    method = "clip" if vecs else "phash"
    parent = {r: r for r in cands}

    def find(r: str) -> str:
        while parent[r] != r:
            parent[r] = parent[parent[r]]
            r = parent[r]
        return r

    for i, a in enumerate(cands):
        pa = probes[a]
        for b in cands[i + 1:]:
            pb = probes[b]
            if hashes[a] == hashes[b]:
                continue
            hp, hd = hamming(pa["phash"], pb["phash"]), hamming(pa["dhash"], pb["dhash"])
            close = hp is not None and hd is not None and hp <= NEAR_PHASH and hd <= NEAR_DHASH
            cos = float(np.dot(vecs[a], vecs[b])) if vecs and a in vecs and b in vecs else None
            if (close and (cos is None or cos >= CLIP_CONFIRM)) or (cos is not None and cos >= CLIP_ONLY):
                parent[find(a)] = find(b)
    near: dict[str, list[str]] = {}
    for r in cands:
        near.setdefault(find(r), []).append(r)
    for rels in near.values():
        if len(rels) > 1:
            rels.sort(key=lambda r: -size[r])
            groups.append({"kind": "near", "files": rels, "wasted_bytes": sum(size[r] for r in rels[1:]),
                           "method": "clip" if vecs and all(r in vecs for r in rels) else "phash"})
    groups.sort(key=lambda g: -g["wasted_bytes"])
    return groups, method


def _audio_unity(meta: dict[str, Any], secs: float | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ai = meta.get("AudioImporter") or {}
    d = ai.get("defaultSettings") or {}
    s = {"load_type": _int(d.get("loadType"), 0), "format": _int(d.get("compressionFormat"), 1),
         "mono": _int(ai.get("forceToMono"), 0) == 1, "preload": _int(d.get("preloadAudioData"), 1) == 1}
    issues = []
    if secs is not None and secs > LONG_CLIP_S and s["load_type"] == 0:
        issues.append(_issue("audio_decompress", "warn",
                             f"Clip dài {secs:.0f}s đang Decompress On Load (tốn RAM); nhạc nền nên Streaming, "
                             "clip vừa nên Compressed In Memory."))
    if secs is not None and secs > PCM_CLIP_S and s["format"] == 0:
        issues.append(_issue("audio_pcm", "warn", f"Clip {secs:.1f}s nén PCM (không nén); nên Vorbis / ADPCM."))
    return s, issues


def _audio_godot(sections: dict[str, dict[str, str]], rel: str, size: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    p = sections.get("params", {})
    s = {"compress_mode": _int(p.get("compress/mode"), 0)} if "compress/mode" in p else {}
    issues = []
    if rel.lower().endswith(".wav") and s.get("compress_mode", 0) == 0 and size > BIG_WAV:
        issues.append(_issue("audio_pcm", "warn", f"WAV PCM {_mb(size)}; nên Compress Mode = QOA hoặc đổi sang .ogg."))
    return s, issues


def _rel_inside(root: Path, p: Path) -> str:
    return p.resolve().relative_to(root).as_posix()


def scan(path: str | Path, engine: str = "auto", tri_budget: int = DEFAULT_TRI_BUDGET, duplicates: bool = True,
         max_files: int = MAX_FILES, embedder: Any = None, progress: Any = None) -> dict[str, Any]:
    """Read-only audit of `path` (a folder inside a Unity or Godot project; references are checked project-wide).
    `embedder` (adapters.embedder.ClipEmbedder) makes near-duplicates use CLIP when its model is installed."""
    folder = Path(str(path or "")).expanduser()
    if not str(path or "").strip() or not folder.is_absolute():
        raise AuditError("path must be an absolute folder path")
    if not folder.is_dir():
        raise AuditError(f"folder not found: {folder}")
    if _is_link(folder):
        raise AuditError("folder is a link")
    folder = folder.resolve()
    found, proj = project_root(folder)
    if engine not in ("auto", "unity", "godot"):
        raise AuditError("engine must be auto, unity or godot")
    if found == "none" or (engine != "auto" and engine != found):
        raise AuditError("not inside a Unity project (Assets + ProjectSettings) or a Godot project (project.godot)")
    engine = found
    t0 = time.time()
    skip = SKIP_DIRS | ({"library", "temp", "logs"} if engine == "unity" else set())
    scope = proj / "Assets" if engine == "unity" else proj
    all_files = list(_walk(scope, skip))
    if len(all_files) > max_files * 4:
        raise AuditError(f"project has more than {max_files * 4} files; audit a sub-folder")
    ref_files = [f for f in all_files if f.suffix.lower() in (UNITY_REF_EXTS if engine == "unity" else GODOT_REF_EXTS)]
    if engine == "unity":
        ref_files += [f for f in _walk(proj / "ProjectSettings", set()) if f.suffix.lower() == ".asset"]
        if (proj / "Packages" / "manifest.json").is_file():
            ref_files.append(proj / "Packages" / "manifest.json")
    else:
        ref_files += [f for f in (proj / "project.godot", proj / "export_presets.cfg") if f.is_file()]
    targets = [f for f in all_files if f.resolve().is_relative_to(folder) and not _is_link(f)]
    notes: list[str] = []
    if len(targets) > max_files:
        notes.append(f"Chỉ xét {max_files} file đầu tiên.")
        targets = targets[:max_files]

    textures: list[dict[str, Any]] = []
    probes: dict[str, dict[str, Any]] = {}
    hashes: dict[str, str] = {}
    meshes: list[dict[str, Any]] = []
    audio: list[dict[str, Any]] = []
    guid_of: dict[str, str] = {}
    uid_of: dict[str, str] = {}
    displays = _unity_displays(ref_files) if engine == "unity" else _godot_displays(proj, ref_files)
    tick = progress or (lambda *_: None)
    for n, p in enumerate(targets):
        tick(n, len(targets), "files")
        low = p.name.lower()
        rel = _rel_inside(proj, p)
        importer = p.with_name(p.name + (".meta" if engine == "unity" else ".import"))
        imp_text = None
        if importer.is_file():
            try:
                imp_text = importer.read_text(encoding="utf-8", errors="replace")
            except OSError:
                imp_text = None
        size = p.stat().st_size
        if engine == "unity" and imp_text:
            m = re.search(r"^guid: ([0-9a-f]{32})", imp_text, re.M)
            if m:
                guid_of[rel] = m.group(1)
        if low.endswith(TEXTURE_EXTS):
            try:
                pr = _image_probe(p, duplicates)
            except (OSError, ValueError, Image.DecompressionBombError) as e:
                notes.append(f"{rel}: không đọc được ảnh ({e})")
                continue
            probes[rel] = pr
            hashes[rel] = file_hash(p)
            textures.append(_texture_row(engine, rel, size, pr, importer, imp_text, displays))
        elif low.endswith(MESH_EXTS):
            row: dict[str, Any] = {"rel": rel, "bytes": size, "tris": None, "over": False}
            try:
                row["tris"] = mesh_tris(p)
                row["over"] = row["tris"] > tri_budget
            except (MeshError, OSError) as e:
                row["error"] = str(e)
            meshes.append(row)
        elif low.endswith(AUDIO_EXTS):
            secs = audio_seconds(p)
            if engine == "unity":
                settings, issues = _audio_unity(_load_yaml(imp_text or ""), secs)
            else:
                settings, issues = _audio_godot(parse_import(imp_text or ""), rel, size)
            audio.append({"rel": rel, "bytes": size, "seconds": round(secs, 2) if secs else None,
                          "settings": settings, "issues": issues})
        if engine == "godot" and imp_text and rel not in uid_of:
            uid = parse_import(imp_text).get("remap", {}).get("uid", "")
            if uid:
                uid_of[rel] = uid

    tick(len(targets), len(targets), "analyze")
    unreferenced = _unreferenced(engine, proj, targets, ref_files, guid_of, uid_of, notes)
    dups, dup_method = _dup_groups(textures, probes, hashes, proj, embedder) if duplicates else ([], "off")
    for g in dups:
        for r in g["files"]:
            t = next(t for t in textures if t["rel"] == r)
            others = [x for x in g["files"] if x != r]
            t["issues"].append(_issue("duplicate", "info", ("Trùng hệt" if g["kind"] == "exact" else "Gần giống") +
                                      f" {len(others)} ảnh khác: " + ", ".join(others[:3])))
    gpu_before = sum(t["gpu_before"] for t in textures)
    gpu_after = sum(t["gpu_after"] for t in textures)
    issue_counts: dict[str, int] = {}
    for t in textures:
        for i in t["issues"]:
            issue_counts[i["code"]] = issue_counts.get(i["code"], 0) + 1
    for a in audio:
        for i in a["issues"]:
            issue_counts[i["code"]] = issue_counts.get(i["code"], 0) + 1
    over = [m for m in meshes if m["over"]]
    if over:
        issue_counts["mesh_tris"] = len(over)
    if engine == "godot" and any(t["kind"] != "pixel" for t in textures):
        notes.append("Godot: bản Android/iOS cần bật Project Settings > Rendering > Textures > VRAM Compression > "
                     "Import ETC2 ASTC để ảnh nén VRAM dùng ETC2/ASTC.")
    return {
        "type": REPORT_TYPE, "version": 1, "root": str(folder), "project": str(proj), "engine": engine,
        "at": time.time(), "seconds": round(time.time() - t0, 2), "tri_budget": tri_budget,
        "summary": {
            "textures": len(textures), "texture_bytes": sum(t["bytes"] for t in textures),
            "gpu_before": int(gpu_before), "gpu_after": int(gpu_after),
            "fixable": sum(1 for t in textures if any(i.get("fix") for i in t["issues"])),
            "duplicate_groups": len(dups), "duplicate_bytes": sum(g["wasted_bytes"] for g in dups),
            "duplicate_method": dup_method,
            "unreferenced": len(unreferenced), "unreferenced_bytes": sum(u["bytes"] for u in unreferenced),
            "meshes": len(meshes), "meshes_over": len(over), "audio": len(audio), "issues": issue_counts,
        },
        "textures": textures, "duplicates": dups, "unreferenced": unreferenced, "meshes": meshes, "audio": audio,
        "notes": notes,
    }


def _texture_row(engine: str, rel: str, size: int, pr: dict[str, Any], importer: Path, imp_text: str | None,
                 displays: dict[str, list[Any]]) -> dict[str, Any]:
    w, h = pr["w"], pr["h"]
    row: dict[str, Any] = {"rel": rel, "w": w, "h": h, "bytes": size, "alpha_channel": pr["alpha_channel"],
                           "alpha_used": pr["alpha_used"], "importer": importer.name if imp_text else "",
                           "importer_hash": file_hash(importer) if imp_text else "", "issues": []}
    issues: list[dict[str, Any]] = row["issues"]
    if imp_text is None:
        row.update(kind=None, settings=None, gpu_before=w * h * 4, gpu_after=w * h * 4, display=None)
        issues.append(_issue("no_importer", "info", "Chưa có file import (engine chưa mở project sau khi thêm ảnh)."))
        return row
    if engine == "unity":
        text, _ = _lf(imp_text)
        meta = _load_yaml(text)
        if "TextureImporter" not in meta:
            row.update(kind=None, settings=None, gpu_before=0, gpu_after=0, display=None)
            return row
        s = unity_texture_settings(meta)
        kind = _texture_kind(rel, text, {**s, "data": pr.get("data")}, engine)
        before = _unity_gpu(w, h, s)
        eff = max(_fit(w, h, s["override_max"] or s["max"]))
        guid = str(meta.get("guid") or "")
        shown = displays.get(guid, []) if s["sprite_mode"] == 1 else []
        disp = None if not shown or any(v is None for v in shown) else \
            (max(v[0] for v in shown), max(v[1] for v in shown))
    else:
        sections = parse_import(imp_text)
        s = godot_texture_settings(sections)
        kind = _texture_kind(rel, None, {**s, "data": pr.get("data")}, engine)
        before = _godot_gpu(w, h, s)
        eff = max(_fit(w, h, s["size_limit"]))
        shown = displays.get("res://" + rel, [])
        disp = None if not shown else (max(v[0] for v in shown) * w, max(v[1] for v in shown) * h)
    row.update(kind=kind, settings=s, display=[round(disp[0]), round(disp[1])] if disp else None)
    if kind is None:
        row.update(gpu_before=int(before), gpu_after=int(before))
        return row
    b = _budget(kind)
    target = b.max_size
    if disp and max(disp) > 0:
        need = _next_pot(int(max(disp) * DISPLAY_HEADROOM))
        if need < eff:
            target = min(target, need)
            issues.append(_issue("display", "warn",
                                 f"Hiển thị tối đa ~{round(disp[0])}×{round(disp[1])} px nhưng ảnh {eff}px; "
                                 f"đủ dùng {need}px.", {"type": "max_size", "rel": rel, "max_size": need}))
    preset_after = _after_gpu(engine, w, h, s, _budget(kind, target))
    row.update(gpu_before=int(before))
    if engine == "unity":
        weak = not s["override"] and (not s["compressed"] or max(w, h) > b.max_size)
    else:
        weak = s["mode"] in (0, 1) and kind != "pixel" or (s["size_limit"] == 0 and max(w, h) > b.max_size)
    if weak and preset_after < before * 0.95:
        issues.append(_issue("mobile", "warn",
                             f"Chưa có thiết lập nén mobile ({mobile.BUDGETS[kind].label}); RAM GPU "
                             f"{_mb(before)} → {_mb(preset_after)}.",
                             {"type": "mobile_preset", "rel": rel, "kind": kind, "max_size": target}))
    elif eff > b.max_size:
        issues.append(_issue("oversize", "warn", f"{eff}px vượt ngân sách {b.max_size}px cho {mobile.BUDGETS[kind].label}.",
                             {"type": "max_size", "rel": rel, "max_size": b.max_size}))
    if kind == "texture" and not (_pot(w) and _pot(h)):
        issues.append(_issue("npot", "info", f"{w}×{h} không phải luỹ thừa 2; texture 3D lặp / mipmap nên POT."))
    if pr["alpha_channel"] and pr["alpha_used"] is False:
        if engine == "unity" and s.get("alpha_usage", 1) != 0:
            issues.append(_issue("alpha", "info", "Kênh alpha toàn 255 (thừa); bỏ alpha để nén RGB nhẹ hơn.",
                                 {"type": "drop_alpha", "rel": rel}))
        elif engine == "godot":
            issues.append(_issue("alpha", "info", "Kênh alpha toàn 255 (thừa); lưu lại PNG không alpha cho gọn."))
    row["gpu_after"] = min(int(before), _planned_gpu(engine, row, [i["fix"] for i in issues if i.get("fix")]))
    return row


def _unreferenced(engine: str, proj: Path, targets: list[Path], ref_files: list[Path], guid_of: dict[str, str],
                  uid_of: dict[str, str], notes: list[str]) -> list[dict[str, Any]]:
    out = []
    if engine == "unity":
        refs, binary = _unity_refs(ref_files)
        by_path = {r.removeprefix("res://") for r in _model_refs(proj, [p for p in targets
                                                                         if p.suffix.lower() in MESH_EXTS])}
        if binary:
            notes.append(f"{len(binary)} asset lưu dạng binary: tham chiếu trong đó được dò theo GUID nhị phân.")
        for p in targets:
            rel = _rel_inside(proj, p)
            kind = _ref_kind(p.name)
            g = guid_of.get(rel)
            if not kind or not g or g in refs or rel in by_path or _dynamic(rel, UNITY_DYNAMIC):
                continue
            if not any(binary_guid(g) in b for b in binary):
                out.append({"rel": rel, "kind": kind, "bytes": p.stat().st_size, "guid": g})
    else:
        paths, uids = _godot_refs(proj, ref_files)
        paths |= _model_refs(proj, [p for p in targets if p.suffix.lower() in MESH_EXTS])
        for p in targets:
            rel = _rel_inside(proj, p)
            kind = _ref_kind(p.name)
            if not kind or _dynamic(rel, GODOT_DYNAMIC):
                continue
            res = "res://" + rel
            uid = uid_of.get(rel)
            if res in paths or (uid and uid in uids):
                continue
            if kind == "prefab":
                own = _UID.search(p.read_bytes()[:400])
                if own and own.group(0).decode() in uids:
                    continue
            out.append({"rel": rel, "kind": kind, "bytes": p.stat().st_size, "uid": uid or ""})
    out.sort(key=lambda r: -r["bytes"])
    return out


def safe_importer(proj: Path, rel: str, engine: str) -> Path:
    """Importer file of the asset `rel`, refusing paths that leave the project, links and missing files."""
    pp = PurePosixPath(rel.replace("\\", "/"))
    if pp.is_absolute() or not pp.parts or any(part in ("", ".", "..") for part in pp.parts) or ":" in rel:
        raise AuditError(f"path escapes the project: {rel}")
    proj = proj.resolve()
    cur = proj
    for part in pp.parts:
        cur = cur / part
        if _is_link(cur):
            raise AuditError(f"link in path: {rel}")
    imp = proj.joinpath(*pp.parts[:-1], pp.name + (".meta" if engine == "unity" else ".import"))
    if _is_link(imp):
        raise AuditError(f"link in path: {rel}")
    real = imp.resolve()
    if not real.is_relative_to(proj) or not real.is_file():
        raise AuditError(f"importer file not found: {rel}")
    return real


def _unity_max(text: str, n: int) -> str:
    text = re.sub(r"(?m)^  maxTextureSize: (\d+)$", lambda m: f"  maxTextureSize: {min(int(m.group(1)), n)}", text)
    return re.sub(r"(?m)^    maxTextureSize: (\d+)$", lambda m: f"    maxTextureSize: {min(int(m.group(1)), n)}", text)


def _godot_set(text: str, values: dict[str, str]) -> str:
    lines = text.split("\n")
    try:
        start = next(i for i, ln in enumerate(lines) if ln.strip() == "[params]")
    except StopIteration:
        return text.rstrip("\n") + "\n\n[params]\n\n" + "".join(f"{k}={v}\n" for k, v in values.items())
    end = next((i for i in range(start + 1, len(lines)) if lines[i].strip().startswith("[")), len(lines))
    todo = dict(values)
    for i in range(start + 1, end):
        m = _KV.match(lines[i].strip())
        if m and m.group(1) in todo:
            lines[i] = f"{m.group(1)}={todo.pop(m.group(1))}"
    insert = end
    while insert > start + 1 and not lines[insert - 1].strip():
        insert -= 1
    lines[insert:insert] = [f"{k}={v}" for k, v in todo.items()]
    return "\n".join(lines)


def patch_importer(engine: str, text: str, action: dict[str, Any], row: dict[str, Any]) -> str:
    kind = str(action.get("kind") or row.get("kind") or "")
    t = action["type"]
    if engine == "unity":
        body, crlf = _lf(text)
        if t == "mobile_preset":
            if kind not in mobile.BUDGETS:
                raise AuditError(f"no mobile budget for kind '{kind}'")
            b = _budget(kind, _int(action.get("max_size")) or None)
            body = mobile.patch_unity_meta(body, b)
        elif t == "max_size":
            n = _int(action.get("max_size"))
            if n not in (32, 64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384):
                raise AuditError("max_size must be a power of two 32..16384")
            body = _unity_max(body, n)
        elif t == "drop_alpha":
            body = re.sub(r"(?m)^  (alphaUsage|alphaSource): [12]$", r"  \1: 0", body)
        return body.replace("\n", "\r\n") if crlf else body
    body, crlf = _lf(text)
    if t == "mobile_preset":
        if kind not in mobile.BUDGETS:
            raise AuditError(f"no mobile budget for kind '{kind}'")
        b = _budget(kind, _int(action.get("max_size")) or None)
        body = _godot_set(body, {"compress/mode": str(b.godot_mode),
                                 "compress/high_quality": "true" if b.godot_hq else "false",
                                 "process/size_limit": str(b.max_size)})
    elif t == "max_size":
        n = _int(action.get("max_size"))
        if not 16 <= n <= 16384:
            raise AuditError("max_size must be 16..16384")
        body = _godot_set(body, {"process/size_limit": str(n)})
    elif t == "drop_alpha":
        raise AuditError("Godot has no import setting to drop alpha; re-save the PNG without alpha")
    return body.replace("\n", "\r\n") if crlf else body


def _atomic_write(dst: Path, data: bytes) -> None:
    # Hidden + trailing "~": neither Unity nor Godot imports the temp file while it exists.
    tmp = dst.with_name(f".{dst.name}.{uuid.uuid4().hex[:8]}.audit~")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, dst)
    finally:
        if tmp.exists():
            tmp.unlink()


def plan_fixes(report: dict[str, Any], actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per importer file: the new text (or why it is skipped), checked against the hash recorded by the scan."""
    engine, proj = report["engine"], Path(report["project"])
    rows = {t["rel"]: t for t in report.get("textures") or []}
    by_rel: dict[str, list[dict[str, Any]]] = {}
    out: list[dict[str, Any]] = []
    for a in actions:
        if not isinstance(a, dict) or a.get("type") not in FIX_TYPES or not isinstance(a.get("rel"), str):
            out.append({"rel": str((a or {}).get("rel") if isinstance(a, dict) else ""), "action": "skip",
                        "why": f"unknown fix {a!r:.80}"})
            continue
        by_rel.setdefault(a["rel"], []).append(a)
    for rel, acts in by_rel.items():
        rec: dict[str, Any] = {"rel": rel, "action": "write", "fixes": [a["type"] for a in acts]}
        try:
            row = rows.get(rel)
            if row is None or not row.get("importer"):
                raise AuditError("not a texture of the last audit")
            imp = safe_importer(proj, rel, engine)
            old = imp.read_bytes()
            if file_hash(imp) != row.get("importer_hash"):
                raise AuditError("importer file changed since the audit; audit again")
            text = old.decode("utf-8")
            new = text
            for a in acts:
                new = patch_importer(engine, new, a, row)
            if new == text:
                raise AuditError("already set")
            rec.update(importer=_rel_inside(proj, imp), old=row["importer_hash"], data=new.encode("utf-8"),
                       gpu_before=row.get("gpu_before"), gpu_after=_planned_gpu(engine, row, acts))
        except (AuditError, OSError, UnicodeDecodeError) as e:
            rec.update(action="skip", why=str(e))
        out.append(rec)
    return out


def _planned_gpu(engine: str, row: dict[str, Any], acts: list[dict[str, Any]]) -> int:
    s = row.get("settings")
    kind = row.get("kind")
    if not s or not kind:
        return int(row.get("gpu_before") or 0)
    w, h = row["w"], row["h"]
    s = dict(s)
    preset = next((a for a in acts if a["type"] == "mobile_preset"), None)
    limit = min([_int(a.get("max_size")) for a in acts if a["type"] in ("max_size", "mobile_preset")
                 and _int(a.get("max_size"))] or [0])
    if preset:
        return int(_after_gpu(engine, w, h, s, _budget(kind, limit or None)))
    if limit:
        if engine == "unity":
            s["max"] = min(s["max"], limit)
            if s["override_max"]:
                s["override_max"] = min(s["override_max"], limit)
            return int(_unity_gpu(w, h, s))
        s["size_limit"] = min(s["size_limit"] or limit, limit)
        return int(_godot_gpu(w, h, s))
    return int(row.get("gpu_before") or 0)


def apply_fixes(report: dict[str, Any], actions: list[dict[str, Any]], backups: Path, dry_run: bool = True,
                source: dict[str, Any] | None = None, progress: Any = None) -> dict[str, Any]:
    """dry_run plans only; otherwise every importer file to change goes into one verified zip first, then each is
    replaced atomically and a manifest is kept for undo. `progress` may cancel between two files: the files already
    written stay (undoable), the rest are marked skipped and doc["cancelled"] is set."""
    proj = Path(report["project"])
    plan = plan_fixes(report, actions)
    todo = [r for r in plan if r["action"] == "write"]
    files = [{k: v for k, v in r.items() if k != "data"} for r in plan]
    doc: dict[str, Any] = {"type": FIX_TYPE, "version": 1, "dry_run": dry_run, "project": str(proj),
                           "engine": report["engine"], "at": time.time(), "source": source or {}, "files": files,
                           "written": 0, "skipped": len(plan) - len(todo),
                           "gpu_before": sum(int(r.get("gpu_before") or 0) for r in todo),
                           "gpu_after": sum(int(r.get("gpu_after") or 0) for r in todo)}
    if dry_run or not todo:
        return doc
    backups.mkdir(parents=True, exist_ok=True)
    bid = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    zpath = backups / f"{bid}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for r in todo:
            z.write(proj / r["importer"], r["importer"])
    with zipfile.ZipFile(zpath) as z:
        if z.testzip() is not None or len(z.namelist()) != len(todo):
            raise AuditError("backup zip failed verification; nothing written")
    doc.update(backup_id=bid, backup=str(zpath))
    written = 0
    pending = [f for f in files if f["action"] == "write"]
    try:
        for i, (r, f) in enumerate(zip(todo, pending)):
            if progress is not None:
                try:
                    progress(i, len(todo), "files")
                except ScanCancelled:
                    doc["cancelled"] = True
                    for left in pending[i:]:
                        left.update(action="skip", why="cancelled")
                    break
            dst = safe_importer(proj, r["rel"], report["engine"])
            if file_hash(dst) != r["old"]:
                f.update(action="skip", why="importer file changed during the fix")
                continue
            _atomic_write(dst, r["data"])
            f["new"] = file_hash(dst)
            written += 1
    finally:
        doc["written"] = written
        doc["skipped"] = len(files) - written
        if doc.get("cancelled") and not written:
            zpath.unlink(missing_ok=True)
            doc.pop("backup_id", None)
            doc.pop("backup", None)
        else:
            (backups / f"{bid}.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
    return doc


def load_manifest(backups: Path, bid: str) -> dict[str, Any]:
    if not re.fullmatch(r"[\w-]{1,64}", bid or ""):
        raise AuditError("bad backup id")
    p = backups / f"{bid}.json"
    if not p.is_file():
        raise AuditError(f"unknown backup {bid}")
    return json.loads(p.read_text(encoding="utf-8"))


def undo(backups: Path, bid: str, force: bool = False) -> dict[str, Any]:
    """Restores the importer files; one edited after the fix (hash != what was written) is kept unless `force`."""
    doc = load_manifest(backups, bid)
    if doc.get("undone_at"):
        raise AuditError("already undone")
    proj = Path(doc["project"])
    restored, conflicts = [], []
    with zipfile.ZipFile(backups / f"{bid}.zip") as z:
        names = set(z.namelist())
        for r in doc.get("files") or []:
            if r.get("action") != "write" or r.get("importer") not in names:
                continue
            try:
                dst = safe_importer(proj, r["rel"], doc["engine"])
            except AuditError as e:
                conflicts.append({"rel": r["rel"], "why": str(e)})
                continue
            if not force and file_hash(dst) != r.get("new"):
                conflicts.append({"rel": r["rel"], "why": "changed after the fix"})
                continue
            _atomic_write(dst, z.read(r["importer"]))
            restored.append(r["rel"])
    doc.update(undone_at=time.time(), restored=restored, conflicts=conflicts)
    (backups / f"{bid}.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
    return {"backup_id": bid, "restored": restored, "conflicts": conflicts}


def list_backups(backups: Path, project: str | None = None) -> list[dict[str, Any]]:
    out = []
    for p in sorted(backups.glob("*.json"), reverse=True) if backups.is_dir() else []:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if project and os.path.normcase(str(d.get("project"))) != os.path.normcase(project):
            continue
        out.append({"backup_id": p.stem, "project": d.get("project"), "engine": d.get("engine"), "at": d.get("at"),
                    "written": d.get("written"), "undone_at": d.get("undone_at"),
                    "gpu_before": d.get("gpu_before"), "gpu_after": d.get("gpu_after")})
    return out


def build_atlas(report: dict[str, Any], rels: list[str], name: str, out_root: Path, max_size: int = 2048
                ) -> dict[str, Any]:
    """Packs the chosen sprites into an engine atlas written OUTSIDE the project (references are not rewired)."""
    from studio_core.export.atlas import AtlasError, AtlasOptions, Sprite, engine_files, pack
    from studio_core.export.sheet import ident

    proj = Path(report["project"])
    rows = {t["rel"]: t for t in report.get("textures") or []}
    sprites: list[Sprite] = []
    sources: dict[str, tuple[int, int, int]] = {}
    for rel in rels:
        if rel not in rows:
            raise AuditError(f"not a texture of the last audit: {rel}")
        p = safe_importer(proj, rel, report["engine"]).with_name(PurePosixPath(rel).name)
        with Image.open(p) as im:
            img = im.convert("RGBA")
        sprites.append(Sprite(PurePosixPath(rel).stem, img, (img.width / 2, img.height / 2),
                              pixel=rows[rel].get("kind") == "pixel"))
        sources[rel] = (p.stat().st_size, img.width, img.height)
    if len(sprites) < 2:
        raise AuditError("pick at least 2 sprites for an atlas")
    try:
        atlas = pack(sprites, ident(name, "atlas"), AtlasOptions(max_size=max_size), sources)
        files = engine_files(atlas, report["engine"])
    except AtlasError as e:
        raise AuditError(str(e)) from e
    dest = out_root / f"{atlas.name}-{time.strftime('%Y%m%d-%H%M%S')}"
    for rel, data in files:
        out = dest.joinpath(*PurePosixPath(rel).parts)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data if isinstance(data, bytes) else Path(data).read_bytes())
    return {"folder": str(dest), "files": [r for r, _ in files], "stats": atlas.stats}
