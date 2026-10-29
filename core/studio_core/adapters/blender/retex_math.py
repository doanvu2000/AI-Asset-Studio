"""Mesh retexture math, numpy only: the Blender worker imports it too (UV overlap check before unwrapping).

Orbit cameras are orthographic, so the same `Camera` maps world points to pixels for the ControlNet depth renders,
the sequential "what is already painted" projection and the final UV bake; no Blender render/camera conventions to
keep in sync."""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# (name, azimuth, elevation) in painting order: front first, then the views sharing a seam with what is painted.
VIEW_SETS: dict[int, list[tuple[str, float, float]]] = {
    4: [("front", 0, 20), ("right", 90, 20), ("left", 270, 20), ("back", 180, 20)],
    6: [("front", 0, 15), ("right", 90, 15), ("left", 270, 15), ("back", 180, 15), ("top", 0, 90),
        ("bottom", 0, -90)],
    8: [("front", 0, 15), ("front_right", 60, 15), ("front_left", 300, 15), ("back_right", 120, 15),
        ("back_left", 240, 15), ("back", 180, 15), ("top", 0, 90), ("bottom", 0, -90)],
}
WINDOW_MAX = 32
VIEW_WORDS = {"front": "front view", "right": "right side view", "left": "left side view", "back": "back view",
              "front_right": "front-right three-quarter view", "front_left": "front-left three-quarter view",
              "back_right": "back-right three-quarter view", "back_left": "back-left three-quarter view",
              "top": "top view seen from directly above", "bottom": "bottom view seen from directly below"}


