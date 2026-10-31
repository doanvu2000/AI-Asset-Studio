"""Combat frame data for an attack/idle sprite sheet: `op.frame_data` finds hurtboxes (body alpha without weapon/effect
pixels), hitboxes (pixels that are new against the same direction's idle on the active frames) and the events
attack_start / hit_active / recover / footstep, writes frame_data.json and a box-overlay QA sheet."""
from __future__ import annotations

import asyncio
import json
import re
from typing import TYPE_CHECKING, Any

import numpy as np
from PIL import Image, ImageDraw

from ..contracts import Outputs, StepContext, StepError, StepResult
from .executors import OpExecutor, _images_in, _load, _put_png, _read_json
from .fx import dilate

if TYPE_CHECKING:
    from ..export.sheet import Layout, Track

FRAME_FILE = "frame_data.json"
FRAME_TYPE = "asset-studio/frame-data"
PREVIEW_FILE = "frame_data_preview.png"
ACTIVE_TYPE = "asset-studio/combat-active"
EVENTS = ("attack_start", "hit_active", "recover", "footstep")

NON_COMBAT = {"idle", "walk", "run", "sprint", "jog", "move", "jump", "fall", "land", "death", "die", "dead", "hit",
              "hurt", "stun", "block", "spawn", "victory", "win"}
MOVE_CLIPS = {"walk", "run", "sprint", "jog", "move"}
BANDS = {1: (1.0,), 2: (0.6, 0.4), 3: (0.25, 0.4, 0.35)}
PEAK_SHARE = 0.6
BOX_COLORS = {"hurt": (60, 220, 90, 255), "hit": (240, 50, 50, 255), "foot": (255, 220, 40, 255)}


