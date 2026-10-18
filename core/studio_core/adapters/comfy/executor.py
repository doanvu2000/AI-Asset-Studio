"""`comfy.<workflow>` executor: binds params/images into an index.json workflow and runs it on the shared server."""
from __future__ import annotations

import asyncio
import copy
import json
import random
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

import blake3

from ...config import Settings
from ...contracts import BlobRef, Lane, Outputs, StepContext, StepError, StepResult
from .._util import IMAGE_EXT, MESH_EXT
from .client import history_files
from .server import ComfyResource

SEED_MAX = 2**53
PREVIEW_INTERVAL_S = 0.25


def load_index(workflows_dir: Path) -> dict[str, Any]:
    return json.loads((workflows_dir / "index.json").read_text(encoding="utf-8"))


def output_prefix(run_id: str, step_id: str) -> str:
    return f"studio/{run_id}/{step_id}"


def build_prompt(workflow: dict[str, Any], entry: dict[str, Any], params: dict[str, Any],
                 image_names: list[str], prefix: str, rng: random.Random | None = None
                 ) -> tuple[dict[str, Any], dict[str, Any]]:
    """-> (prompt, applied values). Params keys are raw '<node_id>.<input>'; keys starting with '_' are options."""
    prompt = copy.deepcopy(workflow)
    values: dict[str, Any] = {}
    image_keys = [i["key"] for i in entry.get("images", [])]
    if len(image_names) > len(image_keys):
        raise StepError("comfy_too_many_images", f"workflow takes {len(image_keys)} image(s), got {len(image_names)}")
    values.update(zip(image_keys, image_names))
    missing = [k for k in image_keys[len(image_names):] if k not in params]
    if missing:
        raise StepError("comfy_missing_image", f"no input image for {missing}")
    rng = rng or random.Random()
    for key in entry.get("seeds", []):
        values[key] = rng.randint(0, SEED_MAX)
    values.update({k: v for k, v in params.items() if not k.startswith("_")})
    for key, v in values.items():
        node, sep, name = key.rpartition(".")
        if not sep or node not in prompt:
            raise StepError("comfy_bad_param", f"param {key!r} does not address a node input of this workflow")
        prompt[node]["inputs"][name] = v
    for out in entry.get("outputs", []):
        node = prompt.get(out["node"])
        if node is not None and "filename_prefix" in node["inputs"]:
            node["inputs"]["filename_prefix"] = prefix
    return prompt, values


