"""`claude.procedural_3d`: Claude writes bpy code -> worker builds + renders -> Claude vision QA -> fix, up to N rounds.

The engine has no loop construct, so the write/build/review loop lives in one step and every round goes to meta."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from ...config import Settings
from ...contracts import Lane, Outputs, StepContext, StepError, StepResult
from ..claude import DEFAULT_MODEL, QueryFn, ask, put_json, sdk_query
from .client import BlenderResource
from .executor import SCRIPT_ERRORS, call_script, put_frames, worker_hash

MAX_ROUNDS = 3

CODE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"code": {"type": "string"}, "notes": {"type": "string"}},
    "required": ["code", "notes"],
    "additionalProperties": False,
}

QA_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ok": {"type": "boolean"},
        "score": {"type": "integer", "minimum": 1, "maximum": 10},
        "issues": {"type": "array", "items": {"type": "string"}},
        "polish": {"type": "array", "items": {"type": "string"}},
        "fix_hint": {"type": "string"},
    },
    "required": ["ok", "score", "issues", "fix_hint"],
    "additionalProperties": False,
}

CODE_SYSTEM = """You write Blender 5.x Python (bpy) scripts that procedurally model ONE game asset from a short idea.
The script runs headless at top level in an EMPTY scene (no objects, camera, lights) inside an isolated namespace.

Contract (checked automatically after the script runs):
- Geometry only: mesh objects (curves/text are converted). No cameras, lights, world, render or export calls,
  no bpy.ops.wm.*, no file I/O, no addons. Allowed imports: bpy, bmesh, math, mathutils, random, colorsys,
  itertools, functools. Predefined names: bpy, bmesh, math, Vector, Matrix, Euler, Quaternion, params.
- Placement: centered on the world origin in X/Y, lowest point exactly at z = 0 (stands on the ground), +Z up,
  the asset's front faces -Y.
- Size: total height = params.get("height", 1.0) Blender units, other dimensions in proportion.
- Materials: set colors through Principled BSDF node inputs, never material.diffuse_color alone, and find shader
  nodes by type, never by name (names are localized). Use the predefined helpers:
  new_material(name, color, metallic=0.0, roughness=0.5, emission=None, emission_strength=0.0) -> Material,
  color = (r, g, b) sRGB 0..1 or "#rrggbb"; principled(material) -> its Principled BSDF node.
  Assign with obj.data.materials.append(mat). Every mesh needs a material.
- Quality: a clear silhouette from 20-30 degrees above, parts that touch or overlap (nothing floating),
  3-20 parts, enough segments for round shapes (24-48), flat shading for hard surfaces and
  `for p in mesh.polygons: p.use_smooth = True` for organic parts. Modifiers (bevel, subdivision, mirror, solidify)
  are fine; they are applied at export. Stay under ~60k triangles.
- Prefer bpy.data + bmesh (bmesh.ops.create_cube/create_cone/create_uvsphere, then bm.to_mesh) or
  bpy.ops.mesh.primitive_*_add(...). Blender 4.1+ removed mesh.use_auto_smooth; do not use it.