@dataclass(frozen=True)
class Camera:
    name: str
    azimuth: float
    elevation: float
    center: tuple[float, float, float]
    half: float
    size: int

    @property
    def basis(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(right, up, forward); forward points from the camera into the scene. Blender axes: Z up, front is -Y."""
        az, el = math.radians(self.azimuth), math.radians(self.elevation)
        to_cam = np.array([math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el)])
        f = -to_cam / np.linalg.norm(to_cam)
        if abs(self.elevation) >= 89.0:
            up_ref = np.array([math.sin(az), -math.cos(az), 0.0]) * (-1.0 if self.elevation > 0 else 1.0)
        else:
            up_ref = np.array([0.0, 0.0, 1.0])
        r = np.cross(f, up_ref)
        r /= np.linalg.norm(r)
        return r, np.cross(r, f), f

    def project(self, pts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """World points (..., 3) -> (pixel xy (..., 2) with pixel centres at +0.5, depth (...) away from camera)."""
        r, u, f = self.basis
        q = pts - np.asarray(self.center)
        x, y, d = q @ r, q @ u, q @ f
        s = self.size
        return np.stack([(x / self.half + 1) * 0.5 * s, (1 - y / self.half) * 0.5 * s], -1), d

    def to_dict(self) -> dict:
        return {"name": self.name, "azimuth": self.azimuth, "elevation": self.elevation,
                "center": list(self.center), "half": self.half, "size": self.size}


def cameras(pos: np.ndarray, count: int, size: int, margin: float = 1.08) -> list[Camera]:
    """Orbit cameras framing every vertex (pos: (T, 3, 3) triangle corners) the same way in every view."""
    if count not in VIEW_SETS:
        raise ValueError(f"views must be one of {sorted(VIEW_SETS)}")
    pts = pos.reshape(-1, 3)
    lo, hi = pts.min(0), pts.max(0)
    c = (lo + hi) / 2
    radius = float(np.sqrt(((pts - c) ** 2).sum(1)).max()) or 1.0
    return [Camera(n, float(a), float(e), tuple(float(x) for x in c), radius * margin, int(size))
            for n, a, e in VIEW_SETS[count]]


def raster(xy: np.ndarray, z: np.ndarray, w: int, h: int, count: bool = False, budget: int = 1 << 22
           ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray | None]:
    """Z-buffered triangle raster. xy (T, 3, 2) pixel coords, z (T, 3).

    -> (triangle id (h, w) or -1, barycentrics (h, w, 3), depth (h, w), strict hit count per pixel or None).
    Small triangles are tested against a shared k×k pixel window in one vectorised pass (k doubling up to
    WINDOW_MAX), so a 30k-triangle mesh costs a handful of numpy calls; bigger ones each scan their own box."""
    n = w * h
    tri_id = np.full(n, -1, np.int64)
    depth = np.full(n, np.inf, np.float64)
    bary = np.zeros((n, 3), np.float32)
    hits = np.zeros(n, np.int32) if count else None
    xy = np.asarray(xy, np.float64)
    z = np.asarray(z, np.float64)
    x0, y0 = xy[:, :, 0], xy[:, :, 1]
    area = (x0[:, 1] - x0[:, 0]) * (y0[:, 2] - y0[:, 0]) - (x0[:, 2] - x0[:, 0]) * (y0[:, 1] - y0[:, 0])
    ix0 = np.clip(np.ceil(x0.min(1) - 0.5), 0, w).astype(np.int64)
    ix1 = np.clip(np.floor(x0.max(1) - 0.5), -1, w - 1).astype(np.int64)
    iy0 = np.clip(np.ceil(y0.min(1) - 0.5), 0, h).astype(np.int64)
    iy1 = np.clip(np.floor(y0.max(1) - 0.5), -1, h - 1).astype(np.int64)
    bw, bh = ix1 - ix0 + 1, iy1 - iy0 + 1
    live = (bw > 0) & (bh > 0) & (np.abs(area) > 1e-12)
    span = np.maximum(bw, bh)
    lo = 0
    k = 2
    while k <= WINDOW_MAX:
        sel = np.nonzero(live & (span > lo) & (span <= k))[0]
        lo = k
        step = max(1, budget // (k * k))
        for s in range(0, sel.size, step):
            _raster_window(sel[s:s + step], k, xy, z, area, ix0, iy0, bw, bh, w, tri_id, depth, bary, hits)
        k *= 2
    for t in np.nonzero(live & (span > WINDOW_MAX))[0]:
        _raster_big(int(t), xy, z, area, ix0, iy0, bw, bh, w, tri_id, depth, bary, hits)
    return tri_id.reshape(h, w), bary.reshape(h, w, 3), depth.reshape(h, w), (
        hits.reshape(h, w) if hits is not None else None)


def _raster_big(t, xy, z, area, ix0, iy0, bw, bh, w, tri_id, depth, bary, hits) -> None:
    """One large triangle over its own bounding box (a shared k×k window would waste most of k²)."""
    py, px = np.mgrid[iy0[t]:iy0[t] + bh[t], ix0[t]:ix0[t] + bw[t]]
    px, py = px.reshape(-1), py.reshape(-1)
    cx, cy = px + 0.5, py + 0.5
    (x0, y0), (x1, y1), (x2, y2) = xy[t]
    a = area[t]
    w0 = ((x1 - cx) * (y2 - cy) - (x2 - cx) * (y1 - cy)) / a
    w1 = ((x2 - cx) * (y0 - cy) - (x0 - cx) * (y2 - cy)) / a
    w2 = 1.0 - w0 - w1
    if hits is not None:
        strict = (w0 > 1e-4) & (w1 > 1e-4) & (w2 > 1e-4)
        np.add.at(hits, (py * w + px)[strict], 1)
    inside = (w0 >= -1e-7) & (w1 >= -1e-7) & (w2 >= -1e-7)
    pix = (py * w + px)[inside]
    b = np.stack([w0[inside], w1[inside], w2[inside]], -1)
    d = b @ z[t]
    near = d < depth[pix]
    pix, b, d = pix[near], b[near], d[near]
    depth[pix] = d
    tri_id[pix] = t
    bary[pix] = b


def _raster_window(idx, k, xy, z, area, ix0, iy0, bw, bh, w, tri_id, depth, bary, hits) -> None:
    oy, ox = np.divmod(np.arange(k * k), k)
    px = ix0[idx, None] + ox[None, :]
    py = iy0[idx, None] + oy[None, :]
    ok = (ox[None, :] < bw[idx, None]) & (oy[None, :] < bh[idx, None])
    cx, cy = px + 0.5, py + 0.5
    v = xy[idx]
    a = area[idx, None]
    w0 = ((v[:, 1, 0, None] - cx) * (v[:, 2, 1, None] - cy) - (v[:, 2, 0, None] - cx) * (v[:, 1, 1, None] - cy)) / a
    w1 = ((v[:, 2, 0, None] - cx) * (v[:, 0, 1, None] - cy) - (v[:, 0, 0, None] - cx) * (v[:, 2, 1, None] - cy)) / a
    w2 = 1.0 - w0 - w1
    eps = 1e-7
    inside = ok & (w0 >= -eps) & (w1 >= -eps) & (w2 >= -eps)
    if hits is not None:
        strict = ok & (w0 > 1e-4) & (w1 > 1e-4) & (w2 > 1e-4)
        np.add.at(hits, (py * w + px)[strict], 1)
    ti, ci = np.nonzero(inside)
    if not ti.size:
        return
    pix = py[ti, ci] * w + px[ti, ci]
    b = np.stack([w0[ti, ci], w1[ti, ci], w2[ti, ci]], -1)
    d = (b * z[idx[ti]]).sum(-1)
    near = d < depth[pix]
    pix, b, d, tri = pix[near], b[near], d[near], idx[ti[near]]
    order = np.lexsort((d, pix))
    pix, b, d, tri = pix[order], b[order], d[order], tri[order]
    first = np.ones(pix.size, bool)
    first[1:] = pix[1:] != pix[:-1]
    pix, b, d, tri = pix[first], b[first], d[first], tri[first]
    depth[pix] = d
    tri_id[pix] = tri
    bary[pix] = b


def uv_pixels(uv: np.ndarray, size: int) -> np.ndarray:
    """Blender UV (v up) -> texture pixel coords (row 0 at the top of the image)."""
    return np.stack([uv[..., 0] * size, (1.0 - uv[..., 1]) * size], -1)


def uv_overlap(uv: np.ndarray, size: int = 512) -> tuple[float, float]:
    """(overlap fraction of covered texels, covered fraction of the texture) for triangle UVs (T, 3, 2).

    Texels claimed strictly inside two or more triangles: mirrored or stacked islands that a bake would average."""
    if not len(uv):
        return 0.0, 0.0
    tri, _, _, hits = raster(uv_pixels(uv, size), np.zeros(uv.shape[:2]), size, size, count=True)
    covered = int((tri >= 0).sum())
    if not covered:
        return 0.0, 0.0
    return float((hits >= 2).sum()) / covered, covered / float(size * size)


@dataclass
class UvSurface:
    """Every covered texel of the UV layout with its surface point and normal."""
    size: int
    texel: np.ndarray  # flat texel index (M,)
    tri: np.ndarray  # triangle id (M,)
    pos: np.ndarray  # world position (M, 3)
    nrm: np.ndarray  # unit normal (M, 3)
    uv_tri: np.ndarray  # (size, size) triangle id or -1


def uv_surface(pos: np.ndarray, nrm: np.ndarray, uv: np.ndarray, size: int) -> UvSurface:
    tri, bary, _, _ = raster(uv_pixels(uv, size), np.zeros(uv.shape[:2]), size, size)
    flat = tri.reshape(-1)
    texel = np.nonzero(flat >= 0)[0]
    t = flat[texel]
    b = bary.reshape(-1, 3)[texel].astype(np.float64)
    p = (pos[t] * b[:, :, None]).sum(1)
    n = (nrm[t] * b[:, :, None]).sum(1)
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    return UvSurface(size, texel, t, p, n, tri)


@dataclass
class ViewRaster:
    cam: Camera
    tri: np.ndarray  # (s, s) triangle id or -1
    bary: np.ndarray  # (s, s, 3)
    depth: np.ndarray  # (s, s), inf on background
    cos: np.ndarray  # (s, s) facing term toward the camera, 0 on background
    edge: np.ndarray  # (s, s) 0..1, low next to the silhouette and depth jumps


def render_view(cam: Camera, pos: np.ndarray, nrm: np.ndarray, edge_px: int = 6) -> ViewRaster:
    xy, d = cam.project(pos)
    tri, bary, depth, _ = raster(xy, d, cam.size, cam.size)
    hit = tri >= 0
    n = (nrm[np.maximum(tri, 0)] * bary[..., None]).sum(-2)
    n /= np.maximum(np.linalg.norm(n, axis=-1, keepdims=True), 1e-12)
    to_cam = -cam.basis[2]
    cos = np.where(hit, np.clip(n @ to_cam, 0, 1), 0).astype(np.float32)
    return ViewRaster(cam, tri, bary, depth, cos, edge_weight(hit, depth, cam, edge_px))


def orient_normals(pos: np.ndarray, nrm: np.ndarray, count: int = 20, size: int = 384) -> tuple[np.ndarray, int]:
    """Flips the normals of triangles that the z-buffer shows from their back side in most probe views.

    Generated meshes (TRELLIS) mix winding; a two-sided cos would instead let a face's back side, seen edge-on
    at a silhouette, pass the depth test. -> (normals, flipped triangle count)."""
    face = nrm.mean(1)
    votes = np.zeros(len(pos))
    for cam in probe_cameras(pos, count, size):
        xy, d = cam.project(pos)
        tri, _, _, _ = raster(xy, d, cam.size, cam.size)
        t = tri[tri >= 0]
        sign = np.sign(face[t] @ (-cam.basis[2]))
        np.add.at(votes, t, sign)
    flip = votes < 0
    out = nrm.copy()
    out[flip] *= -1
    return out, int(flip.sum())


def _erode(m: np.ndarray) -> np.ndarray:
    out = m.copy()
    out[1:] &= m[:-1]
    out[:-1] &= m[1:]
    out[:, 1:] &= m[:, :-1]
    out[:, :-1] &= m[:, 1:]
    return out


def edge_weight(hit: np.ndarray, depth: np.ndarray, cam: Camera, px: int) -> np.ndarray:
    """Distance (in px, capped) to the silhouette or a depth jump, as a 0..1 ramp: generated pixels right at an
    edge mix in background or the occluding surface, so they should not win the bake."""
    if px <= 0:
        return hit.astype(np.float32)
    jump = 4.0 * 2 * cam.half / cam.size
    d = np.where(hit, depth, 1e30)
    solid = hit.copy()
    for a, b in ((d[1:], d[:-1]), (d[:, 1:], d[:, :-1])):
        big = ~(np.abs(a - b) <= jump)
        if a.shape[0] != d.shape[0]:
            solid[1:] &= ~big
            solid[:-1] &= ~big
        else:
            solid[:, 1:] &= ~big
            solid[:, :-1] &= ~big
    solid &= hit
    dist = np.zeros(hit.shape, np.float32)
    cur = solid
    for _ in range(px):
        dist += cur
        cur = _erode(cur)
    return np.where(hit, np.clip((dist + 1) / (px + 1), 0, 1), 0).astype(np.float32)


def depth_image(v: ViewRaster) -> np.ndarray:
    """ControlNet depth: near = white, the far side of the object ~0.25, background black (uint8 (s, s))."""
    hit = v.tri >= 0
    if not hit.any():
        return np.zeros(v.tri.shape, np.uint8)
    d = v.depth[hit]
    lo, hi = float(d.min()), float(d.max())
    t = (v.depth - lo) / max(hi - lo, 1e-9)
    val = np.where(hit, 1.0 - 0.75 * t, 0.0)
    return np.clip(np.round(val * 255), 0, 255).astype(np.uint8)


def normal_image(v: ViewRaster, nrm: np.ndarray) -> np.ndarray:
    """Camera-space normals as RGB (x right, y up, z toward camera), background black (uint8 (s, s, 3))."""
    hit = v.tri >= 0
    n = (nrm[np.maximum(v.tri, 0)] * v.bary[..., None]).sum(-2)
    n /= np.maximum(np.linalg.norm(n, axis=-1, keepdims=True), 1e-12)
    r, u, f = v.cam.basis
    cam_n = np.stack([n @ r, n @ u, -(n @ f)], -1)
    rgb = np.where(hit[..., None], cam_n * 0.5 + 0.5, 0)
    return np.clip(np.round(rgb * 255), 0, 255).astype(np.uint8)


@dataclass
class Visibility:
    """Texels of a UvSurface seen by one view: which ones, where in the image, and how much to trust them."""
    sel: np.ndarray  # indices into UvSurface arrays
    xy: np.ndarray  # (k, 2) pixel coords in the view
    quality: np.ndarray  # (k,) cos θ × edge ramp, 0..1


def visibility(surf: UvSurface, v: ViewRaster, min_cos: float = 0.08) -> Visibility:
    xy, d = v.cam.project(surf.pos)
    s = v.cam.size
    cos = np.clip(surf.nrm @ (-v.cam.basis[2]), 0, 1)
    # Tight depth tolerance: TRELLIS shells have an inner wall ~0.5% of the size behind the outer one. Grazing
    # texels get more slack because a one-pixel depth sample moves by tan θ there.
    tan = np.sqrt(1 - cos * cos) / np.maximum(cos, 0.05)
    eps = (2 * v.cam.half / s) * (1.0 + np.minimum(tan, 8.0))
    bx, by = np.floor(xy[:, 0]).astype(np.int64), np.floor(xy[:, 1]).astype(np.int64)
    seen = np.zeros(len(d), bool)
    ix, iy = np.clip(bx, 0, s - 1), np.clip(by, 0, s - 1)
    # A texel in the outer half pixel of a silhouette lands on a background pixel; its neighbour still shows it.
    for dx, dy in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)):
        cx, cy = bx + dx, by + dy
        inb = ~seen & (cx >= 0) & (cy >= 0) & (cx < s) & (cy < s)
        cx, cy = np.clip(cx, 0, s - 1), np.clip(cy, 0, s - 1)
        tri_at = v.tri[cy, cx]
        hit = inb & (tri_at >= 0) & ((tri_at == surf.tri) | (np.abs(d - v.depth[cy, cx]) <= eps))
        ix[hit], iy[hit] = cx[hit], cy[hit]
        seen |= hit
    q = cos * v.edge[iy, ix]
    keep = seen & (cos >= min_cos) & (q > 0)
    sel = np.nonzero(keep)[0]
    return Visibility(sel, xy[sel], q[sel].astype(np.float32))


def sample_bilinear(img: np.ndarray, xy: np.ndarray) -> np.ndarray:
    """img (h, w, c) float, xy (k, 2) pixel coords with centres at +0.5."""
    h, w = img.shape[:2]
    x = np.clip(xy[:, 0] - 0.5, 0, w - 1)
    y = np.clip(xy[:, 1] - 0.5, 0, h - 1)
    x0, y0 = np.floor(x).astype(np.int64), np.floor(y).astype(np.int64)
    x1, y1 = np.minimum(x0 + 1, w - 1), np.minimum(y0 + 1, h - 1)
    fx, fy = (x - x0)[:, None], (y - y0)[:, None]
    top = img[y0, x0] * (1 - fx) + img[y0, x1] * fx
    bot = img[y1, x0] * (1 - fx) + img[y1, x1] * fx
    return top * (1 - fy) + bot * fy


def blend_weight(quality: np.ndarray, power: float) -> np.ndarray:
    """Views that face a texel squarely dominate: cos^power, so a grazing view only fills what nothing else sees."""
    return np.power(np.clip(quality, 0, 1), power)


class Accumulator:
    """Running weighted sum of view colours per covered texel, plus the best quality any view gave each texel."""

    def __init__(self, surf: UvSurface, power: float = 4.0):
        self.surf = surf
        self.power = power
        m = surf.texel.size
        self.color = np.zeros((m, 3), np.float64)
        self.weight = np.zeros(m, np.float64)
        self.best = np.zeros(m, np.float32)

    def add(self, vis: Visibility, img: np.ndarray, mask: np.ndarray | None = None) -> None:
        """img (s, s, 3) float 0..1; mask (s, s) 0..1 scales the trust of each pixel (e.g. regenerated area)."""
        q = vis.quality
        if mask is not None:
            q = q * sample_bilinear(mask[..., None].astype(np.float32), vis.xy)[:, 0]
        wgt = blend_weight(q, self.power)
        self.color[vis.sel] += sample_bilinear(img, vis.xy) * wgt[:, None]
        self.weight[vis.sel] += wgt
        np.maximum.at(self.best, vis.sel, q.astype(np.float32))

    def painted(self, min_weight: float = 1e-6) -> np.ndarray:
        return self.weight > min_weight

    def mean(self) -> np.ndarray:
        return self.color / np.maximum(self.weight, 1e-12)[:, None]


def view_lookup(v: ViewRaster, uv: np.ndarray, size: int) -> tuple[np.ndarray, np.ndarray]:
    """For every object pixel of a view, the UV texel it shows: (pixel flat index (k,), texel flat index (k,))."""
    hit = np.nonzero(v.tri.reshape(-1) >= 0)[0]
    t = v.tri.reshape(-1)[hit]
    b = v.bary.reshape(-1, 3)[hit].astype(np.float64)
    tuv = (uv[t] * b[:, :, None]).sum(1)
    px = uv_pixels(tuv, size)
    tx = np.clip(np.floor(px[:, 0]).astype(np.int64), 0, size - 1)
    ty = np.clip(np.floor(px[:, 1]).astype(np.int64), 0, size - 1)
    return hit, ty * size + tx


def known_projection(v: ViewRaster, uv: np.ndarray, acc: Accumulator, known_q: float, better: float,
                     view_q: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """What the texture painted so far looks like from view `v`.

    -> (rgb (s, s, 3) float, known (s, s) bool). A pixel stays known when an earlier view saw its texel with
    quality >= known_q and this view does not see it `better` more squarely (then it is repainted here)."""
    s = v.cam.size
    surf = acc.surf
    lut = np.full(surf.size * surf.size, -1, np.int64)
    lut[surf.texel] = np.arange(surf.texel.size)
    pix, tex = view_lookup(v, uv, surf.size)
    j = lut[tex]
    has = j >= 0
    pix, j = pix[has], j[has]
    best = acc.best[j]
    keep = (best >= known_q) & (acc.weight[j] > 0) & (view_q.reshape(-1)[pix] - best <= better)
    rgb = np.zeros((s * s, 3), np.float32)
    known = np.zeros(s * s, bool)
    rgb[pix[keep]] = acc.mean()[j[keep]]
    known[pix[keep]] = True
    return rgb.reshape(s, s, 3), known.reshape(s, s)


def dilate(m: np.ndarray, px: int) -> np.ndarray:
    out = m.copy()
    for _ in range(px):
        cur = out.copy()
        cur[1:] |= out[:-1]
        cur[:-1] |= out[1:]
        cur[:, 1:] |= out[:, :-1]
        cur[:, :-1] |= out[:, 1:]
        out = cur
    return out


def box_blur(a: np.ndarray, r: int) -> np.ndarray:
    if r <= 0:
        return a.astype(np.float32)
    out = a.astype(np.float32)
    for ax in (0, 1):
        c = np.cumsum(np.pad(out, [(r + 1, r) if i == ax else (0, 0) for i in range(out.ndim)], mode="edge"), ax)
        n = out.shape[ax]
        hi = np.take(c, np.arange(2 * r + 1, 2 * r + 1 + n), axis=ax)
        lo = np.take(c, np.arange(0, n), axis=ax)
        out = (hi - lo) / (2 * r + 1)
    return out


def push_pull(rgb: np.ndarray, known: np.ndarray) -> np.ndarray:
    """Fills unknown pixels from a known-weighted image pyramid (smooth, no seams); known pixels are unchanged."""
    levels = []
    c, w = rgb * known[..., None], known.astype(np.float64)
    while min(w.shape) > 1:
        levels.append((c, w))
        h2, w2 = (w.shape[0] + 1) // 2, (w.shape[1] + 1) // 2
        cp = np.zeros((h2 * 2, w2 * 2, c.shape[2]))
        wp = np.zeros((h2 * 2, w2 * 2))
        cp[:c.shape[0], :c.shape[1]] = c
        wp[:w.shape[0], :w.shape[1]] = w
        c = cp.reshape(h2, 2, w2, 2, -1).sum((1, 3))
        w = wp.reshape(h2, 2, w2, 2).sum((1, 3))
    fill = c / np.maximum(w, 1e-12)[..., None]
    for c, w in reversed(levels):
        up = np.repeat(np.repeat(fill, 2, 0), 2, 1)[:w.shape[0], :w.shape[1]]
        mean = c / np.maximum(w, 1e-12)[..., None]
        a = np.clip(w, 0, 1)[..., None]
        fill = mean * a + up * (1 - a)
    return np.where(known[..., None], rgb, fill)


def probe_cameras(pos: np.ndarray, count: int = 20, size: int = 512) -> list[Camera]:
    """`count` directions spread evenly over the sphere (Fibonacci), framed like the paint views."""
    base = cameras(pos, 4, size)[0]
    golden = math.degrees(math.pi * (3 - math.sqrt(5)))
    return [Camera(f"probe{i}", (golden * i) % 360, math.degrees(math.asin(1 - 2 * (i + 0.5) / count)),
                   base.center, base.half, size) for i in range(count)]


def probe_unpainted(surf: UvSurface, painted: np.ndarray, pos: np.ndarray, nrm: np.ndarray, uv: np.ndarray,
                    cams: list[Camera]) -> list[tuple[int, int]]:
    """(object pixels showing an unpainted texel, object pixels with a texel) per camera."""
    lut = np.full(surf.size * surf.size, -1, np.int64)
    lut[surf.texel] = np.arange(surf.texel.size)
    out = []
    for cam in cams:
        _, tex = view_lookup(render_view(cam, pos, nrm, 0), uv, surf.size)
        j = lut[tex]
        j = j[j >= 0]
        out.append((int((~painted[j]).sum()), int(j.size)))
    return out


def visible_unpainted(surf: UvSurface, painted: np.ndarray, pos: np.ndarray, nrm: np.ndarray, uv: np.ndarray,
                      count: int = 20, size: int = 512) -> float:
    """% of the visible surface (object pixels over `count` probe directions) whose texel no paint view covered.

    Counted on screen, not per texel: generated meshes carry inner walls and hidden cavities that own many texels
    but are never seen, and no camera placement can or needs to paint them."""
    counts = probe_unpainted(surf, painted, pos, nrm, uv, probe_cameras(pos, count, size))
    return 100.0 * sum(b for b, _ in counts) / max(1, sum(t for _, t in counts))


def angle_words(azimuth: float, elevation: float) -> str:
    side = ("front", "front-right", "right side", "back-right", "back", "back-left", "left side",
            "front-left")[int(((azimuth % 360) + 22.5) // 45) % 8]
    if elevation <= -25:
        return f"low angle {side} view from below"
    if elevation >= 50:
        return f"high angle {side} view from above"
    return f"{side} view"


@dataclass
class BakeResult:
    texture: np.ndarray  # (size, size, 3) float 0..1
    covered: np.ndarray  # (size, size) bool: texels inside a UV island
    painted: np.ndarray  # (size, size) bool: texels some view saw
    texel_unpainted_pct: float  # % of island texels no view saw (filled from neighbours), hidden ones included


def finish_texture(acc: Accumulator) -> BakeResult:
    surf = acc.surf
    size = surf.size
    n = size * size
    rgb = np.zeros((n, 3), np.float64)
    painted = np.zeros(n, bool)
    covered = np.zeros(n, bool)
    covered[surf.texel] = True
    ok = acc.painted()
    rgb[surf.texel[ok]] = acc.mean()[ok]
    painted[surf.texel[ok]] = True
    img = push_pull(rgb.reshape(size, size, 3), painted.reshape(size, size))
    cov = int(covered.sum())
    pct = 100.0 * (cov - int(ok.sum())) / cov if cov else 0.0
    return BakeResult(np.clip(img, 0, 1).astype(np.float32), covered.reshape(size, size),
                      painted.reshape(size, size), round(pct, 3))
