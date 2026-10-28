from __future__ import annotations

import asyncio
import base64
import logging
import time
import uuid
import weakref
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import Settings
from ..contracts import (
    DEFAULT_PROJECT,
    STEP_DONE_OK,
    STEP_TERMINAL,
    BlobRef,
    BlobStore,
    EventBus,
    Executor,
    ExecutorRegistry,
    GateResolution,
    Outputs,
    RecipeInfo,
    Repo,
    Resource,
    RunRow,
    RunStatus,
    StepError,
    StepResult,
    StepRow,
    StepStatus,
    VersionRow,
)
from .cache import executor_cache_key
from .chain import CHAIN_ERROR, CHAIN_KEY, CHAIN_PARENT, CHAIN_RUNS, map_inputs, parse_chain, published_outputs
from .gates import GATES, gate_enabled
from .library import RecipeLibrary
from .projects import apply_style
from .qa import version_qa
from .recipe import BLOB_TYPES, Recipe, RecipeError, find_step_refs, ref_value, substitute_refs
from .scheduler import LaneScheduler
from ..store.stats import LIVE_COST, meta_cost

log = logging.getLogger(__name__)

INTERRUPTED = "interrupted"
META_KEY = "$meta"
_RUN_TERMINAL = {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}
_ACTIVE = {StepStatus.READY, StepStatus.WAITING_RESOURCE, StepStatus.RUNNING}
_RETRYABLE = (StepStatus.FAILED, StepStatus.CANCELLED)
_REGENERABLE = (StepStatus.SUCCEEDED, StepStatus.CACHED)

PROGRESS_INTERVAL_S = 0.1
PROGRESS_PERSIST_S = 1.0
PREVIEW_INTERVAL_S = 0.25


class NotFoundError(LookupError):
    pass


class EngineStateError(ValueError):
    pass


def _now() -> float:
    return time.time()


def _new_id() -> str:
    return uuid.uuid4().hex


def model_group(ex: Executor, kind: str) -> str:
    return str(getattr(ex, "model_group", None) or kind)


def _err(code: str, message: str, detail: Any = None, retryable: bool = False) -> dict[str, Any]:
    return {"code": code, "message": message, "detail": detail, "retryable": retryable}


# Executors round meta cost (often to 4 dp); a smaller gap to the add_cost total is the same spend.
COST_EPS = 1e-4


def _prior(meta: dict[str, Any]) -> float:
    v = meta.get("prior_cost_usd")
    return float(v) if isinstance(v, (int, float)) else 0.0


def _fold(meta: dict[str, Any], spent: float) -> dict[str, Any]:
    """Meta for an attempt that ended without a result: live spend dropped, `spent` (that attempt's) into prior."""
    out = {k: v for k, v in meta.items() if k != LIVE_COST}
    total = round(_prior(meta) + spent, 6)
    if total:
        out["prior_cost_usd"] = total
    return out


def _live(meta: dict[str, Any]) -> float:
    v = meta.get(LIVE_COST)
    return float(v) if isinstance(v, (int, float)) else 0.0


def _spent_on(e: StepError) -> float:
    """API spend of the failed call(s) behind `e`: set by the runner while unwinding, else reported in detail."""
    v = getattr(e, "spent_usd", None)
    return float(v) if isinstance(v, (int, float)) else meta_cost(e.detail)


@dataclass
class _RunState:
    run: RunRow
    steps: dict[str, StepRow]
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    tasks: dict[str, asyncio.Task[None]] = field(default_factory=dict)
    ctxs: dict[str, _Ctx] = field(default_factory=dict)
    driver: asyncio.Task[None] | None = None
    closing: bool = False

    def ordered(self) -> list[StepRow]:
        return sorted(self.steps.values(), key=lambda s: s.order)


