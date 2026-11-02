"""Workflows: saved graphs of recipe runs where one node's outputs feed the next node's inputs.

Every node runs as an ordinary run (start_run) tagged with `$meta.workflow_run_id/workflow_node_id`. The workflow
run row is the only scheduler state: each child-run status change (StudioEngine._set_run) re-derives the node
states from the child runs and starts the nodes whose dependencies all succeeded, so a core restart resumes by
running the same pass (recover).
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import uuid
import weakref
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from ..contracts import (
    DEFAULT_PROJECT,
    STEP_DONE_OK,
    RunRow,
    RunStatus,
    WorkflowDef,
    WorkflowNodeState,
    WorkflowNodeStatus as NS,
    WorkflowRunRow,
    WorkflowStatus as WS,
)
from .chain import published_outputs
from .library import _signature, dump_yaml
from .recipe import BLOB_TYPES, INPUT_TYPES, Recipe, RecipeError, coerce_value

log = logging.getLogger(__name__)

META_KEY = "$meta"
FLOW_RUN_KEY = "workflow_run_id"
FLOW_NODE_KEY = "workflow_node_id"
FLOW_ATTEMPT_KEY = "workflow_attempt"
FLOW_META_KEYS = (FLOW_RUN_KEY, FLOW_NODE_KEY, FLOW_ATTEMPT_KEY)
MAX_NODES = 24
MAX_FLOW_BYTES = 256 * 1024
WORKFLOW_REF = "workflow"
_NAME = re.compile(r"[A-Za-z_][\w-]*")
_SRC = re.compile(r"(?P<node>[A-Za-z_][\w-]*)\.(?P<kind>outputs|inputs)\.(?P<name>[A-Za-z_][\w-]*)"
                  r"(?:\[(?P<idx>-?\d+)\])?")
# The id is the user file name: Windows refuses these whatever the extension.
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_ACTIVE_WR = [WS.QUEUED, WS.RUNNING, WS.AWAITING_HUMAN]
_LIVE_NODE = (NS.RUNNING, NS.AWAITING_HUMAN)
_DEAD_NODE = (NS.FAILED, NS.CANCELLED, NS.BLOCKED)
_RUN_TO_NODE = {RunStatus.RUNNING: NS.RUNNING, RunStatus.AWAITING_HUMAN: NS.AWAITING_HUMAN,
                RunStatus.SUCCEEDED: NS.SUCCEEDED, RunStatus.FAILED: NS.FAILED, RunStatus.CANCELLED: NS.CANCELLED}
_SNAP_FIELDS = ("status", "nodes", "error", "ended_at")


class FlowNotFound(LookupError):
    pass


class FlowStateError(ValueError):
    pass


class FlowInvalid(ValueError):
    def __init__(self, issues: list[dict[str, Any]]):
        super().__init__("; ".join(f"{i['node'] + ': ' if i['node'] else ''}{i['message']}" for i in issues))
        self.issues = issues


def valid_name(v: Any) -> bool:
    return isinstance(v, str) and _NAME.fullmatch(v) is not None


def valid_flow_id(v: Any) -> bool:
    return valid_name(v) and v.upper() not in _RESERVED


def strip_flow_meta(params: dict[str, Any]) -> dict[str, Any]:
    """Workflow tags in `$meta` are set by FlowEngine only: a hand-made run carrying them could be adopted as a node."""
    meta = params.get(META_KEY)
    if isinstance(meta, dict) and any(k in meta for k in FLOW_META_KEYS):
        return {**params, META_KEY: {k: v for k, v in meta.items() if k not in FLOW_META_KEYS}}
    return params


def _issue(code: str, message: str, node: str | None = None, field: str | None = None, level: str = "error",
           **params: Any) -> dict[str, Any]:
    return {"level": level, "node": node, "field": field, "code": code, "params": params, "message": message}


def parse_source(src: str) -> tuple[str, str, str, int | None] | None:
    """(node | 'workflow', 'outputs' | 'inputs', name, index) or None when malformed."""
    m = _SRC.fullmatch(src) if isinstance(src, str) else None
    if m is None:
        return None
    idx = m["idx"]
    return m["node"], m["kind"], m["name"], int(idx) if idx is not None else None


def flow_deps(d: WorkflowDef) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for n in d.nodes:
        deps: list[str] = []
        for src in n.map.values():
            p = parse_source(src)
            if p and p[0] != WORKFLOW_REF and p[0] not in deps:
                deps.append(p[0])
        out[n.id] = deps
    return out


def downstream(d: WorkflowDef, node_id: str) -> set[str]:
    deps = flow_deps(d)
    out: set[str] = set()
    frontier = [node_id]
    while frontier:
        k = frontier.pop()
        for nid, ds in deps.items():
            if k in ds and nid not in out:
                out.add(nid)
                frontier.append(nid)
    return out


def _cycle(deps: dict[str, list[str]]) -> list[str]:
    """Nodes left over by a topological sort: empty when the graph has no cycle."""
    remaining = {k: {d for d in v if d in deps} for k, v in deps.items()}
    while ready := [k for k, v in remaining.items() if not v]:
        for k in ready:
            del remaining[k]
        for v in remaining.values():
            v.difference_update(ready)
    return sorted(remaining)


def validate_def(d: WorkflowDef, get_recipe: Callable[[str], Recipe | None]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not valid_flow_id(d.id):
        out.append(_issue("invalid_flow_id", f"invalid workflow id {d.id!r}", field="id", id=d.id))
    if not d.title.strip():
        out.append(_issue("title_empty", "title must not be empty", field="title"))
    if not d.nodes:
        out.append(_issue("no_nodes", "a workflow needs at least one node", field="nodes"))
    if len(d.nodes) > MAX_NODES:
        out.append(_issue("too_many_nodes", f"at most {MAX_NODES} nodes ({len(d.nodes)} given)", field="nodes",
                          max=MAX_NODES, count=len(d.nodes)))
        return out
    for name, spec in d.inputs.items():
        if not valid_name(name):
            out.append(_issue("bad_input_name", f"bad input name {name!r}", field=f"inputs.{name}", name=name))
        if spec.type not in INPUT_TYPES:
            out.append(_issue("unknown_input_type", f"input '{name}' has unknown type {spec.type!r}", field=f"inputs.{name}",
                              name=name, type=spec.type))
        elif spec.default is not None:
            try:
                coerce_value(name, {"type": spec.type}, spec.default)
            except RecipeError as e:
                out.append(_issue("bad_default", f"default: {e}", field=f"inputs.{name}", name=name, detail=str(e)))
    counts = Counter(n.id for n in d.nodes)
    nodes = {n.id: n for n in d.nodes}
    recipes = {n.id: get_recipe(n.recipe_id) for n in d.nodes}
    for i, n in enumerate(d.nodes):
        if not valid_name(n.id) or n.id == WORKFLOW_REF:
            out.append(_issue("invalid_node_id", f"node #{i} has invalid id {n.id!r}", n.id, "id", index=i, id=n.id))
        if counts[n.id] > 1:
            counts[n.id] = 0
            out.append(_issue("duplicate_node_id", f"duplicate node id {n.id!r}", n.id, "id", id=n.id))
        r = recipes[n.id]
        if r is None:
            out.append(_issue("unknown_recipe", f"unknown recipe {n.recipe_id!r}", n.id, "recipe_id", recipe=n.recipe_id))
            continue
        for name in n.inputs:
            if name not in r.inputs:
                out.append(_issue("recipe_no_input", f"recipe {r.id} has no input '{name}'", n.id, f"inputs.{name}",
                                  recipe=r.id, name=name))
        for target, src in n.map.items():
            out.extend(_check_map(d, nodes, recipes, n.id, r, target, src))
            if target in n.inputs:
                out.append(_issue("set_and_mapped", f"'{target}' is both set and mapped; the mapped value wins", n.id,
                                  f"map.{target}", "warning", target=target))
        for name, spec in r.inputs.items():
            if not spec.get("required", True) or "default" in spec or n.inputs.get(name) not in (None, ""):
                continue
            if name not in n.map:
                out.append(_issue("required_unset", f"required input '{name}' of {r.id} is neither set nor mapped", n.id,
                                  f"inputs.{name}", name=name, recipe=r.id))
                continue
            p = parse_source(n.map[name])
            w = d.inputs.get(p[2]) if p and p[0] == WORKFLOW_REF else None
            if p and w is not None and not w.required and w.default is None:
                out.append(_issue("required_from_optional", f"'{name}' of {r.id} is required but workflow input '{p[2]}' is optional "
                                  "without a default", n.id, f"map.{name}", "warning", name=name, recipe=r.id,
                                  input=p[2]))
    if cyc := _cycle(flow_deps(d)):
        out.append(_issue("cycle", f"nodes form a cycle: {', '.join(cyc)}", field="nodes", nodes=cyc))
    return out


def _check_map(d: WorkflowDef, nodes: dict[str, Any], recipes: dict[str, Recipe | None], nid: str, r: Recipe,
               target: str, src: str) -> list[dict[str, Any]]:
    field = f"map.{target}"
    if target not in r.inputs:
        return [_issue("map_target_unknown", f"map target '{target}' is not an input of {r.id}", nid, field,
                       target=target, recipe=r.id)]
    target_type = r.inputs[target].get("type", "text")
    p = parse_source(src)
    if p is None:
        return [_issue("bad_source", f"map.{target} must be '<node>.outputs.<name>[i]', '<node>.inputs.<name>' or "
                       f"'workflow.inputs.<name>', got {src!r}", nid, field, target=target, source=src)]
    ref, kind, name, _ = p
    if ref == WORKFLOW_REF:
        spec = d.inputs.get(name)
        if kind != "inputs" or spec is None:
            return [_issue("workflow_no_input", f"{src}: workflow has no input '{name}'", nid, field, source=src, name=name)]
        if (spec.type in BLOB_TYPES) != (target_type in BLOB_TYPES):
            return [_issue("type_mismatch", f"{src} is {spec.type} but '{target}' is {target_type}", nid, field,
                           source=src, source_type=spec.type, target=target, target_type=target_type)]
        return []
    if ref == nid:
        return [_issue("self_feed", f"{src}: a node cannot feed itself", nid, field, source=src)]
    if ref not in nodes:
        return [_issue("unknown_node", f"{src}: unknown node '{ref}'", nid, field, source=src, ref=ref)]
    src_recipe = recipes.get(ref)
    if src_recipe is None:
        return []
    if kind == "outputs":
        if name not in src_recipe.outputs:
            return [_issue("recipe_no_output", f"{src}: recipe {src_recipe.id} has no output '{name}' "
                           f"(has: {', '.join(src_recipe.outputs) or '-'})", nid, field, source=src,
                           recipe=src_recipe.id, name=name, outputs=list(src_recipe.outputs))]
        if target_type not in BLOB_TYPES:
            return [_issue("outputs_need_file", f"'{target}' is {target_type}; only file inputs take a node's outputs", nid, field,
                           target=target, target_type=target_type)]
        return []
    if name not in src_recipe.inputs:
        return [_issue("source_recipe_no_input", f"{src}: recipe {src_recipe.id} has no input '{name}'", nid, field,
                       source=src, recipe=src_recipe.id, name=name)]
    src_type = src_recipe.inputs[name].get("type", "text")
    if (src_type in BLOB_TYPES) != (target_type in BLOB_TYPES):
        return [_issue("type_mismatch", f"{src} is {src_type} but '{target}' is {target_type}", nid, field,
                       source=src, source_type=src_type, target=target, target_type=target_type)]
    return []


def _pick(values: list[Any], idx: int | None) -> list[Any]:
    if idx is None:
        return values
    return [values[idx]] if -len(values) <= idx < len(values) else []


class FlowLibrary:
    """Built-in flows (read-only, shipped with core) plus user flows in <data>/flows that shadow them by id."""

    def __init__(self, builtin_dir: Path, user_dir: Path):
        self.builtin_dir = builtin_dir
        self.user_dir = user_dir
        self._sig: tuple[Any, ...] | None = None
        self.flows: dict[str, WorkflowDef] = {}
        self._builtin_ids: set[str] = set()
        self.reload()

    @staticmethod
    def _load_dir(d: Path) -> dict[str, WorkflowDef]:
        out: dict[str, WorkflowDef] = {}
        if not d.is_dir():
            return out
        for p in sorted(d.glob("*.yaml")):
            try:
                doc = yaml.safe_load(p.read_text(encoding="utf-8"))
                f = WorkflowDef.model_validate(doc)
            except (OSError, yaml.YAMLError, ValidationError, ValueError) as e:
                log.error("skipping workflow %s: %s", p.name, e)
                continue
            if f.id != p.stem:
                # save/delete address the file as <id>.yaml; a mismatched name could never be replaced or removed.
                log.error("skipping workflow %s: id %r does not match the file name", p.name, f.id)
                continue
            out[f.id] = f
        return out

    def reload(self) -> None:
        self._sig = (_signature(self.builtin_dir), _signature(self.user_dir))
        builtin = self._load_dir(self.builtin_dir)
        user = self._load_dir(self.user_dir)
        self._builtin_ids = set(builtin)
        for f in builtin.values():
            f.source = "builtin"
        for f in user.values():
            f.source = "override" if f.id in builtin else "user"
        self.flows = {**builtin, **user}

    def maybe_reload(self) -> None:
        if (_signature(self.builtin_dir), _signature(self.user_dir)) != self._sig:
            self.reload()

    def list(self) -> list[WorkflowDef]:
        self.maybe_reload()
        return list(self.flows.values())

    def get(self, fid: str) -> WorkflowDef | None:
        self.maybe_reload()
        return self.flows.get(fid)

    def has_user(self, fid: str) -> bool:
        return valid_flow_id(fid) and (self.user_dir / f"{fid}.yaml").is_file()

    def has_builtin(self, fid: str) -> bool:
        self.maybe_reload()
        return fid in self._builtin_ids

    def save(self, d: WorkflowDef) -> WorkflowDef:
        """Writes the user copy atomically; built-in files are never touched."""
        if not valid_flow_id(d.id):
            raise RecipeError(f"invalid workflow id {d.id!r}")
        doc = d.model_dump(mode="json", exclude={"source"}, exclude_defaults=True)
        text = dump_yaml({"id": d.id, "title": d.title, **doc})
        if len(text.encode("utf-8")) > MAX_FLOW_BYTES:
            raise RecipeError(f"workflow larger than {MAX_FLOW_BYTES // 1024} KB")
        dest = self.user_dir / f"{d.id}.yaml"
        tmp = dest.with_name(f".{dest.name}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            self.user_dir.mkdir(parents=True, exist_ok=True)
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, dest)
        except OSError as e:
            raise RecipeError(f"cannot write workflow {d.id}: {e}") from e
        finally:
            tmp.unlink(missing_ok=True)
        self.reload()
        saved = self.flows.get(d.id)
        if saved is None:
            raise RecipeError(f"workflow {d.id} was written but does not load back")
        return saved

    def delete(self, fid: str) -> bool:
        """Removes the user copy; returns True when a built-in flow with that id takes over again."""
        if not self.has_user(fid):
            raise FlowNotFound(f"no user workflow '{fid}'")
        try:
            (self.user_dir / f"{fid}.yaml").unlink()
        except OSError as e:
            raise RecipeError(f"cannot delete workflow {fid}: {e}") from e
        self.reload()
        return fid in self.flows


def _now() -> float:
    return time.time()


def _snap(wr: WorkflowRunRow) -> dict[str, Any]:
    return wr.model_dump(mode="json", include=set(_SNAP_FIELDS))


class FlowEngine:
    """Starts, follows, retries and cancels workflow runs on top of a StudioEngine."""

    def __init__(self, engine: Any) -> None:
        self.engine = engine
        self.library = FlowLibrary(engine.settings.flows_dir, engine.settings.user_flows_dir)
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._adopted: set[str] = set()
        self._closed = False
        from .flow_batch import FlowBatches  # flow_batch imports this module's errors
        self.batches = FlowBatches(self)

    @property
    def repo(self) -> Any:
        return self.engine.repo

    def _lock(self, wr_id: str) -> asyncio.Lock:
        lock = self._locks.get(wr_id)
        if lock is None:
            lock = self._locks[wr_id] = asyncio.Lock()
        return lock

    def _get_recipe(self, rid: str) -> Recipe | None:
        return self.engine.library.get(rid)

    # definitions

    def validate(self, d: WorkflowDef) -> list[dict[str, Any]]:
        return validate_def(d, self._get_recipe)

    def get_def(self, fid: str) -> WorkflowDef:
        d = self.library.get(fid)
        if d is None:
            raise FlowNotFound(f"unknown workflow: {fid}")
        return d

    def save_def(self, d: WorkflowDef) -> tuple[WorkflowDef, list[dict[str, Any]]]:
        issues = self.validate(d)
        if any(i["level"] == "error" for i in issues):
            raise FlowInvalid([i for i in issues if i["level"] == "error"])
        return self.library.save(d), issues

    # runs

    async def check_inputs(self, d: WorkflowDef, given: dict[str, Any]) -> dict[str, Any]:
        unknown = sorted(set(given) - set(d.inputs))
        if unknown:
            raise RecipeError(f"workflow {d.id} has no input {', '.join(unknown)}")
        out: dict[str, Any] = {}
        for name, spec in d.inputs.items():
            v = given.get(name)
            if v is None or v == "":
                if spec.default is None and spec.required:
                    raise RecipeError(f"input '{name}' is required")
                out[name] = None if spec.default is None else coerce_value(name, {"type": spec.type}, spec.default)
                continue
            v = out[name] = coerce_value(name, {"type": spec.type}, v)
            if spec.type in BLOB_TYPES:
                for h in v if isinstance(v, list) else [v]:
                    if await self.engine.blobs.get(h) is None:
                        raise RecipeError(f"input '{name}': unknown blob {h}")
        return out

    async def start(self, fid: str, inputs: dict[str, Any] | None = None, project_id: str = DEFAULT_PROJECT,
                    title: str = "", batch_id: str | None = None) -> WorkflowRunRow:
        if self._closed:
            raise FlowStateError("engine is shut down")
        d = self.get_def(fid)
        errors = [i for i in self.validate(d) if i["level"] == "error"]
        if errors:
            raise FlowInvalid(errors)
        values = await self.check_inputs(d, inputs or {})
        if project_id != DEFAULT_PROJECT and await self.repo.get_project(project_id) is None:
            raise FlowNotFound(f"unknown project: {project_id}")
        now = _now()
        wr = WorkflowRunRow(id=uuid.uuid4().hex, workflow_id=d.id, title=title or d.title, project_id=project_id,
                            status=WS.QUEUED, def_snapshot={**d.model_dump(mode="json", exclude={"source"}),
                                          **({"$batch_id": batch_id} if batch_id else {})},
                            inputs=values, nodes={n.id: WorkflowNodeState() for n in d.nodes},
                            created_at=now, updated_at=now)
        await self.repo.create_workflow_run(wr)
        self._adopted.add(wr.id)
        self._publish(wr)
        return await self.advance(wr.id) or wr

    async def get_run(self, wr_id: str) -> WorkflowRunRow:
        wr = await self.repo.get_workflow_run(wr_id)
        if wr is None:
            raise FlowNotFound(f"unknown workflow run: {wr_id}")
        return wr

    async def list_runs(self, limit: int = 50, workflow_id: str | None = None,
                        project_id: str | None = None) -> list[WorkflowRunRow]:
        return await self.repo.list_workflow_runs(limit=limit, workflow_id=workflow_id, project_id=project_id)

    async def detail(self, wr_id: str) -> dict[str, Any]:
        """The run row plus a summary of each node's child run (status, progress 0..1, preview blob hash)."""
        wr = await self.get_run(wr_id)
        d = WorkflowDef.model_validate(wr.def_snapshot)
        run_ids = [ns.run_id for ns in wr.nodes.values() if ns.run_id]
        previews_of = getattr(self.repo, "run_preview_hashes", None)
        previews = await previews_of(run_ids) if previews_of and run_ids else {}
        children: list[dict[str, Any]] = []
        for n in d.nodes:
            ns = wr.nodes.get(n.id) or WorkflowNodeState()
            child: dict[str, Any] = {"node_id": n.id, "recipe_id": n.recipe_id, "title": n.title or n.id,
                                     "status": ns.status.value, "run_id": ns.run_id, "error": ns.error,
                                     "attempts": ns.attempts, "progress": None, "preview": None}
            if ns.run_id:
                steps = await self.repo.get_steps(ns.run_id)
                if steps:
                    child["progress"] = round(sum(1.0 if s.status in STEP_DONE_OK else (s.progress or 0.0)
                                                  for s in steps) / len(steps), 4)
                child["preview"] = previews.get(ns.run_id)
            children.append(child)
        return {**wr.model_dump(mode="json"), "children": children}

    async def retry_node(self, wr_id: str, node_id: str) -> WorkflowRunRow:
        """New child run for a failed/cancelled node; nodes after it (and nodes a cancel stopped) go back to
        pending. Identical steps are served from the step cache."""
        async with self._lock(wr_id):
            wr = await self.get_run(wr_id)
            d = WorkflowDef.model_validate(wr.def_snapshot)
            ns = wr.nodes.get(node_id)
            if ns is None:
                raise FlowNotFound(f"workflow run {wr_id} has no node {node_id}")
            before = _snap(wr)
            await self._sync(wr, d)
            if ns.status not in (NS.FAILED, NS.CANCELLED):
                await self._save(wr, before)
                raise FlowStateError(f"node {node_id} is {ns.status.value}; only failed/cancelled nodes can be retried")
            reset = {node_id} | downstream(d, node_id)
            reset |= {k for k, s in wr.nodes.items() if s.status == NS.CANCELLED}
            for k in reset:
                s = wr.nodes[k]
                if s.run_id and s.status in _LIVE_NODE:
                    try:
                        await self.engine.cancel_run(s.run_id)
                    except LookupError:
                        pass
                wr.nodes[k] = WorkflowNodeState(attempts=s.attempts)
            wr.status, wr.error, wr.ended_at = WS.RUNNING, None, None
            await self._sync(wr, d)
            await self._save(wr, before)
            return wr

    async def cancel(self, wr_id: str) -> WorkflowRunRow:
        async with self._lock(wr_id):
            wr = await self.get_run(wr_id)
            if wr.status in (WS.SUCCEEDED, WS.FAILED, WS.CANCELLED):
                return wr
            before = _snap(wr)
            d = WorkflowDef.model_validate(wr.def_snapshot)
            wr.status, wr.ended_at = WS.CANCELLED, _now()
            await self._adopt(wr)
            await self._sync(wr, d)
            live = [ns.run_id for ns in wr.nodes.values() if ns.run_id and ns.status in _LIVE_NODE]
            for ns in wr.nodes.values():
                if ns.run_id is None and ns.status in (NS.PENDING, NS.BLOCKED):
                    ns.status = NS.CANCELLED
            before = await self._save(wr, before)
            for rid in live:
                try:
                    await self.engine.cancel_run(rid)
                except LookupError:
                    pass
            await self._sync(wr, d)
            await self._save(wr, before)
            return wr

    # driving

    def on_run_status(self, run: RunRow) -> None:
        """Hook from StudioEngine._set_run: a child run changed status."""
        wr_id = (run.params.get(META_KEY) or {}).get(FLOW_RUN_KEY)
        if wr_id and not self._closed:
            self._spawn(self.advance(wr_id))

    def _spawn(self, coro: Any) -> None:
        t = asyncio.ensure_future(coro)
        self._tasks.add(t)
        t.add_done_callback(self._done)

    def _done(self, t: asyncio.Task[Any]) -> None:
        self._tasks.discard(t)
        if not t.cancelled() and t.exception() is not None:
            log.error("workflow advance failed", exc_info=t.exception())

    async def advance(self, wr_id: str) -> WorkflowRunRow | None:
        async with self._lock(wr_id):
            wr = await self.repo.get_workflow_run(wr_id)
            if wr is None:
                return None
            before = _snap(wr)
            try:
                await self._sync(wr, WorkflowDef.model_validate(wr.def_snapshot))
            except Exception as e:
                log.exception("workflow run %s: pass failed", wr_id)
                wr.status, wr.error, wr.ended_at = WS.FAILED, f"internal: {e!r}", _now()
            await self._save(wr, before)
            return wr

    async def _adopt(self, wr: WorkflowRunRow) -> None:
        """A crash between start_run and saving the node leaves a child run the row does not know: take it over
        instead of starting the node twice. Done on the first pass over a run in this process (not only in
        recover), since the runner's own recover fires status hooks before FlowEngine.recover gets its turn."""
        self._adopted.add(wr.id)
        runs_of = getattr(self.repo, "runs_of_workflow_run", None)
        if runs_of is None:
            return
        for run in await runs_of(wr.id):
            meta = run.params.get(META_KEY) or {}
            ns = wr.nodes.get(str(meta.get(FLOW_NODE_KEY)))
            if ns is None or ns.run_id is not None or ns.status != NS.PENDING \
                    or meta.get(FLOW_ATTEMPT_KEY) != ns.attempts + 1:
                continue
            # start_run writes the run before its steps: a run without steps never got a driver.
            if await self.repo.get_steps(run.id):
                ns.run_id, ns.attempts, ns.status = run.id, ns.attempts + 1, NS.RUNNING

    async def _sync(self, wr: WorkflowRunRow, d: WorkflowDef) -> None:
        if wr.id not in self._adopted:
            await self._adopt(wr)
        children: dict[str, RunRow] = {}
        for nid, ns in wr.nodes.items():
            if ns.run_id is None:
                continue
            run = await self.repo.get_run(ns.run_id)
            if run is None:
                ns.status, ns.error = NS.FAILED, f"run {ns.run_id} is missing"
                continue
            if run.status == RunStatus.INTERRUPTED and wr.status != WS.CANCELLED:
                # The core stopped under this node (a quit, not a failure): run it again; the step cache hands
                # back every step that had already finished.
                wr.nodes[nid] = WorkflowNodeState(attempts=ns.attempts)
                continue
            children[nid] = run
            ns.status = _RUN_TO_NODE.get(run.status, NS.RUNNING)
            ns.error = (run.error or run.status.value) if ns.status in (NS.FAILED, NS.CANCELLED) else None
        deps = flow_deps(d)
        changed = True
        while changed:
            changed = False
            for n in d.nodes:
                ns = wr.nodes.setdefault(n.id, WorkflowNodeState())
                if ns.run_id is not None or ns.status in (NS.FAILED, NS.CANCELLED):
                    continue
                states = [wr.nodes[k].status for k in deps[n.id]]
                if any(s in _DEAD_NODE for s in states):
                    want = NS.BLOCKED
                elif all(s == NS.SUCCEEDED for s in states) and self._can_start(wr):
                    await self._start_node(wr, n, children)
                    changed = True
                    continue
                else:
                    want = NS.PENDING
                if ns.status != want:
                    ns.status = want
                    changed = True
        self._roll_up(wr)

    def _can_start(self, wr: WorkflowRunRow) -> bool:
        return wr.status != WS.CANCELLED and not self._closed and not getattr(self.engine, "_closed", False)

    async def _start_node(self, wr: WorkflowRunRow, n: Any, children: dict[str, RunRow]) -> None:
        ns = wr.nodes[n.id]
        ns.attempts += 1
        try:
            inputs = {**n.inputs, **await self._map_inputs(wr, n.map, children)}
            tags = {FLOW_RUN_KEY: wr.id, FLOW_NODE_KEY: n.id, FLOW_ATTEMPT_KEY: ns.attempts}
            run_id = await self.engine.start_run(n.recipe_id, inputs, dict(n.params), project_id=wr.project_id,
                                                 title=f"{wr.title} · {n.title or n.id}", flow_meta=tags)
        except Exception as e:
            if not isinstance(e, (LookupError, ValueError)):
                log.exception("workflow run %s: node %s not started", wr.id, n.id)
            ns.status, ns.error = NS.FAILED, str(e) or repr(e)
            return
        ns.run_id, ns.status, ns.error = run_id, NS.RUNNING, None
        run = await self.repo.get_run(run_id)
        if run is not None:
            children[n.id] = run

    async def _map_inputs(self, wr: WorkflowRunRow, mapping: dict[str, str],
                          children: dict[str, RunRow]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        published: dict[str, dict[str, list[str]]] = {}
        for target, src in mapping.items():
            p = parse_source(src)
            if p is None:
                raise RecipeError(f"map.{target}: bad source {src!r}")
            ref, kind, name, idx = p
            if ref == WORKFLOW_REF:
                v = wr.inputs.get(name)
                if v is None:
                    continue  # optional workflow input left empty: the recipe default applies
                if not isinstance(v, list):
                    out[target] = v
                    continue
                values = _pick(v, idx)
            elif kind == "outputs":
                if ref not in published:
                    run = children.get(ref)
                    published[ref] = published_outputs(await self.repo.get_steps(run.id)) if run else {}
                values = _pick(list(published[ref].get(name) or []), idx)
            else:
                run = children.get(ref)
                v = run.inputs.get(name) if run else None
                if v is not None and not isinstance(v, list):
                    out[target] = v
                    continue
                values = _pick(list(v or []), idx)
            if not values:
                raise RecipeError(f"map.{target}: {src} is empty")
            out[target] = values[0] if len(values) == 1 else values
        return out

    @staticmethod
    def _roll_up(wr: WorkflowRunRow) -> None:
        if wr.status == WS.CANCELLED:
            return
        sts = [ns.status for ns in wr.nodes.values()]
        if sts and all(s == NS.SUCCEEDED for s in sts):
            wr.status, wr.error = WS.SUCCEEDED, None
        elif NS.AWAITING_HUMAN in sts:
            wr.status, wr.error = WS.AWAITING_HUMAN, None
        elif NS.RUNNING in sts or NS.PENDING in sts:
            wr.status, wr.error = WS.RUNNING, None
        else:
            bad = next((k for k, ns in wr.nodes.items() if ns.status in (NS.FAILED, NS.CANCELLED)), None)
            wr.status = WS.FAILED
            wr.error = f"{bad}: {wr.nodes[bad].error or wr.nodes[bad].status.value}" if bad else "stalled"
        if wr.status in (WS.SUCCEEDED, WS.FAILED):
            wr.ended_at = wr.ended_at or _now()
        else:
            wr.ended_at = None

    async def _save(self, wr: WorkflowRunRow, before: dict[str, Any]) -> dict[str, Any]:
        """Writes and publishes the row when it differs from `before` (as loaded or last saved)."""
        now = _snap(wr)
        if now == before:
            return now
        wr.updated_at = _now()
        await self.repo.update_workflow_run(wr.id, **{k: getattr(wr, k) for k in _SNAP_FIELDS},
                                            updated_at=wr.updated_at)
        self._publish(wr)
        return now

    def _publish(self, wr: WorkflowRunRow) -> None:
        self.engine.bus.publish("workflow.updated", wr.model_dump(mode="json"))

    async def recover(self) -> None:
        """After StudioEngine.recover: follow every unfinished workflow run from its child runs' current state."""
        if not hasattr(self.repo, "list_workflow_runs"):
            return
        for wr in await self.repo.list_workflow_runs(limit=100_000, statuses=_ACTIVE_WR):
            try:
                await self.advance(wr.id)
            except Exception:
                log.exception("workflow run %s not recovered", wr.id)

    async def wait_idle(self) -> None:
        """Test helper: wait for queued advance passes."""
        while pending := [t for t in self._tasks if not t.done()]:
            await asyncio.gather(*pending, return_exceptions=True)

    async def close(self) -> None:
        self._closed = True
        for t in list(self._tasks):
            t.cancel()
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