def _tokens(name: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", name.lower()) if t}


def parse_names(raw: Any) -> set[str] | None:
    """`auto`/empty -> None (decide by clip name); otherwise the listed clip names."""
    text = str(raw or "").strip().lower()
    if text in ("", "auto"):
        return None
    return {t.strip() for t in re.split(r"[,\n;]", text) if t.strip()}


def parse_active(raw: Any) -> dict[str | None, list[int]]:
    """`slash: 3-5, 7; cast: 4` (0-based frame indices, like the cell names); with no clip name the frames apply to
    every attack clip (key None)."""
    out: dict[str | None, list[int]] = {}
    for group in re.split(r"[;\n]", str(raw or "")):
        group = group.strip()
        if not group:
            continue
        name: str | None = None
        if ":" in group:
            head, group = group.split(":", 1)
            name = head.strip().lower() or None
        frames: list[int] = []
        for part in group.split(","):
            part = part.strip()
            if not part:
                continue
            m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", part)
            try:
                if m:
                    a, b = int(m[1]), int(m[2])
                    if b < a:
                        raise ValueError
                    frames += range(a, b + 1)
                else:
                    frames.append(int(part))
            except ValueError as e:
                raise StepError("op_bad_params", f"active: cannot read frames {part!r} (use 3, 3-5, 7)") from e
        if not frames:
            raise StepError("op_bad_params", f"active: no frames in {group!r}")
        out.setdefault(name, []).extend(frames)
    return {k: sorted(set(v)) for k, v in out.items()}


def clip_role(name: str, attack: set[str] | None, forced: bool = False) -> str:
    toks = _tokens(name)
    if "idle" in toks:
        return "idle"
    if forced or (attack is not None and (name.lower() in attack or toks & attack)):
        return "attack"
    if attack is None and not toks & NON_COMBAT:
        return "attack"
    return "move" if toks & MOVE_CLIPS else "other"


def mask_bbox(mask: np.ndarray) -> list[int] | None:
    ys, xs = np.nonzero(mask)
    if not len(ys):
        return None
    return [int(xs.min()), int(ys.min()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)]


def hurtboxes(body: np.ndarray, count: int) -> list[list[int]]:
    box = mask_bbox(body)
    if box is None:
        return []
    x0, y0, w, h = box
    out, start = [], 0.0
    for share in BANDS[max(1, min(count, 3))]:
        a, b = y0 + round(start * h), y0 + round((start + share) * h)
        start += share
        if b <= a:
            continue
        band = mask_bbox(body[a:b])
        if band is not None:
            out.append([band[0], a + band[1], band[2], band[3]])
    return out


def label_sparse(mask: np.ndarray) -> np.ndarray:
    """4-connected labels 1..K of a sparse mask by flood fill over its set pixels (damage.components is too slow for
    full-size cells)."""
    h, w = mask.shape
    labels = np.zeros((h, w), np.int32)
    todo = set(zip(*(a.tolist() for a in np.nonzero(mask))))
    k = 0
    while todo:
        k += 1
        stack = [todo.pop()]
        while stack:
            y, x = stack.pop()
            labels[y, x] = k
            for n in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if n in todo:
                    todo.remove(n)
                    stack.append(n)
    return labels


def new_pixels(mask: np.ndarray, ref: np.ndarray, grow: int, min_area: int) -> np.ndarray:
    """Pixels of `mask` farther than `grow` px from the reference silhouette, minus specks under `min_area`."""
    fresh = mask & ~dilate(ref, grow, True)
    if not fresh.any():
        return fresh
    labels = label_sparse(fresh)
    keep = np.bincount(labels.ravel())
    keep[0] = 0
    good = keep >= max(1, min_area)
    return good[labels]


def hit_shape(fresh: np.ndarray) -> dict[str, Any] | None:
    from ..export.collider import trace

    box = mask_bbox(fresh)
    if box is None:
        return None
    polys = trace(fresh, merge="hull")
    poly = [[round(x, 1), round(y, 1)] for x, y in polys[0]] if polys else []
    return {"box": box, "polygon": poly}


def foot_contacts(bodies: list[np.ndarray], pivot_x: float, tol: int) -> list[int]:
    """Frames where one foot lands: that side's lowest opaque row reaches the clip's ground line after being off it
    on the previous (cyclic) frame. Left/right is split at the pivot column."""
    n = len(bodies)
    split = int(round(pivot_x))
    bottoms = np.full((2, n), -1, np.int64)
    for i, b in enumerate(bodies):
        for side, part in enumerate((b[:, :split], b[:, split:])):
            rows = np.nonzero(part.any(axis=1))[0]
            if len(rows):
                bottoms[side, i] = int(rows[-1])
    if bottoms.max() < 0:
        return []
    contact = bottoms >= bottoms.max() - tol
    contact &= bottoms >= 0
    hits = {i for side in range(2) for i in range(n) if contact[side, i] and not contact[side, i - 1]}
    return sorted(hits)


def _track_clip(tracks: list[Track]) -> dict[str, list[Track]]:
    out: dict[str, list[Track]] = {}
    for t in tracks:
        out.setdefault(t.clip, []).append(t)
    return out


def _angle_gap(a: float, b: float) -> float:
    d = abs(a - b) % 360
    return min(d, 360 - d)


def _cells(alpha: np.ndarray, track: Track, thr: int) -> list[np.ndarray]:
    return [alpha[c.y:c.y + c.h, c.x:c.x + c.w] >= thr for c in track.cells]


def pick_active(curve: list[float], min_share: float) -> list[int]:
    peak = max(curve, default=0.0)
    if peak < min_share:
        return []
    cut = max(min_share, PEAK_SHARE * peak)
    return [i for i, v in enumerate(curve) if v >= cut]


def build_frame_data(alpha: np.ndarray, lay: Layout, *, asset: str = "sprite", idle_clip: str = "idle",
                     attack: set[str] | None = None, active_user: dict[str | None, list[int]] | None = None,
                     active_claude: dict[str, list[int]] | None = None, hurt_count: int = 1, min_share: float = 0.03,
                     alpha_thr: int = 128, footsteps: bool = True) -> dict[str, Any]:
    """`alpha` is the whole sheet's alpha channel; cells come from `lay`. Returns the frame_data.json document."""
    if not lay.tracks:
        raise StepError("op_bad_input", "the sheet meta has no clips or directions to read frames from")
    user, claude = active_user or {}, active_claude or {}
    warnings: list[str] = []
    clips = _track_clip(lay.tracks)
    idle_name = idle_clip.strip().lower()
    roles = {c: clip_role(c, attack, forced=c.lower() in user) for c in clips}
    for c in roles:
        if c.lower() == idle_name:
            roles[c] = "idle"
    masks = {t.name: _cells(alpha, t, alpha_thr) for t in lay.tracks}
    grow = max(1, round(0.02 * max(lay.frame_w, lay.frame_h)))
    min_px = max(4, round(0.0004 * lay.frame_w * lay.frame_h))
    tol = max(1, round(0.015 * lay.frame_h))

    idle_tracks = [t for c, ts in clips.items() if roles[c] == "idle" for t in ts]
    if not idle_tracks and any(r == "attack" for r in roles.values()):
        warnings.append("no idle clip in the sheet: each attack track is compared with its own first frame")

    def reference(t: Track) -> tuple[np.ndarray, str | None]:
        pool = [i for i in idle_tracks if i.name != t.name]
        same = [i for i in pool if i.dir == t.dir]
        pick = same[0] if same else min(pool, key=lambda i: _angle_gap(i.angle, t.angle), default=None)
        if pick is None:
            return masks[t.name][0], None
        if not same:
            warnings.append(f"{t.name}: no idle for direction {t.dir or '-'}, used {pick.name}")
        return np.logical_or.reduce(masks[pick.name]), pick.name

    fresh: dict[str, list[np.ndarray]] = {}
    refs: dict[str, str | None] = {}
    share: dict[str, list[float]] = {}
    for c, ts in clips.items():
        if roles[c] != "attack":
            continue
        for t in ts:
            ref, refs[t.name] = reference(t)
            area = max(int(ref.sum()), 1)
            fresh[t.name] = [new_pixels(m, ref, grow, min_px) for m in masks[t.name]]
            share[t.name] = [float(f.sum()) / area for f in fresh[t.name]]

    active_of: dict[str, tuple[list[int], str]] = {}
    for c, ts in clips.items():
        if roles[c] != "attack":
            continue
        n = len(ts[0].cells)
        if c.lower() in user or None in user:
            frames, src = user.get(c.lower(), user.get(None, [])), "user"
        elif c.lower() in claude or c in claude:
            frames, src = claude.get(c.lower(), claude.get(c, [])), "claude"
        else:
            curve = [sum(share[t.name][i] for t in ts) / len(ts) for i in range(n)]
            frames, src = pick_active(curve, min_share), "heuristic"
        kept = [f for f in frames if 0 <= f < n]
        if len(kept) != len(frames):
            warnings.append(f"{c}: active frames outside 0..{n - 1} were dropped")
        if not kept:
            warnings.append(f"{c}: no active frame found (no pixels new against idle): no hitbox")
            src = "none" if src == "heuristic" else src
        active_of[c] = (kept, src)

    out_tracks: list[dict[str, Any]] = []
    for c, ts in clips.items():
        role = roles[c]
        act, act_src = active_of.get(c, ([], "none"))
        for t in ts:
            n = len(t.cells)
            frames_out: list[dict[str, Any]] = []
            body_masks: list[np.ndarray] = []
            hit_seen = False
            for i, m in enumerate(masks[t.name]):
                body = m & ~fresh[t.name][i] if role == "attack" else m
                if not body.any():
                    body = m
                body_masks.append(body)
                entry: dict[str, Any] = {"i": i, "hurtboxes": hurtboxes(body, hurt_count), "hitbox": None}
                if role == "attack":
                    entry["new_share"] = round(share[t.name][i], 4)
                    if i in act:
                        entry["hitbox"] = hit_shape(fresh[t.name][i])
                        hit_seen |= entry["hitbox"] is not None
                if not m.any():
                    warnings.append(f"{t.name}[{i}]: empty frame")
                frames_out.append(entry)
            if role == "attack" and act and not hit_seen:
                warnings.append(f"{t.name}: weapon not separable from the body on the active frames: empty hitbox")
            events: list[dict[str, Any]] = []
            if role == "attack":
                events.append({"frame": 0, "name": "attack_start"})
                if act:
                    runs = [r for k, r in enumerate(act) if k == 0 or act[k - 1] != r - 1]
                    events += [{"frame": r, "name": "hit_active"} for r in runs]
                    events.append({"frame": min(act[-1] + 1, n - 1), "name": "recover"})
            if footsteps and role == "move":
                steps = foot_contacts(body_masks, lay.pivot[0], tol)
                if not steps and n >= 2:
                    steps = sorted({0, n // 2})
                events += [{"frame": s, "name": "footstep"} for s in steps]
            for e in events:
                e["time"] = round(e["frame"] / max(t.fps, 1e-6), 4)
            events.sort(key=lambda e: (e["frame"], EVENTS.index(e["name"])))
            out_tracks.append({"name": t.name, "clip": t.clip, "dir": t.dir, "angle": t.angle, "fps": t.fps,
                               "loop": t.loop, "role": role, "idle_ref": refs.get(t.name), "active": act,
                               "active_source": act_src, "frames": frames_out, "events": events})
    return {"type": FRAME_TYPE, "asset": asset, "frame": [lay.frame_w, lay.frame_h],
            "pivot": [lay.pivot[0], lay.pivot[1]], "events": list(EVENTS), "tracks": out_tracks,
            "warnings": list(dict.fromkeys(warnings))}


def contact_sheet(sheet: Image.Image, lay: Layout, clip_names: list[str], max_w: int = 1568) -> Image.Image:
    """One row per clip (first direction) with frame numbers, for Claude to read the action timing."""
    rows = []
    for name in clip_names:
        t = next((t for t in lay.tracks if t.clip == name), None)
        if t is not None:
            rows.append((name, [sheet.crop((c.x, c.y, c.x + c.w, c.y + c.h)) for c in t.cells]))
    if not rows:
        raise StepError("op_bad_input", "no clip to draw")
    cols = max(len(f) for _, f in rows)
    scale = min(1.0, max_w / (cols * lay.frame_w))
    cw, ch = max(1, round(lay.frame_w * scale)), max(1, round(lay.frame_h * scale))
    out = Image.new("RGBA", (cols * cw, len(rows) * ch), (90, 90, 96, 255))
    d = ImageDraw.Draw(out)
    for r, (name, frames) in enumerate(rows):
        for i, f in enumerate(frames):
            out.alpha_composite(f.resize((cw, ch), Image.Resampling.LANCZOS), (i * cw, r * ch))
            d.rectangle((i * cw, r * ch, i * cw + 22, r * ch + 11), fill=(0, 0, 0, 255))
            d.text((i * cw + 2, r * ch), str(i), fill=(255, 255, 255, 255))
        d.text((2, r * ch + ch - 12), name, fill=(255, 255, 0, 255))
    return out.convert("RGB")


def render_preview(sheet: Image.Image, lay: Layout, doc: dict[str, Any]) -> Image.Image:
    scale = max(1, min(4, 2048 // max(sheet.width, 1)))
    base = Image.new("RGBA", sheet.size, (70, 70, 76, 255))
    base.alpha_composite(sheet)
    img = base.resize((sheet.width * scale, sheet.height * scale), Image.Resampling.NEAREST)
    d = ImageDraw.Draw(img)
    cells = {t.name: t.cells for t in lay.tracks}

    def rect(c: Any, box: list[int], color: tuple[int, ...]) -> None:
        x, y = (c.x + box[0]) * scale, (c.y + box[1]) * scale
        d.rectangle((x, y, x + box[2] * scale - 1, y + box[3] * scale - 1), outline=color, width=max(1, scale // 2))

    for tr in doc["tracks"]:
        marks: dict[int, list[str]] = {}
        for e in tr["events"]:
            marks.setdefault(e["frame"], []).append(e["name"])
        for fr in tr["frames"]:
            c = cells[tr["name"]][fr["i"]]
            for b in fr["hurtboxes"]:
                rect(c, b, BOX_COLORS["hurt"])
            if fr["hitbox"]:
                rect(c, fr["hitbox"]["box"], BOX_COLORS["hit"])
                poly = [((c.x + x) * scale, (c.y + y) * scale) for x, y in fr["hitbox"]["polygon"]]
                if len(poly) >= 3:
                    d.line([*poly, poly[0]], fill=BOX_COLORS["hit"], width=1)
            names = marks.get(fr["i"], [])
            if names:
                d.text((c.x * scale + 2, c.y * scale + 2), ",".join(n[:4] for n in names), fill=BOX_COLORS["foot"])
    return img.convert("RGB")


def claude_active(doc: Any) -> dict[str, list[int]]:
    if not isinstance(doc, dict) or doc.get("type") != ACTIVE_TYPE:
        return {}
    out: dict[str, list[int]] = {}
    for row in doc.get("clips") or []:
        try:
            out[str(row["clip"]).lower()] = sorted({int(f) for f in row["active"]})
        except (KeyError, TypeError, ValueError):
            continue
    return out


class FrameData(OpExecutor):
    kind = "op.frame_data"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        from ..export.sheet import layout

        sheet_ref = _images_in(inputs, "sheet")[0]
        if len(inputs.get("meta") or []) != 1:
            raise StepError("op_bad_input", "expected exactly 1 'meta' (the sheet.json of the same step)")
        meta = await _read_json(ctx, inputs["meta"][0])
        if not isinstance(meta, dict):
            raise StepError("op_bad_input", "sheet meta is not an object")
        try:
            lay = layout(meta, directional_rows=True)
        except (KeyError, ValueError, TypeError) as e:
            raise StepError("op_bad_input", f"bad sheet meta: {e}") from e
        sheet = (await _load(ctx, sheet_ref)).convert("RGBA")
        if sheet.size != (lay.width, lay.height):
            raise StepError("op_bad_input", f"sheet is {sheet.size}, meta says {(lay.width, lay.height)}")
        try:
            hurt = int(params.get("hurtboxes", 1))
            min_share = float(params.get("min_new", 0.03))
            thr = int(params.get("alpha", 128))
        except (TypeError, ValueError) as e:
            raise StepError("op_bad_params", str(e)) from e
        user = parse_active(params.get("active"))
        claude: dict[str, list[int]] = {}
        if not user and inputs.get("claude"):
            try:
                claude = claude_active(await _read_json(ctx, inputs["claude"][0]))
            except StepError:
                ctx.log("Claude active-frame guess unreadable; using the CPU heuristic", "warning")
        asset = str(params.get("asset") or "sprite").strip() or "sprite"
        arr = np.asarray(sheet)[..., 3]
        doc = await asyncio.to_thread(
            build_frame_data, arr, lay, asset=asset, idle_clip=str(params.get("idle_clip") or "idle"),
            attack=parse_names(params.get("attack_clips")), active_user=user, active_claude=claude,
            hurt_count=hurt, min_share=min_share, alpha_thr=thr, footsteps=_flag(params.get("footsteps"), True))
        doc["sheet_meta"] = meta
        for w in doc["warnings"]:
            ctx.log(w, "warning")
        ctx.progress(0.7)
        preview = await asyncio.to_thread(render_preview, sheet, lay, doc)
        prev_ref = await _put_png(ctx, preview, PREVIEW_FILE)
        ref = await ctx.blobs.put_bytes(json.dumps(doc, indent=1).encode(), name=FRAME_FILE, mime="application/json")
        ctx.progress(1.0)
        n_hit = sum(1 for t in doc["tracks"] for f in t["frames"] if f["hitbox"])
        return StepResult(outputs={"frames": [ref], "preview": [prev_ref], "sheet": [sheet_ref],
                                   "meta": inputs["meta"]},
                          meta={"tracks": len(doc["tracks"]), "hitboxes": n_hit, "warnings": doc["warnings"],
                                "active": {t["name"]: t["active"] for t in doc["tracks"] if t["role"] == "attack"}})


def _flag(v: Any, default: bool) -> bool:
    if v is None:
        return default
    return str(v).strip().lower() not in ("false", "0", "no", "off", "")


FRAME_DATA_OPS = (FrameData,)