class _Ctx:
    def __init__(self, engine: StudioEngine, step: StepRow, job_dir: Path):
        self.run_id = step.run_id
        self.step_id = step.id
        self.job_dir = job_dir
        self.blobs = engine.blobs
        self.cancelled = asyncio.Event()
        self._engine = engine
        self._step = step
        self._item = (0, 1)
        self.spent = 0.0  # this attempt's API spend not yet in a result: failed tries, map items, add_cost calls
        self.call_added = 0.0
        self.unreported = 0.0
        self._cost_lock = asyncio.Lock()
        self._last_pub = 0.0
        self._last_persist = 0.0
        self._last_preview = 0.0

    def set_item(self, index: int, total: int) -> None:
        self._item = (index, max(1, total))

    def progress(self, value: float, max: float = 1.0, label: str | None = None, node: str | None = None) -> None:
        frac = min(1.0, value / max) if max else 0.0
        i, n = self._item
        now = time.monotonic()
        if frac < 1.0 and now - self._last_pub < PROGRESS_INTERVAL_S:
            return
        self._last_pub = now
        if n > 1:
            value, max = i + frac, float(n)
        self._engine.bus.publish("step.progress", {"value": value, "max": max, "label": label, "node": node},
                                 run_id=self.run_id, step_id=self.step_id)
        if now - self._last_persist >= PROGRESS_PERSIST_S:
            self._last_persist = now
            self._engine._persist_progress(self._step, (i + frac) / n)

    async def add_cost(self, usd: float) -> None:
        if usd > 0:
            self.call_added += usd
            self.spent += usd
            # Parallel calls could finish their writes out of order; serialised, each writes the latest total.
            async with self._cost_lock:
                await self._engine._note_spent(self._step, self.spent)

    def log(self, msg: str, level: str = "info") -> None:
        self._engine.bus.publish("step.log", {"level": level, "msg": msg}, run_id=self.run_id, step_id=self.step_id)

    def preview(self, data: bytes, mime: str = "image/jpeg") -> None:
        now = time.monotonic()
        if now - self._last_preview < PREVIEW_INTERVAL_S:
            return
        self._last_preview = now
        self._engine.bus.publish("step.preview", {"mime": mime, "b64": base64.b64encode(data).decode("ascii")},
                                 run_id=self.run_id, step_id=self.step_id)


