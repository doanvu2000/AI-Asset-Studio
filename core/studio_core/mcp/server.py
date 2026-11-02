from __future__ import annotations

import asyncio
import io
import json
import logging
import mimetypes
import os
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import CallToolResult, ImageContent, TextContent, ToolAnnotations

from studio_core.config import load_settings
from studio_core.engine.gpu import MAX_YIELD_MIN
from studio_core.mcp.catalog import catalog_markdown
from studio_core.mcp.activity import ActivityReporter, run_id_of, summarize
from studio_core.mcp.followup import ANIM, ANIM_ACTIONS, KIT, follow_ups, kit_available
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
IMPORT_RECIPE = "library-import"
CATALOG_URI = "asset-studio://recipes"
ENGINE_WAIT = {"unity": "waiting for Unity batchmode (import + render, 1-3 min)",
               "godot": "waiting for Godot headless import + render"}

INSTRUCTIONS = """AI Asset Studio: game-asset pipelines (recipes) running on this machine.
Typical flow: list_recipes -> describe_recipe -> start_run (image/mesh/file/video inputs = absolute local file
paths; an atlas file input uploads its page images too)
-> wait_run -> if status is awaiting_human, look at the gate candidates and call resolve_gate (only when
gate_policy is 'agent' or the user asked you to choose) -> wait_run again -> get_outputs (returns absolute
paths of exported copies). Runs started here also appear in the desktop app. GPU recipes can take minutes;
wait_run returns on timeout so call it again.
Shortcuts: generate = start_run + wait (auto_first) + get_outputs in one call; system_status before GPU work;
follow_up_options(run_id) lists what an output can feed next; export_to_engine copies a result into a Unity/Godot
project; create_project/update_project lock a style; import_to_library adds existing files. Resource
asset-studio://recipes is the full recipe catalog (Markdown).
skybox-360: gate candidates are 2:1 equirectangular panoramas (left edge wraps to right, top/bottom rows are the
poles); prefer a level horizon and no objects cut by the edges. Outputs: <asset>_pano.png, six cube faces
(front/right/back/left/up/down), irradiance and skybox.json (sun direction, seam score).
vfx-particles: CPU-only unless `style` is set (then Z-Image draws 4 styled puffs). Outputs: particle textures,
<asset>_vfx_preview.png, vfx_particles.json (engine-neutral emitter presets) and hit_spark (flipbook for hit-FX);
engine export gives Unity ParticleSystem prefabs / Godot GPUParticles2D+3D scenes.
prop-destructible: mesh = a closed prop GLB (e.g. prop-3d's final). The approve gate shows 8 exploded-view renders;
destructible.json lists every <asset>_chunkNN with volume, mass, triangles and closed flag (volume_error should stay
under 0.03). Outputs: <asset>_intact/_fractured GLB + FBX; engine export adds Unity prefab builder / Godot scenes.
mesh-retexture: mesh = an existing .glb/.fbx/.obj path (geometry and UVs are kept; uv=new re-unwraps). Gate `check`
shows the painted views (reject to repaint with another seed); gate `approve` shows a turntable next to the original.
retexture.json gives unpainted_pct (% of the visible surface no view painted, aim < 2) and UV overlap. Outputs:
<stem>_retextured.glb + <stem>_original.glb, PBR maps (albedo/normal/roughness/metallic/AO) and pbr_material.json."""


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


_LANE_WORK = {"gpu": "GPU job (ComfyUI)", "blender": "Blender job", "cloud": "cloud/Claude call", "cpu": "CPU work"}


def substatus(s: dict[str, Any]) -> str | None:
    """What a step is waiting on, in words the app and an agent can show as-is."""
    st, lane = s["status"], s.get("lane")
    if st == "waiting_resource":
        return f"waiting for {(s.get('meta') or {}).get('waiting_for') or lane} to be free"
    if st == "awaiting_human":
        return "waiting for a gate decision (pick/approve)"
    if st == "running":
        return _LANE_WORK.get(str(lane), "running")
    return None


