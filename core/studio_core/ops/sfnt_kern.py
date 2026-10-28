"""Pair kerning straight from a TTF/OTF/TTC: GPOS 'kern' PairPos (formats 1 and 2) or the legacy 'kern' table.

Pillow's basic layout (no raqm here) applies neither, and modern Windows fonts keep kerning only in GPOS."""
from __future__ import annotations

import struct
from pathlib import Path

_U16 = struct.Struct(">H")
_S16 = struct.Struct(">h")
_U32 = struct.Struct(">I")


def _u16(b: bytes, o: int) -> int:
    return _U16.unpack_from(b, o)[0]


def _s16(b: bytes, o: int) -> int:
    return _S16.unpack_from(b, o)[0]


def _u32(b: bytes, o: int) -> int:
    return _U32.unpack_from(b, o)[0]


def _tables(data: bytes, index: int = 0) -> dict[str, tuple[int, int]]:
    off = 0
    if data[:4] == b"ttcf":
        off = _u32(data, 12 + 4 * min(index, _u32(data, 8) - 1))
    out = {}
    for i in range(_u16(data, off + 4)):
        rec = off + 12 + 16 * i
        out[data[rec:rec + 4].decode("latin-1")] = (_u32(data, rec + 8), _u32(data, rec + 12))
    return out


def _cmap(data: bytes, base: int, chars: list[int]) -> dict[int, int]:
    subs = {}
    for i in range(_u16(data, base + 2)):
        rec = base + 4 + 8 * i
        subs[(_u16(data, rec), _u16(data, rec + 2))] = base + _u32(data, rec + 4)
    for key in ((3, 10), (0, 6), (0, 4), (3, 1), (0, 3), (0, 2), (0, 1), (0, 0)):
        if key not in subs:
            continue
        st = subs[key]
        fmt = _u16(data, st)
        if fmt == 12:
            groups = [(_u32(data, st + 16 + 12 * g), _u32(data, st + 20 + 12 * g), _u32(data, st + 24 + 12 * g))
                      for g in range(_u32(data, st + 12))]
            return {c: s + c - lo for c in chars for lo, hi, s in groups if lo <= c <= hi}
        if fmt == 4:
            seg2 = _u16(data, st + 6)
            ends, starts = st + 14, st + 16 + seg2
            deltas, ranges = starts + seg2, starts + 2 * seg2
            out = {}
            for c in chars:
                for k in range(0, seg2, 2):
                    if c > _u16(data, ends + k):
                        continue
                    if c < _u16(data, starts + k):
                        break
                    ro = _u16(data, ranges + k)
                    if ro == 0:
                        g = (c + _u16(data, deltas + k)) & 0xFFFF
                    else:
                        g = _u16(data, ranges + k + ro + 2 * (c - _u16(data, starts + k)))
                        g = (g + _u16(data, deltas + k)) & 0xFFFF if g else 0
                    if g:
                        out[c] = g
                    break
            return out
    return {}


def _coverage(data: bytes, o: int) -> dict[int, int]:
    fmt, n = _u16(data, o), _u16(data, o + 2)
    if fmt == 1:
        return {_u16(data, o + 4 + 2 * i): i for i in range(n)}
    out = {}
    for i in range(n):
        r = o + 4 + 6 * i
        lo, hi, start = _u16(data, r), _u16(data, r + 2), _u16(data, r + 4)
        out.update({g: start + g - lo for g in range(lo, hi + 1)})
    return out


def _classes(data: bytes, o: int) -> dict[int, int]:
    fmt = _u16(data, o)
    if fmt == 1:
        first, n = _u16(data, o + 2), _u16(data, o + 4)
        return {first + i: _u16(data, o + 6 + 2 * i) for i in range(n)}
    out = {}
    for i in range(_u16(data, o + 2)):
        r = o + 4 + 6 * i
        out.update({g: _u16(data, r + 4) for g in range(_u16(data, r), _u16(data, r + 2) + 1)})
    return out


def _vr_size(fmt: int) -> int:
    return 2 * bin(fmt & 0xFF).count("1")


def _x_adv(data: bytes, o: int, fmt: int) -> int:
    return _s16(data, o + _vr_size(fmt & 0x3)) if fmt & 0x4 else 0


