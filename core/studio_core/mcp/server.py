from __future__ import annotations

import asyncio
import io
import json
import logging
import mimetypes
import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import CallToolResult, ImageContent, TextContent, ToolAnnotations

from studio_core.config import load_settings
from studio_core.mcp.link import CoreError, CoreLink
from studio_core.spine_atlas import atlas_page_files

log = logging.getLogger(__name__)

GatePolicy = Literal["human", "agent", "auto_first"]
META_KEY = "$meta"
TERMINAL = {"succeeded", "failed", "cancelled", "interrupted"}
BLOB_TYPES = {"image", "mesh", "file", "video"}
_HASH = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[\w-]{1,80}$")
_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif",
        "model/gltf-binary": ".glb", "model/gltf+json": ".gltf", "application/json": ".json",
        "video/mp4": ".mp4", "video/webm": ".webm"}
INLINE_IMAGE_MAX = 6
INLINE_JSON_MAX = 64 * 1024

INSTRUCTIONS = """AI Asset Studio: game-asset pipelines (recipes) running on this machine.
Typical flow: list_recipes -> describe_recipe -> start_run (image/mesh/file/video inputs = absolute local file
paths; an atlas file input uploads its page images too)
-> wait_run -> if status is awaiting_human, look at the gate candidates and call resolve_gate (only when
gate_policy is 'agent' or the user asked you to choose) -> wait_run again -> get_outputs (returns absolute
paths of exported copies). Runs started here also appear in the desktop app. GPU recipes can take minutes;
wait_run returns on timeout so call it again."""


def _ext(blob: dict[str, Any]) -> str:
    name = blob.get("name") or ""
    return _EXT.get(blob.get("mime", "")) or Path(name).suffix or mimetypes.guess_extension(blob.get("mime", "")) or ".bin"


def _with_siblings(name: str, spec: dict[str, Any], value: Any) -> Any:
    """Check a `file` input's extension; an atlas input also brings its page images (atlas first, pages in order)."""
    if spec.get("type") != "file" or not isinstance(value, str) or not Path(value).is_absolute():
        return value
    p = Path(value)
    exts = spec.get("ext") or []
    if exts and p.suffix.lower().lstrip(".") not in exts:
        raise ToolError(f"input '{name}': expected a .{'/.'.join(exts)} file, got {p.name}")
    if spec.get("siblings") == "aseprite_sheet":
        return _aseprite_sheet(name, p)
    if spec.get("siblings") != "atlas_pages":
        return value
    try:
        pages = atlas_page_files(p)
    except (OSError, FileNotFoundError) as e:
        raise ToolError(f"input '{name}': {e}") from e
    return [str(p), *(str(f) for f in pages)]


def _aseprite_sheet(name: str, p: Path) -> Any:
    """An Aseprite JSON sheet travels with the PNG its meta.image names (same folder); a .aseprite file is whole."""
    if p.suffix.lower() != ".json":
        return str(p)
    try:
        doc = json.loads(p.read_bytes())
    except (OSError, ValueError) as e:
        raise ToolError(f"input '{name}': cannot read {p.name}: {e}") from e
    image = Path(str((doc.get("meta") or {}).get("image") or "") if isinstance(doc, dict) else "").name
    png = p.with_name(image) if image else p.with_suffix(".png")
    if not png.is_file():
        raise ToolError(f"input '{name}': the sheet image {png.name} is not next to {p.name}")
    return [str(p), str(png)]


def _safe_id(value: str, what: str) -> str:
    if not _SAFE_ID.match(value):
        raise ToolError(f"invalid {what}: {value!r}")
    return value


def _run_meta(run: dict[str, Any]) -> dict[str, Any]:
    meta = (run.get("params") or {}).get(META_KEY)
    return meta if isinstance(meta, dict) else {}


