"""CLIP ViT-B/32 image/text embeddings on CPU through onnxruntime (no torch in the core env).

Model: OpenAI CLIP ViT-B/32 (MIT), ONNX export from Xenova/clip-vit-base-patch32 (int8 quantized vision + text),
under <comfy_models_dir>/similarity/clip-vit-base-patch32/ or STUDIO_SIMILAR_MODEL."""
from __future__ import annotations

import json
import os
import re
import threading
import time
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from ..engine.similar import flatten, normalize

MODEL_ID = "clip-vit-b32-q8"
MODEL_DIR = "clip-vit-base-patch32"
HF_URL = "https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/main"
FILES = {"vision_model_quantized.onnx": "onnx/vision_model_quantized.onnx",
         "text_model_quantized.onnx": "onnx/text_model_quantized.onnx", "tokenizer.json": "tokenizer.json"}
SIZE = 224
MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)
CONTEXT = 77
BATCH = 16
INSTALL_HINT = ("Chưa có model tìm ảnh giống. Trong core/: `uv add onnxruntime` (MIT), rồi tải {files} từ {url} "
                "vào {dir} (≈150 MB, CLIP ViT-B/32, MIT).")


def model_dir(settings: Any) -> Path:
    env = os.environ.get("STUDIO_SIMILAR_MODEL")
    if env:
        return Path(env)
    base = getattr(settings, "comfy_models_dir", None)
    return (Path(base) if base else Path(".")) / "similarity" / MODEL_DIR


def missing(root: Path) -> list[str]:
    out = []
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        out.append("onnxruntime")
    return out + [f for f in FILES if not (root / f).is_file()]


def install_hint(root: Path) -> str:
    return INSTALL_HINT.format(files=", ".join(FILES.values()), url=HF_URL, dir=root)


@lru_cache(1)
def _byte_map() -> dict[int, str]:
    bs = list(range(ord("!"), ord("~") + 1)) + list(range(ord("¡"), ord("¬") + 1)) + list(range(ord("®"), ord("ÿ") + 1))
    cs = bs[:]
    n = 0
    for b in range(256):
        if b not in bs:
            bs.append(b)
            cs.append(256 + n)
            n += 1
    return dict(zip(bs, map(chr, cs)))


# tokenizer.json's split regex with \p{L} / \p{N} spelled for Python's re.
_SPLIT = re.compile(r"<\|startoftext\|>|<\|endoftext\|>|'s|'t|'re|'ve|'m|'ll|'d|[^\W\d_]+|\d|(?:[^\s\w]|_)+",
                    re.IGNORECASE)


class ClipTokenizer:
    """Byte-level BPE with the `</w>` word suffix, read from the HF tokenizer.json (saves the `tokenizers` dep,
    which drags in huggingface_hub)."""

    def __init__(self, path: Path) -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        model = data["model"]
        self.vocab: dict[str, int] = model["vocab"]
        merges = [tuple(m.split(" ")) if isinstance(m, str) else tuple(m) for m in model["merges"]]
        self.ranks = {m: i for i, m in enumerate(merges)}
        self.suffix = model.get("end_of_word_suffix") or "</w>"
        self.bos, self.eos = self.vocab["<|startoftext|>"], self.vocab["<|endoftext|>"]
        self.cache: dict[str, list[str]] = {}

    def _bpe(self, word: str) -> list[str]:
        if word in self.cache:
            return self.cache[word]
        parts = list(word[:-1]) + [word[-1] + self.suffix]
        while len(parts) > 1:
            pairs = [(self.ranks.get((a, b), 1 << 30), i) for i, (a, b) in enumerate(zip(parts, parts[1:]))]
            rank, i = min(pairs)
            if rank == 1 << 30:
                break
            parts = parts[:i] + [parts[i] + parts[i + 1]] + parts[i + 2:]
        self.cache[word] = parts
        return parts

    def encode(self, text: str) -> list[int]:
        text = re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip().lower()
        bmap = _byte_map()
        ids = [self.bos]
        for tok in _SPLIT.findall(text):
            word = "".join(bmap[b] for b in tok.encode("utf-8"))
            ids += [self.vocab[p] for p in self._bpe(word) if p in self.vocab]
        return ids[:CONTEXT - 1] + [self.eos]


def preprocess(img: Image.Image) -> np.ndarray:
    """Pads to a square instead of CLIP's centre crop: a tall character keeps its head and feet."""
    im = flatten(img, 1024)
    side = max(im.size)
    sq = Image.new("RGB", (side, side), (255, 255, 255))
    sq.paste(im, ((side - im.width) // 2, (side - im.height) // 2))
    arr = np.asarray(sq.resize((SIZE, SIZE), Image.Resampling.BICUBIC), dtype=np.float32) / 255.0
    return ((arr - MEAN) / STD).transpose(2, 0, 1)


class ClipEmbedder:
    model_id = MODEL_ID

    def __init__(self, root: Path, threads: int | None = None) -> None:
        self.root = root
        self.threads = threads or max(1, min(4, (os.cpu_count() or 2) // 2))
        self._lock = threading.Lock()
        self._vision: Any = None
        self._text: Any = None
        self._tok: ClipTokenizer | None = None
        self._ok: bool | None = None
        self._checked = 0.0

    def available(self) -> bool:
        now = time.monotonic()
        if self._ok is None or (not self._ok and now - self._checked > 30):
            self._ok, self._checked = not missing(self.root), now
        return self._ok

    def _session(self, name: str) -> Any:
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = self.threads
        opts.inter_op_num_threads = 1
        opts.log_severity_level = 3
        return ort.InferenceSession(str(self.root / name), opts, providers=["CPUExecutionProvider"])

    def _load_vision(self) -> Any:
        with self._lock:
            if self._vision is None:
                self._vision = self._session("vision_model_quantized.onnx")
        return self._vision

    def _load_text(self) -> tuple[Any, ClipTokenizer]:
        with self._lock:
            if self._text is None:
                self._tok = ClipTokenizer(self.root / "tokenizer.json")
                self._text = self._session("text_model_quantized.onnx")
        assert self._tok is not None
        return self._text, self._tok

    def embed_images(self, images: list[Image.Image]) -> np.ndarray:
        sess = self._load_vision()
        out = []
        for i in range(0, len(images), BATCH):
            batch = np.stack([preprocess(im) for im in images[i:i + BATCH]])
            out.append(sess.run(None, {"pixel_values": batch})[0])
        return normalize(np.concatenate(out)) if out else np.zeros((0, 512), np.float32)

    def embed_text(self, text: str) -> np.ndarray:
        sess, tok = self._load_text()
        ids = np.array([tok.encode(text)], dtype=np.int64)
        return normalize(sess.run(None, {"input_ids": ids})[0])[0]