class StudioEngine:
    def __init__(self, *, settings: Settings, repo: Repo, blobs: BlobStore, bus: EventBus,
                 registry: ExecutorRegistry, resources: dict[str, Resource] | list[Resource] | None = None):
        self.settings = settings
        self.repo = repo
        self.blobs = blobs
        self.bus = bus
        self.registry = registry
        self.resources = resources
        self.scheduler = LaneScheduler(settings.lane_limits)
        self.library = RecipeLibrary(settings.recipes_dir, settings.user_recipes_dir)
        self._runs: dict[str, _RunState] = {}
        self._control: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
        self._bg: set[asyncio.Task[Any]] = set()
        self._qa_sem = asyncio.Semaphore(1)
        self._closed = False

    # recipes

    def reload_recipes(self) -> None:
        self.library.reload()

    def recipe_info(self, r: Recipe) -> RecipeInfo:
        info = r.info(self.registry)
        info.source = self.library.sources.get(r.id, "builtin")
        return info

    def list_recipes(self) -> list[RecipeInfo]:
        self.library.maybe_reload()
        return [self.recipe_info(r) for r in self.library.recipes.values()]

    def get_recipe(self, recipe_id: str) -> Recipe:
        r = self.library.get(recipe_id)
        if r is None:
            raise NotFoundError(f"unknown recipe: {recipe_id}")
        return r

    # persistence + events

    async def _set_step(self, step: StepRow, **fields: Any) -> None:
        for k, v in fields.items():
            setattr(step, k, v)
        await self.repo.update_step(step.id, **fields)
        self.bus.publish("step.updated", step.model_dump(mode="json"), run_id=step.run_id, step_id=step.id)

    async def _set_run(self, run: RunRow, **fields: Any) -> None:
        for k, v in fields.items():
            setattr(run, k, v)
        await self.repo.update_run(run.id, **fields)
        self.bus.publish("run.updated", {"id": run.id, "status": run.status.value, "error": run.error,
                                         "ended_at": run.ended_at}, run_id=run.id)

    def _spawn_bg(self, coro: Any) -> None:
        t = asyncio.ensure_future(coro)
        self._bg.add(t)
        t.add_done_callback(self._bg.discard)

    def _persist_progress(self, step: StepRow, value: float) -> None:
        step.progress = value
        self._spawn_bg(self._persist_progress_async(step.id, value))

    async def _persist_progress_async(self, step_id: str, value: float) -> None:
        try:
            await self.repo.update_step(step_id, progress=value)
        except Exception:
            log.exception("persist progress failed")

    async def _load_state(self, run_id: str) -> _RunState:
        run = await self.repo.get_run(run_id)
        if run is None:
            raise NotFoundError(f"unknown run: {run_id}")
        steps = await self.repo.get_steps(run_id)
        return _RunState(run=run, steps={s.key: s for s in steps})

    # runs

    async def check_inputs(self, recipe: Recipe | str, inputs: dict[str, Any]) -> dict[str, Any]:
        """Coerced inputs; raises RecipeError (a ValueError) on bad values or unknown blobs."""
        if isinstance(recipe, str):
            recipe = self.get_recipe(recipe)
        values = recipe.coerce_inputs(inputs or {})
        for name, spec in recipe.inputs.items():
            if spec["type"] in BLOB_TYPES and values.get(name) is not None:
                hashes = values[name] if isinstance(values[name], list) else [values[name]]
                for i, h in enumerate(hashes):
                    ref = await self.blobs.get(h)
                    if ref is None:
                        raise RecipeError(f"input '{name}': unknown blob {h}")
                    exts = spec.get("ext") if i == 0 else None
                    if exts and ref.name and Path(ref.name).suffix.lower().lstrip(".") not in exts:
                        raise RecipeError(f"input '{name}': expected .{'/.'.join(exts)}, got {ref.name}")
        return values

    async def start_run(self, recipe_id: str, inputs: dict[str, Any], params: dict[str, Any] | None = None,
                        project_id: str = "default", title: str = "") -> str:
        if self._closed:
            raise EngineStateError("engine is shut down")
        recipe = self.get_recipe(recipe_id)
        overrides = params or {}
        if project_id != DEFAULT_PROJECT:
            project = await self.repo.get_project(project_id)
            if project is None:
                raise NotFoundError(f"unknown project: {project_id}")
            inputs, applied = apply_style(recipe, inputs, project.style)
            meta = {**(overrides.get(META_KEY) or {}), "project_id": project_id, "project_applied": applied}
            overrides = {**overrides, META_KEY: meta}
        values = await self.check_inputs(recipe, inputs)
        chain = parse_chain((overrides.get(META_KEY) or {}).get(CHAIN_KEY), self.get_recipe)
        if chain:
            overrides = {**overrides, META_KEY: {**overrides[META_KEY], CHAIN_KEY: chain}}
        run = RunRow(id=_new_id(), project_id=project_id, recipe_id=recipe.id, recipe_hash=recipe.hash,
                     title=title, inputs=values, params=overrides, status=RunStatus.RUNNING, created_at=_now())
        publish: dict[str, dict[str, str]] = {}
        for out_name, b in recipe.outputs.items():
            publish.setdefault(b.source, {})[out_name] = b.name
        steps: list[StepRow] = []
        for i, spec in enumerate(recipe.steps):
            try:
                ex = self.registry.resolve(spec.kind)
            except KeyError:
                raise RecipeError(f"step {spec.id}: unknown step kind {spec.kind}") from None
            resolved = recipe.resolve_params(spec, values)
            ov = overrides.get(spec.id)
            if isinstance(ov, dict):
                resolved.update(ov)
            if spec.map_param is not None:
                items = resolved.get(spec.map_param)
                resolved[spec.map_param] = items = items if isinstance(items, list) else [items]
                if not [x for x in items if x not in (None, "")]:
                    raise RecipeError(f"step {spec.id}: '{spec.map_param}' has no items")
            steps.append(StepRow(
                id=_new_id(), run_id=run.id, key=spec.id, kind=spec.kind, lane=ex.lane, deps=spec.deps,
                params=resolved, order=i,
                meta={"spec": {"in": {k: str(v) for k, v in spec.bindings.items()}, "map": spec.map,
                               "map_param": spec.map_param, "publish": publish.get(spec.id, {}),
                               "asset_name": title or recipe.name}},
            ))
        await self.repo.create_run(run)
        await self.repo.create_steps(steps)
        self.bus.publish("run.created", run.model_dump(mode="json"), run_id=run.id)
        st = _RunState(run=run, steps={s.key: s for s in steps})
        self._start_driver(st)
        return run.id

    def _start_driver(self, st: _RunState) -> None:
        self._runs[st.run.id] = st
        st.driver = asyncio.create_task(self._drive(st), name=f"run-{st.run.id}")

    async def _drive(self, st: _RunState) -> None:
        try:
            while True:
                st.wake.clear()
                async with st.lock:
                    if st.closing or await self._pass(st):
                        return
                await st.wake.wait()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("run driver crashed: %s", st.run.id)
            async with st.lock:
                if not st.closing:
                    await self._abort(st, RunStatus.FAILED, f"internal: {e!r}")
        finally:
            if self._runs.get(st.run.id) is st:
                del self._runs[st.run.id]

    async def _pass(self, st: _RunState) -> bool:
        """One scheduling pass; returns True when the driver should exit."""
        while True:
            hard = [s for s in st.ordered()
                    if s.status == StepStatus.FAILED and (s.error or {}).get("code") != INTERRUPTED]
            if hard:
                s = hard[0]
                await self._abort(st, RunStatus.FAILED, f"{s.key}: {(s.error or {}).get('message', 'failed')}")
                return True
            changed = False
            for s in st.ordered():
                if s.status not in (StepStatus.PENDING, StepStatus.READY) or s.key in st.tasks:
                    continue
                if not all(st.steps[d].status in STEP_DONE_OK for d in s.deps):
                    continue
                changed = True
                if s.kind in GATES:
                    await self._open_gate(st, s)
                else:
                    await self._set_step(s, status=StepStatus.READY, meta={**s.meta, "ready_at": _now()})
                    self._spawn_step(st, s)
            if not changed:
                break
        if any(not t.done() for t in st.tasks.values()):
            return False
        statuses = [s.status for s in st.steps.values()]
        if StepStatus.AWAITING_HUMAN in statuses:
            if st.run.status != RunStatus.AWAITING_HUMAN:
                await self._set_run(st.run, status=RunStatus.AWAITING_HUMAN)
            return True
        if any(s.status == StepStatus.FAILED for s in st.steps.values()):
            await self._set_run(st.run, status=RunStatus.INTERRUPTED, error=INTERRUPTED, ended_at=_now())
            return True
        if all(x in STEP_DONE_OK for x in statuses):
            await self._finish_success(st)
            return True
        await self._abort(st, RunStatus.FAILED, "stalled: steps cannot become ready")
        return True

    async def _finish_success(self, st: _RunState) -> None:
        made: list[tuple[VersionRow, StepRow]] = []
        for s in st.ordered():
            spec = s.meta.get("spec", {})
            for out_name in (spec.get("publish") or {}).values():
                for b in s.outputs.get(out_name, []):
                    v = VersionRow(id=_new_id(), project_id=st.run.project_id,
                                   asset_name=spec.get("asset_name") or st.run.title or st.run.recipe_id,
                                   run_id=st.run.id, step_id=s.id, blob=b, created_at=_now())
                    await self.repo.add_version(v)
                    self.bus.publish("version.updated", v.model_dump(mode="json"), run_id=st.run.id, step_id=s.id)
                    made.append((v, s))
        await self._set_run(st.run, status=RunStatus.SUCCEEDED, error=None, ended_at=_now())
        meta = st.run.params.get(META_KEY) or {}
        if meta.get(CHAIN_KEY) and CHAIN_RUNS not in meta:
            await self._start_chain(st.run, list(st.steps.values()))
        if made:
            # Scoring takes seconds on big sheets; it runs after SUCCEEDED and outside the run lock.
            steps = {s.id: s.model_copy(deep=True) for s in st.steps.values()}
            self._spawn_bg(self._score_versions([(v, steps[s.id]) for v, s in made], list(steps.values())))

    async def _start_chain(self, run: RunRow, steps: list[StepRow]) -> None:
        meta = dict(run.params.get(META_KEY) or {})
        outputs = published_outputs(steps)
        started: list[str] = []
        errors: list[str] = []
        for entry in meta.get(CHAIN_KEY) or []:
            try:
                inputs = {**(entry.get("inputs") or {}), **map_inputs(entry["map"], outputs, run.inputs)}
                child_meta = {k: meta[k] for k in ("source",) if k in meta}
                child_meta[CHAIN_PARENT] = run.id
                if entry.get(CHAIN_KEY):
                    child_meta[CHAIN_KEY] = entry[CHAIN_KEY]
                started.append(await self.start_run(entry["recipe_id"], inputs, {META_KEY: child_meta},
                                                    project_id=run.project_id, title=entry.get("title") or run.title))
            except Exception as e:
                log.warning("follow-up %s of run %s not started: %s", entry.get("recipe_id"), run.id, e)
                errors.append(f"{entry.get('recipe_id')}: {e}")
        meta[CHAIN_RUNS] = started
        if errors:
            meta[CHAIN_ERROR] = "; ".join(errors)
        await self._set_run(run, params={**run.params, META_KEY: meta})

    async def _score_versions(self, made: list[tuple[VersionRow, StepRow]], steps: list[StepRow]) -> None:
        async with self._qa_sem:
            for v, s in made:
                if self._closed:
                    return
                try:
                    qa = await version_qa(self.blobs, steps, s, v.blob)
                    if qa is None:
                        continue
                    out = await self.repo.set_version_qa(v.id, qa)
                    if out is not None:
                        self.bus.publish("version.updated", out.model_dump(mode="json"), run_id=v.run_id,
                                         step_id=s.id)
                except Exception as e:
                    log.warning("qa scoring failed for version %s: %s", v.id, e)

    async def wait_qa(self) -> None:
        """Test helper: wait for background QA scoring to finish."""
        while pending := [t for t in self._bg if not t.done()]:
            await asyncio.gather(*pending, return_exceptions=True)

    async def _abort(self, st: _RunState, run_status: RunStatus, error: str | None) -> None:
        interrupted = run_status == RunStatus.INTERRUPTED
        st.closing = True
        live = {k: t for k, t in st.tasks.items() if not t.done()}
        for k, t in live.items():
            ctx = st.ctxs.get(k)
            if ctx is not None:
                ctx.cancelled.set()
            t.cancel()
        if live:
            await asyncio.gather(*live.values(), return_exceptions=True)
        now = _now()
        for s in st.ordered():
            if s.status in STEP_TERMINAL:
                continue
            if s.key in live or s.status in _ACTIVE:
                extra = {"meta": _fold(s.meta, _live(s.meta))} if LIVE_COST in s.meta else {}
                if interrupted:
                    await self._set_step(s, status=StepStatus.FAILED, ended_at=now,
                                         error=_err(INTERRUPTED, "core stopped while step was active", retryable=True),
                                         **extra)
                else:
                    await self._set_step(s, status=StepStatus.CANCELLED, ended_at=now, **extra)
            elif interrupted:
                continue
            elif s.status == StepStatus.AWAITING_HUMAN and run_status == RunStatus.CANCELLED:
                await self._set_step(s, status=StepStatus.CANCELLED, ended_at=now)
            else:
                await self._set_step(s, status=StepStatus.SKIPPED)
        await self._set_run(st.run, status=run_status, error=error, ended_at=now)

    # steps

    def _spawn_step(self, st: _RunState, step: StepRow) -> None:
        task = asyncio.create_task(self._exec_step(st, step), name=f"step-{step.id}")
        st.tasks[step.key] = task

        def done(t: asyncio.Task[None], key: str = step.key) -> None:
            if st.tasks.get(key) is t:
                del st.tasks[key]
                st.ctxs.pop(key, None)
            if not t.cancelled() and t.exception() is not None:
                log.error("step task crashed", exc_info=t.exception())
            st.wake.set()

        task.add_done_callback(done)

    async def _resolve_inputs(self, st: _RunState, step: StepRow) -> Outputs:
        out: Outputs = {}
        for name, raw in (step.meta.get("spec", {}).get("in") or {}).items():
            refs: list[BlobRef] = []
            for part in raw.split("+"):
                src, oname = part.split(".", 1)
                if src == "inputs":
                    v = st.run.inputs.get(oname)
                    for h in v if isinstance(v, list) else ([v] if v else []):
                        ref = await self.blobs.get(h)
                        if ref is None:
                            raise StepError("blob_missing", f"input blob {h} not found")
                        refs.append(ref)
                else:
                    refs.extend(st.steps[src].outputs.get(oname, []))
            out[name] = refs
        return out

    async def _resolve_refs(self, st: _RunState, step: StepRow) -> tuple[dict[str, Any], dict[str, Any]]:
        """step.params keeps the `${steps.…}` placeholders so a retry re-reads the upstream's newest output."""
        found = find_step_refs(step.params)
        if not found:
            return step.params, {}
        values: dict[str, Any] = {}
        for text, sid, oname, path in found:
            src = st.steps.get(sid)
            refs = src.outputs.get(oname, []) if src else []
            if not refs:
                raise StepError("ref_unresolved", f"{text}: step {sid} has no '{oname}' output")
            raw = await asyncio.to_thread(self.blobs.path(refs[0].hash).read_bytes)
            try:
                values[text] = ref_value(raw, path)
            except (UnicodeDecodeError, ValueError, KeyError, IndexError, TypeError) as e:
                raise StepError("ref_unresolved", f"{text}: {type(e).__name__}: {e}") from e
        return substitute_refs(step.params, values), values

    async def _exec_step(self, st: _RunState, step: StepRow) -> None:
        try:
            ex = self.registry.resolve(step.kind)
            inputs = await self._resolve_inputs(st, step)
            params, ref_values = await self._resolve_refs(st, step)
            key = executor_cache_key(ex, step.kind, params, inputs)
            extra: dict[str, Any] = {}
            if ref_values:
                extra["meta"] = {**step.meta, "refs": {k: str(v)[:500] for k, v in ref_values.items()}}
            await self._set_step(step, inputs=inputs, cache_key=key, **extra)
            fresh = bool(step.meta.get("fresh"))
            if fresh:
                await self._set_step(step, meta={k: v for k, v in step.meta.items() if k != "fresh"})
            if key is not None and not fresh:
                hit = await self.repo.cache_get(key)
                if hit is not None:
                    now = _now()
                    await self._set_step(step, status=StepStatus.CACHED, outputs=hit, progress=1.0,
                                         started_at=now, ended_at=now)
                    return
            lane = step.lane

            async def on_wait() -> None:
                await self._set_step(step, status=StepStatus.WAITING_RESOURCE,
                                     meta={**step.meta, "waiting_for": lane.value})

            await self.scheduler.acquire(lane, step.meta.get("ready_at", _now()), on_wait,
                                         hint={"group": model_group(ex, step.kind)})
            try:
                await self._ensure_resource(ex, step)
                job_dir = self.settings.jobs_dir / step.run_id / f"{step.key}-{step.attempt}"
                await asyncio.to_thread(job_dir.mkdir, parents=True, exist_ok=True)
                ctx = _Ctx(self, step, job_dir)
                st.ctxs[step.key] = ctx
                meta = {k: v for k, v in step.meta.items() if k != "waiting_for"}
                await self._set_step(step, status=StepStatus.RUNNING, started_at=_now(), progress=0.0, meta=meta)
                result = await self._run_with_auto_retry(ex, ctx, step, inputs, params)
            finally:
                self.scheduler.release(lane)
            kept = {k: v for k, v in step.meta.items() if k != LIVE_COST}
            await self._set_step(step, status=StepStatus.SUCCEEDED, outputs=result.outputs, progress=1.0,
                                 meta={**kept, **result.meta}, ended_at=_now())
            if key is not None:
                await self.repo.cache_put(key, result.outputs)
        except asyncio.CancelledError:
            raise
        except StepError as e:
            spent = _spent_on(e)
            extra = {"meta": _fold(step.meta, spent)} if spent or LIVE_COST in step.meta else {}
            await self._set_step(step, status=StepStatus.FAILED, ended_at=_now(),
                                 error=_err(e.code, e.message, e.detail, e.retryable), **extra)
        except Exception as e:
            log.exception("step %s (%s) crashed", step.key, step.kind)
            await self._set_step(step, status=StepStatus.FAILED, ended_at=_now(),
                                 error=_err("internal", f"{type(e).__name__}: {e}"))

    async def _ensure_resource(self, ex: Executor, step: StepRow) -> None:
        # Booting ComfyUI/Blender takes 3–30s; showing that as "running" would misreport the step.
        res = getattr(ex, "required_resource", None)
        if res is None or (await res.health()).status in ("up", "busy"):
            return
        await self._set_step(step, status=StepStatus.WAITING_RESOURCE, meta={**step.meta, "waiting_for": res.name})
        await res.ensure_started()

    async def _run_with_auto_retry(self, ex: Executor, ctx: _Ctx, step: StepRow, inputs: Outputs,
                                   params: dict[str, Any]) -> StepResult:
        """GPU/Blender crashes near the VRAM ceiling are usually transient; one in-place rerun saves a manual retry."""
        retries = int(params.get("_auto_retry", getattr(ex, "auto_retry", 0)))
        lost = 0.0
        for n in range(retries + 1):
            ctx.spent = lost
            try:
                result = await self._run_executor(ex, ctx, step, inputs, params)
            except StepError as e:
                lost += _spent_on(e)
                if lost:
                    await self._note_spent(step, lost)
                if not e.retryable or n == retries or ctx.cancelled.is_set():
                    e.spent_usd = lost  # type: ignore[attr-defined]
                    raise
                log.warning("step %s (%s) failed with %s, auto-retry %d/%d", step.key, step.kind, e.code, n + 1,
                            retries)
                ctx.log(f"auto-retry {n + 1}/{retries} after {e.code}: {e.message[:200]}")
                free = getattr(getattr(ex, "required_resource", None), "free", None)
                if free is not None:
                    await free()
                continue
            if n:
                result.meta["auto_retries"] = n
            if lost or ctx.unreported:
                result.meta["prior_cost_usd"] = round(_prior(step.meta) + lost + ctx.unreported, 6)
            return result
        raise AssertionError("unreachable")

    async def _note_spent(self, step: StepRow, usd: float) -> None:
        # Persisted as it accrues so a hard kill (no _abort) still leaves the spend for recover() to fold.
        await self._set_step(step, meta={**step.meta, LIVE_COST: round(usd, 6)})

    @staticmethod
    async def _call(ex: Executor, ctx: _Ctx, params: dict[str, Any], inputs: Outputs) -> tuple[StepResult, float]:
        """One executor call. Its spend is max(ctx.add_cost total, cost in result meta / error detail), so an
        executor may report each paid call and also total them in meta without double counting. Returns the result
        and the reported spend its meta lacks."""
        ctx.call_added = 0.0
        try:
            res = await ex.run(ctx, params, inputs)
        except StepError as e:
            e.spent_usd = max(ctx.call_added, _spent_on(e))  # type: ignore[attr-defined]
            raise
        extra = ctx.call_added - meta_cost(res.meta)
        return res, extra if extra > COST_EPS else 0.0

    async def _run_executor(self, ex: Executor, ctx: _Ctx, step: StepRow, inputs: Outputs,
                            step_params: dict[str, Any]) -> StepResult:
        ctx.unreported = 0.0
        spec = step.meta.get("spec", {})
        mp, mpp = spec.get("map"), spec.get("map_param")
        calls: list[tuple[dict[str, Any], Outputs]]
        if mp:
            calls = [(step_params, {**inputs, mp: [item]}) for item in inputs.get(mp, [])]
        elif mpp:
            values = step_params.get(mpp)
            # `[ "${steps.plan.json.prompts}" ]` resolves to a nested list: map over the upstream list's items.
            values = [x for v in (values if isinstance(values, list) else [values])
                      for x in (v if isinstance(v, list) else [v])]
            calls = [({**step_params, mpp: v}, inputs) for v in values]
        else:
            res, ctx.unreported = await self._call(ex, ctx, step_params, inputs)
            return res
        outputs: Outputs = {}
        metas: list[dict[str, Any]] = []
        base, recorded = ctx.spent, 0.0
        for i, (params, item_inputs) in enumerate(calls):
            ctx.set_item(i, len(calls))
            try:
                res, extra = await self._call(ex, ctx, params, item_inputs)
            except StepError as e:
                e.spent_usd += recorded  # type: ignore[attr-defined]
                raise
            for name, refs in res.outputs.items():
                outputs.setdefault(name, []).extend(refs)
            metas.append(res.meta)
            ctx.unreported += extra
            if cost := meta_cost(res.meta) + extra:
                recorded += cost
                ctx.spent = base + recorded
                await self._note_spent(step, ctx.spent)
            self.bus.publish("step.progress", {"value": i + 1, "max": len(calls), "label": f"{i + 1}/{len(calls)}",
                                               "node": None}, run_id=step.run_id, step_id=step.id)
        return StepResult(outputs=outputs, meta={"items": metas} if any(metas) else {})

    async def _open_gate(self, st: _RunState, step: StepRow) -> None:
        gate = GATES[step.kind]
        try:
            inputs = await self._resolve_inputs(st, step)
        except StepError as e:
            await self._set_step(step, status=StepStatus.FAILED, ended_at=_now(), error=_err(e.code, e.message))
            return
        if not gate_enabled(step.params):
            res = gate.auto_resolution(step.params, inputs)
            try:
                outputs = gate.resolve(step.params, inputs, res)
            except (StepError, ValueError) as e:
                await self._set_step(step, status=StepStatus.FAILED, ended_at=_now(),
                                     error=_err(getattr(e, "code", "gate_auto"), str(e)))
                return
            now = _now()
            await self._set_step(step, status=StepStatus.SUCCEEDED, inputs=inputs, outputs=outputs, progress=1.0,
                                 started_at=now, ended_at=now,
                                 meta={**step.meta, "resolution": {**res.model_dump(), "auto": True}})
            return
        data = gate.awaiting_data(step.params, inputs)
        await self._set_step(step, status=StepStatus.AWAITING_HUMAN, inputs=inputs, started_at=_now(),
                             meta={**step.meta, "gate": {"kind": data["kind"], "k": data["k"]}})
        self.bus.publish("gate.awaiting", data, run_id=step.run_id, step_id=step.id)

    # external control

    def _run_control(self, run_id: str) -> asyncio.Lock:
        # Serialises load-state -> mutate -> start-driver so two callers cannot both see "no driver" and start two.
        lock = self._control.get(run_id)
        if lock is None:
            lock = self._control[run_id] = asyncio.Lock()
        return lock

    async def _with_state(self, run_id: str) -> tuple[_RunState, bool]:
        st = self._runs.get(run_id)
        if st is not None and st.driver is not None and not st.driver.done():
            return st, True
        return await self._load_state(run_id), False

    async def resolve_gate(self, step_id: str, res: GateResolution) -> None:
        row = await self.repo.get_step(step_id)
        if row is None:
            raise NotFoundError(f"unknown step: {step_id}")
        if row.kind not in GATES:
            raise EngineStateError(f"step {step_id} is not a gate")
        async with self._run_control(row.run_id):
            st, alive = await self._with_state(row.run_id)
            async with st.lock:
                step = st.steps[row.key]
                if step.status != StepStatus.AWAITING_HUMAN:
                    raise EngineStateError(f"gate {step.key} is not awaiting (status={step.status.value})")
                gate = GATES[step.kind]
                try:
                    outputs = gate.resolve(step.params, step.inputs, res)
                except StepError as e:
                    await self._set_step(step, status=StepStatus.FAILED, ended_at=_now(),
                                         error=_err(e.code, e.message))
                else:
                    await self._set_step(step, status=StepStatus.SUCCEEDED, outputs=outputs, progress=1.0,
                                         ended_at=_now(), meta={**step.meta, "resolution": res.model_dump()})
                if alive:
                    st.wake.set()
                    return
                if st.run.status == RunStatus.AWAITING_HUMAN:
                    await self._set_run(st.run, status=RunStatus.RUNNING)
            if st.run.status == RunStatus.RUNNING and not self._closed:
                self._start_driver(st)

    async def retry_step(self, step_id: str) -> None:
        row = await self.repo.get_step(step_id)
        if row is None:
            raise NotFoundError(f"unknown step: {step_id}")
        if row.status not in (*_RETRYABLE, *_REGENERABLE):
            raise EngineStateError(f"step {row.key} is {row.status.value}; only failed/cancelled steps can be retried")
        async with self._run_control(row.run_id):
            old = self._runs.get(row.run_id)
            if old is not None and old.driver is not None and old.closing:
                await asyncio.gather(old.driver, return_exceptions=True)
            st, alive = await self._with_state(row.run_id)
            async with st.lock:
                target = st.steps[row.key]
                downstream = self._downstream(st, target.key)
                # Re-running a finished step is only offered as "regenerate" for a gate still waiting on its output.
                regen = target.status in _REGENERABLE and any(
                    st.steps[k].status == StepStatus.AWAITING_HUMAN and st.steps[k].kind in GATES for k in downstream)
                if target.status not in _RETRYABLE and not regen:
                    raise EngineStateError(f"step {target.key} is {target.status.value}; only failed/cancelled steps "
                                           "or the source of a waiting gate can be retried")
                for s in st.ordered():
                    if s.key == target.key or s.key in downstream or s.status in (StepStatus.SKIPPED,
                                                                                    StepStatus.CANCELLED):
                        if s.key in st.tasks:
                            continue
                        # The reset wipes meta; spend already paid for this step must still show in the stats.
                        spent = round(meta_cost(s.meta), 6)
                        await self._set_step(
                            s, status=StepStatus.PENDING, attempt=s.attempt + (1 if s.started_at else 0),
                            error=None, outputs={}, inputs={}, progress=None, cache_key=None, started_at=None,
                            ended_at=None, meta={"spec": s.meta.get("spec", {}),
                                                 **({"prior_cost_usd": spent} if spent else {}),
                                                 **({"fresh": True} if regen and s.key == target.key else {})})
                if st.run.status != RunStatus.RUNNING:
                    await self._set_run(st.run, status=RunStatus.RUNNING, error=None, ended_at=None)
                if alive:
                    st.wake.set()
                    return
            if not self._closed:
                self._start_driver(st)

    @staticmethod
    def _downstream(st: _RunState, key: str) -> set[str]:
        out: set[str] = set()
        frontier = [key]
        while frontier:
            k = frontier.pop()
            for s in st.steps.values():
                if k in s.deps and s.key not in out:
                    out.add(s.key)
                    frontier.append(s.key)
        return out

    async def cancel_run(self, run_id: str) -> None:
        async with self._run_control(run_id):
            st, alive = await self._with_state(run_id)
            if not alive and st.run.status in _RUN_TERMINAL:
                return
            async with st.lock:
                if st.run.status in _RUN_TERMINAL:
                    return
                await self._abort(st, RunStatus.CANCELLED, None)
                st.wake.set()
            if st.driver is not None:
                await asyncio.gather(st.driver, return_exceptions=True)

    async def recover(self) -> None:
        stale = await self.repo.list_steps_by_status(
            [StepStatus.RUNNING, StepStatus.WAITING_RESOURCE, StepStatus.READY])
        touched: set[str] = set()
        now = _now()
        for s in stale:
            extra = {"meta": _fold(s.meta, _live(s.meta))} if LIVE_COST in s.meta else {}
            await self._set_step(s, status=StepStatus.FAILED, ended_at=now,
                                 error=_err(INTERRUPTED, "core restarted while step was active", retryable=True),
                                 **extra)
            touched.add(s.run_id)
        running = await self.repo.list_runs(limit=100_000, status=RunStatus.RUNNING)
        for run in running:
            if run.id in self._runs:
                continue
            if run.id in touched:
                await self._set_run(run, status=RunStatus.INTERRUPTED, error=INTERRUPTED, ended_at=now)
            else:
                # nothing was executing (crash between steps): resuming is safe
                self._start_driver(await self._load_state(run.id))
        for run_id in touched - {r.id for r in running}:
            run = await self.repo.get_run(run_id)
            if run is not None and run.status not in _RUN_TERMINAL and run.status != RunStatus.INTERRUPTED:
                await self._set_run(run, status=RunStatus.INTERRUPTED, error=INTERRUPTED, ended_at=now)

    async def shutdown(self) -> None:
        self._closed = True
        for st in list(self._runs.values()):
            st.closing = True
            if st.driver is not None:
                st.driver.cancel()
                await asyncio.gather(st.driver, return_exceptions=True)
            if st.run.status not in _RUN_TERMINAL:
                await self._abort(st, RunStatus.INTERRUPTED, INTERRUPTED)
        self._runs.clear()
        if self._bg:
            await asyncio.gather(*list(self._bg), return_exceptions=True)

    async def wait_idle(self, run_id: str) -> None:
        """Test/CLI helper: wait until the run's driver exits (terminal or awaiting_human)."""
        st = self._runs.get(run_id)
        if st is not None and st.driver is not None:
            await asyncio.gather(st.driver, return_exceptions=True)