class ComfyExecutor:
    lane = Lane.GPU
    version = "1"

    def __init__(self, kind: str, settings: Settings, resource: ComfyResource):
        self.kind = kind
        self.workflow = kind.removeprefix("comfy.")
        self.settings = settings
        self.resource = resource
        self.required_resource = resource
        index = load_index(settings.comfy_workflows_dir)
        if self.workflow not in index:
            raise KeyError(f"unknown comfy workflow: {self.workflow}")
        self.entry: dict[str, Any] = index[self.workflow]
        self.workflow_path = settings.comfy_workflows_dir / self.entry["file"]

    def cacheable(self, params: dict[str, Any]) -> bool:
        return all(k in params for k in self.entry.get("seeds", []))

    def cache_material(self, params: dict[str, Any]) -> str:
        h = blake3.blake3(self.workflow_path.read_bytes())
        h.update(json.dumps(self.entry, sort_keys=True).encode())
        return h.hexdigest()

    async def _stage_images(self, ctx: StepContext, refs: list[BlobRef]) -> list[str]:
        dest_dir = self.settings.comfy_dir / "input" / "studio"

        def copy_all() -> list[str]:
            dest_dir.mkdir(parents=True, exist_ok=True)
            names = []
            for ref in refs:
                src = ctx.blobs.path(ref.hash)
                dst = dest_dir / f"{ref.hash}.png"
                if not dst.exists() or dst.stat().st_size != src.stat().st_size:
                    tmp = dst.with_suffix(f".{uuid.uuid4().hex[:8]}.tmp")
                    shutil.copyfile(src, tmp)
                    tmp.replace(dst)
                names.append(f"studio/{dst.name}")
            return names

        return await asyncio.to_thread(copy_all)

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        t0 = time.perf_counter()
        workflow = json.loads(await asyncio.to_thread(self.workflow_path.read_text, encoding="utf-8"))
        names = await self._stage_images(ctx, inputs.get("image", []))
        prompt, values = build_prompt(workflow, self.entry, params, names, output_prefix(ctx.run_id, ctx.step_id))
        ctx.job_dir.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread((ctx.job_dir / "prompt.json").write_text, json.dumps(prompt, indent=1), "utf-8")

        await self.resource.ensure_started()
        client = self.resource.client
        if client is None:
            raise StepError("comfy_unavailable", "ComfyUI is not running", retryable=True)
        tracker = _Progress(ctx, prompt)
        prompt_id = str(uuid.uuid4())
        ctx.log(f"comfy {self.workflow} prompt_id={prompt_id}")
        pid, entry = await client.run(prompt, prompt_id, on_event=tracker.on_event, cancelled=ctx.cancelled,
                                      timeout=float(params.get("_timeout_s", 1800)))
        ctx.progress(1.0, 1.0, "done")

        files = history_files(entry, client.output_dir, [o["node"] for o in self.entry.get("outputs", [])])
        outputs: Outputs = {"images": [], "meshes": []}
        for f in files:
            ext = f.suffix.lower()
            if ext in IMAGE_EXT:
                outputs["images"].append(await ctx.blobs.put_file(f, name=f.name, mime=IMAGE_EXT[ext]))
            elif ext in MESH_EXT:
                outputs["meshes"].append(await ctx.blobs.put_file(f, name=f.name, mime=MESH_EXT[ext]))
        outputs = {k: v for k, v in outputs.items() if v}
        if not outputs:
            raise StepError("comfy_no_output", "workflow finished without output files", retryable=True,
                            detail={"outputs": entry.get("outputs")})
        seeds = {k: values[k] for k in self.entry.get("seeds", []) if k in values}
        return StepResult(outputs=outputs, meta={
            "prompt_id": pid, "workflow": self.workflow, "comfyui_version": self.resource.version,
            "seconds": round(time.perf_counter() - t0, 2), "seeds": seeds, "files": [str(f) for f in files],
            "previews": tracker.previews_sent,
        })


class _Progress:
    def __init__(self, ctx: StepContext, prompt: dict[str, Any]):
        self.ctx = ctx
        self.prompt = prompt
        self.total = max(len(prompt), 1)
        self.done: set[str] = set()
        self.current: str | None = None
        self.last_preview = 0.0
        self.previews_sent = 0

    def _label(self, node: str | None) -> str | None:
        return (self.prompt.get(node) or {}).get("class_type") if node else None

    def _emit(self, inner: float = 0.0) -> None:
        value = min((len(self.done) + inner) / self.total, 0.999)
        self.ctx.progress(value, 1.0, self._label(self.current), self.current)

    def on_event(self, t: str, d: dict[str, Any]) -> None:
        if t == "execution_cached":
            self.done.update(str(n) for n in d.get("nodes") or [])
            self._emit()
        elif t == "executing":
            if self.current:
                self.done.add(self.current)
            self.current = d.get("node")
            self._emit()
        elif t == "executed" and d.get("node"):
            self.done.add(str(d["node"]))
        elif t == "progress":
            mx = d.get("max") or 0
            if d.get("node"):
                self.current = str(d["node"])
            self._emit(d.get("value", 0) / mx if mx else 0.0)
        elif t == "_preview":
            now = time.monotonic()
            if now - self.last_preview >= PREVIEW_INTERVAL_S:
                self.last_preview = now
                self.previews_sent += 1
                self.ctx.preview(d["bytes"], d["mime"])
