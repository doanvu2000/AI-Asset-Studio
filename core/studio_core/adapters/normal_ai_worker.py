"""Runs inside the dedicated normal-ai venv (torch + MoGe-2); must not import studio_core.

Usage: python normal_ai_worker.py <manifest.json> (one shot) or `--serve`: stdin lines {"manifest": path} are
answered with one {"out","s"} line per item then {"done": true} or {"error": msg}; EOF on stdin exits. manifest = {"model": path|repo id, "long_side": int,
"items": [{"in": rgba.png, "out": normal.png}]}. Writes camera-space normals as OpenGL tangent-space RGB PNGs
(+G up, +Z toward the viewer) with no alpha handling: the core masks and blends them.
"""
import json
import os
import sys
import time
import warnings

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")  # CPU only: the GPU is shared with other apps
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
warnings.filterwarnings("ignore")

import numpy as np  # noqa: E402
import torch  # noqa: E402
from PIL import Image  # noqa: E402


def _prep(path: str, long_side: int) -> tuple[torch.Tensor, tuple[int, int]]:
    im = Image.open(path).convert("RGBA")
    w, h = im.size
    # Transparent pixels are often black; a neutral grey keeps the model from reading the sprite as a hole.
    bg = Image.new("RGBA", im.size, (128, 128, 128, 255))
    bg.alpha_composite(im)
    scale = long_side / max(w, h)
    nw, nh = max(14, round(w * scale)), max(14, round(h * scale))
    rgb = np.asarray(bg.convert("RGB").resize((nw, nh), Image.BICUBIC if scale > 1 else Image.LANCZOS))
    return torch.from_numpy(rgb.copy()).float().div(255).permute(2, 0, 1), (w, h)


_MODEL: tuple[str, object] | None = None


def _load(cfg: dict):
    global _MODEL
    if _MODEL is None or _MODEL[0] != cfg["model"]:
        sys.path.insert(0, cfg["code_dir"])
        from moge.model.v2 import MoGeModel

        _MODEL = (cfg["model"], MoGeModel.from_pretrained(cfg["model"]).eval())
        torch.set_num_threads(int(cfg.get("threads") or min(16, os.cpu_count() or 4)))
    return _MODEL[1]


def _run(cfg: dict, emit) -> None:
    model = _load(cfg)
    for item in cfg["items"]:
        t0 = time.perf_counter()
        img, (w, h) = _prep(item["in"], int(cfg.get("long_side", 448)))
        with torch.inference_mode():
            out = model.infer(img, resolution_level=int(cfg.get("resolution_level", 5)), use_fp16=False,
                              apply_mask=False)
        n = out["normal"].cpu().numpy()
        # MoGe: OpenCV camera space (x right, y down, z forward); sprites want +G up and +Z toward the viewer.
        n = np.stack([n[..., 0], -n[..., 1], -n[..., 2]], -1)
        n /= np.maximum(np.linalg.norm(n, axis=-1, keepdims=True), 1e-6)
        chans = [Image.fromarray(np.round((n[..., i] * 0.5 + 0.5) * 255).astype(np.uint8)).resize((w, h), Image.BILINEAR)
                 for i in range(3)]
        Image.merge("RGB", chans).save(item["out"])
        emit({"out": item["out"], "s": round(time.perf_counter() - t0, 2)})


def _serve() -> int:
    # stdout carries the protocol only; anything a library prints would corrupt it.
    proto = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)  # also catches C / OpenMP writes to fd 1
    sys.stdout = sys.stderr

    def emit(msg: dict) -> None:
        proto.write(json.dumps(msg) + "\n")
        proto.flush()

    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            _run(json.load(open(json.loads(line)["manifest"], encoding="utf-8")), emit)
            emit({"done": True})
        except Exception as e:  # noqa: BLE001 - reported to the core, worker stays warm
            emit({"error": f"{type(e).__name__}: {e}"[:600]})
    return 0


def main() -> int:
    if sys.argv[1] == "--serve":
        return _serve()
    _run(json.load(open(sys.argv[1], encoding="utf-8")),
         lambda m: print(json.dumps(m), flush=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
