"""Header-only readers for the project audit: triangle counts of .fbx (binary/ASCII), .glb/.gltf and .obj meshes, and
clip length of .wav / .ogg / .mp3, without an engine or Blender."""
from __future__ import annotations

import json
import re
import struct
import wave
import zlib
from pathlib import Path

FBX_MAGIC = b"Kaydara FBX Binary  \x00"
MAX_MESH_BYTES = 512 << 20
_ARRAY = {b"f": ("f", 4), b"d": ("d", 8), b"l": ("q", 8), b"i": ("i", 4), b"b": ("b", 1)}
_SCALAR = {b"Y": 2, b"C": 1, b"I": 4, b"F": 4, b"D": 8, b"L": 8}


class MeshError(ValueError):
    pass


def _poly_tris(indices: list[int] | tuple[int, ...]) -> int:
    """FBX PolygonVertexIndex: a polygon ends at a negative (bitwise-not) index; an n-gon is n - 2 triangles."""
    tris = n = 0
    for v in indices:
        n += 1
        if v < 0:
            tris += max(0, n - 2)
            n = 0
    return tris


def _fbx_binary(data: bytes) -> int:
    version = struct.unpack_from("<I", data, 23)[0]
    wide = version >= 7500
    head = struct.Struct("<QQQB" if wide else "<IIIB")
    tris = 0

    def props(pos: int, count: int, want: bool) -> tuple[int, list[int] | None]:
        found = None
        for _ in range(count):
            t = data[pos:pos + 1]
            pos += 1
            if t in _SCALAR:
                pos += _SCALAR[t]
            elif t in (b"S", b"R"):
                pos += 4 + struct.unpack_from("<I", data, pos)[0]
            elif t in _ARRAY:
                n, enc, clen = struct.unpack_from("<III", data, pos)
                pos += 12
                raw = data[pos:pos + clen]
                pos += clen
                if want and t == b"i":
                    buf = zlib.decompress(raw) if enc == 1 else raw
                    found = list(struct.unpack(f"<{n}i", buf[:4 * n]))
            else:
                raise MeshError(f"unknown FBX property type {t!r}")
        return pos, found

    def walk(pos: int, end: int, parent: bytes) -> int:
        nonlocal tris
        while pos + head.size <= end:
            end_off, nprops, plen, nlen = head.unpack_from(data, pos)
            if end_off == 0:
                return pos + head.size
            name = data[pos + head.size:pos + head.size + nlen]
            body = pos + head.size + nlen
            want = name == b"PolygonVertexIndex" and parent == b"Geometry"
            _, found = props(body, nprops, want) if want else (None, None)
            if found:
                tris += _poly_tris(found)
            child = body + plen
            if child < end_off:
                walk(child, end_off, name)
            pos = end_off
        return pos

    walk(27, len(data), b"")
    return tris


def _fbx_ascii(text: str) -> int:
    tris = 0
    for m in re.finditer(r"PolygonVertexIndex:\s*\*?\d*\s*\{?\s*(?:a:)?\s*([-\d,\s]+)", text):
        tris += _poly_tris([int(v) for v in m.group(1).replace("\n", "").split(",") if v.strip()])
    return tris


def _gltf_doc(path: Path, data: bytes) -> dict:
    if path.suffix.lower() == ".glb":
        if data[:4] != b"glTF":
            raise MeshError("not a GLB")
        length = struct.unpack_from("<I", data, 12)[0]
        return json.loads(data[20:20 + length])
    return json.loads(data)


def _gltf_tris(doc: dict) -> int:
    acc = doc.get("accessors") or []
    tris = 0
    for mesh in doc.get("meshes") or []:
        for prim in mesh.get("primitives") or []:
            mode = prim.get("mode", 4)
            idx = prim.get("indices")
            n = acc[idx]["count"] if isinstance(idx, int) and idx < len(acc) else \
                acc[(prim.get("attributes") or {}).get("POSITION", -1)]["count"] if \
                isinstance((prim.get("attributes") or {}).get("POSITION"), int) else 0
            if mode == 4:
                tris += n // 3
            elif mode in (5, 6):
                tris += max(0, n - 2)
    return tris


def _obj_tris(path: Path) -> int:
    tris = 0
    with open(path, "rb") as f:
        for line in f:
            if line.startswith(b"f "):
                tris += max(0, len(line.split()) - 3)
    return tris


def mesh_tris(path: Path) -> int:
    """Triangles over every mesh in the file (each mesh counted once, not per instance)."""
    ext = path.suffix.lower()
    if path.stat().st_size > MAX_MESH_BYTES:
        raise MeshError("file too large to read")
    if ext == ".obj":
        return _obj_tris(path)
    data = path.read_bytes()
    try:
        if ext == ".fbx":
            if data.startswith(FBX_MAGIC):
                return _fbx_binary(data)
            return _fbx_ascii(data.decode("utf-8", errors="replace"))
        if ext in (".glb", ".gltf"):
            return _gltf_tris(_gltf_doc(path, data))
    except (struct.error, zlib.error, ValueError, KeyError, IndexError, TypeError) as e:
        raise MeshError(f"unreadable {ext}: {e}") from e
    raise MeshError(f"unsupported mesh type {ext}")


def _ogg_seconds(path: Path) -> float | None:
    size = path.stat().st_size
    with open(path, "rb") as f:
        head = f.read(4096)
        i = head.find(b"\x01vorbis")
        if i < 0:
            return None
        rate = struct.unpack_from("<I", head, i + 12)[0]
        f.seek(max(0, size - 65536))
        tail = f.read()
    j = tail.rfind(b"OggS")
    if j < 0 or not rate:
        return None
    granule = struct.unpack_from("<q", tail, j + 6)[0]
    return granule / rate if granule > 0 else None


def audio_seconds(path: Path) -> float | None:
    ext = path.suffix.lower()
    try:
        if ext == ".wav":
            with wave.open(str(path), "rb") as w:
                return w.getnframes() / float(w.getframerate() or 1)
        if ext == ".ogg":
            return _ogg_seconds(path)
        if ext == ".mp3":
            # 128 kbps is the common game-audio export rate; good enough to tell a jingle from a music track.
            return path.stat().st_size * 8 / 128_000
    except (OSError, EOFError, wave.Error, struct.error):
        return None
    return None
