"""`codex.image`: headless Codex CLI image generation (native codex.exe, one process per image)."""
from __future__ import annotations

import asyncio
import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from ..config import Settings
from ..contracts import BlobRef, Lane, Outputs, ResourceHealth, StepContext, StepError, StepResult
from ._util import MIME_EXT, log
from ._winproc import spawn_in_job

# A bare "generate an image" prompt makes the model answer with text and never call the tool (P0-b); this suffix
# made it call the image tool 5/5 times.
PROMPT_SUFFIX = ("\n\nIMPORTANT: Only call your image generation tool once to produce the image. Do not run shell "
                 "commands, do not write code, do not try to save files. After the image is generated just reply DONE.")
ALPHA_INSTRUCTION = ("\nOutput a PNG with a TRUE alpha channel: transparent background, no checkerboard, "
                     "no solid fill.")
PROCESS_TIMEOUT_S = 300.0
MAX_COUNT = 8


def build_prompt(prompt: str, transparent: bool) -> str:
    text = prompt.strip()
    if transparent:
        text += ALPHA_INSTRUCTION
    return text + PROMPT_SUFFIX


def build_args(exe: Path, job_dir: Path, prompt: str, refs: list[Path], transparent: bool) -> list[str]:
    args = [str(exe), "exec"]
    for r in refs:
        args += ["-i", str(r)]
    # "--" because -i is variadic and would otherwise swallow the prompt.
    args += ["--json", "--skip-git-repo-check", "--sandbox", "read-only", "--ephemeral", "-C", str(job_dir),
             "--", build_prompt(prompt, transparent)]
    return args