def _step_view(s: dict[str, Any]) -> dict[str, Any]:
    v = {"step_id": s["id"], "key": s["key"], "kind": s["kind"], "lane": s["lane"], "status": s["status"],
         "attempt": s.get("attempt", 1)}
    if sub := substatus(s):
        v["substatus"] = sub
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


def _wait_detail(run: dict[str, Any], steps: list[dict[str, Any]]) -> str:
    busy = [f"{s['key']}: {substatus(s)}" for s in steps if substatus(s)]
    return f"{run['recipe_id']} {run['status']}" + (f" — {'; '.join(busy[:3])}" if busy else "")


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
            elif spec.get("path") == "dir":
                spec["pass_as"] = ("absolute local folder path (read in place, not uploaded; restyle-folder writes "
                                   "into it only with output=apply, after a zip backup)")
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
                        use_style_bible: bool = True, then: list[dict[str, Any]] | None = None,
                        no_cache: bool = False) -> dict[str, Any]:
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
        if then:
            body["params"][META_KEY]["then"] = then
        if no_cache:
            body["params"][META_KEY]["no_cache"] = True
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

    async def wait_run(self, run_id: str, timeout_s: float, ctx: Context | None,
                       notify: Any = None) -> dict[str, Any]:
        _safe_id(run_id, "run_id")
        timeout_s = max(1.0, min(float(timeout_s), 3600.0))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        last_report: tuple[float, str] | None = None
        while True:
            data = await self._call("GET", f"/api/runs/{run_id}")
            run, steps = data["run"], data["steps"]
            status = run["status"]
            done, label = _progress(steps)
            if (done, label) != last_report:
                changed = last_report is None or last_report[1] != label
                last_report = (done, label)
                if ctx is not None:
                    try:
                        await ctx.report_progress(done, len(steps), f"{status}: {label}")
                    except Exception:
                        log.debug("progress notification failed", exc_info=True)
                if notify is not None and changed:
                    await notify(_wait_detail(run, steps), run_id)
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

    async def system_status(self) -> dict[str, Any]:
        try:
            out = await self._call("GET", "/api/agent/status")
        except ToolError:
            out = {"resources": await self._call("GET", "/api/resources")}
        out["mcp"] = {"data_dir": str(self.link.data_dir), "core_url": self.link.base_url,
                      "spawned_private_core": self.link.spawned_pid is not None}
        return out

    async def style_body(self, base: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
        style = dict(base)
        for k, v in fields.items():
            if v is None:
                continue
            if k in ("reference_image", "palette_image"):
                v = await self._blob_input(k, v) if v else None
            style[k] = v
        return style

    async def save_project(self, project_id: str | None, name: str | None, description: str | None,
                           archived: bool | None, style: dict[str, Any], replace_style: bool) -> dict[str, Any]:
        if project_id is None:
            if not name:
                raise ToolError("name is required to create a project")
            body: dict[str, Any] = {"name": name, "description": description or "",
                                    "style": await self.style_body({}, style)}
            p = await self._call("POST", "/api/projects", json=body)
        else:
            pid = _safe_id(project_id, "project_id")
            body = {k: v for k, v in (("name", name), ("description", description), ("archived", archived))
                    if v is not None}
            if any(v is not None for v in style.values()) or replace_style:
                current = {} if replace_style else (await self._call("GET", f"/api/projects/{pid}")).get("style", {})
                body["style"] = await self.style_body(current or {}, style)
            if not body:
                raise ToolError("nothing to change")
            p = await self._call("PATCH", f"/api/projects/{pid}", json=body)
        return {"project_id": p["id"], "name": p["name"], "description": p.get("description", ""),
                "archived": p.get("archived", False), "style": p.get("style", {})}

    async def follow_up_options(self, run_id: str, output: str | None, blob: str | None) -> dict[str, Any]:
        data = await self._call("GET", f"/api/runs/{_safe_id(run_id, 'run_id')}")
        run, steps = data["run"], data["steps"]
        clicked, step = None, None
        for s in steps:
            pub = ((s.get("meta") or {}).get("spec") or {}).get("publish") or {}
            for recipe_out, step_out in pub.items():
                for b in (s.get("outputs") or {}).get(step_out, []):
                    if (b["hash"] == blob) if blob else output in (None, recipe_out):
                        clicked, step = b, s
                        break
                if clicked:
                    break
            if clicked:
                break
        if clicked is None or step is None:
            raise ToolError(f"run {run_id} has no published output matching output={output!r} blob={blob!r} "
                            f"(status {run['status']})")
        recipes = await self._call("GET", "/api/recipes")
        options = [{**f, "inputs": {k: v[0] if len(v) == 1 else v for k, v in f["inputs"].items()}}
                   for f in follow_ups(recipes, clicked, step.get("outputs") or {}, run.get("recipe_id"))]
        out: dict[str, Any] = {
            "run_id": run["id"], "project_id": run.get("project_id"),
            "output": {"hash": clicked["hash"], "name": clicked.get("name"), "mime": clicked.get("mime"),
                       "step": step["key"]},
            "options": options,
            "next": "start_run(recipe, inputs=<option inputs + the recipe's text inputs>, project_id=...); "
                    "blob hashes are accepted wherever a file path is."}
        if any(o["recipe"] == ANIM and not o["missing"] for o in options):
            out["animation_actions"] = list(ANIM_ACTIONS)
        if kit_available(run["recipe_id"], clicked, {r["id"] for r in recipes}):
            out["character_kit"] = {"recipe": KIT["first"], "inputs": {"image": clicked["hash"]},
                                    "then": [{"recipe_id": KIT["then"], "map": {"image": "inputs.image"}}],
                                    "note": "start_run(recipe, inputs, then=then) chains 8-dir -> Spine in the core"}
        return out

    async def finish(self, run_id: str, timeout_s: float, ctx: Context | None, notify: Any,
                     include_images: bool) -> CallToolResult:
        view = await self.wait_run(run_id, timeout_s, ctx, notify)
        if view["status"] != "succeeded":
            return await self.with_thumbs(view, include_images and view["status"] == "awaiting_human")
        res = await self.get_outputs(run_id, include_images)
        sc = {**(res.structured_content or {}), "steps": view["steps"], "recipe": view["recipe"]}
        res.structured_content = sc
        res.content[0] = TextContent(type="text", text=json.dumps(sc, indent=1))
        return res

    async def import_files(self, paths: list[str], project_id: str | None, asset_name: str | None,
                           approve: bool) -> dict[str, Any]:
        if not paths:
            raise ToolError("paths must list at least one absolute file path")
        title = asset_name or Path(paths[0]).stem
        started = await self.start_run(IMPORT_RECIPE, {"files": list(paths), "review": False}, title, "auto_first",
                                       project_id)
        view = await self.wait_run(started["run_id"], 120, None)
        if view["status"] != "succeeded":
            raise ToolError(f"import run {started['run_id']} ended {view['status']}: {view.get('error')}")
        out = []
        for v in await self._call("GET", "/api/versions", params={"run_id": started["run_id"]}):
            if approve:
                v = await self._call("POST", f"/api/versions/{v['id']}/approve")
            out.append({"version_id": v["id"], "status": v["status"], "asset_name": v["asset_name"],
                        "name": v["blob"].get("name"), "hash": v["blob"]["hash"], "mime": v["blob"].get("mime")})
        return {"run_id": started["run_id"], "versions": out}

    async def _engine_step(self, run_id: str, engine: str) -> str:
        data = await self._call("GET", f"/api/runs/{_safe_id(run_id, 'run_id')}")
        for s in reversed(data["steps"]):
            if s["status"] not in ("succeeded", "cached") or not s.get("outputs") or s["kind"].startswith("gate."):
                continue
            if engine in (await self._call("GET", f"/api/steps/{s['id']}/export")).get("engines", []):
                return s["id"]
        raise ToolError(f"no finished step of run {run_id} exports to {engine}")

    async def export_to_engine(self, step_id: str | None, run_id: str | None, engine: str, dest_dir: str | None,
                               name: str | None, ppu: float, pixel: bool | None, package: str | None = None,
                               webp: bool = False) -> dict[str, Any]:
        if (step_id is None) == (run_id is None):
            raise ToolError("pass exactly one of step_id or run_id")
        dest: Path | None = None
        if dest_dir and engine in ("flutter", "android", "ios"):
            raise ToolError(f"dest_dir is only for unity/godot; {engine} exports are read from export_dir")
        if dest_dir:
            dest = Path(dest_dir)
            if not dest.is_absolute() or not await asyncio.to_thread(dest.is_dir):
                raise ToolError(f"dest_dir must be an existing absolute folder: {dest_dir}")
            from ..export.restyle import project_root

            # An agent-chosen path is only written to when it sits inside a project of the requested engine.
            kind, root = await asyncio.to_thread(project_root, dest)
            if kind != engine or (engine == "unity" and not dest.resolve().is_relative_to(root / "Assets")):
                where = "<project>/Assets" if engine == "unity" else "a Godot project (folder with project.godot)"
                raise ToolError(f"dest_dir must be inside {where}: {dest_dir}")
        sid = _safe_id(step_id, "step_id") if step_id else await self._engine_step(run_id or "", engine)
        body: dict[str, Any] = {"engine": engine, "ppu": ppu}
        if name:
            body["name"] = name
        if pixel is not None:
            body["pixel"] = pixel
        if package:
            body["package"] = package
        if webp:
            body["webp"] = True
        out = await self._call("POST", f"/api/steps/{sid}/export", json=body)
        res: dict[str, Any] = {"step_id": sid, "engine": engine, "export_dir": out["dir"], "name": out.get("name")}
        if dest is not None:
            src = Path(out["dir"])
            target = dest / src.name
            if engine == "unity":
                from ..export.unity import copy_unity_export

                reused = await asyncio.to_thread(copy_unity_export, src, target, root / "Assets")
                if reused:
                    res["reused_scripts"] = reused
            else:
                from ..export.godot_copy import copy_godot_export

                reused = await asyncio.to_thread(copy_godot_export, src, target, root)
                if reused:
                    res["reused_scripts"] = reused
            res["copied_to"] = str(target)
        return res


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


class StudioServer(MCPServer):
    """Reports every tool call to the core's agent activity log so the desktop app can show it live."""

    def __init__(self, reporter: ActivityReporter, *args: Any, **kw: Any) -> None:
        super().__init__(*args, **kw)
        self.reporter = reporter

    def _note_client(self, request_context: Any) -> None:
        try:
            self.reporter.set_client(request_context.session.client_params.client_info)
        except AttributeError:
            pass

    async def _handle_list_tools(self, ctx: Any, params: Any) -> Any:
        self._note_client(ctx)
        await self.reporter.connect(spawn=False)
        return await super()._handle_list_tools(ctx, params)

    async def call_tool(self, name: str, arguments: dict[str, Any], context: Context | None = None) -> Any:
        rep = self.reporter
        if context is not None and context._request_context is not None:
            self._note_client(context.request_context)
        await rep.connect(spawn=True)
        t0 = time.monotonic()
        await rep.post("start", tool=name, summary=summarize(arguments), run_id=run_id_of(arguments))
        try:
            res = await super().call_tool(name, arguments, context)
        except Exception as e:
            await rep.post("error", tool=name, detail=str(e)[:400], run_id=run_id_of(arguments),
                           seconds=round(time.monotonic() - t0, 2))
            raise
        sc = getattr(res, "structured_content", None)
        await rep.post("error" if getattr(res, "is_error", False) else "end", tool=name,
                       run_id=run_id_of(arguments, sc), seconds=round(time.monotonic() - t0, 2),
                       detail=_result_detail(sc))
        return res


def _result_detail(sc: Any) -> str | None:
    if not isinstance(sc, dict):
        return None
    sc = sc.get("result", sc) if set(sc) == {"result"} else sc
    if not isinstance(sc, dict):
        return None
    if "status" in sc:
        return f"status {sc['status']}" + (f", {len(sc['files'])} files" if isinstance(sc.get("files"), list) else "")
    return None


def build_server(link: CoreLink) -> MCPServer:
    tools = StudioTools(link)
    reporter = ActivityReporter(link)

    @asynccontextmanager
    async def lifespan(_: MCPServer) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await reporter.disconnect()
            await link.close()

    mcp = StudioServer(reporter, "asset-studio", instructions=INSTRUCTIONS, lifespan=lifespan, log_level="WARNING")
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
                        use_style_bible: bool = True, then: list[dict[str, Any]] | None = None,
                        no_cache: bool = False) -> dict[str, Any]:
        """Start a recipe run and return its run_id immediately (use wait_run to follow it).

        inputs: {input_name: value}; image/mesh/file/video inputs take absolute file paths (uploaded for
        you; an atlas input brings its page images).
        gate_policy: 'human' (default) = the user picks/approves in the desktop app;
        'agent' = you look at candidates (get_run/wait_run) and call resolve_gate;
        'auto_first' = wait_run auto-picks the first candidate(s) and approves every approve gate.
        project_id (from list_projects): the project's locked style (prompt prefix/suffix, preset, palette,
        reference image, default inputs) fills the inputs you leave out; inputs you pass always win.
        use_style_bible=False skips the project's active style bible (get_style_bible) for this run.
        then: follow-up runs the core starts when this one succeeds, [{recipe_id, map: {input: 'outputs.<name>' |
        'inputs.<name>'}, inputs?, title?}] (see follow_up_options' character_kit).
        no_cache=True runs every step again instead of reusing results of an identical earlier step (deterministic
        steps and Claude answers are reused by default; random-seed generations never are)."""
        return await tools.start_run(recipe, inputs, title, gate_policy, project_id, use_style_bible, then,
                                     no_cache)

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
        view = await tools.wait_run(run_id, timeout_s, ctx, notify)
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
    async def build_asset_pack(project_id: str, engines: list[Literal["flutter", "android", "ios", "unity", "godot"]] | None = None,
                               license: Literal["cc0", "cc-by-4.0", "proprietary"] = "cc0",
                               version: str | None = None, include: list[str] | None = None,
                               out_dir: str | None = None, author: str = "", org: str = "assetstudio",
                               description: str = "", atlas: bool = False, mobile: bool = False,
                               package: str | None = None, webp: bool = False,
                               flutter_demo: bool = False) -> dict[str, Any]:
        """Package a project's approved assets into per-engine folders + zip on local disk (never pushed anywhere):
        flutter (main target: assets/ + merged pubspec + Assets.dart), android (res/drawable-*dpi + Kotlin helper),
        ios (xcassets + Swift helper), Unity UPM package and/or Godot addon, with README, CHANGELOG (vs the previous pack), LICENSE and provenance.json
        (recipe, models + licenses, prompts, seeds per asset; assets made with non-commercial/unverified models are
        flagged). version None = previous pack's patch + 1; include = approved version ids (None = all);
        out_dir = absolute folder (None = <data>/packs). atlas = also pack every 2D sprite into one trimmed atlas
        (2048 POT pages; Unity pre-sliced sprites, Godot AtlasTexture .tres; result.atlas has bytes before/after).
        mobile = Android/iOS texture budget (ASTC/ETC2, max size, mipmaps per asset type; result.mobile has the
        estimated GPU memory table). package = Android package of the Kotlin helper (engine android); webp = Flutter
        density images as lossless WebP. flutter_demo = also write flutter/demo/, a runnable Flame project
        (8-dir player, follow camera, tmx map, parallax; `flutter run` inside it). engines None = ["flutter"]."""
        body: dict[str, Any] = {"engines": engines or ["flutter"], "license": license, "author": author, "org": org,
                                "description": description, "atlas": atlas, "mobile": mobile}
        if package:
            body["package"] = package
        if webp:
            body["webp"] = True
        if flutter_demo:
            body["flutter_demo"] = True
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

        await reporter.post("wait", tool="engine_preview", run_id=None, step_id=step_id,
                            detail=ENGINE_WAIT[engine])
        return await run_preview(link, _safe_id(step_id, "step_id"), engine, timeout_s, include_image, ppu)

    async def notify(detail: str, run_id: str | None) -> None:
        await reporter.post("wait", tool="wait", detail=detail, run_id=run_id)

    @mcp.resource(CATALOG_URI, name="recipe-catalog", title="Recipe catalog", mime_type="text/markdown",
                  description="Every recipe with inputs, outputs, gates and whether it needs the GPU/Blender.")
    async def recipe_catalog() -> str:
        return catalog_markdown(await tools._call("GET", "/api/recipes"))

    @mcp.tool(annotations=read_only)
    async def system_status() -> dict[str, Any]:
        """Health before heavy work: core version + data dir, ComfyUI/Blender status, what each lane (gpu, blender)
        is busy with (one GPU job at a time), open gates, missing model packs, GPU name/VRAM, which engines
        (Unity/Godot) can preview, and which agents are connected. Check it when a run sits in waiting_resource."""
        return await tools.system_status()

    @mcp.tool(annotations=read_only)
    async def gpu_status() -> dict[str, Any]:
        """GPU lane state: whether the studio is yielding the GPU to another app (and until when), which runs hold
        or wait for the GPU/Blender lanes in grant order, and whether ComfyUI has models loaded."""
        return await tools._call("GET", "/api/gpu")

    @mcp.tool(annotations=acts)
    async def yield_gpu(minutes: float = 15) -> dict[str, Any]:
        """Lend the GPU to another project or app on this machine: new GPU/Blender work waits, ComfyUI unloads its
        models, and the yield ends by itself after `minutes` (max 1440). Call resume_gpu as soon as you are done."""
        if not 0 < minutes <= MAX_YIELD_MIN:
            raise ToolError(f"minutes must be between 0 and {MAX_YIELD_MIN}")
        return await tools._call("POST", "/api/gpu/yield", json={"minutes": minutes})

    @mcp.tool(annotations=acts)
    async def resume_gpu() -> dict[str, Any]:
        """End a GPU yield now so queued GPU/Blender runs continue."""
        return await tools._call("POST", "/api/gpu/resume")

    @mcp.tool(annotations=acts)
    async def prioritize_run(run_id: str, delta: int | None = None, position: int | None = None) -> dict[str, Any]:
        """Move a run waiting for the GPU in the queue: delta = priority change (positive = sooner), or position =
        0-based place in the grant order. Give exactly one."""
        if (delta is None) == (position is None):
            raise ToolError("give exactly one of delta or position")
        body = {"delta": delta} if delta is not None else {"position": position}
        return await tools._call("POST", f"/api/gpu/queue/{_safe_id(run_id, 'run_id')}/priority", json=body)

    @mcp.tool(annotations=acts)
    async def control_service(name: Literal["comfyui", "blender"], action: Literal["start", "stop"],
                              force: bool = False) -> dict[str, Any]:
        """Start or stop a local service (resource). Stopping a busy one needs force=true (kills its job)."""
        path = f"/api/resources/{name}/{action}"
        return await tools._call("POST", path, params={"force": force} if action == "stop" else None, timeout=300)

    @mcp.tool(annotations=acts)
    async def create_project(name: str, description: str = "", prompt_prefix: str | None = None,
                             prompt_suffix: str | None = None, negative: str | None = None,
                             preset: str | None = None, reference_image: str | None = None,
                             palette_image: str | None = None, default_inputs: dict[str, Any] | None = None,
                             pixel: dict[str, Any] | None = None, notes: str | None = None) -> dict[str, Any]:
        """Create a project (one game / asset set) with a locked style: English prompt prefix/suffix/negative,
        export preset id, reference/palette image (absolute path or blob hash), default recipe inputs, pixel-art
        settings. Every start_run with its project_id gets that style for inputs left empty."""
        style = {"prompt_prefix": prompt_prefix, "prompt_suffix": prompt_suffix, "negative": negative,
                 "preset": preset, "reference_image": reference_image, "palette_image": palette_image,
                 "inputs": default_inputs, "pixel": pixel, "notes": notes}
        return await tools.save_project(None, name, description, None, style, False)

    @mcp.tool(annotations=acts)
    async def update_project(project_id: str, name: str | None = None, description: str | None = None,
                             archived: bool | None = None, prompt_prefix: str | None = None,
                             prompt_suffix: str | None = None, negative: str | None = None,
                             preset: str | None = None, reference_image: str | None = None,
                             palette_image: str | None = None, default_inputs: dict[str, Any] | None = None,
                             pixel: dict[str, Any] | None = None, notes: str | None = None,
                             replace_style: bool = False) -> dict[str, Any]:
        """Rename / describe / archive (archived=true) or restore a project, or change its locked style. Style
        fields you pass are merged into the current style (an empty string clears one); replace_style=true starts
        from an empty style instead."""
        style = {"prompt_prefix": prompt_prefix, "prompt_suffix": prompt_suffix, "negative": negative,
                 "preset": preset, "reference_image": reference_image, "palette_image": palette_image,
                 "inputs": default_inputs, "pixel": pixel, "notes": notes}
        return await tools.save_project(project_id, name, description, archived, style, replace_style)

    @mcp.tool(annotations=acts)
    async def import_to_library(paths: list[str], project_id: str | None = None, asset_name: str | None = None,
                                approve: bool = True) -> dict[str, Any]:
        """Put existing files (absolute paths: PNG, GLB/FBX, JSON, atlas...) into the Library as versions of a
        project, like generated assets (approve=true marks them approved so packs/QA/similar search see them)."""
        return await tools.import_files(paths, project_id, asset_name, approve)

    @mcp.tool(annotations=read_only)
    async def follow_up_options(run_id: str, output: str | None = None, blob: str | None = None) -> dict[str, Any]:
        """"Dùng tiếp cho…": recipes a finished run's output can feed next (e.g. concept image -> 8-dir sprite,
        Spine export -> skin/animation), with file inputs already filled as blob hashes. output = recipe output name
        (default: the first published one) or blob = a hash. Also returns the character kit chain when it applies
        (8-dir then Spine) and the animation actions for sprite-anim-video. Start one with start_run."""
        return await tools.follow_up_options(run_id, output, blob)

    @mcp.tool(annotations=acts)
    async def generate(recipe: str, inputs: dict[str, Any], project_id: str | None = None, title: str | None = None,
                       timeout_s: float = 1800, include_images: bool = True,
                       ctx: Context | None = None) -> CallToolResult:
        """One call for simple agents: start_run with gate_policy auto_first, wait until it finishes (auto-picking
        the first candidate at every gate) and return get_outputs (absolute file paths). On timeout it returns the
        run view with timed_out=true: then call wait_run(run_id) and get_outputs yourself."""
        started = await tools.start_run(recipe, inputs, title, "auto_first", project_id)
        await reporter.post("wait", tool="generate", detail=f"{recipe} started", run_id=started["run_id"])
        return await tools.finish(started["run_id"], timeout_s, ctx, notify, include_images)

    @mcp.tool(annotations=acts)
    async def export_to_engine(engine: Literal["flutter", "android", "ios", "unity", "godot"],
                               step_id: str | None = None, run_id: str | None = None, dest_dir: str | None = None,
                               name: str | None = None, ppu: float = 100.0, pixel: bool | None = None,
                               package: str | None = None, webp: bool = False) -> dict[str, Any]:
        """Export a finished step (or the last engine-ready step of run_id) for an engine. flutter is the main
        target (assets/ + pubspec snippet + Assets.dart); android (res/drawable-*dpi + Kotlin helper, `package` sets
        its namespace) and ios (xcassets + Swift helper) are mobile-native; unity/godot give a folder (sprites
        pre-sliced, animations, prefab/scene builders). webp = Flutter density images as lossless WebP. With
        dest_dir, unity/godot folders are copied straight into a game project (absolute folder; for Unity use a
        folder under <project>/Assets, for Godot under the project root); mobile targets are not copied (read
        export_dir)."""
        return await tools.export_to_engine(step_id, run_id, engine, dest_dir, name, ppu, pixel, package, webp)

    @mcp.tool(annotations=read_only)
    async def restyle_scan(folder: str, include: str = "", exclude: str = "") -> dict[str, Any]:
        """Read-only scan of a game project folder for recipe restyle-folder: images per kind (sprite sheets, 9-slice,
        textures), Unity/Godot importer info kept (GUIDs), edit units and the estimated run time. Then
        start_run('restyle-folder', {folder, style, ...}) and, once approved, restyle_apply(run_id)."""
        items = await tools._call("POST", "/api/restyle/scan", json={"folder": folder, "include": include,
                                                                      "exclude": exclude}, timeout=300)
        items["items"] = items.get("items", [])[:100]
        return items

    @mcp.tool(annotations=acts)
    async def restyle_apply(run_id: str) -> dict[str, Any]:
        """Write a finished, approved restyle-folder run back into the game folder (zip backup first; importer
        files and GUIDs untouched). Returns the backup id for restyle_undo."""
        return await tools._call("POST", "/api/restyle/apply", json={"run_id": _safe_id(run_id, "run_id")},
                                 timeout=600)

    @mcp.tool(annotations=acts)
    async def restyle_undo(backup_id: str | None = None, run_id: str | None = None,
                           force: bool = False) -> dict[str, Any]:
        """Restore a folder from a restyle backup (backup_id from restyle_apply, or the newest backup of run_id;
        with neither, lists the backups). force=true restores files changed after the apply too."""
        if backup_id is None:
            params = {"run_id": _safe_id(run_id, "run_id")} if run_id else None
            rows = (await tools._call("GET", "/api/restyle/backups", params=params))["backups"]
            if run_id is None or not rows:
                return {"backups": rows, "next": "pass backup_id to restore one"}
            backup_id = str(rows[0]["backup_id"])
        return await tools._call("POST", "/api/restyle/undo", json={"backup_id": backup_id, "force": force},
                                 timeout=600)

    @mcp.tool(annotations=read_only)
    async def audit_project(path: str, engine: Literal["auto", "unity", "godot"] = "auto", tri_budget: int = 15000,
                            duplicates: bool = True, max_rows: int = 80) -> dict[str, Any]:
        """Read-only audit of a Unity / Godot project folder (absolute path): textures vs. mobile budget and on-screen
        size (estimated GPU MB before/after), duplicate images (pHash), assets nothing references, meshes over
        tri_budget, audio import settings. Each texture issue may carry a `fix` to pass to audit_fix."""
        rep = await tools._call("POST", "/api/audit", json={"path": path, "engine": engine, "tri_budget": tri_budget,
                                                            "duplicates": duplicates}, timeout=600)
        n = max(1, min(int(max_rows), 500))
        rep["textures"] = sorted((t for t in rep.get("textures", []) if t.get("issues")),
                                 key=lambda t: -(t.get("gpu_before", 0) - t.get("gpu_after", 0)))[:n]
        for k in ("duplicates", "unreferenced", "meshes", "audio"):
            rep[k] = rep.get(k, [])[:n]
        rep["fixes"] = [i["fix"] for t in rep["textures"] for i in t.get("issues", []) if i.get("fix")]
        return rep

    @mcp.tool(annotations=acts)
    async def audit_fix(path: str, actions: list[dict[str, Any]], dry_run: bool = True) -> dict[str, Any]:
        """Apply fixes from the last audit_project of that project: {type: mobile_preset | max_size | drop_alpha,
        rel, ...} rewrite only .meta / .import (zip backup, undo via backup_id with audit_undo); {type: atlas, rels,
        name} packs sprites into a folder outside the project. dry_run=true (default) only plans. Duplicates and
        unreferenced assets are never deleted."""
        out = await tools._call("POST", "/api/audit/fix", json={"path": path, "actions": actions, "dry_run": dry_run},
                                timeout=600)
        if isinstance(out.get("report"), dict):
            out["report"] = {"summary": out["report"].get("summary")}
        return out

    @mcp.tool(annotations=acts)
    async def audit_undo(backup_id: str | None = None, path: str | None = None, force: bool = False) -> dict[str, Any]:
        """Restore importer files from an audit_fix backup; with no backup_id lists the backups (of `path`)."""
        if backup_id is None:
            rows = (await tools._call("GET", "/api/audit/backups", params={"path": path} if path else None))["backups"]
            return {"backups": rows, "next": "pass backup_id to restore one"}
        return await tools._call("POST", "/api/audit/undo", json={"backup_id": backup_id, "force": force},
                                 timeout=600)

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
