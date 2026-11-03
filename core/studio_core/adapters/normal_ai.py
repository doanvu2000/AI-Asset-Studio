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
IDLE_SECONDS = 120
REAP_SECONDS = 10


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


def locate(settings: Any = None, models_dir: Path | None = None) -> NormalAi:
    """Env / Settings.normal_ai_dir win; else the pack's folder under the models dir, then the legacy
    setup/normal-ai.ps1 folder, whichever is complete (a partial install resumes in the models dir)."""
    root = os.environ.get("STUDIO_NORMAL_AI_DIR") or getattr(settings, "normal_ai_dir", None)
    if root:
        return NormalAi(Path(root))
    if settings is None and models_dir is None:
        return NormalAi(DEFAULT_ROOT)
    if models_dir is None:
        from ..bootstrap.plan import default_dirs

        models_dir = default_dirs(settings)[1]
    from ..bootstrap.plan import manifest

    own = NormalAi(models_dir / manifest()["normal_ai"]["dir"])
    legacy = NormalAi(DEFAULT_ROOT)
    return next((c for c in (own, legacy) if c.missing() is None), own)


class _WorkerDied(NormalAiError):
    items = 0


class _Worker:
    def __init__(self, key: str, proc: asyncio.subprocess.Process, job: Any) -> None:
        self.key, self.proc, self.job = key, proc, job
        self.busy = False
        self.tail = b""
        self._drain = asyncio.ensure_future(self._read_err())

    async def _read_err(self) -> None:
        # Unread stderr would fill the pipe and stall the worker mid-infer.
        while chunk := await self.proc.stderr.read(4096):
            self.tail = (self.tail + chunk)[-600:]

    @property
    def alive(self) -> bool:
        return self.proc.returncode is None

    def kill(self) -> None:
        if self.job is not None:
            self.job.terminate()
        else:
            try:
                self.proc.kill()
            except ProcessLookupError:
                pass

    async def reap(self) -> None:
        self.kill()
        try:
            await asyncio.wait_for(self.proc.wait(), REAP_SECONDS)
        except asyncio.TimeoutError:
            pass
        self._drain.cancel()
        if self.job is not None:
            self.job.close()


def _worker_args(ai: NormalAi) -> list[str]:
    return [str(ai.python), "-I", str(WORKER), "--serve"]


async def _spawn(args: list[str], env: dict[str, str], key: str) -> _Worker:
    pipes = dict(stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                 limit=1 << 20)
    if sys.platform == "win32":
        from ._winproc import spawn_in_job

        proc, job = await spawn_in_job(args, env=env, **pipes)
    else:
        proc, job = await asyncio.create_subprocess_exec(*args, env=env, **pipes), None
    return _Worker(key, proc, job)