@dataclass
class CodexRunInfo:
    thread_id: str | None = None
    last_message: str | None = None
    errors: list[str] = field(default_factory=list)
    event_types: list[str] = field(default_factory=list)

    def feed(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        try:
            ev = json.loads(line)
        except ValueError:
            return
        if not isinstance(ev, dict):
            return
        t = ev.get("type", "")
        self.event_types.append(t)
        if t == "thread.started":
            self.thread_id = ev.get("thread_id")
        elif t == "error":
            self.errors.append(str(ev.get("message")))
        elif t == "turn.failed":
            self.errors.append(str((ev.get("error") or {}).get("message")))
        item = ev.get("item") or {}
        if item.get("type") == "agent_message":
            self.last_message = item.get("text")
        elif item.get("type") == "error":
            self.errors.append(str(item.get("message")))


def parse_events(lines: Iterable[str]) -> CodexRunInfo:
    info = CodexRunInfo()
    for line in lines:
        info.feed(line)
    return info


def collect_images(codex_home: Path, thread_id: str | None) -> list[Path]:
    if not thread_id:
        return []
    d = codex_home / "generated_images" / thread_id
    if not d.is_dir():
        return []
    return sorted(d.glob("*.png"), key=lambda p: (p.stat().st_mtime, p.name))


class CodexResource:
    name = "codex"

    def __init__(self, settings: Settings, bus: Any = None):
        self.settings = settings
        self.bus = bus
        self._version: str | None = None

    async def _get_version(self) -> str | None:
        if self._version is None and self.settings.codex_exe.exists():
            try:
                proc = await asyncio.create_subprocess_exec(
                    str(self.settings.codex_exe), "--version", stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, creationflags=0x08000000)
                out, _ = await asyncio.wait_for(proc.communicate(), 15)
                self._version = out.decode("utf-8", "replace").strip() or None
            except (OSError, TimeoutError) as e:
                log.warning("codex --version failed: %r", e)
        return self._version

    async def health(self) -> ResourceHealth:
        exe = self.settings.codex_exe
        if not exe.exists():
            return ResourceHealth(name=self.name, status="down", detail={"error": f"missing {exe}"})
        return ResourceHealth(name=self.name, status="up",
                              detail={"version": await self._get_version(), "exe": str(exe), "managed": False})

    async def ensure_started(self) -> None:
        if not self.settings.codex_exe.exists():
            raise StepError("codex_missing", f"codex.exe not found at {self.settings.codex_exe}")

    async def stop(self) -> None:
        return None


class CodexImageExecutor:
    kind = "codex.image"
    lane = Lane.CLOUD
    version = "1"

    def __init__(self, settings: Settings, resource: CodexResource | None = None):
        self.settings = settings
        self.resource = resource or CodexResource(settings)

    def cacheable(self, params: dict[str, Any]) -> bool:
        return False

    def cache_material(self, params: dict[str, Any]) -> str:
        return ""

    async def _stage_refs(self, ctx: StepContext, refs: list[BlobRef]) -> list[Path]:
        def copy_all() -> list[Path]:
            d = ctx.job_dir / "refs"
            d.mkdir(parents=True, exist_ok=True)
            out = []
            for r in refs:
                dst = d / f"{r.hash}{MIME_EXT.get(r.mime, '.png')}"
                shutil.copyfile(ctx.blobs.path(r.hash), dst)
                out.append(dst)
            return out

        return await asyncio.to_thread(copy_all) if refs else []

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        prompt = str(params.get("prompt") or "").strip()
        if not prompt:
            raise StepError("codex_bad_params", "params.prompt is required")
        count = max(1, min(int(params.get("count", 1)), MAX_COUNT))
        transparent = bool(params.get("transparent", False))
        await self.resource.ensure_started()
        refs = await self._stage_refs(ctx, inputs.get("refs", []))
        t0 = time.perf_counter()
        done = 0
        ctx.progress(0, count, "codex")

        async def one(i: int) -> tuple[CodexRunInfo, list[Path]]:
            nonlocal done
            res = await self._run_one(ctx, ctx.job_dir / f"gen{i}", prompt, refs, transparent)
            done += 1
            ctx.progress(done, count, "codex")
            return res

        results = await asyncio.gather(*(one(i) for i in range(count)), return_exceptions=True)
        for r in results:
            if isinstance(r, asyncio.CancelledError):
                raise r
        images: list[BlobRef] = []
        threads, failures = [], []
        for r in results:
            if isinstance(r, BaseException):
                failures.append(r.code if isinstance(r, StepError) else repr(r))
                continue
            info, files = r
            threads.append(info.thread_id)
            for f in files:
                images.append(await ctx.blobs.put_file(f, name=f.name, mime="image/png"))
        if not images:
            first = next((r for r in results if isinstance(r, StepError)), None)
            if first:
                raise first
            raise StepError("codex_no_image", "Codex produced no image", retryable=True,
                            detail={"failures": failures})
        if failures:
            ctx.log(f"{len(failures)}/{count} codex runs failed: {failures}", "warning")
        return StepResult(outputs={"images": images}, meta={
            "thread_ids": threads, "failed": failures, "seconds": round(time.perf_counter() - t0, 2)})

    async def _run_one(self, ctx: StepContext, job_dir: Path, prompt: str, refs: list[Path],
                       transparent: bool) -> tuple[CodexRunInfo, list[Path]]:
        await asyncio.to_thread(job_dir.mkdir, parents=True, exist_ok=True)
        args = build_args(self.settings.codex_exe, job_dir, prompt, refs, transparent)
        proc, job = await spawn_in_job(args, cwd=job_dir, stdin=asyncio.subprocess.DEVNULL,
                                       stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                                       limit=16 * 2**20)
        info = CodexRunInfo()
        stderr_task = asyncio.create_task(proc.stderr.read())

        async def pump() -> None:
            async for raw in proc.stdout:
                info.feed(raw.decode("utf-8", "replace"))
            await proc.wait()

        stderr = b""
        try:
            await asyncio.wait_for(pump(), PROCESS_TIMEOUT_S)
            stderr = await asyncio.wait_for(stderr_task, 5)
        except TimeoutError:
            if proc.returncode is None:
                raise StepError("codex_timeout", f"codex did not finish in {PROCESS_TIMEOUT_S:.0f}s",
                                retryable=True, detail={"thread_id": info.thread_id})
        finally:
            job.close()
            if not stderr_task.done():
                stderr_task.cancel()
        stderr_tail = stderr.decode("utf-8", "replace").strip().splitlines()[-5:]
        images = await asyncio.to_thread(collect_images, self.settings.codex_home, info.thread_id)
        if not images:
            raise StepError("codex_no_image", info.errors[-1] if info.errors else "no image generated",
                            retryable=True, detail={"thread_id": info.thread_id, "rc": proc.returncode,
                                                    "last_message": info.last_message, "errors": info.errors,
                                                    "stderr": stderr_tail})
        return info, images
