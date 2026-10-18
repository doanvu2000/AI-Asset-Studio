"""Dependency-free GLB (glTF 2.0 binary) structural inspection."""
from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

MAGIC = b"glTF"
CHUNK_JSON = 0x4E4F534A
CHUNK_BIN = 0x004E4942
MODE_TRIANGLES = 4


class GlbError(ValueError):
    pass


def parse_glb(data: bytes) -> tuple[dict[str, Any], int]:
    """-> (json document, BIN chunk length)."""
    if len(data) < 20:
        raise GlbError(f"file too small ({len(data)} bytes)")
    magic, version, length = struct.unpack_from("<4sII", data, 0)
    if magic != MAGIC:
        raise GlbError(f"bad magic {magic!r}")
    if version != 2:
        raise GlbError(f"unsupported glTF version {version}")
    if length != len(data):
        raise GlbError(f"header length {length} != file size {len(data)}")
    doc: dict[str, Any] | None = None
    bin_len = 0
    off = 12
    while off + 8 <= length:
        clen, ctype = struct.unpack_from("<II", data, off)
        body = data[off + 8: off + 8 + clen]
        if len(body) != clen:
            raise GlbError("truncated chunk")
        if ctype == CHUNK_JSON and doc is None:
            try:
                doc = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                raise GlbError(f"bad JSON chunk: {e}") from None
        elif ctype == CHUNK_BIN:
            bin_len = clen
        off += 8 + clen
    if not isinstance(doc, dict):
        raise GlbError("missing JSON chunk")
    return doc, bin_len


def inspect_glb(data: bytes) -> dict[str, Any]:
    doc, bin_len = parse_glb(data)
    accessors = doc.get("accessors") or []

    def acc_count(i: Any) -> int:
        if not isinstance(i, int) or not 0 <= i < len(accessors):
            raise GlbError(f"accessor index {i!r} out of range")
        return int(accessors[i].get("count", 0))

    meshes = doc.get("meshes") or []
    prims = verts = tris = 0
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    for m in meshes:
        for p in m.get("primitives") or []:
            prims += 1
            pos = (p.get("attributes") or {}).get("POSITION")
            if pos is None:
                raise GlbError("primitive without POSITION")
            n = acc_count(pos)
            verts += n
            acc = accessors[pos]
            if len(acc.get("min") or []) == 3 and len(acc.get("max") or []) == 3:
                lo = [min(a, b) for a, b in zip(lo, acc["min"])]
                hi = [max(a, b) for a, b in zip(hi, acc["max"])]
            if p.get("mode", MODE_TRIANGLES) == MODE_TRIANGLES:
                tris += (acc_count(p["indices"]) if "indices" in p else n) // 3
    images = doc.get("images") or []
    return {
        "bytes": len(data), "bin_bytes": bin_len, "generator": (doc.get("asset") or {}).get("generator"),
        "meshes": len(meshes), "primitives": prims, "vertices": verts, "triangles": tris,
        "materials": len(doc.get("materials") or []), "textures": len(doc.get("textures") or []),
        "images": len(images), "embedded_images": sum(1 for i in images if "bufferView" in i),
        "nodes": len(doc.get("nodes") or []),
        "bounds": {"min": lo, "max": hi, "size": [round(b - a, 6) for a, b in zip(lo, hi)]} if verts and lo[0] <= hi[0]
        else None,
    }


def problems(report: dict[str, Any], require_textures: bool = True, max_triangles: int | None = None,
             max_bytes: int | None = None) -> list[str]:
    out: list[str] = []
    if report["meshes"] == 0 or report["primitives"] == 0:
        out.append("no meshes")
    if report["vertices"] == 0:
        out.append("no vertices")
    if require_textures and report["textures"] == 0:
        out.append("no textures")
    if require_textures and report["images"] > report["embedded_images"]:
        out.append("external image references (not self-contained)")
    if max_triangles and report["triangles"] > max_triangles:
        out.append(f"{report['triangles']} triangles > max {max_triangles}")
    if max_bytes and report["bytes"] > max_bytes:
        out.append(f"{report['bytes']} bytes > max {max_bytes}")
    return out


def read_report(path: Path) -> dict[str, Any]:
    return inspect_glb(path.read_bytes())
