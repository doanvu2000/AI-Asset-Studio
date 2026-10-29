"""Minimal layered PSD writer: 8-bit RGB document, one pixel layer per RGBA image (bottom first), PackBits."""
from __future__ import annotations

import struct

import numpy as np

MAX_SIDE = 30000


def packbits_row(row: bytes) -> bytes:
    out = bytearray()
    n, i = len(row), 0
    lit = bytearray()

    def flush() -> None:
        for k in range(0, len(lit), 128):
            chunk = lit[k:k + 128]
            out.append(len(chunk) - 1)
            out.extend(chunk)
        lit.clear()

    while i < n:
        b = row[i]
        j = i + 1
        while j < n and j - i < 128 and row[j] == b:
            j += 1
        run = j - i
        if run >= 3:
            flush()
            out.append(257 - run)
            out.append(b)
        else:
            lit.extend(row[i:j])
        i = j
    flush()
    return bytes(out)


def _rle_planes(planes: list[np.ndarray]) -> tuple[bytes, list[bytes]]:
    """Row byte counts of all planes, then each plane's packed rows (the PSD RLE layout)."""
    counts, datas = [], []
    for p in planes:
        rows = [packbits_row(r.tobytes()) for r in p]
        counts.append(b"".join(struct.pack(">H", len(r)) for r in rows))
        datas.append(b"".join(rows))
    return b"".join(counts), datas


def _pascal(name: str) -> bytes:
    raw = name.encode("latin-1", "replace")[:255]
    data = bytes([len(raw)]) + raw
    return data + b"\0" * (-len(data) % 4)


def _unicode_name(name: str) -> bytes:
    text = name.encode("utf-16-be")
    body = struct.pack(">I", len(text) // 2) + text
    body += b"\0" * (-len(body) % 4)
    return b"8BIMluni" + struct.pack(">I", len(body)) + body


def _bbox(alpha: np.ndarray) -> tuple[int, int, int, int] | None:
    ys, xs = np.nonzero(alpha)
    if not len(ys):
        return None
    return int(ys.min()), int(xs.min()), int(ys.max()) + 1, int(xs.max()) + 1


def composite(layers: list[np.ndarray]) -> np.ndarray:
    """Straight-alpha 'over' of uint8 RGBA layers, bottom first."""
    h, w = layers[0].shape[:2]
    rgb = np.zeros((h, w, 3), np.float32)
    a = np.zeros((h, w, 1), np.float32)
    for lay in layers:
        la = lay[..., 3:4].astype(np.float32) / 255
        rgb = lay[..., :3].astype(np.float32) / 255 * la + rgb * (1 - la)
        a = la + a * (1 - la)
    out = np.where(a > 0, rgb / np.maximum(a, 1e-6), 0)
    return np.round(np.concatenate([out, a], -1) * 255).clip(0, 255).astype(np.uint8)


def write_psd(layers: list[tuple[str, np.ndarray]]) -> bytes:
    """`layers`: (name, HxWx4 uint8 straight-alpha RGBA) bottom → top, all the canvas size. Each layer is stored
    cropped to its alpha bounds; the merged image (with transparency) is the composite of all of them."""
    if not layers:
        raise ValueError("a PSD needs at least one layer")
    h, w = layers[0][1].shape[:2]
    if not (1 <= w <= MAX_SIDE and 1 <= h <= MAX_SIDE):
        raise ValueError(f"PSD canvas {w}x{h} out of range")
    records, channel_data = [], []
    for name, arr in layers:
        if arr.shape != (h, w, 4) or arr.dtype != np.uint8:
            raise ValueError(f"layer {name!r}: expected {h}x{w}x4 uint8, got {arr.shape} {arr.dtype}")
        box = _bbox(arr[..., 3])
        top, left, bottom, right = box or (0, 0, 0, 0)
        crop = arr[top:bottom, left:right]
        chans: list[tuple[int, bytes]] = []
        if box is None:
            chans = [(cid, struct.pack(">H", 0)) for cid in (-1, 0, 1, 2)]
        else:
            planes = [crop[..., 3], crop[..., 0], crop[..., 1], crop[..., 2]]
            for cid, p in zip((-1, 0, 1, 2), planes):
                counts, (data,) = _rle_planes([p])
                chans.append((cid, struct.pack(">H", 1) + counts + data))
        extra = struct.pack(">II", 0, 0) + _pascal(name) + _unicode_name(name)
        rec = struct.pack(">iiiiH", top, left, bottom, right, len(chans))
        rec += b"".join(struct.pack(">hI", cid, len(d)) for cid, d in chans)
        # flags 0x08: bit 4 is meaningful, so bit 3 (pixel data irrelevant) reads as clear.
        rec += b"8BIMnorm" + struct.pack(">BBBB", 255, 0, 0x08, 0) + struct.pack(">I", len(extra)) + extra
        records.append(rec)
        channel_data.append(b"".join(d for _, d in chans))
    # Negative count: the merged image's first alpha channel holds the composite's transparency.
    info = struct.pack(">h", -len(layers)) + b"".join(records) + b"".join(channel_data)
    info += b"\0" * (len(info) % 2)
    layer_info = struct.pack(">I", len(info)) + info
    lam = layer_info + struct.pack(">I", 0)

    merged = composite([a for _, a in layers])
    counts, datas = _rle_planes([merged[..., 0], merged[..., 1], merged[..., 2], merged[..., 3]])
    header = b"8BPS" + struct.pack(">H6xHIIHH", 1, 4, h, w, 8, 3)
    return b"".join([header, struct.pack(">I", 0), struct.pack(">I", 0), struct.pack(">I", len(lam)), lam,
                     struct.pack(">H", 1), counts, *datas])