def _candidates(step: dict[str, Any]) -> list[dict[str, Any]]:
    """Same order as engine PickGate.candidates so 'first' matches what the app shows first."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for refs in (step.get("inputs") or {}).values():
        for b in refs:
            if b["hash"] not in seen:
                seen.add(b["hash"])
                out.append(b)
    return out


def _gate_k(step: dict[str, Any]) -> int:
    gate = (step.get("meta") or {}).get("gate") or {}
    return int(gate.get("k", (step.get("params") or {}).get("k", 1)))


def _step_view(s: dict[str, Any]) -> dict[str, Any]:
    v = {"step_id": s["id"], "key": s["key"], "kind": s["kind"], "lane": s["lane"], "status": s["status"],
         "attempt": s.get("attempt", 1)}
    if s.get("progress") is not None and s["status"] == "running":
        v["progress"] = round(float(s["progress"]), 3)
    if s.get("error"):
        v["error"] = {k: s["error"].get(k) for k in ("code", "message") if s["error"].get(k)}
    if s.get("started_at") and s.get("ended_at"):
        v["seconds"] = round(s["ended_at"] - s["started_at"], 1)
    if isinstance((s.get("meta") or {}).get("score"), (int, float)):
        v["score"] = s["meta"]["score"]
    return v


def _review(steps: list[dict[str, Any]], gate: dict[str, Any]) -> dict[str, Any] | None:
    """Score + critique of the latest scored step before a gate (claude.task / review loop with a `score`)."""
    found = None
    for s in steps:
        if s["id"] == gate["id"]:
            break
        m = s.get("meta") or {}
        if isinstance(m.get("score"), (int, float)) and not isinstance(m.get("score"), bool):
            found = {"step": s["key"], "score": m["score"], "critique": m.get("critique", "")}
            for k in ("score_history", "warning", "knee_overlap", "seam_visible"):
                if m.get(k):
                    found[k] = m[k]
    return found


def _progress(steps: list[dict[str, Any]]) -> tuple[float, str]:
    done = sum(1 for s in steps if s["status"] in ("succeeded", "cached", "skipped"))
    running = [s for s in steps if s["status"] in ("running", "waiting_resource")]
    extra = sum(float(s.get("progress") or 0) for s in running if s["status"] == "running")
    label = ", ".join(f"{s['key']} {s['status']}" for s in running) or "idle"
    return done + extra, label


def _hint(status: str, policy: str, gates: list[dict[str, Any]]) -> str:
    if status == "succeeded":
        return "Done. Call get_outputs(run_id) to export the result files."
    if status in ("failed", "interrupted"):
        return "Run stopped. Inspect step errors; retry_step(step_id) on the failed step to resume."
    if status == "cancelled":
        return "Run was cancelled."
    if status == "awaiting_human" and gates:
        if policy == "human":
            return ("Waiting for the user to choose in the Asset Studio app (gate_policy=human). Ask the user, "
                    "or call resolve_gate only if the user told you to choose.")
        return "Pick/approve with resolve_gate(step_id, pick=[hash...] or approve=true/false), then wait_run."
    return "Still running; call wait_run(run_id)."


class StudioTools:
    def __init__(self, link: CoreLink) -> None:
        self.link = link

    async def _call(self, method: str, path: str, **kw: Any) -> Any:
        try:
            return await self.link.request(method, path, **kw)
        except CoreError as e:
            raise ToolError(f"{e.code}: {e.message}") from e

    async def _recipe(self, recipe_id: str) -> dict[str, Any]:
        for r in await self._call("GET", "/api/recipes"):
            if r["id"] == recipe_id:
                return r
        raise ToolError(f"unknown recipe: {recipe_id}. Call list_recipes for valid ids.")

    async def list_recipes(self) -> dict[str, Any]:
        recipes = await self._call("GET", "/api/recipes")
        return {"recipes": [{
            "id": r["id"], "name": r["name"], "description": r.get("description", ""),
            "inputs": {k: v.get("type", "text") for k, v in r.get("inputs", {}).items()},
            "lanes": sorted({s.get("lane") for s in r.get("steps", []) if s.get("lane")}),
        } for r in recipes]}

    async def describe_recipe(self, recipe_id: str) -> dict[str, Any]:
        r = await self._recipe(recipe_id)
        inputs = {}
        for name, spec in r.get("inputs", {}).items():
            spec = dict(spec)
            spec.setdefault("required", "default" not in spec and spec.get("required", True))
            if spec.get("type") in BLOB_TYPES:
                spec["pass_as"] = "absolute local file path (or a list of paths)"
            inputs[name] = spec
        gates = [s["id"] for s in r.get("steps", []) if str(s.get("kind", "")).startswith("gate.")]
        return {"id": r["id"], "name": r["name"], "description": r.get("description", ""), "inputs": inputs,
                "steps": [{"id": s["id"], "kind": s["kind"], "lane": s.get("lane")} for s in r.get("steps", [])],
                "gates": gates,
                "note": "lane gpu/blender steps use the local GPU and can take minutes; gates pause the run."}

    async def _blob_input(self, name: str, value: Any) -> Any:
        if isinstance(value, list):
            return [await self._blob_input(name, v) for v in value]
        if isinstance(value, dict) and "hash" in value:
            return value["hash"]
        if not isinstance(value, str) or not value:
            raise ToolError(f"input '{name}': expected an absolute file path, got {value!r}")
        p = Path(value)
        if p.is_absolute():
            if not await asyncio.to_thread(p.is_file):
                raise ToolError(f"input '{name}': file not found: {value}")
            ref = await self._call_upload(p)
            return ref["hash"]
        if _HASH.match(value):
            return value
        raise ToolError(f"input '{name}': use an absolute path (the server's working dir is not yours): {value}")

    async def _call_upload(self, p: Path) -> dict[str, Any]:
        try:
            return await self.link.upload(p)
        except CoreError as e:
            raise ToolError(f"upload {p}: {e.code}: {e.message}") from e

    async def start_run(self, recipe: str, inputs: dict[str, Any], title: str | None,
                        gate_policy: GatePolicy, project_id: str | None = None,
                        use_style_bible: bool = True) -> dict[str, Any]:
        r = await self._recipe(recipe)
        specs = r.get("inputs", {})
        unknown = sorted(set(inputs) - set(specs))
        if unknown:
            raise ToolError(f"unknown inputs {unknown}; recipe '{recipe}' accepts {sorted(specs)}")
        values = dict(inputs)
        for name, spec in specs.items():
            if spec.get("type") in BLOB_TYPES and values.get(name) not in (None, ""):
                values[name] = await self._blob_input(name, _with_siblings(name, spec, values[name]))
        body: dict[str, Any] = {"recipe_id": recipe, "inputs": values, "title": title or "",
                                "params": {META_KEY: {"source": "mcp", "gate_policy": gate_policy}}}
        if project_id:
            body["project_id"] = _safe_id(project_id, "project_id")
        if not use_style_bible:
            body["params"][META_KEY]["use_style_bible"] = False
        res = await self._call("POST", "/api/runs", json=body)
        out = {"run_id": res["run_id"], "recipe": recipe, "gate_policy": gate_policy,
               "next": "call wait_run(run_id)"}
        if project_id:
            out["project_id"] = project_id
        return out

    async def list_projects(self, include_archived: bool) -> dict[str, Any]:
        rows = await self._call("GET", "/api/projects", params={"archived": include_archived, "summary": True})
        return {"projects": [{
            "project_id": p["id"], "name": p["name"], "description": p.get("description", ""),
            "archived": p.get("archived", False), "style": p.get("style", {}),
            "runs_total": (p.get("summary") or {}).get("runs_total", 0),
            "approved_versions": (p.get("summary") or {}).get("approved_versions", 0),
        } for p in rows]}

    async def _gate_files(self, run_id: str, step: dict[str, Any]) -> list[dict[str, Any]]:
        out = []
        base = self.link.exports_dir / run_id / "gates" / _safe_id(step["key"], "step key")
        for i, b in enumerate(_candidates(step)):
            dest = base / f"{i + 1:02d}_{b['hash'][:10]}{_ext(b)}"
            if not await asyncio.to_thread(dest.is_file):
                await self.link.download(f"/api/blobs/{b['hash']}", dest)
            out.append({"index": i + 1, "hash": b["hash"], "path": str(dest), "mime": b.get("mime"),
                        "width": b.get("width"), "height": b.get("height")})
        return out

    async def _thumb(self, h: str, size: int = 256) -> ImageContent | None:
        try:
            data = await self.link.fetch_bytes(f"/api/blobs/{h}/thumb", params={"size": size})
        except CoreError:
            return None
        png = await asyncio.to_thread(_to_png, data)
        return ImageContent(type="image", data=_b64(png), mime_type="image/png")

    async def run_view(self, run_id: str, *, with_gate_files: bool = True) -> dict[str, Any]:
        _safe_id(run_id, "run_id")
        data = await self._call("GET", f"/api/runs/{run_id}")
        run, steps = data["run"], data["steps"]
        meta = _run_meta(run)
        policy = meta.get("gate_policy", "human")
        gates = []
        for s in steps:
            if s["status"] != "awaiting_human":
                continue
            g = {"step_id": s["id"], "key": s["key"], "kind": "approve" if s["kind"] == "gate.approve" else "pick",
                 "k": _gate_k(s)}
            if review := _review(steps, s):
                g["review"] = review
            if with_gate_files:
                g["candidates"] = await self._gate_files(run_id, s)
            gates.append(g)
        return {"run_id": run["id"], "recipe": run["recipe_id"], "title": run.get("title", ""),
                "status": run["status"], "error": run.get("error"), "source": meta.get("source", "app"),
                "gate_policy": policy, "created_at": run["created_at"], "ended_at": run.get("ended_at"),
                "steps": [_step_view(s) for s in steps], "pending_gates": gates,
                "next": _hint(run["status"], policy, gates)}

    async def with_thumbs(self, view: dict[str, Any], include: bool) -> CallToolResult:
        content: list[TextContent | ImageContent] = [TextContent(type="text", text=json.dumps(view, indent=1))]
        if include:
            n = 0
            for g in view.get("pending_gates", []):
                for c in g.get("candidates", []):
                    if n >= 8 or not str(c.get("mime", "")).startswith("image/"):
                        continue
                    img = await self._thumb(c["hash"])
                    if img is not None:
                        content.append(TextContent(type="text", text=f"candidate {c['index']} ({g['key']}): {c['hash']}"))
                        content.append(img)
                        n += 1
        return CallToolResult(content=content, structured_content=view)

    async def _auto_resolve(self, run_id: str) -> bool:
        data = await self._call("GET", f"/api/runs/{run_id}")
        acted = False
        for s in data["steps"]:
            if s["status"] != "awaiting_human":
                continue
            if s["kind"] == "gate.approve":
                body: dict[str, Any] = {"approved": True}
            else:
                cands = _candidates(s)
                k = _gate_k(s) or 1
                body = {"picked": [c["hash"] for c in cands[:k]]}
            await self._call("POST", f"/api/gates/{s['id']}", json=body)
            acted = True
        return acted

    async def wait_run(self, run_id: str, timeout_s: float, ctx: Context | None) -> dict[str, Any]:
        _safe_id(run_id, "run_id")
        timeout_s = max(1.0, min(float(timeout_s), 3600.0))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        last_report: tuple[float, str] | None = None
        while True:
            data = await self._call("GET", f"/api/runs/{run_id}")
            run, steps = data["run"], data["steps"]
            status = run["status"]
            if ctx is not None:
                done, label = _progress(steps)
                if (done, label) != last_report:
                    last_report = (done, label)
                    try:
                        await ctx.report_progress(done, len(steps), f"{status}: {label}")
                    except Exception:
                        log.debug("progress notification failed", exc_info=True)
            if status == "awaiting_human" and _run_meta(run).get("gate_policy") == "auto_first":
                if await self._auto_resolve(run_id):
                    continue
            if status in TERMINAL or status == "awaiting_human":
                view = await self.run_view(run_id)
                view["timed_out"] = False
                return view
            if loop.time() >= deadline:
                view = await self.run_view(run_id, with_gate_files=False)
                view["timed_out"] = True
                view["next"] = "Not finished yet; call wait_run(run_id) again."
                return view
            await asyncio.sleep(min(1.0, max(0.05, deadline - loop.time())))

    async def resolve_gate(self, step_id: str, pick: list[str] | None, approve: bool | None) -> dict[str, Any]:
        _safe_id(step_id, "step_id")
        if (pick is None) == (approve is None):
            raise ToolError("pass exactly one of pick (for pick gates) or approve (for approve gates)")
        body: dict[str, Any]
        if approve is not None:
            body = {"approved": approve}
        else:
            body = {"picked": await self._pick_hashes(step_id, pick or [])}
        await self._call("POST", f"/api/gates/{step_id}", json=body)
        return {"ok": True, "resolved": body, "next": "call wait_run(run_id)"}

    async def _pick_hashes(self, step_id: str, pick: list[str]) -> list[str]:
        gates = await self._call("GET", "/api/gates")
        step = next((g for g in gates if g["id"] == step_id), None)
        if step is None:
            raise ToolError(f"step {step_id} is not an open gate")
        cands = _candidates(step)
        hashes: list[str] = []
        for p in pick:
            p = str(p).strip()
            match = [c["hash"] for c in cands if c["hash"] == p]
            if not match and p.isdigit() and 1 <= int(p) <= len(cands):
                match = [cands[int(p) - 1]["hash"]]
            if not match:
                stem = Path(p).name.split("_", 1)[-1].split(".")[0] if ("/" in p or "\\" in p) else p
                match = [c["hash"] for c in cands if len(stem) >= 8 and c["hash"].startswith(stem)]
            if len(match) != 1:
                raise ToolError(f"pick {p!r} does not identify exactly one candidate")
            hashes.append(match[0])
        return hashes

    async def get_outputs(self, run_id: str, include_images: bool) -> CallToolResult:
        _safe_id(run_id, "run_id")
        data = await self._call("GET", f"/api/runs/{run_id}")
        run, steps = data["run"], data["steps"]
        versions = await self._call("GET", "/api/versions", params={"run_id": run_id})
        version_of = {(v["step_id"], v["blob"]["hash"]): v for v in versions}
        out_dir = self.link.exports_dir / run_id
        files: list[dict[str, Any]] = []
        for s in steps:
            publish = ((s.get("meta") or {}).get("spec") or {}).get("publish") or {}
            for recipe_out, step_out in publish.items():
                blobs = (s.get("outputs") or {}).get(step_out, [])
                for i, b in enumerate(blobs):
                    stem = recipe_out if len(blobs) == 1 else f"{recipe_out}_{i + 1:02d}"
                    dest = out_dir / f"{_safe_id(stem, 'output name')}{_ext(b)}"
                    await self.link.download(f"/api/blobs/{b['hash']}", dest)
                    entry: dict[str, Any] = {"output": recipe_out, "path": str(dest), "mime": b.get("mime"),
                                             "size": b.get("size"), "width": b.get("width"),
                                             "height": b.get("height"), "hash": b["hash"], "step": s["key"]}
                    v = version_of.get((s["id"], b["hash"]))
                    if v:
                        entry["version_id"], entry["version_status"] = v["id"], v["status"]
                    if b.get("mime") == "application/json" and (b.get("size") or 0) <= INLINE_JSON_MAX:
                        try:
                            entry["json"] = json.loads(await asyncio.to_thread(dest.read_text, encoding="utf-8"))
                        except ValueError:
                            pass
                    files.append(entry)
        step_meta = {s["key"]: _small_meta(s.get("meta") or {}) for s in steps
                     if ((s.get("meta") or {}).get("spec") or {}).get("publish")}
        view = {"run_id": run_id, "status": run["status"], "complete": run["status"] == "succeeded",
                "export_dir": str(out_dir), "files": files, "step_meta": {k: v for k, v in step_meta.items() if v}}
        if not files:
            view["next"] = ("No outputs yet." if run["status"] != "succeeded" else "Recipe published no outputs.")
        content: list[TextContent | ImageContent] = [TextContent(type="text", text=json.dumps(view, indent=1))]
        if include_images:
            shown = 0
            for f in files:
                if shown >= INLINE_IMAGE_MAX or not str(f.get("mime", "")).startswith("image/"):
                    continue
                img = await self._thumb(f["hash"], 512)
                if img is not None:
                    content.append(TextContent(type="text", text=f"{f['output']}: {f['path']}"))
                    content.append(img)
                    shown += 1
        return CallToolResult(content=content, structured_content=view)

    async def list_runs(self, limit: int, only_mcp: bool) -> dict[str, Any]:
        rows = await self._call("GET", "/api/runs", params={"limit": max(1, min(limit, 200))})
        out = []
        for r in rows:
            meta = _run_meta(r)
            if only_mcp and meta.get("source") != "mcp":
                continue
            out.append({"run_id": r["id"], "recipe": r["recipe_id"], "title": r.get("title", ""),
                        "status": r["status"], "source": meta.get("source", "app"), "created_at": r["created_at"]})
        return {"runs": out}

    async def list_versions(self, run_id: str | None, status: str | None, limit: int) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if run_id:
            params["run_id"] = _safe_id(run_id, "run_id")
        if status:
            params["status"] = status
        rows = await self._call("GET", "/api/versions", params=params)
        return {"versions": [{"version_id": v["id"], "asset_name": v["asset_name"], "status": v["status"],
                              "run_id": v["run_id"], "hash": v["blob"]["hash"], "mime": v["blob"].get("mime"),
                              "width": v["blob"].get("width"), "height": v["blob"].get("height"),
                              "created_at": v["created_at"]} for v in rows[:max(1, limit)]]}

    async def set_version_status(self, version_id: str, status: str) -> dict[str, Any]:
        _safe_id(version_id, "version_id")
        action = {"approved": "approve", "rejected": "reject"}.get(status)
        if action is None:
            raise ToolError("status must be 'approved' or 'rejected'")
        v = await self._call("POST", f"/api/versions/{version_id}/{action}")
        return {"version_id": v["id"], "status": v["status"], "asset_name": v["asset_name"]}


def _small_meta(meta: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in meta.items():
        if k in ("spec", "gate", "resolution", "ready_at", "waiting_for"):
            continue
        if len(json.dumps(v, default=str)) <= 4096:
            out[k] = v
    return out


def _to_png(data: bytes) -> bytes:
    from PIL import Image

    with Image.open(io.BytesIO(data)) as im:
        buf = io.BytesIO()
        im.save(buf, "PNG")
        return buf.getvalue()


def _b64(data: bytes) -> str:
    import base64

    return base64.b64encode(data).decode()


def build_server(link: CoreLink) -> MCPServer:
    tools = StudioTools(link)

    @asynccontextmanager
    async def lifespan(_: MCPServer) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await link.close()

    mcp = MCPServer("asset-studio", instructions=INSTRUCTIONS, lifespan=lifespan, log_level="WARNING")
    read_only = ToolAnnotations(read_only_hint=True, open_world_hint=False)
    acts = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)

    @mcp.tool(annotations=read_only)
    async def list_recipes() -> dict[str, Any]:
        """List the asset recipes (pipelines) available in AI Asset Studio, with input names/types and
        resource lanes (gpu/blender lanes are slow and use the local GPU)."""
        return await tools.list_recipes()

    @mcp.tool(annotations=read_only)
    async def describe_recipe(recipe_id: str) -> dict[str, Any]:
        """Full input spec of one recipe: type, default, min/max, options, required. Inputs of type
        image/mesh/file/video must be given to start_run as absolute local file paths. Also lists steps and gates."""
        return await tools.describe_recipe(recipe_id)

    @mcp.tool(annotations=acts)
    async def start_run(recipe: str, inputs: dict[str, Any], title: str | None = None,
                        gate_policy: GatePolicy = "human", project_id: str | None = None,
                        use_style_bible: bool = True) -> dict[str, Any]:
        """Start a recipe run and return its run_id immediately (use wait_run to follow it).

        inputs: {input_name: value}; image/mesh/file/video inputs take absolute file paths (uploaded for
        you; an atlas input brings its page images).
        gate_policy: 'human' (default) = the user picks/approves in the desktop app;
        'agent' = you look at candidates (get_run/wait_run) and call resolve_gate;
        'auto_first' = wait_run auto-picks the first candidate(s) and approves every approve gate.
        project_id (from list_projects): the project's locked style (prompt prefix/suffix, preset, palette,
        reference image, default inputs) fills the inputs you leave out; inputs you pass always win.
        use_style_bible=False skips the project's active style bible (get_style_bible) for this run."""
        return await tools.start_run(recipe, inputs, title, gate_policy, project_id, use_style_bible)

    @mcp.tool(annotations=read_only)
    async def list_projects(include_archived: bool = False) -> dict[str, Any]:
        """Projects (one game / asset set each) with their locked style and run/approved counts. Pass a
        project_id to start_run so new assets match the rest of the set."""
        return await tools.list_projects(include_archived)

    @mcp.tool(annotations=read_only)
    async def get_run(run_id: str, include_thumbnails: bool = True) -> CallToolResult:
        """Run status with every step (status, attempt, progress, error) and any pending gate. Gate
        candidates are exported as image files (absolute paths) and, if include_thumbnails, shown inline."""
        return await tools.with_thumbs(await tools.run_view(run_id), include_thumbnails)

    @mcp.tool(annotations=acts)
    async def wait_run(run_id: str, timeout_s: float = 600, include_thumbnails: bool = True,
                       ctx: Context | None = None) -> CallToolResult:
        """Block until the run finishes (succeeded/failed/cancelled/interrupted), stops at a gate
        (awaiting_human), or timeout_s passes (timed_out=true; just call again). Sends progress
        notifications. With gate_policy auto_first, gates are resolved automatically while waiting.
        If your client limits tool-call time, pass a timeout_s below that limit and loop."""
        view = await tools.wait_run(run_id, timeout_s, ctx)
        return await tools.with_thumbs(view, include_thumbnails and view["status"] == "awaiting_human")

    @mcp.tool(annotations=acts)
    async def resolve_gate(step_id: str, pick: list[str] | None = None, approve: bool | None = None) -> dict[str, Any]:
        """Resolve a pending gate (step_id from get_run/wait_run pending_gates).
        Pick gates: pick = exactly k candidates, each a full hash, a 1-based candidate index, or the
        candidate file path. Approve gates: approve = true to continue, false to reject (fails the run)."""
        return await tools.resolve_gate(step_id, pick, approve)

    @mcp.tool(annotations=acts)
    async def cancel_run(run_id: str) -> dict[str, Any]:
        """Cancel a running or waiting run."""
        await tools._call("POST", f"/api/runs/{_safe_id(run_id, 'run_id')}/cancel")
        return {"ok": True, "run_id": run_id}

    @mcp.tool(annotations=acts)
    async def retry_step(step_id: str) -> dict[str, Any]:
        """Retry a failed or cancelled step; downstream steps rerun and the run resumes. Then wait_run."""
        await tools._call("POST", f"/api/steps/{_safe_id(step_id, 'step_id')}/retry")
        return {"ok": True, "step_id": step_id, "next": "call wait_run(run_id)"}

    @mcp.tool(annotations=acts)
    async def get_outputs(run_id: str, include_images: bool = True) -> CallToolResult:
        """Export the run's final outputs to <data>/exports/mcp/<run_id>/ with readable names and return
        their absolute paths plus metadata (size, mime, JSON content such as sprite-sheet metadata,
        Library version ids). Image outputs are also shown inline (downscaled) if include_images."""
        return await tools.get_outputs(run_id, include_images)

    @mcp.tool(annotations=read_only)
    async def list_runs(limit: int = 20, only_mcp: bool = False) -> dict[str, Any]:
        """Most recent runs (newest first); only_mcp=true keeps runs started through this MCP server."""
        return await tools.list_runs(limit, only_mcp)

    @mcp.tool(annotations=read_only)
    async def list_versions(run_id: str | None = None, status: Literal["draft", "approved", "rejected"] | None = None,
                            limit: int = 50) -> dict[str, Any]:
        """Library asset versions (published outputs of finished runs), optionally filtered by run/status."""
        return await tools.list_versions(run_id, status, limit)

    @mcp.tool(annotations=acts)
    async def set_version_status(version_id: str, status: Literal["approved", "rejected"]) -> dict[str, Any]:
        """Approve or reject a Library version (version_id from get_outputs or list_versions)."""
        return await tools.set_version_status(version_id, status)

    @mcp.tool(annotations=read_only)
    async def get_asset_list(project_id: str) -> dict[str, Any]:
        """A project's asset list (manifest): rows {id, name, recipe_id, inputs, notes, run_id, status} where status
        is todo | running | review | approved | failed, derived from the row's last run."""
        return await tools._call("GET", f"/api/projects/{_safe_id(project_id, 'project_id')}/manifest")

    @mcp.tool(annotations=acts)
    async def asset_list_from_gdd(project_id: str, gdd_text: str, limit: int = 30, append: bool = True
                                  ) -> dict[str, Any]:
        """Let Claude read a game design document and propose asset-list rows (at most 30 per call), each bound
        to an available recipe with its inputs filled. append=True saves them to the project's list; False only
        returns the proposal. `dropped` lists proposals bound to unknown recipes."""
        return await tools._call("POST", f"/api/projects/{_safe_id(project_id, 'project_id')}/manifest/from-gdd",
                                 json={"text": gdd_text, "limit": limit, "append": append}, timeout=300)

    @mcp.tool(annotations=acts)
    async def run_asset_list(project_id: str, row_ids: list[str] | None = None) -> dict[str, Any]:
        """Start asset-list rows as one batch (each row with its own recipe, project style applied). row_ids None =
        every row not run yet or failed. Returns batch_id and run_ids (follow them with wait_run)."""
        body: dict[str, Any] = {} if row_ids is None else {"row_ids": row_ids}
        return await tools._call("POST", f"/api/projects/{_safe_id(project_id, 'project_id')}/manifest/run", json=body)

    @mcp.tool(annotations=acts)
    async def project_qa(project_id: str, only_approved: bool = False, vision: bool = False,
                         flagged_only: bool = True) -> dict[str, Any]:
        """Consistency check of a project's image assets (approved, else latest, per run): palette drift vs the
        project palette/reference, subject scale vs the median, outline weight, light direction. Each asset gets a
        0-100 score, issues and a `fix` (recipe + inputs) usable with project_qa_fix. vision=True adds a Claude
        summary (costs API credits). flagged_only drops assets without issues from the reply."""
        report = await tools._call("POST", f"/api/projects/{_safe_id(project_id, 'project_id')}/qa",
                                   json={"only_approved": only_approved, "vision": vision}, timeout=300)
        assets = [{k: a.get(k) for k in ("version_id", "asset_name", "score", "grade", "issues", "vision_note",
                                         "fix", "note") if a.get(k) not in (None, "", [])}
                  for a in report.get("assets", []) if not flagged_only or a.get("issues") or a.get("vision_note")]
        return {"summary": report.get("summary"), "reference": report.get("reference"),
                "vision": report.get("vision"), "assets": assets}

    @mcp.tool(annotations=acts)
    async def project_qa_fix(project_id: str, version_id: str, instructions: str | None = None) -> dict[str, Any]:
        """One-click fix of one asset from project_qa: starts a Klein edit run (variants-2d) on its image with the
        report's suggested instructions (or yours, English). Returns run_id; follow with wait_run."""
        body: dict[str, Any] = {"version_id": _safe_id(version_id, "version_id")}
        if instructions:
            body["instructions"] = instructions
        return await tools._call("POST", f"/api/projects/{_safe_id(project_id, 'project_id')}/qa/fix", json=body)

    @mcp.tool(annotations=read_only)
    async def find_similar_assets(version_id: str | None = None, blob: str | None = None, image_path: str | None = None,
                                  text: str | None = None, k: int = 12, project_id: str | None = None,
                                  only_approved: bool = False) -> dict[str, Any]:
        """Top-k Library image assets most similar to one query: a version_id, a blob hash, an absolute local
        image_path, or a text description (text needs the CLIP model). Scores are cosine similarity (mode 'clip') or
        1 - pHash distance/64 (mode 'phash', when the model is not installed). project_id limits the search."""
        k = max(1, min(100, k))
        filters: dict[str, Any] = {"k": k, "only_approved": only_approved}
        if project_id:
            filters["project_id"] = _safe_id(project_id, "project_id")
        if image_path:
            p = Path(image_path)
            if not p.is_absolute() or not p.is_file():
                raise ToolError(f"image_path must be an absolute path to an existing file: {image_path}")
            blob = (await tools._call_upload(p))["hash"]
        if version_id or blob:
            params = {**filters, **({"version_id": _safe_id(version_id, "version_id")} if version_id else {"blob": blob})}
            if blob and not version_id and not _HASH.match(blob):
                raise ToolError("blob must be a 64-char hex hash")
            return await tools._call("GET", "/api/search/similar", params=params, timeout=300)
        if text and text.strip():
            return await tools._call("POST", "/api/search/similar", data={**filters, "text": text}, timeout=300)
        raise ToolError("pass one of version_id, blob, image_path or text")

    @mcp.tool(annotations=read_only)
    async def find_duplicate_assets(project_id: str, threshold: float = 0.95, only_approved: bool = False
                                    ) -> dict[str, Any]:
        """Near-duplicate groups among a project's image assets: 'exact' (same file / identical pixels), 'near'
        (pHash distance <= 6) and 'similar' (CLIP cosine >= threshold; frames of one run are not paired that way)."""
        return await tools._call("GET", f"/api/projects/{_safe_id(project_id, 'project_id')}/duplicates",
                                 params={"threshold": threshold, "only_approved": only_approved}, timeout=300)

    @mcp.tool(annotations=read_only)
    async def get_style_bible(project_id: str) -> dict[str, Any]:
        """The project's style bible (null if never built): palette, outline, light direction, pixel_art flag,
        reference images, the prompt `suffix`/`negative` added to new runs of the project, `active`, `version`."""
        return await tools._call("GET", f"/api/projects/{_safe_id(project_id, 'project_id')}/style")

    @mcp.tool(annotations=acts)
    async def build_style_bible(project_id: str, vision: bool = True) -> dict[str, Any]:
        """(Re)build the project's style bible from its approved image assets. vision=True lets Claude write the
        prompt suffix/negative from a contact sheet (costs API credits); False uses measured stats only. While
        active, new runs in the project get the suffix, negative and default style reference (start_run
        use_style_bible=False opts one run out)."""
        return await tools._call("POST", f"/api/projects/{_safe_id(project_id, 'project_id')}/style/build",
                                 json={"vision": vision}, timeout=300)

    @mcp.tool(annotations=acts)
    async def build_asset_pack(project_id: str, engines: list[Literal["unity", "godot"]] | None = None,
                               license: Literal["cc0", "cc-by-4.0", "proprietary"] = "cc0",
                               version: str | None = None, include: list[str] | None = None,
                               out_dir: str | None = None, author: str = "", org: str = "assetstudio",
                               description: str = "", atlas: bool = False, mobile: bool = False
                               ) -> dict[str, Any]:
        """Package a project's approved assets into a Unity UPM package and/or Godot addon folder + zip on local
        disk (never pushed anywhere), with README, CHANGELOG (vs the previous pack), LICENSE and provenance.json
        (recipe, models + licenses, prompts, seeds per asset; assets made with non-commercial/unverified models are
        flagged). version None = previous pack's patch + 1; include = approved version ids (None = all);
        out_dir = absolute folder (None = <data>/packs). atlas = also pack every 2D sprite into one trimmed atlas
        (2048 POT pages; Unity pre-sliced sprites, Godot AtlasTexture .tres; result.atlas has bytes before/after).
        mobile = Android/iOS texture budget (ASTC/ETC2, max size, mipmaps per asset type; result.mobile has the
        estimated GPU memory table)."""
        body: dict[str, Any] = {"engines": engines or ["unity"], "license": license, "author": author, "org": org,
                                "description": description, "atlas": atlas, "mobile": mobile}
        for k, v in (("version", version), ("include", include), ("out_dir", out_dir)):
            if v is not None:
                body[k] = v
        return await tools._call("POST", f"/api/projects/{_safe_id(project_id, 'project_id')}/pack", json=body,
                                 timeout=900)

    @mcp.tool(annotations=read_only)
    async def recipe_stats(days: int | None = 30, project_id: str | None = None) -> dict[str, Any]:
        """Cost and time per recipe over the last `days` calendar days including today (from local midnight, the
        same window as the app's dashboard; None = all time): run counts by status, failure rate, active duration
        avg/p50/p90 in seconds (succeeded runs; union of step run times, so gate waits and idle gaps are excluded),
        Claude/API cost in USD, cache-hit ratio, plus totals and the most expensive step kinds."""
        params: dict[str, Any] = {}
        if days is not None:
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            params["since"] = (today - timedelta(days=max(1, days) - 1)).timestamp()
        if project_id:
            params["project_id"] = project_id
        body = await tools._call("GET", "/api/stats/recipes", params=params)
        body["steps"] = (await tools._call("GET", "/api/stats/steps", params=params))["steps"]
        return body

    @mcp.tool(annotations=acts)
    async def engine_preview(step_id: str, engine: Literal["godot", "unity"] = "godot", timeout_s: float = 900,
                             include_image: bool = True, ppu: float = 100.0) -> CallToolResult:
        """Export a finished step for Godot/Unity, open it in the real engine (Godot headless import + GL render;
        Unity batchmode, ~1-3 min) and return import errors/warnings plus a screenshot. Needs godot_exe or
        unity_exe + unity_project set in the app's tool settings."""
        from studio_core.mcp.engine_preview import engine_preview as run_preview

        return await run_preview(link, _safe_id(step_id, "step_id"), engine, timeout_s, include_image, ppu)

    return mcp


def run_stdio(data_dir: Path | None = None) -> None:
    settings = load_settings(data_dir)
    logs = settings.data_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(logs / "mcp.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter(f"%(asctime)s [{os.getpid()}] %(levelname)s %(name)s: %(message)s"))
    fh.setLevel(logging.INFO)
    logging.getLogger("studio_core").addHandler(fh)
    logging.getLogger("studio_core").setLevel(logging.INFO)
    log.info("mcp server start, data_dir=%s", settings.data_dir)
    try:
        build_server(CoreLink(settings.data_dir)).run("stdio")
    finally:
        log.info("mcp server exit")
