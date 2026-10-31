from pathlib import Path

import numpy as np
from PIL import Image

from studio_core.export import audit as au


class FakeEmbedder:
    """Vector = mean colour, so a recolour is 'different' and a re-crop of the same art is 'the same'."""

    def __init__(self, ok: bool = True) -> None:
        self.ok, self.calls = ok, 0

    def available(self) -> bool:
        return self.ok

    def embed_images(self, images: list[Image.Image]) -> np.ndarray:
        self.calls += 1
        v = np.array([np.asarray(im.convert("RGB"), np.float32).mean((0, 1)) + 1 for im in images])
        return v / np.linalg.norm(v, axis=1, keepdims=True)


def _project(tmp: Path) -> Path:
    proj = tmp / "proj"
    (proj / "ProjectSettings").mkdir(parents=True)
    art = proj / "Assets" / "Art"
    art.mkdir(parents=True)
    y, x = np.mgrid[0:64, 0:64] / 64.0
    gray = 0.5 + 0.25 * np.sin(x * 9) * np.cos(y * 7) + 0.2 * x
    tint = lambda rgb: (gray[..., None] * np.array(rgb) * 255).clip(0, 255).astype(np.uint8)  # noqa: E731
    Image.fromarray(tint((1.0, 0.5, 0.5))).save(art / "a.png")
    Image.fromarray(tint((0.98, 0.5, 0.5))).save(art / "a_tweak.png")
    # Same luminance pattern in another hue: pHash cannot tell them apart.
    Image.fromarray(tint((0.5, 0.5, 1.0))).save(art / "a_swap.png")
    return proj


def test_phash_fallback_without_model(tmp_path: Path) -> None:
    proj = _project(tmp_path)
    emb = FakeEmbedder(ok=False)
    rep = au.scan(str(proj), embedder=emb)
    assert rep["summary"]["duplicate_method"] == "phash" and emb.calls == 0
    near = [g for g in rep["duplicates"] if g["kind"] == "near"]
    assert near and all(g["method"] == "phash" for g in near)


def test_clip_rejects_recolour_that_phash_groups(tmp_path: Path) -> None:
    proj = _project(tmp_path)
    plain = au.scan(str(proj))
    grouped = {f for g in plain["duplicates"] for f in g["files"]}
    assert "Assets/Art/a_swap.png" in grouped
    rep = au.scan(str(proj), embedder=FakeEmbedder())
    assert rep["summary"]["duplicate_method"] == "clip"
    files = [set(g["files"]) for g in rep["duplicates"]]
    assert {"Assets/Art/a.png", "Assets/Art/a_tweak.png"} in files
    assert all("Assets/Art/a_swap.png" not in f for f in files)


def test_broken_embedder_does_not_fail_scan(tmp_path: Path) -> None:
    class Broken(FakeEmbedder):
        def embed_images(self, images: list[Image.Image]) -> np.ndarray:
            raise RuntimeError("onnx exploded")

    rep = au.scan(str(_project(tmp_path)), embedder=Broken())
    assert rep["summary"]["duplicate_method"] == "phash"