- Deterministic: if you use random, call random.seed(params.get("seed", 0)) first. Must finish in a few seconds.
Return the COMPLETE script in `code` (no markdown fences) and a one-line summary in `notes`."""

QA_SYSTEM = """You review procedurally modeled game assets. You get renders of ONE asset, rotated about its vertical
axis (angles given), orthographic camera slightly above, transparent background.
Judge: does it clearly read as the requested idea; plausible proportions; all parts attached (nothing floating or
clipping badly); sensible colors/materials; no artifacts (black/inverted faces, z-fighting, stray geometry).
Ignore absolute scale and ground placement (checked numerically elsewhere). Judge against the requested style: flat
shading, few parts and no surface detail (planks, rivets, textures) are correct for low-poly, not defects.
Score: 9-10 polished; 7-8 reads clearly as the idea with no blocking defect, fine as a first pass; 5-6 recognizable
but has a blocking defect; 3-4 hard to recognize or broken; 1-2 wrong object or empty.
issues: ONLY blocking defects (wrong read, missing requested part, floating/detached part, artifact, broken
material), at most 4, short and concrete. polish: optional nice-to-haves; they never lower the score below 7.
ok = true when issues is empty.
fix_hint: concrete modeling instructions for the blocking issues only (which part, what to change, by how much)."""


def contract_issues(report: dict[str, Any], height: float) -> list[str]:
    lo, hi = report.get("bbox_min"), report.get("bbox_max")
    if not lo or not hi:
        return ["no bounding box reported"]
    issues: list[str] = []
    h = hi[2] - lo[2]
    tol = max(0.02 * height, 0.005)
    if abs(lo[2]) > tol:
        issues.append(f"lowest point is at z={lo[2]:.3f}; it must be at z=0")
    if height > 0 and not 0.85 * height <= h <= 1.15 * height:
        issues.append(f"total height is {h:.3f}; it must be {height:g} (±15%)")
    for axis, i in (("x", 0), ("y", 1)):
        c = (lo[i] + hi[i]) / 2
        if abs(c) > max(0.05 * height, 0.01):
            issues.append(f"bounding box center {axis}={c:.3f}; it must be centered on the origin")
    if report.get("no_material"):
        issues.append(f"objects without material: {', '.join(report['no_material'][:8])}")
    if int(report.get("tris", 0)) > 150_000:
        issues.append(f"{report['tris']} triangles is too heavy (keep under ~60k)")
    return issues


def write_prompt(idea: str, style: str, height: float, prev: dict[str, Any] | None) -> str:
    parts = [f"Idea: {idea}"]
    if style:
        parts.append(f"Style: {style}")
    parts.append(f'params = {{"height": {height:g}}}')
    if prev is None:
        parts.append("Write the script.")
        return "\n".join(parts)
    parts.append("Your previous script:\n" + prev["code"])
    if prev.get("error"):
        parts.append("It FAILED in Blender:\n" + prev["error"])
    else:
        problems = list(prev.get("contract") or [])
        qa = prev.get("qa") or {}
        problems += [f"reviewer: {x}" for x in qa.get("issues") or []]
        parts.append("Problems found:\n- " + "\n- ".join(problems or ["reviewer was not satisfied"]))
        if qa.get("fix_hint"):
            parts.append("Reviewer fix hint: " + qa["fix_hint"])
    parts.append("Return the full corrected script; keep what already works.")
    return "\n\n".join(parts)


def qa_prompt(idea: str, style: str, angles: list[float], report: dict[str, Any], contract: list[str]) -> str:
    lines = [f"Requested asset: {idea}" + (f" (style: {style})" if style else ""),
             f"Views in order, yaw degrees: {', '.join(str(round(a)) for a in angles)}",
             f"Mesh: {report.get('mesh_objects')} objects, {report.get('tris')} triangles, "
             f"materials: {', '.join(report.get('materials') or []) or 'none'}"]
    if contract:
        lines.append("Automatic checks already failed: " + "; ".join(contract))
    return "\n".join(lines)


def pick_best(rounds: list[dict[str, Any]]) -> dict[str, Any] | None:
    built = [r for r in rounds if r.get("glb")]
    if not built:
        return None
    return max(built, key=lambda r: (r["accepted"], (r.get("qa") or {}).get("score", 0), r["round"]))


class Procedural3DExecutor:
    """CLOUD lane: most of the wall time is Claude; the worker's own call lock serializes the Blender part."""

    kind = "claude.procedural_3d"
    lane = Lane.CLOUD
    version = "1"

    def __init__(self, settings: Settings, blender: BlenderResource, query: QueryFn = sdk_query):
        self.settings = settings
        self.blender = blender
        self.required_resource = blender
        self.query = query

    def cacheable(self, params: dict[str, Any]) -> bool:
        return bool(params.get("cache"))

    def cache_material(self, params: dict[str, Any]) -> str:
        return worker_hash() + CODE_SYSTEM + QA_SYSTEM

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        idea = str(params.get("idea") or "").strip()
        if not idea:
            raise StepError("procedural_bad_params", "params.idea is required")
        style = str(params.get("style") or "").strip()
        height = float(params.get("height", 1.0))
        max_rounds = max(1, min(MAX_ROUNDS, int(params.get("rounds", MAX_ROUNDS))))
        accept_score = int(params.get("accept_score", 7))
        render = {"count": int(params.get("views", 6)), "res": int(params.get("res", 256)),
                  "elevation": params.get("elevation", 20), "engine": "BLENDER_EEVEE"}
        code_q = {"model": params.get("code_model") or DEFAULT_MODEL, "max_turns": 2, "system": CODE_SYSTEM,
                  "schema": CODE_SCHEMA, "_timeout_s": 600}
        qa_q = {"model": params.get("qa_model") or DEFAULT_MODEL, "max_turns": 2, "system": QA_SYSTEM, "schema": QA_SCHEMA,
                "_timeout_s": 300}
        cwd = str(ctx.job_dir)
        await asyncio.to_thread(ctx.job_dir.mkdir, parents=True, exist_ok=True)
        t0 = time.perf_counter()
        rounds: list[dict[str, Any]] = []
        cost = 0.0
        prev: dict[str, Any] | None = None
        for n in range(1, max_rounds + 1):
            if ctx.cancelled.is_set():
                break
            rec: dict[str, Any] = {"round": n, "accepted": False}
            rounds.append(rec)
            ctx.progress(3 * (n - 1), 3 * max_rounds, f"round {n}: writing bpy")
            reply = await ask(self.query, code_q, write_prompt(idea, style, height, prev), cwd)
            cost += reply.meta.get("cost_usd") or 0
            code = _strip_fences(str((reply.structured or {}).get("code") or ""))
            rec |= {"code": code, "notes": (reply.structured or {}).get("notes"), "write_s": reply.meta.get("seconds")}
            ctx.progress(3 * (n - 1) + 1, 3 * max_rounds, f"round {n}: building")
            try:
                res = await call_script(self.blender, ctx, code, script_params={"height": height}, render=render,
                                        timeout_s=float(params.get("timeout_s", 120)), tag=f"round{n}")
            except StepError as e:
                if e.code not in SCRIPT_ERRORS:
                    raise
                rec["error"] = e.message
                ctx.log(f"round {n}: script failed: {e.message.splitlines()[0][:200]}", "warning")
                prev = rec
                continue
            report = {k: v for k, v in res.items() if k not in ("out", "render")}
            frames = sorted(res["render"]["frames"], key=lambda f: f["angle"])
            rec |= {"glb": res["out"], "frames": frames, "report": report,
                    "contract": contract_issues(report, height), "build_s": res.get("total_s")}
            ctx.progress(3 * (n - 1) + 2, 3 * max_rounds, f"round {n}: reviewing")
            images = await asyncio.to_thread(lambda: [(Path(f["file"]).read_bytes(), "image/png") for f in frames])
            qa = await ask(self.query, qa_q, qa_prompt(idea, style, [f["angle"] for f in frames], report,
                                                       rec["contract"]), cwd, images)
            cost += qa.meta.get("cost_usd") or 0
            verdict = qa.structured if isinstance(qa.structured, dict) else {}
            rec |= {"qa": verdict, "qa_s": qa.meta.get("seconds")}
            good = bool(verdict.get("ok")) or int(verdict.get("score") or 0) >= accept_score
            rec["accepted"] = good and not rec["contract"]
            ctx.log(f"round {n}: score {verdict.get('score')} ok={verdict.get('ok')} contract={rec['contract']}")
            prev = rec
            if rec["accepted"]:
                break
        best = pick_best(rounds)
        summary = [{k: v for k, v in r.items() if k not in ("frames", "glb")} for r in rounds]
        if best is None:
            raise StepError("procedural_failed", f"no round produced a mesh in {len(rounds)} round(s)",
                            detail={"rounds": summary})
        outputs: Outputs = {
            "meshes": [await ctx.blobs.put_file(Path(best["glb"]), name="procedural.glb", mime="model/gltf-binary")],
            "images": await put_frames(ctx, best["frames"]),
            "code": [await ctx.blobs.put_bytes(best["code"].encode("utf-8"), name="procedural.py",
                                               mime="text/x-python")],
            "json": [await put_json(ctx, {"idea": idea, "style": style, "best_round": best["round"],
                                          "rounds": summary}, "rounds.json")],
        }
        ctx.progress(1, 1, "done")
        return StepResult(outputs=outputs, meta={
            "rounds": len(rounds), "best_round": best["round"], "accepted": best["accepted"],
            "score": (best.get("qa") or {}).get("score"), "issues": (best.get("qa") or {}).get("issues"),
            "contract": best.get("contract"), "tris": best["report"].get("tris"),
            "history": [{"round": r["round"], "accepted": r["accepted"], "error": (r.get("error") or "")[:300] or None,
                         "score": (r.get("qa") or {}).get("score"), "contract": r.get("contract")} for r in rounds],
            "cost_usd": round(cost, 4), "seconds": round(time.perf_counter() - t0, 2)})


def _strip_fences(code: str) -> str:
    s = code.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else ""
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()
