"""Ops for animating an existing Spine skeleton from a reference video: `op.video_frames`, `op.spine_rig_summary`,
`op.spine_add_anim`, `op.spine_render_strip`."""
from __future__ import annotations

import asyncio
import json
import math
import re
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ..config import _default_ffmpeg_exe
from ..contracts import BlobRef, Outputs, StepContext, StepError, StepResult
from . import spine_anim as sa
from . import spine_runtime as rt
from . import spine_skirt as sk
from . import spine_weld as sw
from .executors import OpExecutor, _put_png
from .rig import _put_json, _read_json

STRIP_BG = (236, 232, 244, 255)
MAX_FRAMES = 48


def _one(inputs: Outputs, name: str) -> BlobRef:
    refs = inputs.get(name) or []
    if not refs:
        raise StepError("op_no_input", f"input '{name}' is empty")
    return refs[0]


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _label(img: Image.Image, text: str) -> None:
    d = ImageDraw.Draw(img)
    size = max(14, img.height // 28)
    f = _font(size)
    box = d.textbbox((0, 0), text, font=f)
    d.rectangle((0, 0, box[2] + size, box[3] + size // 2), fill=(0, 0, 0, 170))
    d.text((size // 2, size // 4), text, fill=(255, 255, 255, 255), font=f)


async def read_text(ctx: StepContext, ref: BlobRef) -> str:
    return await asyncio.to_thread(ctx.blobs.path(ref.hash).read_text, encoding="utf-8-sig")


async def read_skeleton(ctx: StepContext, inputs: Outputs, name: str = "skeleton") -> tuple[str, dict[str, Any]]:
    text = await read_text(ctx, _one(inputs, name))
    try:
        doc = json.loads(text)
    except ValueError as e:
        raise StepError("op_bad_input", f"'{name}' is not skeleton JSON: {e}") from e
    if not isinstance(doc, dict) or "bones" not in doc:
        raise StepError("op_bad_input", f"'{name}' is not a Spine skeleton JSON (no bones)")
    return text, doc


def region_images(ctx: StepContext, inputs: Outputs, name: str = "atlas") -> rt.RegionImages:
    refs = inputs.get(name) or []
    if not refs:
        raise StepError("op_no_input", f"input '{name}' is empty")
    atlas_path = ctx.blobs.path(refs[0].hash)
    images = rt.RegionImages.from_files(atlas_path, [ctx.blobs.path(r.hash) for r in refs[1:]])
    need = len(images.atlas.pages)
    if len(refs) - 1 < need:
        raise StepError("op_bad_input", f"atlas has {need} page images but {len(refs) - 1} were given; pass the "
                        "atlas file path so its pages next to it come along")
    return images


def _times(raw: Any, duration: float, n: int) -> list[float]:
    if isinstance(raw, (list, tuple)):
        return [float(t) for t in raw]
    if isinstance(raw, str) and raw.strip() and raw.strip() != "auto":
        return [float(t) for t in re.split(r"[,\s]+", raw.strip()) if t]
    n = max(2, n)
    return [round(duration * i / (n - 1), 3) for i in range(n)]


def animation_duration(doc: dict[str, Any], anim: str) -> float:
    a = (doc.get("animations") or {}).get(anim)
    if a is None:
        raise StepError("op_bad_input", f"skeleton has no animation '{anim}'")
    return max((float(k.get("time", 0)) for tl in a.get("bones", {}).values() for tr in tl.values() for k in tr),
               default=0.0)


def render_strip(doc: dict[str, Any], images: rt.RegionImages, anim: str | None, times: list[float],
                 parts: list[str], height: int = 560) -> tuple[Image.Image, dict[str, Any]]:
    """One labelled cell per time, all framed by the same box (union over the clip) so nothing is cropped."""
    skins, missing = rt.resolve_skins(doc, parts)
    atts = rt.skin_attachments(doc, skins)
    poses = [rt.Pose(doc, anim, t) for t in times]
    pts = np.concatenate([rt.attachment_points(p, atts) for p in [rt.Pose(doc), *poses]])
    if not len(pts):
        raise StepError("op_bad_input", "nothing visible with these skins")
    x0, y0 = pts.min(0)
    x1, y1 = pts.max(0)
    pad = 0.04 * max(x1 - x0, y1 - y0)
    box = (x0 - pad, y0 - pad, x1 + pad, y1 + pad)
    scale = height / (box[3] - box[1])
    cells = []
    for t, pose in zip(times, poses):
        cell = Image.new("RGBA", (round((box[2] - box[0]) * scale), height), STRIP_BG)
        cell.alpha_composite(rt.render(pose, atts, images, box, scale))
        _label(cell, f"t={t:g}s")
        cells.append(cell)
    strip = Image.new("RGBA", (sum(c.width for c in cells) + 4 * (len(cells) - 1), height), (40, 36, 52, 255))
    x = 0
    for c in cells:
        strip.paste(c, (x, 0))
        x += c.width + 4
    return strip, {"times": times, "skins": skins, "missing_skins": missing, "box": [round(v, 2) for v in box],
                   "anim": anim}


def render_zoom(doc: dict[str, Any], images: rt.RegionImages, anim: str | None, t: float,
                center: tuple[float, float], r: float, parts: list[str], label: str, size: int = 420) -> Image.Image:
    skins, _ = rt.resolve_skins(doc, parts)
    atts = rt.skin_attachments(doc, skins)
    pose = rt.Pose(doc, anim, t)
    x, y = center
    cell = Image.new("RGBA", (size, size), STRIP_BG)
    cell.alpha_composite(rt.render(pose, atts, images, (x - r, y - r, x + r, y + r), size / (2 * r)))
    _label(cell, label)
    return cell


def render_joint_zoom(doc: dict[str, Any], images: rt.RegionImages, anim: str | None, t: float, bone: str,
                      parts: list[str], size: int = 420) -> Image.Image:
    """Close-up around a joint pivot (radius ≈ the parent bone's length) at time t, labelled."""
    by = {b["name"]: b for b in doc["bones"]}
    parent = by[bone].get("parent")
    r = 0.75 * max(by.get(parent, {}).get("length", 0.0), by[bone].get("length", 0.0), 40.0)
    x, y = rt.Pose(doc, anim, t).world[bone][1]
    return render_zoom(doc, images, anim, t, (x, y), r, parts, f"zoom {bone} t={t:g}s", size)


def render_held(doc: dict[str, Any], images: rt.RegionImages, anim: str, held: list[str], parts: list[str],
                height: int = 520, step: float = 0.05) -> tuple[Image.Image, dict[str, Any]] | None:
    """The draft wearing a hand-carried accessory, at the time a held bone is highest (arm raised the most)."""
    if not held:
        return None
    end = animation_duration(doc, anim)
    poses = [(round(i * step, 4), rt.Pose(doc, anim, i * step)) for i in range(int(end / step) + 1)]
    _, t, bone = max((float(p.world[b][1][1]), t, b) for t, p in poses for b in held)
    slots = [s for s in doc.get("slots") or [] if s["bone"] == bone and s.get("attachment")]
    skin = next((sk["name"] for sk in doc.get("skins") or [] for s in slots
                 if s["attachment"] in ((sk.get("attachments") or {}).get(s["name"]) or {})), None)
    if skin is None:
        return None
    img, _ = render_strip(doc, images, anim, [t], [*parts, skin], height)
    return img, {"bone": bone, "time": t, "skin": skin}


def render_hand_zoom(doc: dict[str, Any], images: rt.RegionImages, anim: str, follow: list[dict[str, Any]],
                     parts: list[str], step: float = 0.05) -> tuple[Image.Image, dict[str, Any]] | None:
    """Close-up of a hand when its arm swings fastest, to judge whether the fingers trail or stay rigid."""
    tl = ((doc.get("animations") or {}).get(anim) or {}).get("bones") or {}
    if not follow:
        return None
    src = follow[0]["src"]
    end = animation_duration(doc, anim)

    def angle(t: float) -> float:
        return sum(sa._sample_rotate(tl[b]["rotate"], t) for b in src if "rotate" in tl.get(b, {}))

    _, t = max((abs(angle(i * step + step) - angle(i * step)), round(i * step + step / 2, 3))
               for i in range(int(end / step)))
    hand = src[-1]
    return render_joint_zoom(doc, images, anim, t, hand, parts), {"bone": hand, "time": t}


def skirt_views(doc: dict[str, Any], chains: list[list[str]], mode: str = "auto") -> list[str]:
    """Skins to preview the skirt with: the given list, or the longest and the shortest skin bound to the chains."""
    mode = (mode or "auto").strip()
    if mode.lower() == "none" or not chains:
        return []
    if mode.lower() != "auto":
        found, _ = rt.resolve_skins(doc, [p for p in re.split(r"[,\s]+", mode) if p])
        return found
    bones = {b for c in chains for b in c}
    names = [b["name"] for b in doc["bones"]]
    slot_bone = {s["name"]: s["bone"] for s in doc.get("slots") or []}
    pose = rt.Pose(doc)
    reach: dict[str, float] = {}
    for skin in doc.get("skins") or []:
        for slot, atts in (skin.get("attachments") or {}).items():
            for att in atts.values():
                if att.get("type") == "mesh" and slot in slot_bone and \
                        sa._att_bones(att, slot_bone[slot], names) & bones:
                    low = float(rt.mesh_world(att, pose, slot_bone[slot])[:, 1].min())
                    reach[skin["name"]] = min(reach.get(skin["name"], low), low)
    if not reach:
        return []
    ordered = sorted(reach, key=reach.get)
    return list(dict.fromkeys([ordered[0], ordered[-1]]))


def render_views(doc: dict[str, Any], images: rt.RegionImages, anim: str, skins: list[str], parts: list[str],
                 height: int = 420) -> list[tuple[Image.Image, str]]:
    end = animation_duration(doc, anim)
    times = [round(end * f, 2) for f in (0.2, 0.4, 0.6, 0.8)]
    return [(render_strip(doc, images, anim, times, [*parts, s], height)[0], s) for s in skins]


def render_knee_zoom(doc: dict[str, Any], images: rt.RegionImages, anim: str, t: float, knees: list[str],
                     parts: list[str], size: int = 420) -> Image.Image:
    """Both knees at time t, framed to show the thighs and shins around them."""
    pose = rt.Pose(doc, anim, t)
    a, b = (pose.world[k][1] for k in knees)
    by = {x["name"]: x for x in doc["bones"]}
    r = max(0.6 * abs(float(a[0] - b[0])) + 0.5 * max(by[k].get("length", 0.0) for k in knees), 60.0)
    return render_zoom(doc, images, anim, t, ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), r, parts,
                       f"knees t={t:g}s", size)


class SpineRigSummary(OpExecutor):
    kind = "op.spine_rig_summary"
    version = "2"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        _, doc = await read_skeleton(ctx, inputs)
        covers = sa.joint_covers(doc, skin_parts(params), str(params.get("joint_cover") or "auto"))
        welded = await welded_joints(ctx, inputs)
        text = await asyncio.to_thread(sa.rig_summary, doc, int(params.get("limit", 3000)), covers, welded)
        ref = await ctx.blobs.put_bytes(text.encode("utf-8"), name="rig_summary.txt", mime="text/plain")
        return StepResult(outputs={"summary": [ref]}, meta={"chars": len(text), "bones": len(doc["bones"]),
                                                            "cover": covers, "welded": welded,
                                                            "hip": sa.hip_bone(doc)})


def skin_parts(params: dict[str, Any]) -> list[str]:
    return [p for p in re.split(r"[,\s]+", str(params.get("skin") or "")) if p]


async def welded_joints(ctx: StepContext, inputs: Outputs) -> list[str]:
    """Joint bones from an `op.spine_weld_joints` report ({joints: [...]}) wired as input 'welded'."""
    raw = await _read_json(ctx, inputs, "welded", required=False)
    joints = raw.get("joints") if isinstance(raw, dict) else raw
    return [str(b) for b in joints] if isinstance(joints, list) else []


class SpineWeldJoints(OpExecutor):
    kind = "op.spine_weld_joints"
    version = "2"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        text, doc = await read_skeleton(ctx, inputs)
        spec = await _read_json(ctx, inputs, "spec", required=False)
        if isinstance(spec, dict) and isinstance(spec.get("spec"), dict):
            spec = spec["spec"]
        joints = sw.resolve_joints(doc, str(params.get("joints") or "auto"), spec if isinstance(spec, dict) else None)
        src = _one(inputs, "skeleton")
        if not joints:
            report = {"joints": [], "changed": 0, "skipped": []}
            return StepResult(outputs={"skeleton": [src], "original": [src],
                                       "joints": [await _put_json(ctx, report, "welded_joints.json")]}, meta=report)
        new_text, report = await asyncio.to_thread(sw.weld_text, text, joints)
        if report["skipped"]:
            ctx.log(f"left as is (deform timelines): {', '.join(report['skipped'][:8])}", "warning")
        stem = Path(src.name or "skeleton.json").stem
        out: Outputs = {
            "skeleton": [await ctx.blobs.put_bytes(new_text.encode("utf-8"), name=f"{stem}.json",
                                                   mime="application/json")],
            "original": [src],
            "joints": [await _put_json(ctx, report, "welded_joints.json")],
        }
        return StepResult(outputs=out, meta=report)


class SpineAddAnim(OpExecutor):
    kind = "op.spine_add_anim"
    version = "2"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        text, _ = await read_skeleton(ctx, inputs)
        spec = await _read_json(ctx, inputs, "spec")
        if isinstance(spec, dict) and isinstance(spec.get("spec"), dict):
            spec = spec["spec"]  # a review result {score, critique, spec}
        name = str(params.get("name") or "anim").strip()
        covers = sa.joint_covers(json.loads(text), skin_parts(params), str(params.get("joint_cover") or "auto"))
        res = await asyncio.to_thread(add_anim, text, spec, name, covers, await welded_joints(ctx, inputs),
                                      str(params.get("hang") or "auto"))
        stem = Path(_one(inputs, "skeleton").name or "skeleton.json").stem
        out: Outputs = {
            "skeleton": [await ctx.blobs.put_bytes(res["text"].encode("utf-8"), name=f"{stem}.json",
                                                   mime="application/json")],
            "anim": [await _put_json(ctx, res["anim"], f"{name}.anim.json")],
        }
        return StepResult(outputs=out, meta={"name": name, "bones": len(res["anim"]["bones"])})


def add_anim(text: str, spec: Any, name: str, covers: dict[str, str] | None = None,
             welded: list[str] | None = None, hang: str = "auto", skirt: str = "auto") -> dict[str, Any]:
    if not isinstance(spec, dict) or "duration" not in spec:
        raise StepError("spec_invalid", "motion spec must be an object with at least 'duration'")
    doc = json.loads(text)
    hip = sa.hip_bone(doc)
    bone_map = {"hip": hip} if hip and not (spec.get("bones") or {}).get("hip") else None
    try:
        plan = sa.hang_plan(doc, spec, hang)
        swing = sk.skirt_plan(doc, spec, skirt)
        fingers = sa.follow_plan(doc, spec)
        anim = sa.build_animation(spec, bone_map, sa.joint_limits(doc, spec, covers, welded), plan, swing, fingers)
    except (sa.SpecError, KeyError, TypeError, ValueError) as e:
        raise StepError("spec_invalid", f"motion spec: {e}") from e
    new_text = sa.add_animation(text, name, anim)
    problems = sa.check_animation(json.loads(new_text), name)
    if problems:
        raise StepError("spec_invalid", "; ".join(problems[:6]), detail={"problems": problems})
    return {"text": new_text, "anim": anim, "hang": [h["bone"] for h in plan],
            "skirt": swing["chains"] if swing else [], "follow": fingers}


class SpineRenderStrip(OpExecutor):
    kind = "op.spine_render_strip"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        _, doc = await read_skeleton(ctx, inputs)
        images = region_images(ctx, inputs)
        anim = params.get("anim") or None
        dur = animation_duration(doc, anim) if anim else 0.0
        times = _times(params.get("times", "auto"), dur, int(params.get("count", 8))) if anim else [0.0]
        parts = skin_parts(params)
        strip, meta = await asyncio.to_thread(render_strip, doc, images, anim, times, parts,
                                              int(params.get("height", 560)))
        if meta["missing_skins"]:
            ctx.log(f"skins not found: {', '.join(meta['missing_skins'])}", "warning")
        return StepResult(outputs={"strip": [await _put_png(ctx, strip, f"{anim or 'setup'}_strip.png")]}, meta=meta)


def parse_window(raw: Any) -> tuple[float | None, float | None]:
    s = str(raw or "auto").strip().lower()
    if s in ("", "auto", "all"):
        return None, None
    m = re.fullmatch(r"([\d.]+)\s*[-–:]\s*([\d.]+)", s)
    if not m or float(m.group(2)) <= float(m.group(1)):
        raise StepError("op_bad_param", f"window must look like '1.6-5.6' or 'auto', got {raw!r}")
    return float(m.group(1)), float(m.group(2))


def figure_box(frames: list[Image.Image], pad: float = 0.06) -> tuple[int, int, int, int] | None:
    """Union bbox of pixels that differ from the frame border colour (the figure on a plain backdrop)."""
    box = None
    for f in frames:
        a = np.asarray(f.convert("RGB"), np.int16)
        border = np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]])
        bg = np.median(border, axis=0)
        mask = np.abs(a - bg).max(axis=2) > 40
        ys, xs = np.nonzero(mask)
        if len(xs) < 50:
            continue
        b = (xs.min(), ys.min(), xs.max(), ys.max())
        box = b if box is None else (min(box[0], b[0]), min(box[1], b[1]), max(box[2], b[2]), max(box[3], b[3]))
    if box is None:
        return None
    w, h = frames[0].size
    px, py = (box[2] - box[0]) * pad, (box[3] - box[1]) * pad
    return (max(0, int(box[0] - px)), max(0, int(box[1] - py)), min(w, int(box[2] + px) + 1),
            min(h, int(box[3] + py) + 1))


def contact_sheets(frames: list[Image.Image], times: list[float], cols: int, rows: int, cell_h: int
                   ) -> list[Image.Image]:
    per = cols * rows
    sheets = []
    for start in range(0, len(frames), per):
        chunk = list(zip(frames[start:start + per], times[start:start + per]))
        cell_w = round(frames[0].width * cell_h / frames[0].height)
        n_rows = math.ceil(len(chunk) / cols)
        sheet = Image.new("RGB", (cols * cell_w + (cols - 1) * 4, n_rows * cell_h + (n_rows - 1) * 4), (40, 36, 52))
        for i, (f, t) in enumerate(chunk):
            cell = f.convert("RGBA").resize((cell_w, cell_h), Image.Resampling.LANCZOS)
            _label(cell, f"#{start + i} {t:.2f}s")
            sheet.paste(cell.convert("RGB"), ((i % cols) * (cell_w + 4), (i // cols) * (cell_h + 4)))
        sheets.append(sheet)
    return sheets


class VideoFrames(OpExecutor):
    kind = "op.video_frames"
    version = "1"

    def __init__(self, settings: Any = None):
        self.settings = settings

    def _ffmpeg(self) -> str:
        exe = getattr(self.settings, "ffmpeg_exe", None)
        return str(exe) if exe else _default_ffmpeg_exe()

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        src = ctx.blobs.path(_one(inputs, "video").hash)
        fps = min(max(float(params.get("fps", 5)), 0.5), 12.0)
        t0, t1 = parse_window(params.get("window"))
        cols, _, rows = str(params.get("tile", "8x2")).lower().partition("x")
        cols_n, rows_n = max(1, int(cols or 8)), max(1, int(rows or 2))
        out_dir = Path(ctx.job_dir) / "frames"
        out_dir.mkdir(parents=True, exist_ok=True)
        args = [self._ffmpeg(), "-v", "error", "-y"]
        if t0 is not None:
            args += ["-ss", f"{t0}", "-to", f"{t1}"]
        args += ["-i", str(src), "-vf", f"fps={fps}", "-frames:v", str(MAX_FRAMES), str(out_dir / "f_%03d.png")]
        try:
            proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.PIPE)
        except OSError as e:
            raise StepError("ffmpeg_missing", f"ffmpeg not runnable ({args[0]}): {e}. Set its path in Settings.") from e
        _, err = await proc.communicate()
        files = sorted(out_dir.glob("f_*.png"))
        if proc.returncode != 0 or not files:
            raise StepError("ffmpeg_failed", (err or b"").decode("utf-8", "replace")[-800:] or "no frames decoded")
        start = t0 or 0.0
        times = [round(start + i / fps, 3) for i in range(len(files))]
        frames = [Image.open(f).convert("RGB") for f in files]
        box = figure_box(frames) if params.get("crop", "auto") == "auto" else None
        if box:
            frames = [f.crop(box) for f in frames]
        sheets = await asyncio.to_thread(contact_sheets, frames, times, cols_n, rows_n, int(params.get("cell", 360)))
        meta = {"fps": fps, "t0": start, "times": times, "count": len(frames), "crop": list(box) if box else None,
                "tiles": f"{cols_n}x{rows_n}"}
        out: Outputs = {
            "sheets": [await _put_png(ctx, s, f"ref_sheet_{i + 1}.png") for i, s in enumerate(sheets)],
            "frames": [await _put_png(ctx, f, f"ref_{t:06.2f}s.png") for f, t in zip(frames, times)],
            "meta": [await _put_json(ctx, meta, "frames_meta.json")],
        }
        return StepResult(outputs=out, meta=meta)


class SpineSkirtMesh(OpExecutor):
    kind = "op.spine_skirt_mesh"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        text, doc = await read_skeleton(ctx, inputs)
        chains = sk.skirt_chains(doc, str(params.get("chains") or "auto"))
        new_text, report = await asyncio.to_thread(sk.skirt_text, text, chains)
        if report["skipped"]:
            ctx.log(f"skirt pieces left rigid (deform timeline or under 16px): {', '.join(report['skipped'][:8])}",
                    "warning")
        src = _one(inputs, "skeleton")
        out = [src] if new_text == text else [
            await ctx.blobs.put_bytes(new_text.encode("utf-8"), name=src.name or "skeleton.json",
                                      mime="application/json")]
        meta = {"chains": chains, "changed": len(report["changed"]), "skipped": report["skipped"]}
        return StepResult(outputs={"skeleton": out, "report": [await _put_json(ctx, report, "skirt_mesh.json")]},
                          meta=meta)


SPINE_REF_OPS: tuple[type[OpExecutor], ...] = (SpineRigSummary, SpineAddAnim, SpineRenderStrip, SpineWeldJoints,
                                               SpineSkirtMesh)
