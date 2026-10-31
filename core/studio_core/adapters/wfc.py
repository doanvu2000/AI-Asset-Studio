"""Overlapping-model wave function collapse on numpy: patterns are the n x n windows of hand-written sample bitmaps
(with rotations and mirrors), output cells are collapsed lowest-entropy first, and cells can be pinned to a value
beforehand. Same seed and inputs give the same grid. A contradiction restarts with the next attempt's random stream;
after `max_restarts` it raises WfcContradiction."""
from __future__ import annotations

from collections.abc import Iterable

import numpy as np

DIRS = ((1, 0), (0, 1), (-1, 0), (0, -1))


class WfcError(ValueError):
    pass


class WfcContradiction(WfcError):
    pass


def sample_array(rows: Iterable[str], symbols: str) -> np.ndarray:
    rows = list(rows)
    if not rows or len({len(r) for r in rows}) != 1:
        raise WfcError("sample rows must be non-empty and of equal length")
    bad = {c for r in rows for c in r} - set(symbols)
    if bad:
        raise WfcError(f"sample uses unknown symbols {sorted(bad)}")
    return np.array([[symbols.index(c) for c in r] for r in rows], np.uint8)


def _variants(a: np.ndarray) -> list[np.ndarray]:
    out = []
    for k in range(4):
        r = np.rot90(a, k)
        out += [r, r[:, ::-1]]
    return out


def extract_patterns(samples: list[np.ndarray], n: int = 2) -> tuple[np.ndarray, np.ndarray]:
    """Unique n x n windows of every sample (symmetry-augmented) and how often each occurs."""
    counts: dict[bytes, int] = {}
    shape: dict[bytes, np.ndarray] = {}
    for s in samples:
        for v in _variants(s):
            h, w = v.shape
            for y in range(h - n + 1):
                for x in range(w - n + 1):
                    win = np.ascontiguousarray(v[y:y + n, x:x + n])
                    key = win.tobytes()
                    counts[key] = counts.get(key, 0) + 1
                    shape[key] = win
    if not counts:
        raise WfcError(f"no sample is at least {n}x{n}")
    keys = sorted(counts)
    return (np.stack([shape[k] for k in keys]), np.array([counts[k] for k in keys], np.float64))


def adjacency(patterns: np.ndarray) -> np.ndarray:
    """[dir, a, b] is True when pattern b may sit one step in `dir` from pattern a (their overlap agrees)."""
    p, n = len(patterns), patterns.shape[1]
    adj = np.zeros((4, p, p), bool)
    for d, (dx, dy) in enumerate(DIRS):
        a_rows, a_cols = slice(max(dy, 0), n + min(dy, 0)), slice(max(dx, 0), n + min(dx, 0))
        b_rows, b_cols = slice(max(-dy, 0), n + min(-dy, 0)), slice(max(-dx, 0), n + min(-dx, 0))
        left = patterns[:, a_rows, a_cols].reshape(p, 1, -1)
        right = patterns[:, b_rows, b_cols].reshape(1, p, -1)
        adj[d] = (left == right).all(axis=2)
    return adj


class Wfc:
    def __init__(self, samples: list[np.ndarray], width: int, height: int, n: int = 2):
        if width < n or height < n:
            raise WfcError(f"output {width}x{height} is smaller than the {n}x{n} pattern")
        self.width, self.height, self.n = width, height, n
        self.patterns, self.weights = extract_patterns(samples, n)
        self.adj = adjacency(self.patterns)
        self.gw, self.gh = width - n + 1, height - n + 1

    def _restrict(self, wave: np.ndarray, fixed: dict[tuple[int, int], int]) -> None:
        n = self.n
        for (x, y), v in fixed.items():
            if not (0 <= x < self.width and 0 <= y < self.height):
                raise WfcError(f"pinned cell ({x}, {y}) is outside the {self.width}x{self.height} output")
            for py in range(max(0, y - n + 1), min(self.gh - 1, y) + 1):
                for px in range(max(0, x - n + 1), min(self.gw - 1, x) + 1):
                    wave[py, px] &= self.patterns[:, y - py, x - px] == v

    def _propagate(self, wave: np.ndarray, stack: list[tuple[int, int]]) -> bool:
        while stack:
            x, y = stack.pop()
            here = wave[y, x]
            for d, (dx, dy) in enumerate(DIRS):
                nx, ny = x + dx, y + dy
                if not (0 <= nx < self.gw and 0 <= ny < self.gh):
                    continue
                new = wave[ny, nx] & self.adj[d][here].any(axis=0)
                if not new.any():
                    return False
                if new.sum() != wave[ny, nx].sum():
                    wave[ny, nx] = new
                    stack.append((nx, ny))
        return True

    def _collapse(self, wave: np.ndarray, rng: np.random.Generator) -> bool:
        w = self.weights
        logw = np.log(w)
        while True:
            count = wave.sum(axis=2)
            open_cells = count > 1
            if not open_cells.any():
                return True
            total = wave @ w
            entropy = np.log(total) - (wave @ (w * logw)) / total + rng.random(count.shape) * 1e-6
            entropy[~open_cells] = np.inf
            y, x = np.unravel_index(int(np.argmin(entropy)), entropy.shape)
            p = w * wave[y, x]
            pick = int(rng.choice(len(w), p=p / p.sum()))
            wave[y, x] = False
            wave[y, x, pick] = True
            if not self._propagate(wave, [(int(x), int(y))]):
                return False

    def solve(self, seed: int, fixed: dict[tuple[int, int], int] | None = None, max_restarts: int = 20) -> np.ndarray:
        """Output cells as symbol indices, shape (height, width). Pinned cells that the samples cannot satisfy raise
        WfcError at once; a contradiction while collapsing retries up to `max_restarts` times."""
        base = np.ones((self.gh, self.gw, len(self.weights)), bool)
        self._restrict(base, fixed or {})
        if not base.reshape(-1, base.shape[2]).any(axis=1).all() or not self._propagate(
                base, [(x, y) for y in range(self.gh) for x in range(self.gw)]):
            raise WfcError("the pinned cells contradict the sample patterns")
        for attempt in range(max(1, max_restarts)):
            wave = base.copy()
            if self._collapse(wave, np.random.default_rng([int(seed) & 0xFFFFFFFF, attempt])):
                return self._render(wave)
        raise WfcContradiction(f"wave function collapse hit a contradiction in all {max(1, max_restarts)} attempts "
                               f"(seed {seed}, {self.width}x{self.height})")

    def _render(self, wave: np.ndarray) -> np.ndarray:
        pat = wave.argmax(axis=2)
        out = np.zeros((self.height, self.width), np.uint8)
        for y in range(self.height):
            for x in range(self.width):
                px, py = min(x, self.gw - 1), min(y, self.gh - 1)
                out[y, x] = self.patterns[pat[py, px], y - py, x - px]
        return out