def _pair_subtable(data: bytes, st: int, glyphs: set[int]) -> dict[tuple[int, int], int]:
    """Every (left, right) pair among `glyphs` this subtable decides, including explicit zeros."""
    fmt, cov = _u16(data, st), _coverage(data, st + _u16(data, st + 2))
    vf1, vf2 = _u16(data, st + 4), _u16(data, st + 6)
    s1, s2 = _vr_size(vf1), _vr_size(vf2)
    out: dict[tuple[int, int], int] = {}
    if fmt == 1:
        for left, idx in cov.items():
            if left not in glyphs:
                continue
            ps = st + _u16(data, st + 10 + 2 * idx)
            for i in range(_u16(data, ps)):
                r = ps + 2 + i * (2 + s1 + s2)
                right = _u16(data, r)
                if right in glyphs:
                    out[(left, right)] = _x_adv(data, r + 2, vf1)
    elif fmt == 2:
        c1, c2 = _classes(data, st + _u16(data, st + 8)), _classes(data, st + _u16(data, st + 10))
        n2 = _u16(data, st + 14)
        rec = s1 + s2
        rights = [(g, c2.get(g, 0)) for g in glyphs]
        for left in glyphs:
            if left not in cov:
                continue
            row = st + 16 + c1.get(left, 0) * n2 * rec
            for right, k in rights:
                out[(left, right)] = _x_adv(data, row + k * rec, vf1)
    return out


def _gpos(data: bytes, base: int, glyphs: set[int]) -> dict[tuple[int, int], int] | None:
    feats, lookups = base + _u16(data, base + 6), base + _u16(data, base + 8)
    idx: set[int] = set()
    for i in range(_u16(data, feats)):
        rec = feats + 2 + 6 * i
        if data[rec:rec + 4] == b"kern":
            ft = feats + _u16(data, rec + 4)
            idx.update(_u16(data, ft + 4 + 2 * j) for j in range(_u16(data, ft + 2)))
    if not idx:
        return None
    total: dict[tuple[int, int], int] = {}
    for li in sorted(idx):
        lk = lookups + _u16(data, lookups + 2 + 2 * li)
        ltype = _u16(data, lk)
        decided: dict[tuple[int, int], int] = {}
        for s in range(_u16(data, lk + 4)):
            st = lk + _u16(data, lk + 6 + 2 * s)
            if ltype == 9:
                if _u16(data, st + 2) != 2:
                    continue
                st += _u32(data, st + 4)
            elif ltype != 2:
                continue
            # The first subtable covering a pair wins within one lookup.
            for k, v in _pair_subtable(data, st, glyphs).items():
                decided.setdefault(k, v)
        for k, v in decided.items():
            total[k] = total.get(k, 0) + v
    return total


def _kern_table(data: bytes, base: int, glyphs: set[int]) -> dict[tuple[int, int], int]:
    out: dict[tuple[int, int], int] = {}
    if _u16(data, base) != 0:
        return out
    o = base + 4
    for _ in range(_u16(data, base + 2)):
        length, coverage = _u16(data, o + 2), _u16(data, o + 4)
        if coverage >> 8 == 0 and coverage & 1:
            for i in range(_u16(data, o + 6)):
                r = o + 14 + 6 * i
                left, right = _u16(data, r), _u16(data, r + 2)
                if left in glyphs and right in glyphs:
                    out[(left, right)] = out.get((left, right), 0) + _s16(data, r + 4)
        o += length
    return out


def kerning(path: str | Path, chars: str, size: float, index: int = 0) -> dict[tuple[str, str], int]:
    """(left, right) -> px adjustment at `size` px, non-zero pairs only; {} when the font can't be read."""
    try:
        data = Path(path).read_bytes()
        tables = _tables(data, index)
        if "cmap" not in tables or "head" not in tables:
            return {}
        cmap = _cmap(data, tables["cmap"][0], sorted({ord(c) for c in chars}))
        upem = _u16(data, tables["head"][0] + 18) or 1000
        glyphs = set(cmap.values())
        raw = _gpos(data, tables["GPOS"][0], glyphs) if "GPOS" in tables else None
        # Some fonts (Cambria) keep Latin pairs only in the legacy table while GPOS 'kern' covers other scripts.
        if not any((raw or {}).values()) and "kern" in tables:
            raw = _kern_table(data, tables["kern"][0], glyphs)
    except (OSError, struct.error, IndexError, ValueError):
        return {}
    by_glyph: dict[int, list[str]] = {}
    for c, g in cmap.items():
        by_glyph.setdefault(g, []).append(chr(c))
    out: dict[tuple[str, str], int] = {}
    for (lg, rg), v in (raw or {}).items():
        px = round(v * size / upem)
        if px:
            for a in by_glyph.get(lg, ()):
                for b in by_glyph.get(rg, ()):
                    out[(a, b)] = px
    return out
