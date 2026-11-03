"""MoGe-2 ViT-S normal estimation for `op.normal2d`, run on CPU in its own venv (torch is kept out of the core venv)."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from PIL import Image

if TYPE_CHECKING:
    from ..export.normal2d import Normal2DOptions
    from ..export.sheet import Cell

MODEL_ID = "Ruicheng/moge-2-vits-normal"
ADAPTER_VERSION = "1"
DEFAULT_ROOT = Path(r"L:\AI\setup\normal-ai")
WORKER = Path(__file__).with_name("normal_ai_worker.py")
LONG_SIDE = 384
RESOLUTION_LEVEL = 2
MAX_FRAMES = 64
MIN_FRAME_PX = 8
# Added on top of the model normals so the silhouette still rolls off toward the rim.
RIM_WEIGHT = 0.6
SECONDS_PER_FRAME = 20
LOAD_SECONDS = 90


class NormalAiError(RuntimeError):
    pass


@dataclass(frozen=True)
class NormalAi:
    root: Path

    @property
    def python(self) -> Path:
        return self.root / ".venv" / "Scripts" / "python.exe"

    @property
    def code_dir(self) -> Path:
        return self.root / "src" / "moge"

    @property
    def hf_home(self) -> Path:
        return self.root / "hf"

    def snapshot(self) -> str | None:
        """Revision hash of the cached weights, None while they are not downloaded."""
        snaps = self.hf_home / "hub" / ("models--" + MODEL_ID.replace("/", "--")) / "snapshots"
        try:
            for d in sorted(snaps.iterdir()):
                if (d / "model.pt").is_file():
                    return d.name
        except OSError:
            pass
        return None

    def missing(self) -> str | None:
        if not self.python.is_file():
            return f"venv not found at {self.python}"
        if not (self.code_dir / "moge").is_dir():
            return f"MoGe code not found at {self.code_dir}"
        if self.snapshot() is None:
            return f"weights {MODEL_ID} not downloaded under {self.hf_home}"
        return None

    @property
    def material(self) -> str:
        return f"{MODEL_ID}@{self.snapshot()}|adapter={ADAPTER_VERSION}|side={LONG_SIDE}|lvl={RESOLUTION_LEVEL}"


def locate(settings: Any = None) -> NormalAi:
    root = os.environ.get("STUDIO_NORMAL_AI_DIR") or getattr(settings, "normal_ai_dir", None) or DEFAULT_ROOT
    return NormalAi(Path(root))


async def _run(args: list[str], env: dict[str, str], cancelled: asyncio.Event | None, timeout: float) -> str:
    if sys.platform == "win32":
        from ._winproc import spawn_in_job

        proc, job = await spawn_in_job(args, env=env, stdout=asyncio.subprocess.PIPE,
                                       stderr=asyncio.subprocess.PIPE, stdin=asyncio.subprocess.DEVNULL)
        kill = job.terminate
    else:
        proc = await asyncio.create_subprocess_exec(*args, env=env, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.PIPE)
        job, kill = None, proc.kill
    comm = asyncio.ensure_future(proc.communicate())
    try:
        waiters: list[Any] = [comm]
        cancel = asyncio.ensure_future(cancelled.wait()) if cancelled is not None else None
        if cancel is not None:
            waiters.append(cancel)
        done, _ = await asyncio.wait(waiters, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        if cancel is not None:
            cancel.cancel()
        if comm not in done:
            kill()
            await proc.wait()
            if cancelled is not None and cancelled.is_set():
                raise asyncio.CancelledError
            raise NormalAiError(f"normal-ai worker timed out after {timeout:.0f}s")
        out, err = comm.result()
    finally:
        if not comm.done():
            comm.cancel()
        if job is not None:
            job.close()
    if proc.returncode != 0:
        raise NormalAiError((err or b"").decode("utf-8", "replace")[-600:] or f"worker exited {proc.returncode}")
    return out.decode("utf-8", "replace")


async def estimate(ai: NormalAi, frames: list[np.ndarray], cancelled: asyncio.Event | None = None
                   ) -> list[np.ndarray]:
    """Raw model normals (H, W, 3) in [-1, 1], OpenGL convention, one per RGBA frame."""
    why = ai.missing()
    if why:
        raise NormalAiError(why)
    with tempfile.TemporaryDirectory(prefix="normal-ai-") as tmp:
        d = Path(tmp)
        items = []
        for i, f in enumerate(frames):
            Image.fromarray(f, "RGBA").save(d / f"in{i}.png")
            items.append({"in": str(d / f"in{i}.png"), "out": str(d / f"out{i}.png")})
        manifest = d / "manifest.json"
        manifest.write_text(json.dumps({"code_dir": str(ai.code_dir), "model": MODEL_ID, "long_side": LONG_SIDE,
                                        "resolution_level": RESOLUTION_LEVEL, "items": items}), encoding="utf-8")
        env = {**os.environ, "HF_HOME": str(ai.hf_home), "HF_HUB_OFFLINE": "1", "CUDA_VISIBLE_DEVICES": "",
               "PYTHONIOENCODING": "utf-8"}
        try:
            await _run([str(ai.python), "-I", str(WORKER), str(manifest)], env, cancelled,
                       LOAD_SECONDS + SECONDS_PER_FRAME * len(frames))
        except OSError as e:
            raise NormalAiError(f"cannot start normal-ai worker: {e}") from e
        out = []
        for it in items:
            try:
                with Image.open(it["out"]) as im:
                    out.append(np.asarray(im.convert("RGB"), np.float32) / 127.5 - 1)
            except OSError as e:
                raise NormalAiError(f"worker produced no output for {Path(it['in']).name}") from e
    return out


def refine(rgba: np.ndarray, raw: np.ndarray, opts: Normal2DOptions, bevel: float | None = None) -> np.ndarray:
    """Model normals -> uint-ready [0, 1] map: masked by alpha, smoothed inside the silhouette only, strength applied
    to the tilt, rim rolled off with the filter bevel, unit length restored."""
    from ..export.normal2d import FLAT, auto_bevel, height_field, normal_from_height
    from ..ops.tile import soft_blur

    h, w = rgba.shape[:2]
    mask = rgba[..., 3] > 0
    out = np.empty((h, w, 3), np.float32)
    out[...] = FLAT
    if not mask.any():
        return out
    r = max(1, round(min(h, w) / 96))
    wgt = soft_blur(mask.astype(np.float32), r, wrap=False)
    n = np.stack([soft_blur(raw[..., i] * mask, r, wrap=False) for i in range(3)], -1) / np.maximum(wgt, 1e-4)[..., None]
    xy = n[..., :2] * opts.strength
    b = opts.bevel if opts.bevel is not None else (bevel or auto_bevel(w, h))
    rim = normal_from_height(height_field(rgba.astype(np.float32), b, 0.0), mask, opts.strength) * 2 - 1
    xy = xy + rim[..., :2] * RIM_WEIGHT
    z = np.maximum(n[..., 2], 0.15)
    vec = np.concatenate([xy, z[..., None]], -1)
    vec /= np.linalg.norm(vec, axis=-1, keepdims=True)
    out[mask] = (vec * 0.5 + 0.5)[mask]
    return out


def usable(crop: np.ndarray) -> bool:
    return min(crop.shape[:2]) >= MIN_FRAME_PX and int((crop[..., 3] > 0).sum()) >= MIN_FRAME_PX ** 2


async def normal_image_ai(rgba: np.ndarray, opts: Normal2DOptions, cells: list[Cell] | None, ai: NormalAi,
                          cancelled: asyncio.Event | None = None) -> tuple[np.ndarray, int]:
    """AI counterpart of export.normal2d.normal_image; frames the model cannot judge (tiny / empty) keep the
    filter normals. Returns the uint8 map and how many frames fell back."""
    from ..export.normal2d import FLAT, auto_bevel, normal_rgba

    hgt, wid = rgba.shape[:2]
    boxes = [(c.x, c.y, c.w, c.h) for c in cells] if cells else [(0, 0, wid, hgt)]
    crops = [rgba[y:y + h, x:x + w] for x, y, w, h in boxes]
    todo = [i for i, c in enumerate(crops) if c.size and usable(c)]
    if len(todo) > MAX_FRAMES:
        raise NormalAiError(f"{len(todo)} frames exceed the AI limit of {MAX_FRAMES}")
    raws = dict(zip(todo, await estimate(ai, [np.ascontiguousarray(crops[i]) for i in todo], cancelled))) if todo else {}
    bevel = auto_bevel(boxes[0][2], boxes[0][3]) if cells else None
    out = np.empty((hgt, wid, 3), np.float32)
    out[...] = FLAT
    for i, (x, y, w, h) in enumerate(boxes):
        crop = crops[i]
        if not crop.size:
            continue
        nm = refine(crop, raws[i], opts, bevel) if i in raws else normal_rgba(crop, opts, bevel)
        out[y:y + crop.shape[0], x:x + crop.shape[1]] = nm
    fell = sum(1 for i, c in enumerate(crops) if c.size and i not in raws and (c[..., 3] > 0).any())
    return np.clip(np.round(out * 255), 0, 255).astype(np.uint8), fell
