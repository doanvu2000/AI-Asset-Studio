"""Workflow batches: one workflow started for N input sets. Each item is an ordinary workflow run, so the scheduler
queues the child runs like any others; the batch row only remembers which runs belong together."""
from __future__ import annotations

import time
import uuid
from collections import Counter
from typing import Any

from pydantic import BaseModel, Field

from ..contracts import DEFAULT_PROJECT, WorkflowRunRow, WorkflowStatus as WS
from .flows import FlowInvalid, FlowNotFound, FlowStateError

MAX_FLOW_BATCH = 200
_TERMINAL = (WS.SUCCEEDED, WS.FAILED, WS.CANCELLED)


class FlowBatchItem(BaseModel):
    inputs: dict[str, Any] = {}
    title: str = Field("", max_length=200)


class FlowBatchCreate(BaseModel):
    flow_id: str
    items: list[FlowBatchItem]
    shared_params: dict[str, Any] = {}
    project_id: str = DEFAULT_PROJECT
    title: str = Field("", max_length=200)


def _status(counts: Counter[str]) -> str:
    if counts[WS.QUEUED.value] or counts[WS.RUNNING.value]:
        return "running"
    if counts[WS.AWAITING_HUMAN.value]:
        return "awaiting_human"
    if counts[WS.FAILED.value]:
        return "failed"
    if counts[WS.SUCCEEDED.value]:
        return "succeeded"
    return "cancelled"


def _run_brief(wr: WorkflowRunRow) -> dict[str, Any]:
    return {"id": wr.id, "title": wr.title, "status": wr.status.value, "error": wr.error, "created_at": wr.created_at,
            "ended_at": wr.ended_at, "nodes": {k: n.status.value for k, n in wr.nodes.items()}}


class FlowBatches:
    def __init__(self, flows: Any) -> None:
        self.flows = flows

    @property
    def repo(self) -> Any:
        return self.flows.repo

    async def start(self, body: FlowBatchCreate) -> dict[str, Any]:
        if not body.items:
            raise ValueError("batch needs at least one item")
        if len(body.items) > MAX_FLOW_BATCH:
            raise ValueError(f"batch limited to {MAX_FLOW_BATCH} items")
        flows = self.flows
        d = flows.get_def(body.flow_id)
        errors = [i for i in flows.validate(d) if i["level"] == "error"]
        if errors:
            raise FlowInvalid(errors)
        merged = [{**body.shared_params, **it.inputs} for it in body.items]
        for i, values in enumerate(merged):
            try:
                await flows.check_inputs(d, values)
            except ValueError as e:
                raise ValueError(f"item {i + 1}: {e}") from e
        if body.project_id != DEFAULT_PROJECT and await self.repo.get_project(body.project_id) is None:
            raise FlowNotFound(f"unknown project: {body.project_id}")
        base = body.title or d.title
        run_ids: list[str] = []
        try:
            for i, (it, values) in enumerate(zip(body.items, merged)):
                wr = await flows.start(d.id, values, project_id=body.project_id,
                                       title=it.title or f"{base} #{i + 1}")
                run_ids.append(wr.id)
        except Exception:
            for rid in run_ids:
                await flows.cancel(rid)
            raise
        batch = {"id": uuid.uuid4().hex, "workflow_id": d.id, "title": base, "project_id": body.project_id,
                 "run_ids": run_ids, "created_at": time.time()}
        await self.repo.create_workflow_batch(batch)
        return await self.detail(batch["id"])

    async def _batch(self, batch_id: str) -> dict[str, Any]:
        batch = await self.repo.get_workflow_batch(batch_id)
        if batch is None:
            raise FlowNotFound(f"unknown batch: {batch_id}")
        return batch

    async def _runs(self, run_ids: list[str]) -> list[WorkflowRunRow]:
        by_id = {r.id: r for r in await self.repo.workflow_runs_by_ids(run_ids)}
        return [by_id[i] for i in run_ids if i in by_id]

    @staticmethod
    def _summary(batch: dict[str, Any], runs: list[WorkflowRunRow]) -> dict[str, Any]:
        counts = Counter(r.status.value for r in runs)
        return {"id": batch["id"], "workflow_id": batch["workflow_id"], "title": batch["title"],
                "project_id": batch["project_id"], "created_at": batch["created_at"], "total": len(batch["run_ids"]),
                "counts": dict(counts), "status": _status(counts)}

    async def detail(self, batch_id: str) -> dict[str, Any]:
        batch = await self._batch(batch_id)
        runs = await self._runs(batch["run_ids"])
        return {**self._summary(batch, runs), "runs": [_run_brief(r) for r in runs]}

    async def list_all(self, limit: int = 50) -> list[dict[str, Any]]:
        out = []
        for batch in await self.repo.list_workflow_batches(limit):
            out.append(self._summary(batch, await self._runs(batch["run_ids"])))
        return out

    async def cancel(self, batch_id: str) -> dict[str, Any]:
        batch = await self._batch(batch_id)
        for wr in await self._runs(batch["run_ids"]):
            if wr.status not in _TERMINAL:
                await self.flows.cancel(wr.id)
        return await self.detail(batch_id)

    async def retry_failed(self, batch_id: str) -> dict[str, Any]:
        """Retries every failed node of every failed run (cancelled runs are left alone)."""
        batch = await self._batch(batch_id)
        retried = 0
        for wr in await self._runs(batch["run_ids"]):
            if wr.status != WS.FAILED:
                continue
            for node_id, ns in wr.nodes.items():
                if ns.status.value != "failed":
                    continue
                try:
                    await self.flows.retry_node(wr.id, node_id)
                    retried += 1
                except FlowStateError:
                    continue
        return {**await self.detail(batch_id), "retried": retried}
