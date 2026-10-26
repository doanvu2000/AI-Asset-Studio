"""Shared contracts between store, engine, adapters, ops and API. Change only with all owners in mind."""
from __future__ import annotations

import asyncio
import enum
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class Lane(str, enum.Enum):
    GPU = "gpu"
    BLENDER = "blender"
    CLOUD = "cloud"
    CPU = "cpu"
    NONE = "none"  # gates: never occupy a resource


class StepStatus(str, enum.Enum):
    PENDING = "pending"
    READY = "ready"
    WAITING_RESOURCE = "waiting_resource"
    RUNNING = "running"
    AWAITING_HUMAN = "awaiting_human"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    CACHED = "cached"
    SKIPPED = "skipped"


STEP_TERMINAL = {StepStatus.SUCCEEDED, StepStatus.FAILED, StepStatus.CANCELLED, StepStatus.CACHED, StepStatus.SKIPPED}
STEP_DONE_OK = {StepStatus.SUCCEEDED, StepStatus.CACHED, StepStatus.SKIPPED}


class RunStatus(str, enum.Enum):
    RUNNING = "running"
    AWAITING_HUMAN = "awaiting_human"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class VersionStatus(str, enum.Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    REJECTED = "rejected"


class BlobRef(BaseModel):
    hash: str  # blake3 hex
    mime: str
    size: int
    width: int | None = None
    height: int | None = None
    name: str | None = None  # original/display file name


Outputs = dict[str, list[BlobRef]]


class StepError(Exception):
    """Raised by executors. `code` is stable for UI/i18n (e.g. comfy_node_errors, comfy_crashed, codex_no_image)."""

    def __init__(self, code: str, message: str, retryable: bool = False, detail: Any = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retryable = retryable
        self.detail = detail


# ---- rows (persisted) ----

class RunRow(BaseModel):
    id: str
    project_id: str = "default"
    recipe_id: str
    recipe_hash: str
    title: str = ""
    inputs: dict[str, Any] = {}
    params: dict[str, Any] = {}
    status: RunStatus = RunStatus.RUNNING
    error: str | None = None
    created_at: float
    ended_at: float | None = None


class StepRow(BaseModel):
    id: str
    run_id: str
    key: str  # step id inside the recipe
    kind: str
    lane: Lane
    status: StepStatus = StepStatus.PENDING
    attempt: int = 1
    deps: list[str] = []  # recipe keys
    params: dict[str, Any] = {}  # resolved params actually used
    inputs: Outputs = {}
    outputs: Outputs = {}
    cache_key: str | None = None
    error: dict[str, Any] | None = None  # {code, message, detail}
    progress: float | None = None  # 0..1, last known
    meta: dict[str, Any] = {}
    order: int = 0
    started_at: float | None = None
    ended_at: float | None = None


class VersionRow(BaseModel):
    id: str
    project_id: str = "default"
    asset_name: str
    run_id: str
    step_id: str
    blob: BlobRef
    status: VersionStatus = VersionStatus.DRAFT
    created_at: float


class BlobRow(BlobRef):
    created_at: float


DEFAULT_PROJECT = "default"


class PixelStyle(BaseModel):
    size: int | None = None
    palette: str | None = None
    colors: int | None = None
    outline: str | None = None
    dither: str | None = None


class ProjectStyle(BaseModel):
    """Locked look of a project; English text, applied to inputs the user left empty (engine/projects.py)."""
    prompt_prefix: str = ""
    prompt_suffix: str = ""
    negative: str = ""
    preset: str | None = None
    pixel: PixelStyle | None = None
    palette_image: str | None = None
    reference_image: str | None = None
    inputs: dict[str, Any] = {}
    notes: str = ""


class ProjectRow(BaseModel):
    id: str
    name: str
    description: str = ""
    style: ProjectStyle = ProjectStyle()
    created_at: float
    updated_at: float
    archived: bool = False


# ---- events (WS) ----

class Event(BaseModel):
    seq: int
    ts: float
    type: str
    run_id: str | None = None
    step_id: str | None = None
    data: dict[str, Any] = {}


EVENT_TYPES = (
    "run.created",  # data: RunRow
    "run.updated",  # data: {status, error, ended_at}
    "step.updated",  # data: StepRow (full)
    "step.progress",  # data: {value, max, label, node}
    "step.log",  # data: {level, msg}
    "step.preview",  # data: {mime, b64}   (throttled by producer, ≤ 4/s)
    "gate.awaiting",  # data: {kind: pick|approve, k, candidates: [BlobRef]}
    "version.updated",  # data: VersionRow
    "resource.updated",  # data: ResourceHealth
)


class EventBus(Protocol):
    def publish(self, type: str, data: dict[str, Any] | None = None, run_id: str | None = None,
                step_id: str | None = None) -> Event: ...

    def subscribe(self, after_seq: int | None = None) -> AsyncIterator[Event]: ...


# ---- blob store ----

class BlobStore(Protocol):
    root: Path

    async def put_file(self, src: Path, name: str | None = None, mime: str | None = None) -> BlobRef:
        """Copy (never move) src into the content-addressed store; idempotent."""

    async def put_bytes(self, data: bytes, name: str | None = None, mime: str | None = None) -> BlobRef: ...

    async def get(self, hash: str) -> BlobRef | None: ...

    def path(self, hash: str) -> Path: ...

    async def thumbnail(self, hash: str, size: int = 256) -> Path:
        """WebP thumbnail, generated lazily via ops.image.make_thumbnail and cached under thumbs_dir."""


# ---- executors ----

class StepContext(Protocol):
    run_id: str
    step_id: str
    job_dir: Path  # scratch dir, unique per step attempt
    blobs: BlobStore
    cancelled: asyncio.Event

    def progress(self, value: float, max: float = 1.0, label: str | None = None, node: str | None = None) -> None: ...

    def log(self, msg: str, level: str = "info") -> None: ...

    def preview(self, data: bytes, mime: str = "image/jpeg") -> None: ...


class StepResult(BaseModel):
    outputs: Outputs = {}
    meta: dict[str, Any] = {}


@runtime_checkable
class Executor(Protocol):
    kind: str
    lane: Lane
    version: str  # bump to invalidate cache entries

    def cacheable(self, params: dict[str, Any]) -> bool:
        """False for nondeterministic runs (e.g. no explicit seed, cloud image gen)."""

    def cache_material(self, params: dict[str, Any]) -> str:
        """Extra cache-key material (e.g. workflow file hash). Return '' if none."""

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult: ...


ExecutorFactory = Callable[[str], Executor]  # receives full kind, e.g. "comfy.t2i_zimage_turbo"


class ExecutorRegistry:
    def __init__(self) -> None:
        self._exact: dict[str, Executor] = {}
        self._prefix: dict[str, ExecutorFactory] = {}
        self._cache: dict[str, Executor] = {}

    def register(self, ex: Executor) -> None:
        self._exact[ex.kind] = ex

    def register_prefix(self, prefix: str, factory: ExecutorFactory) -> None:
        """prefix like 'comfy.' -> factory(kind)."""
        self._prefix[prefix] = factory

    def resolve(self, kind: str) -> Executor:
        if kind in self._exact:
            return self._exact[kind]
        if kind in self._cache:
            return self._cache[kind]
        for prefix, factory in self._prefix.items():
            if kind.startswith(prefix):
                ex = self._cache[kind] = factory(kind)
                return ex
        raise KeyError(f"unknown step kind: {kind}")

    def kinds(self) -> list[str]:
        return sorted(self._exact) + [p + "*" for p in self._prefix]


# ---- resources (long-lived external processes) ----

class ResourceHealth(BaseModel):
    name: str  # comfyui | blender | codex | claude
    status: str  # down | starting | up | busy | error
    detail: dict[str, Any] = {}  # e.g. {vram_free_mb, version, pid, managed}


class Resource(Protocol):
    name: str

    async def health(self) -> ResourceHealth: ...

    async def ensure_started(self) -> None: ...

    async def stop(self) -> None: ...


# ---- repository (SQLite) ----

class Repo(Protocol):
    async def create_run(self, run: RunRow) -> None: ...
    async def update_run(self, run_id: str, **fields: Any) -> None: ...
    async def get_run(self, run_id: str) -> RunRow | None: ...
    async def list_runs(self, limit: int = 50, before: float | None = None, status: RunStatus | None = None,
                        batch_id: str | None = None, batched: bool = False,
                        project_id: str | None = None) -> list[RunRow]: ...

    async def create_steps(self, steps: list[StepRow]) -> None: ...
    async def update_step(self, step_id: str, **fields: Any) -> None: ...
    async def get_step(self, step_id: str) -> StepRow | None: ...
    async def get_steps(self, run_id: str) -> list[StepRow]: ...
    async def list_steps_by_status(self, statuses: list[StepStatus]) -> list[StepRow]: ...
    async def recent_failed_steps(self, limit: int = 20) -> list[StepRow]: ...

    async def cache_get(self, key: str) -> Outputs | None: ...
    async def cache_put(self, key: str, outputs: Outputs) -> None: ...

    async def add_version(self, v: VersionRow) -> None: ...
    async def set_version_status(self, version_id: str, status: VersionStatus) -> VersionRow | None: ...
    async def list_versions(self, run_id: str | None = None, status: VersionStatus | None = None,
                            limit: int = 200, project_id: str | None = None) -> list[VersionRow]: ...

    async def put_blob_row(self, b: BlobRow) -> None: ...
    async def get_blob_row(self, hash: str) -> BlobRow | None: ...

    async def get_project(self, project_id: str) -> ProjectRow | None: ...


# ---- engine (used by API) ----

class RecipeInfo(BaseModel):
    id: str
    name: str
    description: str = ""
    inputs: dict[str, dict[str, Any]] = {}  # name -> {type, default, label, min, max, options}
    steps: list[dict[str, Any]] = []  # [{id, kind, lane}]
    hash: str
    source: str = "builtin"  # builtin | user | override (user copy shadowing a built-in)


class GateResolution(BaseModel):
    picked: list[str] | None = None  # blob hashes (gate.pick)
    approved: bool | None = None  # gate.approve


class Engine(Protocol):
    def list_recipes(self) -> list[RecipeInfo]: ...

    async def start_run(self, recipe_id: str, inputs: dict[str, Any], params: dict[str, Any] | None = None,
                        project_id: str = "default", title: str = "") -> str: ...

    async def cancel_run(self, run_id: str) -> None: ...

    async def retry_step(self, step_id: str) -> None:
        """New attempt of a failed/cancelled step; downstream steps reset to pending; run back to running."""

    async def resolve_gate(self, step_id: str, res: GateResolution) -> None: ...

    async def recover(self) -> None:
        """On startup: running/waiting steps -> failed(code=interrupted), their runs -> interrupted."""

    async def shutdown(self) -> None: ...