class _Pool:
    """One warm worker shared by every step: the model load (~2.3 s) dominates a small infer, and the lock keeps
    two CPU infers from running on top of each other. Idle workers go away so RAM is not held for nothing."""

    def __init__(self) -> None:
        self.worker: _Worker | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.lock: asyncio.Lock | None = None
        self.timer: asyncio.TimerHandle | None = None
        self.reapers: set[asyncio.Future] = set()
        self.closed = False

    def bind(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self.loop is not loop:
            self.discard()
            self.loop, self.lock, self.closed = loop, asyncio.Lock(), False
        return self.lock

    def discard(self) -> None:
        self.disarm()
        w, self.worker = self.worker, None
        if w is None:
            return
        w.kill()
        try:
            same = w.proc._loop is asyncio.get_running_loop()  # noqa: SLF001
        except RuntimeError:
            same = False
        if not same:  # owning loop is gone or foreign: a reap task would never run, the kill is enough
            if w.job is not None:
                w.job.close()
            return
        fut = asyncio.ensure_future(w.reap())
        self.reapers.add(fut)
        fut.add_done_callback(self.reapers.discard)

    def disarm(self) -> None:
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None

    def arm(self) -> None:
        self.disarm()
        if self.worker is not None:
            self.timer = asyncio.get_running_loop().call_later(IDLE_SECONDS, self._idle)

    def _idle(self) -> None:
        self.timer = None
        self.discard()


_pool = _Pool()


async def shutdown() -> None:
    """Called from the core lifespan so no worker outlives the server."""
    w = _pool.worker
    _pool.closed = True
    _pool.discard()
    if w is not None:
        await w.reap()
    if _pool.reapers:
        await asyncio.gather(*_pool.reapers, return_exceptions=True)


def reopen() -> None:
    _pool.closed = False


def worker_pids() -> list[int]:
    w = _pool.worker
    if w is None or not w.alive:
        return []
    return (w.job.pids() if w.job is not None else []) or [w.proc.pid]


async def _acquire(lock: asyncio.Lock, cancelled: asyncio.Event | None) -> None:
    if cancelled is None:
        await lock.acquire()
        return
    acq, stop = asyncio.ensure_future(lock.acquire()), asyncio.ensure_future(cancelled.wait())
    try:
        await asyncio.wait({acq, stop}, return_when=asyncio.FIRST_COMPLETED)
    except BaseException:
        if acq.done() and not acq.cancelled():
            lock.release()
        raise
    finally:
        stop.cancel()
        if not acq.done():
            acq.cancel()
    if not acq.done() or acq.cancelled():
        raise asyncio.CancelledError
    if cancelled.is_set():
        lock.release()
        raise asyncio.CancelledError


async def _request(w: _Worker, manifest: Path, cancelled: asyncio.Event | None, timeout: float) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    w.busy = True
    try:
        w.proc.stdin.write(json.dumps({"manifest": str(manifest)}).encode() + b"\n")
        await w.proc.stdin.drain()
    except OSError as e:
        raise _WorkerDied(w.tail.decode("utf-8", "replace") or "worker pipe closed") from e
    stop = asyncio.ensure_future(cancelled.wait()) if cancelled is not None else None
    got = 0
    try:
        while True:
            line = asyncio.ensure_future(w.proc.stdout.readline())
            try:
                done, _ = await asyncio.wait([line, *([stop] if stop else [])],
                                             timeout=max(0.0, deadline - loop.time()),
                                             return_when=asyncio.FIRST_COMPLETED)
            finally:
                if not line.done():
                    line.cancel()
            if line not in done:
                if stop is not None and stop in done:
                    raise asyncio.CancelledError
                raise NormalAiError(f"normal-ai worker timed out after {timeout:.0f}s")
            try:
                raw = line.result()
            except ValueError as e:  # line over the stream limit: protocol is broken, do not trust the worker
                raise NormalAiError("normal-ai worker sent an oversized line") from e
            if not raw:
                try:
                    await asyncio.wait_for(w.proc.wait(), REAP_SECONDS)
                except asyncio.TimeoutError:
                    w.kill()
                await asyncio.sleep(0)
                e = _WorkerDied(w.tail.decode("utf-8", "replace") or f"worker exited {w.proc.returncode}")
                e.items = got
                raise e
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            if "error" in msg:
                w.busy = False
                raise NormalAiError(str(msg["error"]))
            if msg.get("done"):
                w.busy = False
                return
            got += 1
    finally:
        if stop is not None:
            stop.cancel()


async def _run(ai: NormalAi, env: dict[str, str], manifest: Path, n_items: int,
               cancelled: asyncio.Event | None) -> None:
    """Send one manifest to the warm worker (spawning it when absent or dead) and wait for its `done` line.
    Cancel / timeout / protocol failure kill the worker; the next call spawns a fresh one."""
    lock = _pool.bind()
    if _pool.closed:
        raise NormalAiError("normal-ai worker is shut down")
    await _acquire(lock, cancelled)
    try:
        _pool.disarm()
        for attempt in (0, 1):
            if _pool.closed:
                raise NormalAiError("normal-ai worker is shut down")
            w = _pool.worker
            if w is not None and (not w.alive or w.key != str(ai.root)):
                _pool.discard()
                w = None
            reused = w is not None
            if w is None:
                w = _pool.worker = await _spawn(_worker_args(ai), env, str(ai.root))
            timeout = SECONDS_PER_FRAME * n_items + (0 if reused else LOAD_SECONDS)
            try:
                await _request(w, manifest, cancelled, timeout)
                return
            except _WorkerDied as e:
                _pool.discard()
                if reused and attempt == 0 and not e.items:
                    continue
                raise
            except BaseException:
                if w.busy:
                    _pool.discard()
                raise
    finally:
        _pool.arm()
        lock.release()


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
            await _run(ai, env, manifest, len(frames), cancelled)
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
